#!/usr/bin/env python3
"""Read-only production audit for external AI releases, receipts and private objects."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, text


BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.internal_services.private_objects import PrivateObjectReader  # noqa: E402


def audit(*, verify_private_object: bool) -> tuple[dict[str, object], bool]:
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    failures: list[str] = []
    with engine.connect() as connection:
        revision = connection.execute(
            text("select version_num from alembic_version")
        ).scalar_one_or_none()
        cutover = connection.execute(
            text(
                """
                select activated_at
                from ai_audit.external_processing_boundary_releases
                where boundary_version = 'external-ai-data-boundary@v1'
                """
            )
        ).scalar_one()
        status_rows = connection.execute(
            text(
                """
                select real_user_status, count(*) as release_count
                from ai_audit.processor_release_records
                group by real_user_status
                order by real_user_status
                """
            )
        ).mappings()
        release_counts = {
            str(row["real_user_status"]): int(row["release_count"])
            for row in status_rows
        }
        checks = connection.execute(
            text(
                """
                select
                  (select count(*) from ai_audit.processor_release_records
                    where real_user_status = 'approved'
                      and transport_security not in ('tls', 'cloud_function'))
                    as approved_insecure,
                  (select count(*) from ai_audit.processor_release_records
                    where real_user_status = 'approved'
                      and (model_training_use <> 'contractually_prohibited'
                        or retention_policy <> 'zero_retention'
                        or retention_seconds <> 0))
                    as approved_lifecycle_unknown,
                  (select count(*) from ai_audit.model_call_ledger call
                    where call.started_at >= cast(:cutover as timestamptz)
                      and not exists (
                        select 1 from ai_audit.external_processing_receipts receipt
                        where receipt.model_call_id = call.call_id
                      )) as model_calls_without_receipt,
                  (select count(*) from ai_audit.external_ai_operation_ledger operation
                    where operation.completed_at >= cast(:cutover as timestamptz)
                      and not exists (
                        select 1 from ai_audit.external_processing_receipts receipt
                        where receipt.external_operation_id = operation.operation_id
                      )) as recognition_calls_without_receipt,
                  (select count(*) from product.community_method_card_candidates candidate
                    where candidate.created_at >= cast(:cutover as timestamptz)
                      and not exists (
                        select 1 from ai_audit.external_processing_receipts receipt
                        where receipt.community_candidate_id = candidate.candidate_id
                      )) as community_calls_without_receipt,
                  (select count(*) from ai_audit.external_processing_receipts
                    where authorization_state = 'local_unreleased')
                    as local_unreleased_receipts,
                  (select count(*) from learning.source_artifact_versions artifact
                    where artifact.object_ref is not null
                      and artifact.object_ref not like
                        ('tos://' || :bucket || '/private/%'))
                    as invalid_private_object_refs
                """
            ),
            {"cutover": cutover, "bucket": settings.tos_bucket},
        ).mappings().one()
        integer_checks = {key: int(value) for key, value in checks.items()}
        for key, value in integer_checks.items():
            if value:
                failures.append(f"{key}:{value}")

        object_probe: dict[str, object] = {"requested": verify_private_object}
        if verify_private_object:
            artifact = connection.execute(
                text(
                    """
                    select object_ref, content_sha256, byte_size, mime_type
                    from learning.source_artifact_versions
                    where state = 'available' and object_ref is not null
                    order by created_at desc
                    limit 1
                    """
                )
            ).mappings().one_or_none()
            if artifact is None:
                failures.append("private_object_probe:no_available_artifact")
                object_probe["status"] = "no_available_artifact"
            else:
                try:
                    with PrivateObjectReader(settings).open(
                        object_ref=str(artifact["object_ref"]),
                        expected_sha256=str(artifact["content_sha256"]),
                        maximum_bytes=max(int(artifact["byte_size"]), 1),
                        prefix="external-ai-boundary-audit-",
                    ) as path:
                        observed_bytes = path.stat().st_size
                    object_probe.update(
                        {
                            "status": "verified",
                            "byteSize": observed_bytes,
                            "mimeType": str(artifact["mime_type"]),
                        }
                    )
                except Exception as exc:
                    failures.append(f"private_object_probe:{type(exc).__name__}")
                    object_probe["status"] = "failed"

    engine.dispose()
    report: dict[str, object] = {
        "contractVersion": "external-ai-boundary-audit@v1",
        "checkedAt": datetime.now().astimezone().isoformat(),
        "databaseRevision": revision,
        "cutover": cutover.isoformat(),
        "processorReleaseCounts": release_counts,
        "realUserDispatchEnabled": release_counts.get("approved", 0) > 0,
        "checks": integer_checks,
        "privateObjectProbe": object_probe,
        "failures": failures,
        "passed": not failures,
    }
    return report, not failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-private-object",
        action="store_true",
        help="read one real private object and verify its immutable SHA-256",
    )
    args = parser.parse_args()
    report, passed = audit(verify_private_object=args.verify_private_object)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
