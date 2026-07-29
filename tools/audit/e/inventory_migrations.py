"""Inventory Alembic lineage and the hand-assembled pytest schema fixture.

This is an audit-only reader.  It never imports migration modules or connects to
PostgreSQL, so migration side effects cannot run during inventory collection.
"""

from __future__ import annotations

import ast
import csv
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
VERSIONS = ROOT / "backend" / "migrations" / "versions"
CONFTEST = ROOT / "backend" / "tests" / "conftest.py"


def _literal_assignment(tree: ast.Module, name: str) -> str | None:
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            return value
        if value is None:
            return None
        raise ValueError(f"{name} is not a scalar string in {tree}")
    raise ValueError(f"{name} is missing")


def main() -> int:
    fixture_text = CONFTEST.read_text()
    rows: list[dict[str, str]] = []
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        revision = _literal_assignment(tree, "revision")
        if revision is None:
            raise ValueError(f"{path}: revision cannot be None")
        rows.append(
            {
                "revision": revision,
                "down_revision": _literal_assignment(tree, "down_revision") or "",
                "file": str(path.relative_to(ROOT)),
                "referenced_by_pytest_fixture": str(path.stem in fixture_text).lower(),
                "imports_current_app_runtime": str(
                    any(
                        isinstance(node, (ast.Import, ast.ImportFrom))
                        and (
                            (
                                isinstance(node, ast.ImportFrom)
                                and (node.module or "").startswith("app.")
                            )
                            or (
                                isinstance(node, ast.Import)
                                and any(alias.name.startswith("app.") for alias in node.names)
                            )
                        )
                        for node in ast.walk(tree)
                    )
                ).lower(),
            }
        )

    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
