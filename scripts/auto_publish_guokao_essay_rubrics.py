#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.domains.essay_intelligence.rubric_ai_contracts import (  # noqa: E402
    RUBRIC_AI_PIPELINE_VERSION,
)
from app.domains.essay_intelligence.rubric_ai_pipeline import (  # noqa: E402
    GuokaoRubricAIPipeline,
    RubricAIPipelineError,
)
from app.domains.essay_intelligence.rubric_compiler import (  # noqa: E402
    EssayRubricCompilationError,
)
from app.domains.essay_intelligence.rubric_publisher import (  # noqa: E402
    EssayRubricPublishError,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Discover pending current Guokao essay tasks, generate and independently "
            "criticize complete rubrics, deterministically compile them, and append-only "
            "publish system_compiled + unverified versions."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--task-version-id",
        action="append",
        type=UUID,
        dest="task_version_ids",
        help="Only process this task version. Repeat to select multiple tasks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run both audited model calls and deterministic compilation, but do not "
            "insert rubric versions. Model-call cost records are still persisted."
        ),
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = None
    sources = ()
    successes: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    try:
        engine = create_engine(
            _normalize_database_url(args.database_url),
            pool_pre_ping=True,
            future=True,
        )
        pipeline = GuokaoRubricAIPipeline(settings=get_settings())
        with Session(engine, future=True) as session:
            sources = pipeline.discover_pending_sources(
                session,
                task_version_ids=(
                    tuple(args.task_version_ids)
                    if args.task_version_ids is not None
                    else None
                ),
            )
            session.rollback()
            for source in sources:
                try:
                    result = pipeline.run_source(
                        session,
                        source=source,
                        dry_run=args.dry_run,
                    )
                    successes.append(asdict(result))
                except EssayRubricCompilationError as exc:
                    session.rollback()
                    failures.append(
                        {
                            "taskVersionId": str(source.task_version_id),
                            "code": "rubric_compilation_failed",
                            "issues": [asdict(item) for item in exc.issues],
                        }
                    )
                except (RubricAIPipelineError, EssayRubricPublishError) as exc:
                    session.rollback()
                    failures.append(
                        {
                            "taskVersionId": str(source.task_version_id),
                            "code": getattr(exc, "code", "rubric_publish_failed"),
                            "message": str(exc),
                        }
                    )
    except Exception as exc:
        failures.append(
            {
                "taskVersionId": None,
                "code": getattr(exc, "code", "rubric_pipeline_configuration_failed"),
                "message": str(exc),
            }
        )
    finally:
        if engine is not None:
            engine.dispose()

    report = {
        "ok": not failures,
        "pipelineVersion": RUBRIC_AI_PIPELINE_VERSION,
        "dryRun": args.dry_run,
        "discoveredCount": len(sources),
        "succeededCount": len(successes),
        "failedCount": len(failures),
        "results": successes,
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if not failures else 5


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


if __name__ == "__main__":
    raise SystemExit(main())
