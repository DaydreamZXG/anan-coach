from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.external_processing import (
    ExternalProcessingRepository,
    endpoint_fingerprint,
)
from app.domains.math_ink.schemas import FormulaVersion, MathInkRecognitionResult
from app.domains.math_ink.provider import MathInkProviderDescriptor


class MathInkConflict(RuntimeError):
    pass


class MathInkNotFound(LookupError):
    pass


class MathInkConfigurationError(RuntimeError):
    pass


class MathInkRepository:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def owned_active_question(
        self, *, owner_user_id: str, session_id: UUID, question_id: str
    ) -> bool:
        bundle = self.session.execute(
            text(
                "select safe_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid) "
                "and user_id = cast(:owner as uuid) and state = 'active'"
            ),
            {"session_id": str(session_id), "owner": owner_user_id},
        ).scalar_one_or_none()
        if not isinstance(bundle, dict):
            return False
        questions = bundle.get("questions")
        return isinstance(questions, list) and any(
            isinstance(item, dict) and item.get("questionId") == question_id
            for item in questions
        )

    def preflight(
        self,
        *,
        input_fingerprint: str,
        provider: MathInkProviderDescriptor,
    ) -> None:
        if not provider.provider_release.strip():
            raise MathInkConfigurationError("formula provider release is not declared")
        if not provider.model_release.strip():
            raise MathInkConfigurationError("formula model release is not declared")
        try:
            unit_price = Decimal(provider.unit_price_cny)
        except InvalidOperation as exc:
            raise MathInkConfigurationError("formula price card is invalid") from exc
        if (
            not unit_price.is_finite()
            or unit_price < 0
            or not provider.price_version.strip()
        ):
            raise MathInkConfigurationError("formula price card is incomplete")
        if (
            self.settings.environment.strip().casefold() == "production"
            and not provider.deployment_id.strip()
        ):
            raise MathInkConfigurationError(
                "formula production deployment is not evaluated"
            )
        self.require_production_release(
            input_fingerprint=input_fingerprint,
            provider=provider,
        )

    def replay(self, *, owner_user_id: str, idempotency_key: str):
        return (
            self.session.execute(
                text(
                    "select * from learning.math_ink_commands "
                    "where owner_user_id = cast(:owner as uuid) "
                    "and idempotency_key = :key"
                ),
                {"owner": owner_user_id, "key": idempotency_key},
            )
            .mappings()
            .one_or_none()
        )

    def formula_for_request(
        self, *, owner_user_id: str, request_id: UUID
    ) -> FormulaVersion | None:
        row = (
            self.session.execute(
                text(
                    "select * from learning.math_formula_versions "
                    "where owner_user_id = cast(:owner as uuid) "
                    "and request_id = cast(:request_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"owner": owner_user_id, "request_id": str(request_id)},
            )
            .mappings()
            .one_or_none()
        )
        return _formula(row) if row else None

    def insert_result(
        self,
        *,
        request_id: UUID,
        owner_user_id: str,
        session_id: UUID,
        question_id: str,
        idempotency_key: str,
        input_fingerprint: str,
        ink_payload: dict[str, object],
        provider: MathInkProviderDescriptor,
        endpoint_hash: str,
        latex: str | None,
        recognition_metadata: dict[str, object] | None,
        failure_code: str | None,
        latency_ms: int,
        created_at: datetime,
    ) -> MathInkRecognitionResult:
        output_fingerprint = (
            sha256(latex.encode("utf-8")).hexdigest() if latex else None
        )
        state = "candidate" if latex else "failed"
        self.session.execute(
            text(
                """
                insert into learning.math_ink_commands (
                  request_id, owner_user_id, session_id, question_id,
                  idempotency_key, input_fingerprint, ink_payload,
                  provider_release, provider_endpoint_fingerprint, state,
                  output_fingerprint, failure_code, latency_ms, created_at,
                  completed_at, recognition_metadata
                ) values (
                  cast(:request_id as uuid), cast(:owner as uuid),
                  cast(:session_id as uuid), :question_id, :key, :input,
                  cast(:ink as jsonb), :release, :endpoint_hash, :state,
                  :output, :failure, :latency, :created_at, :created_at
                  , cast(:recognition_metadata as jsonb)
                )
                """
            ),
            {
                "request_id": str(request_id),
                "owner": owner_user_id,
                "session_id": str(session_id),
                "question_id": question_id,
                "key": idempotency_key,
                "input": input_fingerprint,
                "ink": json.dumps(ink_payload, ensure_ascii=False, sort_keys=True),
                "release": provider.provider_release,
                "endpoint_hash": endpoint_hash,
                "state": state,
                "output": output_fingerprint,
                "failure": failure_code,
                "latency": latency_ms,
                "created_at": created_at,
                "recognition_metadata": (
                    json.dumps(recognition_metadata, sort_keys=True)
                    if recognition_metadata is not None
                    else None
                ),
            },
        )
        formula = None
        if latex is not None:
            formula_id = uuid4()
            version_id = uuid4()
            self.session.execute(
                text(
                    """
                    insert into learning.math_formula_versions (
                      formula_version_id, formula_id, request_id, owner_user_id,
                      session_id, question_id, version_no, state, latex, source,
                      created_at
                    ) values (
                      cast(:version_id as uuid), cast(:formula_id as uuid),
                      cast(:request_id as uuid), cast(:owner as uuid),
                      cast(:session_id as uuid), :question_id, 1, 'candidate',
                      :latex, :candidate_source, :created_at
                    )
                    """
                ),
                {
                    "version_id": str(version_id),
                    "formula_id": str(formula_id),
                    "request_id": str(request_id),
                    "owner": owner_user_id,
                    "session_id": str(session_id),
                    "question_id": question_id,
                    "latex": latex,
                    "candidate_source": provider.candidate_source,
                    "created_at": created_at,
                },
            )
            formula = self.formula(
                owner_user_id=owner_user_id, formula_version_id=version_id
            )
        self._record_external_operation(
            request_id=request_id,
            owner_user_id=owner_user_id,
            session_id=session_id,
            question_id=question_id,
            idempotency_key=idempotency_key,
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
            failure_code=failure_code,
            latency_ms=latency_ms,
            completed_at=created_at,
            provider=provider,
        )
        return MathInkRecognitionResult(
            state=state,
            requestId=request_id,
            formula=formula,
            failureCode=failure_code,
        )

    def replay_projection(self, row) -> MathInkRecognitionResult:
        request_id = UUID(str(row["request_id"]))
        return MathInkRecognitionResult(
            state=row["state"],
            requestId=request_id,
            formula=self.formula_for_request(
                owner_user_id=str(row["owner_user_id"]), request_id=request_id
            ),
            failureCode=row["failure_code"],
        )

    def formula(
        self, *, owner_user_id: str, formula_version_id: UUID
    ) -> FormulaVersion:
        row = (
            self.session.execute(
                text(
                    "select * from learning.math_formula_versions "
                    "where owner_user_id = cast(:owner as uuid) "
                    "and formula_version_id = cast(:version_id as uuid)"
                ),
                {"owner": owner_user_id, "version_id": str(formula_version_id)},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MathInkNotFound("formula version")
        return _formula(row)

    def confirm(
        self,
        *,
        owner_user_id: str,
        formula_version_id: UUID,
        expected_version_no: int,
        latex: str,
        now: datetime,
    ) -> FormulaVersion:
        source = self.formula(
            owner_user_id=owner_user_id, formula_version_id=formula_version_id
        )
        if source.version_no != expected_version_no or source.state != "candidate":
            raise MathInkConflict("formula candidate is stale or already decided")
        successor = (
            self.session.execute(
                text(
                    "select * from learning.math_formula_versions "
                    "where supersedes_formula_version_id = cast(:version_id as uuid)"
                ),
                {"version_id": str(formula_version_id)},
            )
            .mappings()
            .one_or_none()
        )
        if successor is not None:
            if successor["state"] == "confirmed" and successor["latex"] == latex:
                return _formula(successor)
            raise MathInkConflict("formula candidate already has another decision")
        version_id = uuid4()
        self.session.execute(
            text(
                """
                insert into learning.math_formula_versions (
                  formula_version_id, formula_id, request_id, owner_user_id,
                  session_id, question_id, version_no, state, latex, source,
                  supersedes_formula_version_id, created_at, confirmed_at
                ) values (
                  cast(:version_id as uuid), cast(:formula_id as uuid),
                  cast(:request_id as uuid), cast(:owner as uuid),
                  cast(:session_id as uuid), :question_id, :version_no,
                  'confirmed', :latex, 'user_confirmed',
                  cast(:supersedes as uuid), :now, :now
                )
                """
            ),
            {
                "version_id": str(version_id),
                "formula_id": str(source.formula_id),
                "request_id": str(source.request_id),
                "owner": owner_user_id,
                "session_id": str(source.session_id),
                "question_id": source.question_id,
                "version_no": source.version_no + 1,
                "latex": latex,
                "supersedes": str(source.formula_version_id),
                "now": now,
            },
        )
        return self.formula(owner_user_id=owner_user_id, formula_version_id=version_id)

    def require_production_release(
        self,
        *,
        input_fingerprint: str,
        provider: MathInkProviderDescriptor,
    ) -> None:
        if self.settings.environment.strip().casefold() != "production":
            return
        ExternalProcessingRepository().require_active_release(
            self.session,
            declared_provider_release=provider.provider_release,
            provider_endpoint_fingerprint=endpoint_fingerprint(
                provider.endpoint
            ),
            purpose="handwriting_math_recognition",
            data_categories=("handwriting_strokes",),
        )

    def _record_external_operation(
        self,
        *,
        request_id: UUID,
        owner_user_id: str,
        session_id: UUID,
        question_id: str,
        idempotency_key: str,
        input_fingerprint: str,
        output_fingerprint: str | None,
        failure_code: str | None,
        latency_ms: int,
        completed_at: datetime,
        provider: MathInkProviderDescriptor,
    ) -> None:
        try:
            unit_price = Decimal(provider.unit_price_cny)
        except InvalidOperation as exc:
            raise MathInkConflict("formula price card is invalid") from exc
        if (
            not unit_price.is_finite()
            or unit_price < 0
            or not provider.price_version.strip()
        ):
            raise MathInkConflict("formula price card is incomplete")
        environment = self.settings.environment.strip().casefold()
        deployment_id = provider.deployment_id.strip() or None
        evaluation_state = (
            "passed" if environment == "production" else "local_unreleased"
        )
        if environment == "production" and deployment_id is None:
            raise MathInkConflict("formula production deployment is not evaluated")
        if environment != "production":
            deployment_id = None
        status = "succeeded" if output_fingerprint else "failed"
        self.session.execute(
            text(
                """
                insert into ai_audit.external_ai_operation_ledger (
                  operation_id, operation_key, user_id, scenario_key, operation,
                  responding_agent, impact_level, model_tier, deployment_id,
                  evaluation_state, provider, provider_release, model,
                  prompt_version, schema_version, price_version, billing_unit,
                  unit_count, unit_price_cny, total_cost_cny, status,
                  input_fingerprint, output_fingerprint, evidence_refs,
                  latency_ms, error_code, completed_at
                ) values (
                  cast(:request_id as uuid), :operation_key, cast(:owner as uuid),
                  'math_ink_recognition', 'formula_recognition',
                  'recognition', 'medium', 'small', cast(:deployment_id as uuid),
                  :evaluation_state, :provider, :provider_release, :model,
                  'math-ink-recognition-request@v2', 'latex-candidate@v1', :price_version,
                  'call', 1, cast(:unit_price as numeric),
                  round(cast(:unit_price as numeric), 8), :status,
                  :input, :output, cast(:evidence as jsonb), :latency,
                  :error_code, :completed_at
                )
                """
            ),
            {
                "request_id": str(request_id),
                "operation_key": (
                    f"math-ink:{provider.provider.casefold()}:"
                    f"{owner_user_id}:{idempotency_key}"
                ),
                "owner": owner_user_id,
                "deployment_id": deployment_id,
                "evaluation_state": evaluation_state,
                "provider": provider.provider,
                "provider_release": provider.provider_release,
                "model": provider.model_release,
                "price_version": provider.price_version,
                "unit_price": unit_price,
                "status": status,
                "input": input_fingerprint,
                "output": output_fingerprint,
                "evidence": json.dumps(
                    [
                        {
                            "kind": "math_ink",
                            "sourceId": str(session_id),
                            "version": str(request_id),
                            "anchor": question_id,
                            "title": "数学草稿手写",
                        }
                    ],
                    ensure_ascii=False,
                ),
                "latency": latency_ms,
                "error_code": failure_code,
                "completed_at": completed_at,
            },
        )
        ExternalProcessingRepository().record_external_operation_dispatch(
            self.session,
            operation_id=request_id,
            user_id=owner_user_id,
            scenario_key="math_ink_recognition",
            purpose="handwriting_math_recognition",
            data_categories=("handwriting_strokes",),
            declared_provider_release=provider.provider_release,
            provider_endpoint_fingerprint=endpoint_fingerprint(
                provider.endpoint
            ),
            input_fingerprint=input_fingerprint,
            production_authorization_required=environment == "production",
            dispatched_at=completed_at,
        )


def _formula(row) -> FormulaVersion:
    return FormulaVersion(
        formulaVersionId=row["formula_version_id"],
        formulaId=row["formula_id"],
        requestId=row["request_id"],
        sessionId=row["session_id"],
        questionId=row["question_id"],
        versionNo=row["version_no"],
        state=row["state"],
        latex=row["latex"],
        source=row["source"],
        supersedesFormulaVersionId=row["supersedes_formula_version_id"],
        createdAt=row["created_at"],
        confirmedAt=row["confirmed_at"],
    )
