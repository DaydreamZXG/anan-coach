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

from app.core.config import Settings, get_settings  # noqa: E402
from app.internal_services.private_objects import PrivateObjectReader  # noqa: E402


def audit(
    *,
    verify_private_object: bool,
    settings: Settings | None = None,
) -> tuple[dict[str, object], bool]:
    settings = settings or get_settings()
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
        checks = (
            connection.execute(
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
                    where operation.started_at >= cast(:cutover as timestamptz)
                      and not exists (
                        select 1 from ai_audit.external_processing_receipts receipt
                        where receipt.external_operation_id = operation.operation_id
                      )) as recognition_calls_without_receipt,
                  (select count(*) from ai_audit.external_ai_operation_ledger operation
                    where operation.status = 'running')
                    as recognition_nonterminal_operations,
                  (select count(*)
                     from ai_audit.external_ai_operation_ledger operation
                     left join commerce.feature_usage_reservations catalog_reservation
                       on operation.reservation_source = 'catalog'
                      and catalog_reservation.reservation_id = operation.reservation_id
                     left join commerce.community_reward_ai_usage_reservations
                       reward_reservation
                       on operation.reservation_source = 'community_reward'
                      and reward_reservation.reservation_id = operation.reservation_id
                     left join commerce.community_reward_ai_usage_reservation_events
                       reward_event
                       on reward_event.reservation_id = reward_reservation.reservation_id
                    where operation.reservation_id is not null
                      and (
                        operation.reservation_source not in ('catalog', 'community_reward')
                        or (
                          operation.reservation_source = 'catalog'
                          and (
                            catalog_reservation.reservation_id is null
                            or (
                              operation.status = 'running'
                              and (operation.entitlement_state <> 'reserved'
                                or catalog_reservation.status <> 'reserved')
                            )
                            or (
                              operation.status = 'succeeded'
                              and (operation.entitlement_state <> 'consumed'
                                or catalog_reservation.status <> 'consumed')
                            )
                            or (
                              operation.status in ('failed', 'outcome_unknown')
                              and (operation.entitlement_state <> 'released'
                                or catalog_reservation.status <> 'refunded')
                            )
                          )
                        )
                        or (
                          operation.reservation_source = 'community_reward'
                          and (
                            reward_reservation.reservation_id is null
                            or (
                              operation.status = 'running'
                              and (operation.entitlement_state <> 'reserved'
                                or reward_event.reservation_id is not null)
                            )
                            or (
                              operation.status = 'succeeded'
                              and (operation.entitlement_state <> 'consumed'
                                or reward_event.event_type is distinct from 'consumed')
                            )
                            or (
                              operation.status in ('failed', 'outcome_unknown')
                              and (operation.entitlement_state <> 'released'
                                or reward_event.event_type is distinct from 'released')
                            )
                          )
                        )
                      ))
                    as recognition_quota_lifecycle_mismatches,
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
            )
            .mappings()
            .one()
        )
        integer_checks = {key: int(value) for key, value in checks.items()}
        for key, value in integer_checks.items():
            if value:
                failures.append(f"{key}:{value}")
        recognition_usage_rows = connection.execute(
            text(
                """
                select operation.usage_state, count(*) as receipt_count
                  from ai_audit.external_processing_receipts receipt
                  join ai_audit.external_ai_operation_ledger operation
                    on operation.operation_id = receipt.external_operation_id
                 where receipt.purpose = 'recognition_process'
                 group by operation.usage_state
                 order by operation.usage_state
                """
            )
        ).mappings()
        recognition_usage_counts = {
            str(row["usage_state"]): int(row["receipt_count"])
            for row in recognition_usage_rows
        }

        object_probe: dict[str, object] = {"requested": verify_private_object}
        if verify_private_object:
            artifact = (
                connection.execute(
                    text(
                        """
                    select object_ref, content_sha256, byte_size, mime_type
                    from learning.source_artifact_versions
                    where state = 'available' and object_ref is not null
                    order by created_at desc
                    limit 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
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
        "recognitionReceiptUsageStateCounts": recognition_usage_counts,
        "receiptSemantics": (
            "external_processing_receipts are durable authorized dispatch intents; "
            "they are not standalone proof of provider egress or acceptance. "
            "For OCR, join external_ai_operation_ledger.usage_state: "
            "not_dispatched proves local rejection before provider I/O, actual "
            "records a received provider response with conservative cost attribution "
            "(not provider acceptance or invoiceable usage), and unknown records an "
            "unknowable outcome."
        ),
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
