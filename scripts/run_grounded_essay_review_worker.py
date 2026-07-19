#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.domains.essay_intelligence.review_worker import (  # noqa: E402
    GroundedEssayReviewWorker,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the durable v2 three-candidate grounded Guokao essay review worker."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy PostgreSQL URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=0,
        help="Stop after N claimed jobs; 0 keeps polling.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1.0,
        help="Polling interval when no job is ready.",
    )
    parser.add_argument(
        "--stale-recovery-seconds",
        type=float,
        default=60.0,
        help="How often to recover stale claims owned by this worker domain.",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    if args.max_jobs < 0:
        parser.error("--max-jobs cannot be negative")
    if not 0.1 <= args.poll_seconds <= 30:
        parser.error("--poll-seconds must be between 0.1 and 30")
    if not 10 <= args.stale_recovery_seconds <= 300:
        parser.error("--stale-recovery-seconds must be between 10 and 300")

    engine = create_engine(
        _normalize_database_url(args.database_url),
        pool_pre_ping=True,
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    worker = GroundedEssayReviewWorker(settings=get_settings())
    handled = 0
    next_stale_recovery_at = 0.0
    try:
        while args.max_jobs == 0 or handled < args.max_jobs:
            monotonic_now = time.monotonic()
            if monotonic_now >= next_stale_recovery_at:
                with factory() as session:
                    worker.recover_stale_claims(session)
                next_stale_recovery_at = (
                    monotonic_now + args.stale_recovery_seconds
                )
            with factory() as session:
                job = worker.process_next(session)
            if job is None:
                time.sleep(args.poll_seconds)
                continue
            handled += 1
    finally:
        engine.dispose()
    return 0


def _normalize_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
