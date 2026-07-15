from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domains.qualification.rule_releases import PositionRuleReleaseService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile an immutable, auditable position-rule release."
    )
    parser.add_argument("--exam-code", default="guokao")
    parser.add_argument("--exam-year", type=int, required=True)
    parser.add_argument("--rule-set-version", required=True)
    parser.add_argument("--position-data-version", required=True)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Activate only after every position passes compilation audit.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL is required")
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    service = PositionRuleReleaseService()
    with Session(engine) as session, session.begin():
        release_id = service.ensure_draft(
            session,
            exam_code=args.exam_code,
            exam_year=args.exam_year,
            rule_set_version=args.rule_set_version,
            position_data_version=args.position_data_version,
        )
        summary = service.compile_draft(session, release_id=release_id)
        if args.activate:
            service.activate(session, release_id=release_id)

    print(
        json.dumps(
            {
                "releaseId": release_id,
                "activated": args.activate,
                "summary": asdict(summary),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
