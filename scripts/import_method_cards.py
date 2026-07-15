#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.method_cards.importer import (  # noqa: E402
    MethodCardImportError,
    import_method_cards,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import method-card YAML into product.method_cards")
    parser.add_argument(
        "paths",
        nargs="+",
        help="YAML file or directory containing method-card YAML files",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL. Defaults to DATABASE_URL.",
    )
    args = parser.parse_args()

    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = create_engine(args.database_url, pool_pre_ping=True, future=True)
    try:
        with Session(engine, future=True) as session:
            result = import_method_cards(
                session=session,
                paths=[Path(item) for item in args.paths],
            )
    except MethodCardImportError as exc:
        print(f"method-card import failed: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    ids = ", ".join(result.card_ids)
    print(
        f"imported {result.card_count} method cards from "
        f"{result.file_count} YAML file(s): {ids}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
