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

from app.importers.gwy_positions import (  # noqa: E402
    GwyPositionImportError,
    verify_gwy_position_import,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify imported 国考 position counts")
    parser.add_argument("--source", required=True, help="Path to gwy.db")
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
            result = verify_gwy_position_import(session, source_path=Path(args.source))
    except GwyPositionImportError as exc:
        print(f"position import verification failed: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


if __name__ == "__main__":
    raise SystemExit(main())
