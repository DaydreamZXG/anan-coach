#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg

try:
    from tools.audit.load.target_safety import (
        ValidatedDatabaseTarget,
        reject_target_overriding_libpq_environment,
        validate_database_connection_identity,
        validate_database_load_target,
    )
except ModuleNotFoundError:  # Direct execution resolves modules from this directory.
    from target_safety import (
        ValidatedDatabaseTarget,
        reject_target_overriding_libpq_environment,
        validate_database_connection_identity,
        validate_database_load_target,
    )


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-nonlocal", action="store_true")
    parser.add_argument("--target-environment-id", default="")
    parser.add_argument("--expected-target-host", default="")
    parser.add_argument("--target-attestation", type=Path, required=True)
    parser.add_argument("--target-attestation-signature", type=Path, required=True)
    return parser.parse_args()


def validated_database_url(
    *,
    allow_nonlocal: bool = False,
    target_environment_id: str = "",
    expected_target_host: str = "",
    attestation_path: Path | str | None = None,
    signature_path: Path | str | None = None,
) -> tuple[str, ValidatedDatabaseTarget]:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    try:
        reject_target_overriding_libpq_environment()
        target = validate_database_load_target(
            value,
            allow_nonlocal=allow_nonlocal,
            target_environment_id=target_environment_id,
            expected_target_host=expected_target_host,
            attestation_path=attestation_path,
            signature_path=signature_path,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return (
        value.replace("postgresql+psycopg://", "postgresql://", 1),
        target,
    )


def database_url(
    *,
    allow_nonlocal: bool = False,
    target_environment_id: str = "",
    expected_target_host: str = "",
    attestation_path: Path | str | None = None,
    signature_path: Path | str | None = None,
) -> str:
    value, _target = validated_database_url(
        allow_nonlocal=allow_nonlocal,
        target_environment_id=target_environment_id,
        expected_target_host=expected_target_host,
        attestation_path=attestation_path,
        signature_path=signature_path,
    )
    return value


def seed_users_on_connection(
    connection: psycopg.Connection[Any],
    *,
    count: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Seed users through an already-open, caller-owned database connection."""

    if not 1 <= count <= 500:
        raise ValueError("count must be between 1 and 500")
    seeded_at = now or datetime.now(UTC)
    local_date = seeded_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    rest_weekday = local_date.isoweekday() % 7 + 1
    mock_weekday = rest_weekday % 7 + 1
    run_id = seeded_at.strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, str]] = []

    with connection.cursor() as cursor:
        for index in range(count):
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
                    seeded_at + timedelta(hours=6),
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
                    seeded_at,
                ),
            )
            records.append({"userId": user_id, "accessToken": token})
    connection.commit()
    return {
        "runId": run_id,
        "createdAt": seeded_at.isoformat(),
        "users": records,
    }


def main() -> None:
    args = arguments()
    url, target = validated_database_url(
        allow_nonlocal=args.allow_nonlocal,
        target_environment_id=args.target_environment_id,
        expected_target_host=args.expected_target_host,
        attestation_path=args.target_attestation,
        signature_path=args.target_attestation_signature,
    )
    with psycopg.connect(url) as connection:
        try:
            validate_database_connection_identity(
                connection,
                target=target,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        manifest = seed_users_on_connection(connection, count=args.count)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    args.output.chmod(0o600)
    print(
        json.dumps(
            {
                "runId": manifest["runId"],
                "users": len(manifest["users"]),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
