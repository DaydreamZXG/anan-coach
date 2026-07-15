#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit("publish_guokao_essay_catalog.py requires Python 3.11+")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.essay_intelligence.catalog import (  # noqa: E402
    EssayCatalogValidationError,
)
from app.domains.essay_intelligence.catalog_publisher import (  # noqa: E402
    EssayCatalogPublishError,
    GuokaoEssayCatalogPublisher,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Publish the append-only 国考申论 v2 catalog from "
            "product.questions and product.knowledge_chunks"
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compile and validate the complete catalog without inserting versions.",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = create_engine(
        _normalize_database_url(args.database_url),
        pool_pre_ping=True,
        future=True,
    )
    try:
        with Session(engine, future=True) as session, session.begin():
            result = GuokaoEssayCatalogPublisher().publish(
                session,
                dry_run=args.dry_run,
            )
    except EssayCatalogValidationError as exc:
        payload = {
            "ok": False,
            "error": "catalog_validation_failed",
            "issues": [asdict(item) for item in exc.issues],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except EssayCatalogPublishError as exc:
        print(
            json.dumps(
                {"ok": False, "error": "catalog_publish_failed", "message": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 3
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "ok": True,
                "contractVersion": result.contract_version,
                "sourceQuestionCount": result.source_question_count,
                "insertedCount": result.inserted_count,
                "unchangedCount": result.unchanged_count,
                "rubricPendingCount": result.rubric_pending_count,
                "dryRun": result.dry_run,
                "versions": [asdict(item) for item in result.versions],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


if __name__ == "__main__":
    raise SystemExit(main())
