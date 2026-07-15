#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit("publish_guokao_essay_rubrics.py requires Python 3.11+")

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.essay_intelligence.rubric_compiler import (  # noqa: E402
    EssayRubricCompilationError,
    SystemRubricCompilationDraft,
)
from app.domains.essay_intelligence.rubric_publisher import (  # noqa: E402
    EssayRubricPublishError,
    GuokaoEssayRubricPublisher,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and publish append-only system_compiled + unverified "
            "rubrics for the current Guokao essay catalog"
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="JSON file containing a drafts array of pinned rubric compilation outputs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the complete batch and report versions without inserting rows.",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        raw_drafts = payload["drafts"]
        if not isinstance(raw_drafts, list):
            raise TypeError("drafts must be an array")
        drafts = [
            SystemRubricCompilationDraft.model_validate_json(
                json.dumps(item, ensure_ascii=False)
            )
            for item in raw_drafts
        ]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "rubric_manifest_invalid",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    engine = create_engine(
        _normalize_database_url(args.database_url),
        pool_pre_ping=True,
        future=True,
    )
    try:
        with Session(engine, future=True) as session, session.begin():
            result = GuokaoEssayRubricPublisher().publish(
                session,
                drafts=drafts,
                dry_run=args.dry_run,
            )
    except EssayRubricCompilationError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "rubric_compilation_failed",
                    "issues": [asdict(item) for item in exc.issues],
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 3
    except EssayRubricPublishError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "rubric_publish_failed",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 4
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "ok": True,
                "contractVersion": result.contract_version,
                "insertedCount": result.inserted_count,
                "unchangedCount": result.unchanged_count,
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
