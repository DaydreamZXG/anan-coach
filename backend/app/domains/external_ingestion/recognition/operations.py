from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionRevision
from app.core.config import Settings
from app.domains.ai.external_processing import (
    ExternalProcessingAuthorizationError,
    ExternalProcessingRepository,
)
from app.domains.ai.governance.catalog import scenario_policy
from app.domains.ai.governance.contracts import ScenarioKey
from app.domains.ai.governance.repository import (
    AIGovernanceReleaseError,
    AIGovernanceRepository,
)
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import (
    CommerceQuotaExceededError,
    CommerceRepository,
    CommerceReservationContractError,
)
from app.domains.commerce.service import CommerceService
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    RecognitionIdempotencyConflict,
    RecognitionOperationPersistenceError,
    RecognitionQuotaExceeded,
)
from app.domains.external_ingestion.recognition.operation_contracts import (
    OperationStatus,
    RecognitionOperationSpec,
    RecognitionOperationState,
    RecognitionReconciliationResult,
    UsageState,
)
from app.domains.external_ingestion.recognition.repository import (
    SQLRecognitionRepository,
)


RECOGNITION_PROVIDER_MAX_TIMEOUT_SECONDS = 300
RECOGNITION_RECONCILIATION_MIN_AGE_SECONDS = RECOGNITION_PROVIDER_MAX_TIMEOUT_SECONDS * 2


class SQLRecognitionOperationCoordinator:
    """Own the durable transaction boundary around one external OCR dispatch.

    The request session may later roll back. This coordinator always uses a
    separate session so the quota reservation, running ledger row and dispatch
    receipt become durable before any provider call.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        settings: Settings,
        processor_endpoint_fingerprint: str,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._processor_endpoint_fingerprint = processor_endpoint_fingerprint

    def begin(self, *, spec: RecognitionOperationSpec) -> RecognitionOperationState:
        policy = scenario_policy(cast(ScenarioKey, spec.scenario_key))
        with self._session_factory() as session:
            existing = self._existing(session, operation_key=spec.operation_key, for_update=True)
            if existing is not None:
                self._assert_replay(existing, spec=spec)
                return _state(existing, created=False)

            deployment_id, evaluation_state = self._deployment(session=session, spec=spec)
            reservation_id: UUID | None = None
            reservation_source: str | None = None
            entitlement_state = "not_applicable"
            feature = policy.entitlement_feature
            if feature is not None:
                try:
                    reservation = self._commerce(session).reserve_feature_usage(
                        user_id=str(spec.owner_user_id),
                        feature=feature,
                        reservation_key=spec.operation_key,
                        business_key=spec.operation_key,
                        server_session_ref=(f"recognition:{spec.artifact_version_id}"),
                        commit=False,
                    )
                except CommerceQuotaExceededError as exc:
                    session.rollback()
                    raise RecognitionQuotaExceeded("recognition quota is exhausted") from exc
                except CommerceReservationContractError as exc:
                    session.rollback()
                    raise RecognitionIdempotencyConflict(
                        "recognition operation reservation changed immutable input"
                    ) from exc
                reservation_id = reservation.reservation_id
                reservation_source = reservation.source
                entitlement_state = "reserved"

            evidence_refs = [
                {
                    "kind": "source_artifact",
                    "sourceId": str(spec.artifact_version_id),
                    "version": spec.input_fingerprint,
                    "anchor": "artifact",
                    "requestHash": spec.request_hash,
                }
            ]
            inserted = (
                session.execute(
                    text(
                        """
                    insert into ai_audit.external_ai_operation_ledger (
                      operation_id, operation_key, user_id, scenario_key, operation,
                      responding_agent, impact_level, model_tier, deployment_id,
                      evaluation_state, provider, provider_release, prompt_version,
                      model, schema_version, price_version, billing_unit, unit_count,
                      unit_price_cny, total_cost_cny, status, usage_state,
                      entitlement_state, input_fingerprint, output_fingerprint,
                      evidence_refs, reservation_id, reservation_source, latency_ms,
                      error_code, started_at, completed_at
                    ) values (
                      cast(:operation_id as uuid), :operation_key,
                      cast(:user_id as uuid), :scenario_key, :operation,
                      :responding_agent, :impact_level, :model_tier,
                      cast(:deployment_id as uuid), :evaluation_state, :provider,
                      :provider_release, :prompt_version, :model, :schema_version,
                      :price_version, :billing_unit, :unit_count, :unit_price_cny,
                      null, 'running', 'planned', :entitlement_state,
                      :input_fingerprint, null, cast(:evidence_refs as jsonb),
                      cast(:reservation_id as uuid), :reservation_source,
                      null, null, :started_at, null
                    )
                    on conflict (operation_key) do nothing
                    returning *
                    """
                    ),
                    {
                        "operation_id": str(spec.operation_id),
                        "operation_key": spec.operation_key,
                        "user_id": str(spec.owner_user_id),
                        "scenario_key": spec.scenario_key,
                        "operation": spec.operation,
                        "responding_agent": policy.responding_agent,
                        "impact_level": policy.impact_level,
                        "model_tier": policy.model_tier,
                        "deployment_id": (
                            str(deployment_id) if deployment_id is not None else None
                        ),
                        "evaluation_state": evaluation_state,
                        "provider": spec.provider,
                        "provider_release": spec.provider_release,
                        "prompt_version": spec.prompt_version,
                        "model": spec.model,
                        "schema_version": spec.schema_version,
                        "price_version": spec.price_version,
                        "billing_unit": spec.billing_unit,
                        "unit_count": spec.unit_count,
                        "unit_price_cny": spec.unit_price_cny,
                        "entitlement_state": entitlement_state,
                        "input_fingerprint": spec.input_fingerprint,
                        "evidence_refs": json.dumps(
                            evidence_refs, ensure_ascii=False, sort_keys=True
                        ),
                        "reservation_id": (
                            str(reservation_id) if reservation_id is not None else None
                        ),
                        "reservation_source": reservation_source,
                        "started_at": spec.started_at,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if inserted is None:
                existing = self._existing(
                    session, operation_key=spec.operation_key, for_update=True
                )
                if existing is None:
                    session.rollback()
                    raise RecognitionOperationPersistenceError(
                        "recognition operation disappeared during durable begin"
                    )
                self._assert_replay(existing, spec=spec)
                session.commit()
                return _state(existing, created=False)

            try:
                ExternalProcessingRepository().record_recognition_dispatch(
                    session,
                    operation_id=spec.operation_id,
                    user_id=str(spec.owner_user_id),
                    scenario_key=spec.scenario_key,
                    declared_provider_release=spec.provider_release,
                    provider_endpoint_fingerprint=self._processor_endpoint_fingerprint,
                    input_fingerprint=spec.input_fingerprint,
                    production_authorization_required=(
                        self._settings.environment.strip().casefold() == "production"
                        and self._settings.recognition_release_gate_enabled
                    ),
                    dispatched_at=spec.started_at,
                )
            except ExternalProcessingAuthorizationError as exc:
                session.rollback()
                raise ProcessorConfigurationError(
                    "recognition processor release is not authorized"
                ) from exc
            session.commit()
            return _state(inserted, created=True)

    def succeed(
        self,
        *,
        operation_id: UUID,
        revision: RecognitionRevision,
        output_fingerprint: str,
        latency_ms: int,
        completed_at: datetime,
    ) -> RecognitionOperationState:
        if len(output_fingerprint) != 64:
            raise ValueError("recognition output fingerprint must be SHA-256")
        if (
            revision.recognition_revision_id != operation_id
            or revision.output_sha256 != output_fingerprint
        ):
            raise ValueError("recognition revision does not match its operation")
        return self._finish(
            operation_id=operation_id,
            status="succeeded",
            usage_state="actual",
            revision=revision,
            output_fingerprint=output_fingerprint,
            error_code=None,
            latency_ms=latency_ms,
            completed_at=completed_at,
        )

    def claim_provider_dispatch(
        self,
        *,
        operation_id: UUID,
        claimed_at: datetime,
    ) -> RecognitionOperationState:
        """Fence provider I/O against a concurrent stale-operation close.

        The adapter calls this immediately before it creates its provider client.
        A reconciler that wins the row lock first makes the claim fail closed; a
        claim that wins first resets the stale clock to the provider-I/O boundary.
        """

        if claimed_at.tzinfo is None:
            raise ValueError("recognition provider dispatch claim must be timezone-aware")
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                    select * from ai_audit.external_ai_operation_ledger
                    where operation_id = cast(:operation_id as uuid)
                    for update
                    """
                    ),
                    {"operation_id": str(operation_id)},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RecognitionOperationPersistenceError(
                    "recognition operation intent does not exist"
                )
            current = _state(row, created=False)
            if current.status != "running":
                return current
            if row["provider_dispatch_claimed_at"] is None:
                row = (
                    session.execute(
                        text(
                            """
                        update ai_audit.external_ai_operation_ledger
                           set provider_dispatch_claimed_at = :claimed_at
                         where operation_id = cast(:operation_id as uuid)
                           and status = 'running'
                           and provider_dispatch_claimed_at is null
                        returning *
                        """
                        ),
                        {
                            "operation_id": str(operation_id),
                            "claimed_at": claimed_at,
                        },
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    session.rollback()
                    raise RecognitionOperationPersistenceError(
                        "recognition provider dispatch claim was lost"
                    )
            session.commit()
            return _state(row, created=False)

    def fail(
        self,
        *,
        operation_id: UUID,
        error_code: str,
        usage_state: Literal["actual", "unknown", "not_dispatched"],
        latency_ms: int,
        completed_at: datetime,
    ) -> RecognitionOperationState:
        normalized_error = error_code.strip()[:160]
        if not normalized_error:
            raise ValueError("recognition failure requires an error code")
        return self._finish(
            operation_id=operation_id,
            status=("outcome_unknown" if usage_state == "unknown" else "failed"),
            usage_state=usage_state,
            revision=None,
            output_fingerprint=None,
            error_code=normalized_error,
            latency_ms=latency_ms,
            completed_at=completed_at,
        )

    def reconcile_stale(
        self,
        *,
        stale_before: datetime,
        completed_at: datetime,
        limit: int,
    ) -> RecognitionReconciliationResult:
        """Close abandoned dispatches without guessing or replaying provider work.

        Each selected row and its quota refund transition in one transaction.
        `skip locked` makes concurrent maintenance cycles safe, while the terminal
        trigger prevents a late provider completion from replacing the result.
        """

        with self._session_factory() as session:
            result = reconcile_stale_recognition_operations(
                session,
                stale_before=stale_before,
                completed_at=completed_at,
                limit=limit,
            )
            session.commit()
            return result

    def _finish(
        self,
        *,
        operation_id: UUID,
        status: Literal["succeeded", "failed", "outcome_unknown"],
        usage_state: Literal["actual", "unknown", "not_dispatched"],
        revision: RecognitionRevision | None,
        output_fingerprint: str | None,
        error_code: str | None,
        latency_ms: int,
        completed_at: datetime,
    ) -> RecognitionOperationState:
        if latency_ms < 0:
            raise ValueError("recognition latency cannot be negative")
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                    select * from ai_audit.external_ai_operation_ledger
                    where operation_id = cast(:operation_id as uuid)
                    for update
                    """
                    ),
                    {"operation_id": str(operation_id)},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RecognitionOperationPersistenceError(
                    "recognition operation intent does not exist"
                )
            current = _state(row, created=False)
            if current.status != "running":
                return current

            operation_key = str(row["operation_key"])
            if revision is not None:
                if (
                    revision.owner_user_id != row["user_id"]
                    or revision.input_sha256 != row["input_fingerprint"]
                ):
                    raise RecognitionOperationPersistenceError(
                        "recognition revision changed durable operation attribution"
                    )
                SQLRecognitionRepository(session).insert(revision)
            if row["reservation_id"] is not None:
                commerce = CommerceRepository(session)
                if status == "succeeded":
                    commerce.consume_feature_usage(reservation_key=operation_key, commit=False)
                else:
                    commerce.refund_feature_usage(reservation_key=operation_key, commit=False)

            terminal = (
                session.execute(
                    text(
                        """
                    update ai_audit.external_ai_operation_ledger
                       set status = :status,
                           usage_state = :usage_state,
                           entitlement_state = case
                             when reservation_id is null then 'not_applicable'
                             when :status = 'succeeded' then 'consumed'
                             else 'released'
                           end,
                           total_cost_cny = case
                             when :usage_state = 'actual'
                             then round(unit_count * unit_price_cny, 8)
                             when :usage_state = 'not_dispatched'
                             then 0
                             else null
                           end,
                           output_fingerprint = :output_fingerprint,
                           latency_ms = :latency_ms,
                           error_code = :error_code,
                           completed_at = :completed_at
                     where operation_id = cast(:operation_id as uuid)
                       and status = 'running'
                    returning *
                    """
                    ),
                    {
                        "operation_id": str(operation_id),
                        "status": status,
                        "usage_state": usage_state,
                        "output_fingerprint": output_fingerprint,
                        "latency_ms": latency_ms,
                        "error_code": error_code,
                        "completed_at": completed_at,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if terminal is None:
                session.rollback()
                raise RecognitionOperationPersistenceError(
                    "recognition operation terminal transition was lost"
                )
            session.commit()
            return _state(terminal, created=False)

    def _deployment(
        self,
        *,
        session: Session,
        spec: RecognitionOperationSpec,
    ) -> tuple[UUID | None, str]:
        if self._settings.environment.strip().casefold() != "production":
            return None, "local_unreleased"
        if not self._settings.recognition_release_gate_enabled:
            # This personal beta pins provider, route, MIME, size and expiry in
            # ProcessorApproval. Keep the durable operation/dispatch ledgers,
            # but do not require a second, empty database release registry.
            return None, "runtime_approved"
        try:
            deployment_id = AIGovernanceRepository().require_current_deployment(
                session,
                scenario_key=cast(ScenarioKey, spec.scenario_key),
                operation=spec.operation,
                provider=spec.provider,
                model=spec.model,
                prompt_version=spec.prompt_version,
                schema_version=spec.schema_version,
                price_version=spec.price_version,
            )
        except AIGovernanceReleaseError as exc:
            raise ProcessorConfigurationError(
                "no evaluated production deployment matches recognition operation"
            ) from exc
        return deployment_id, "passed"

    def _commerce(self, session: Session) -> CommerceService:
        return CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=AppStoreSignedTransactionVerifier(self._settings),
            commercial_mode=self._settings.commercial_mode,
            usage_access_mode=self._settings.usage_access_mode,
        )

    @staticmethod
    def _existing(session: Session, *, operation_key: str, for_update: bool):
        suffix = " for update" if for_update else ""
        return (
            session.execute(
                text(
                    "select * from ai_audit.external_ai_operation_ledger "
                    "where operation_key = :operation_key" + suffix
                ),
                {"operation_key": operation_key},
            )
            .mappings()
            .one_or_none()
        )

    @staticmethod
    def _assert_replay(row, *, spec: RecognitionOperationSpec) -> None:
        immutable = {
            "operation_id": spec.operation_id,
            "user_id": spec.owner_user_id,
            "scenario_key": spec.scenario_key,
            "operation": spec.operation,
            "provider": spec.provider,
            "provider_release": spec.provider_release,
            "model": spec.model,
            "prompt_version": spec.prompt_version,
            "schema_version": spec.schema_version,
            "price_version": spec.price_version,
            "billing_unit": spec.billing_unit,
            "unit_count": spec.unit_count,
            "unit_price_cny": spec.unit_price_cny,
            "input_fingerprint": spec.input_fingerprint,
        }
        if any(row[key] != value for key, value in immutable.items()):
            raise RecognitionIdempotencyConflict(
                "recognition operation key belongs to different immutable input"
            )
        evidence_refs = row["evidence_refs"]
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise RecognitionOperationPersistenceError(
                "recognition operation has no replay evidence"
            )
        if str(evidence_refs[0].get("requestHash", "")) != spec.request_hash:
            raise RecognitionIdempotencyConflict("recognition idempotency key request changed")


def _state(row, *, created: bool) -> RecognitionOperationState:
    return RecognitionOperationState(
        operation_id=UUID(str(row["operation_id"])),
        operation_key=str(row["operation_key"]),
        status=cast(OperationStatus, str(row["status"])),
        usage_state=cast(UsageState, str(row["usage_state"])),
        created=created,
    )


def reconcile_stale_recognition_operations(
    session: Session,
    *,
    stale_before: datetime,
    completed_at: datetime,
    limit: int,
) -> RecognitionReconciliationResult:
    """Stage stale-operation terminal transitions in the caller's transaction."""

    if stale_before.tzinfo is None or completed_at.tzinfo is None:
        raise ValueError("recognition reconciliation timestamps must be timezone-aware")
    if stale_before > completed_at:
        raise ValueError("recognition stale cutoff cannot follow completion time")
    if limit < 1 or limit > 1_000:
        raise ValueError("recognition reconciliation limit must be between 1 and 1000")

    rows = (
        session.execute(
            text(
                """
            select operation_id, operation_key, reservation_id, started_at,
                   provider_dispatch_claimed_at
              from ai_audit.external_ai_operation_ledger
             where status = 'running'
               and usage_state = 'planned'
               and coalesce(provider_dispatch_claimed_at, started_at) <= :stale_before
             order by coalesce(provider_dispatch_claimed_at, started_at), operation_id
             for update skip locked
             limit :limit
            """
            ),
            {"stale_before": stale_before, "limit": limit},
        )
        .mappings()
        .all()
    )
    refunded = 0
    for row in rows:
        if row["reservation_id"] is not None:
            CommerceRepository(session).refund_feature_usage(
                reservation_key=str(row["operation_key"]),
                commit=False,
            )
            refunded += 1
        latency_ms = min(
            2_147_483_647,
            max(
                0,
                int((completed_at - row["started_at"]).total_seconds() * 1_000),
            ),
        )
        terminal = session.execute(
            text(
                """
                update ai_audit.external_ai_operation_ledger
                   set status = 'outcome_unknown',
                       usage_state = 'unknown',
                       entitlement_state = case
                         when reservation_id is null then 'not_applicable'
                         else 'released'
                       end,
                       total_cost_cny = null,
                       output_fingerprint = null,
                       latency_ms = :latency_ms,
                       error_code = 'recognition_stale_outcome_unknown',
                       completed_at = :completed_at
                 where operation_id = cast(:operation_id as uuid)
                   and status = 'running'
                returning operation_id
                """
            ),
            {
                "operation_id": str(row["operation_id"]),
                "latency_ms": latency_ms,
                "completed_at": completed_at,
            },
        ).scalar_one_or_none()
        if terminal is None:
            raise RecognitionOperationPersistenceError(
                "stale recognition terminal transition was lost"
            )
    return RecognitionReconciliationResult(
        operations=len(rows),
        refunded_reservations=refunded,
    )
