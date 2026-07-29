#!/usr/bin/env python3
"""Read-only FastAPI route/dependency inventory for audit subagent E."""

from __future__ import annotations

import csv
import inspect
import sys
from pathlib import Path
from typing import Any, Iterable

from fastapi.routing import APIRoute, iter_route_contexts


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.main import create_app  # noqa: E402


def walk_dependencies(dependant: Any) -> Iterable[Any]:
    for dependency in dependant.dependencies:
        yield dependency
        yield from walk_dependencies(dependency)


def callable_label(value: Any) -> str:
    if value is None:
        return ""
    return f"{getattr(value, '__module__', '')}.{getattr(value, '__name__', repr(value))}"


def source_label(value: Any) -> str:
    try:
        file = Path(inspect.getsourcefile(value) or "").resolve()
        line = inspect.getsourcelines(value)[1]
    except (OSError, TypeError):
        return ""
    try:
        file = file.relative_to(ROOT)
    except ValueError:
        pass
    return f"{file}:{line}"


def main() -> None:
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            "methods",
            "path",
            "endpoint",
            "source",
            "dependency_count",
            "dependencies",
            "bearer_auth",
        ],
    )
    writer.writeheader()
    for context in iter_route_contexts(create_app().routes):
        route = context.route
        if not isinstance(route, APIRoute):
            continue
        dependencies = sorted(
            {
                callable_label(item.call)
                for item in walk_dependencies(route.dependant)
                if item.call is not None
            }
        )
        bearer_auth = any(
            dependency.endswith(".get_current_user")
            or dependency.endswith(".require_bearer_token")
            for dependency in dependencies
        )
        writer.writerow(
            {
                "methods": "|".join(sorted(route.methods or ())),
                "path": context.path,
                "endpoint": callable_label(route.endpoint),
                "source": source_label(route.endpoint),
                "dependency_count": len(dependencies),
                "dependencies": "|".join(dependencies),
                "bearer_auth": str(bearer_auth).lower(),
            }
        )


if __name__ == "__main__":
    main()
