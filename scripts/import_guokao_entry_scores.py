#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.importers.guokao_entry_scores import (  # noqa: E402
    DEFAULT_SOURCE_NAME,
    DEFAULT_SOURCE_URL,
    GuokaoEntryScoreImportError,
    import_guokao_entry_scores,
    verify_guokao_entry_score_import,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import 国考岗位进面分证据 from an interview-list xlsx")
    parser.add_argument("--source", required=True, help="Path to interview-list xlsx")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--source-name", default=DEFAULT_SOURCE_NAME)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--database-url",
        default=os.getenv("QBANK_IMPORT_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL. Defaults to QBANK_IMPORT_DATABASE_URL or DATABASE_URL.",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url, QBANK_IMPORT_DATABASE_URL, or DATABASE_URL is required")

    engine = create_engine(_normalize_database_url(args.database_url), pool_pre_ping=True, future=True)
    try:
        with Session(engine, future=True) as session:
            result = import_guokao_entry_scores(
                session=session,
                source_path=Path(args.source),
                year=args.year,
                source_name=args.source_name,
                source_url=args.source_url,
                batch_size=args.batch_size,
            )
            verification = verify_guokao_entry_score_import(
                session,
                year=args.year,
                source_file_sha256=result.source_file_sha256,
            )
    except GuokaoEntryScoreImportError as exc:
        print(f"entry score import failed: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    print(json.dumps({"import": result.__dict__, "verification": verification}, ensure_ascii=False, indent=2))
    return 0 if verification["ok"] else 1


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


if __name__ == "__main__":
    raise SystemExit(main())
