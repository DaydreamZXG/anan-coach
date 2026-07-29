#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> None:
    args = arguments()
    if not 1 <= args.count <= 500:
        raise ValueError("count must be between 1 and 500")

    now = datetime.now(UTC)
    local_date = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    rest_weekday = local_date.isoweekday() % 7 + 1
    mock_weekday = rest_weekday % 7 + 1
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, str]] = []

    with psycopg.connect(database_url()) as connection:
        with connection.cursor() as cursor:
            for index in range(args.count):
                user_id = str(uuid4())
                session_id = str(uuid4())
                preference_version_id = str(uuid4())
                token = f"codex-load-{run_id}-{index:03d}-{uuid4().hex}"
                token_hash = sha256(token.encode("utf-8")).hexdigest()
                command_id = f"codex-load-preference:{run_id}:{index:03d}"
                fingerprint = sha256(command_id.encode("utf-8")).hexdigest()
                cursor.execute(
                    """
                    INSERT INTO learner.users (user_id, display_name)
                    VALUES (%s::uuid, %s)
                    """,
                    (user_id, f"压测账号-{run_id}-{index:03d}"),
                )
                cursor.execute(
                    """
                    INSERT INTO learner.app_sessions (
                      session_id, user_id, session_token_hash, device_id, expires_at
                    ) VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                    """,
                    (
                        session_id,
                        user_id,
                        token_hash,
                        f"codex-load-{run_id}",
                        now + timedelta(hours=6),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO learner.profiles (
                      user_id, daily_minutes, study_mode, onboarding_complete,
                      profile_version
                    ) VALUES (%s::uuid, 45, 'parttime', true, 1)
                    """,
                    (user_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO learning.weekly_study_preference_versions (
                      preference_version_id, command_id, user_id, version_no,
                      timezone, weekday_minutes, weekend_minutes, long_block_minutes,
                      fragment_minutes, regular_rest_weekday, regular_mock_weekday,
                      effective_from, source_type, confirmed, input_fingerprint,
                      supersedes_preference_version_id, created_at
                    ) VALUES (
                      %s::uuid, %s, %s::uuid, 2, 'Asia/Shanghai',
                      45, 45, 45, 25, %s, %s, %s,
                      'user_confirmed', true, %s, null, %s
                    )
                    """,
                    (
                        preference_version_id,
                        command_id,
                        user_id,
                        rest_weekday,
                        mock_weekday,
                        local_date,
                        fingerprint,
                        now,
                    ),
                )
                records.append({"userId": user_id, "accessToken": token})
        connection.commit()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"runId": run_id, "createdAt": now.isoformat(), "users": records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    args.output.chmod(0o600)
    print(json.dumps({"runId": run_id, "users": len(records), "output": str(args.output)}))


if __name__ == "__main__":
    main()
