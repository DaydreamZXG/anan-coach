from __future__ import annotations

import json
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionRevision
from app.domains.ai.external_processing import ExternalProcessingRepository
from app.domains.external_ingestion.recognition.models import ArtifactInput


class RecognitionRepositoryPort(Protocol):
    def lock(self, *, key: str) -> None: ...

    def artifact(self, *, artifact_version_id: UUID, owner_user_id: UUID) -> ArtifactInput | None: ...

    def revision(self, *, recognition_revision_id: UUID) -> RecognitionRevision | None: ...

    def head(
        self,
        *,
        artifact_version_id: UUID,
        owner_user_id: UUID,
        recognition_kind: str,
    ) -> RecognitionRevision | None: ...

    def insert(self, revision: RecognitionRevision) -> None: ...

    def record_external_operation(self, *, values: dict[str, object]) -> None: ...


class SQLRecognitionRepository:
    def __init__(
        self,
        session: Session,
        *,
        environment: str = "local",
        processor_endpoint_fingerprint: str | None = None,
    ) -> None:
        self._session = session
        self._environment = environment
        self._processor_endpoint_fingerprint = processor_endpoint_fingerprint

    def lock(self, *, key: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def artifact(
        self, *, artifact_version_id: UUID, owner_user_id: UUID
    ) -> ArtifactInput | None:
        row = self._session.execute(
            text(
                """
                select artifact_version_id, owner_user_id, state, source_kind, object_ref,
                       mime_type, content_sha256, page_count
                from learning.source_artifact_versions
                where artifact_version_id = :artifact_version_id
                  and owner_user_id = :owner_user_id
                  and state = 'available'
                """
            ),
            {
                "artifact_version_id": str(artifact_version_id),
                "owner_user_id": str(owner_user_id),
            },
        ).mappings().one_or_none()
        return ArtifactInput.model_validate(dict(row)) if row is not None else None

    def revision(
        self, *, recognition_revision_id: UUID
    ) -> RecognitionRevision | None:
        row = self._session.execute(
            text(
                """
                select recognition_revision_id, artifact_version_id, owner_user_id,
                       revision_no, recognition_kind, state,
                       processor_policy_version, processor_route, input_sha256,
                       output_sha256, candidate_payload, confidence, expires_at,
                       supersedes_recognition_revision_id,
                       confirmed_at, created_at
                from learning.recognition_revisions
                where recognition_revision_id = :recognition_revision_id
                """
            ),
            {"recognition_revision_id": str(recognition_revision_id)},
        ).mappings().one_or_none()
        return _revision(row) if row is not None else None

    def head(
        self,
        *,
        artifact_version_id: UUID,
        owner_user_id: UUID,
        recognition_kind: str,
    ) -> RecognitionRevision | None:
        row = self._session.execute(
            text(
                """
                select recognition_revision_id, artifact_version_id, owner_user_id,
                       revision_no, recognition_kind, state,
                       processor_policy_version, processor_route, input_sha256,
                       output_sha256, candidate_payload, confidence, expires_at,
                       supersedes_recognition_revision_id,
                       confirmed_at, created_at
                from learning.recognition_revisions
                where artifact_version_id = :artifact_version_id
                  and owner_user_id = :owner_user_id
                  and recognition_kind = :recognition_kind
                order by revision_no desc
                limit 1
                """
            ),
            {
                "artifact_version_id": str(artifact_version_id),
                "owner_user_id": str(owner_user_id),
                "recognition_kind": recognition_kind,
            },
        ).mappings().one_or_none()
        return _revision(row) if row is not None else None

    def insert(self, revision: RecognitionRevision) -> None:
        self._session.execute(
            text(
                """
                insert into learning.recognition_revisions (
                  recognition_revision_id, artifact_version_id, owner_user_id,
                  revision_no, recognition_kind, state,
                  processor_policy_version, processor_route, input_sha256,
                  output_sha256, candidate_payload, confidence, expires_at,
                  supersedes_recognition_revision_id, confirmed_at, created_at
                ) values (
                  :recognition_revision_id, :artifact_version_id, :owner_user_id,
                  :revision_no, :recognition_kind, :state,
                  :processor_policy_version, :processor_route, :input_sha256,
                  :output_sha256, cast(:candidate_payload as jsonb), :confidence,
                  :expires_at, :supersedes_recognition_revision_id,
                  :confirmed_at, :created_at
                )
                """
            ),
            {
                "recognition_revision_id": str(revision.recognition_revision_id),
                "artifact_version_id": str(revision.artifact_version_id),
                "owner_user_id": str(revision.owner_user_id),
                "revision_no": revision.revision_no,
                "recognition_kind": revision.recognition_kind.value,
                "state": revision.state.value,
                "processor_policy_version": revision.processor_policy_version,
                "processor_route": revision.processor_route,
                "input_sha256": revision.input_sha256,
                "output_sha256": revision.output_sha256,
                "candidate_payload": json.dumps(
                    revision.candidate_payload, ensure_ascii=False, sort_keys=True
                ),
                "confidence": revision.confidence,
                "expires_at": revision.expires_at,
                "supersedes_recognition_revision_id": (
                    str(revision.supersedes_recognition_revision_id)
                    if revision.supersedes_recognition_revision_id
                    else None
                ),
                "confirmed_at": revision.confirmed_at,
                "created_at": revision.created_at,
            },
        )
    def record_external_operation(self, *, values: dict[str, object]) -> None:
        from app.domains.ai.governance.catalog import scenario_policy
        from app.domains.ai.governance.repository import AIGovernanceReleaseError

        scenario_key = str(values["scenario_key"])
        policy = scenario_policy(scenario_key)  # type: ignore[arg-type]
        deployment_id = None
        evaluation_state = "local_unreleased"
        if self._environment.strip().casefold() == "production":
            deployment_id = self._session.execute(
                text(
                    """
                    select deployment_id from ai_audit.scenario_deployments
                    where scenario_key = :scenario_key
                      and operation = :operation
                      and provider = :provider
                      and model = :model
                      and prompt_version = :prompt_version
                      and schema_version = :schema_version
                      and evaluation_suite_version = :evaluation_suite_version
                      and not exists (
                        select 1 from ai_audit.scenario_deployments successor
                        where successor.supersedes_deployment_id =
                              scenario_deployments.deployment_id
                      )
                    order by published_at desc
                    limit 1
                    """
                ),
                {
                    **values,
                    "evaluation_suite_version": policy.evaluation_suite_version,
                },
            ).scalar_one_or_none()
            if deployment_id is None:
                raise AIGovernanceReleaseError(
                    "No evaluated production deployment matches this recognition operation"
                )
            evaluation_state = "passed"
        self._session.execute(
            text(
                """
                insert into ai_audit.external_ai_operation_ledger (
                  operation_id, operation_key, user_id, scenario_key, operation,
                  responding_agent, impact_level, model_tier, deployment_id,
                  evaluation_state, provider, provider_release, prompt_version,
                  model, schema_version, price_version, billing_unit, unit_count,
                  unit_price_cny, total_cost_cny, status, input_fingerprint,
                  output_fingerprint, evidence_refs, reservation_id,
                  reservation_source, latency_ms, error_code, completed_at
                ) values (
                  cast(:operation_id as uuid), :operation_key,
                  cast(:user_id as uuid), :scenario_key, :operation,
                  :responding_agent, :impact_level, :model_tier,
                  cast(:deployment_id as uuid), :evaluation_state, :provider,
                  :provider_release, :prompt_version, :model, :schema_version,
                  :price_version, :billing_unit, :unit_count, :unit_price_cny,
                  round(:unit_count * :unit_price_cny, 8), 'succeeded',
                  :input_fingerprint, :output_fingerprint,
                  cast(:evidence_refs as jsonb), null, null, :latency_ms,
                  null, :completed_at
                )
                on conflict (operation_key) do nothing
                """
            ),
            {
                **values,
                "responding_agent": policy.responding_agent,
                "impact_level": policy.impact_level,
                "model_tier": policy.model_tier,
                "deployment_id": deployment_id,
                "evaluation_state": evaluation_state,
            },
        )
        endpoint_fingerprint = self._processor_endpoint_fingerprint
        if self._environment.strip().casefold() == "production" and not endpoint_fingerprint:
            raise AIGovernanceReleaseError(
                "production recognition requires a processor endpoint fingerprint"
            )
        ExternalProcessingRepository().record_recognition_dispatch(
            self._session,
            operation_id=UUID(str(values["operation_id"])),
            user_id=str(values["user_id"]),
            scenario_key=scenario_key,
            declared_provider_release=str(values["provider_release"]),
            provider_endpoint_fingerprint=(
                endpoint_fingerprint
                or sha256(str(values["provider"]).encode("utf-8")).hexdigest()
            ),
            input_fingerprint=str(values["input_fingerprint"]),
            production_authorization_required=(
                self._environment.strip().casefold() == "production"
            ),
        )


def _revision(row) -> RecognitionRevision:
    revision = RecognitionRevision.model_validate(
        {
            "recognitionRevisionId": row["recognition_revision_id"],
            "artifactVersionId": row["artifact_version_id"],
            "ownerUserId": row["owner_user_id"],
            "revisionNo": row["revision_no"],
            "recognitionKind": row["recognition_kind"],
            "state": row["state"],
            "processorPolicyVersion": row["processor_policy_version"],
            "processorRoute": row["processor_route"],
            "inputSha256": row["input_sha256"],
            "outputSha256": row["output_sha256"],
            "candidatePayload": dict(row["candidate_payload"]),
            "confidence": (
                float(row["confidence"]) if row["confidence"] is not None else None
            ),
            "expiresAt": row["expires_at"],
            "supersedesRecognitionRevisionId": row[
                "supersedes_recognition_revision_id"
            ],
            "confirmedAt": row["confirmed_at"],
            "createdAt": row["created_at"],
        }
    )
    payload_hash = sha256(
        json.dumps(
            revision.candidate_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    if payload_hash != revision.output_sha256:
        raise RuntimeError("recognition candidate payload hash is invalid")
    return revision
