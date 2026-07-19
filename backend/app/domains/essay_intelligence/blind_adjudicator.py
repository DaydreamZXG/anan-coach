from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.governance.contracts import EvidenceReference
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.ai.model_cost import (
    ModelCallLedgerRepository,
    ModelCallRecord,
    ModelPriceCard,
    ModelTokenUsage,
)
from app.domains.ai.provider_contract import (
    parse_provider_json_object,
    provider_failure_is_retryable,
    provider_request_audit,
)
from app.domains.essay_intelligence.multi_model_grading import (
    ADJUDICATION_CONTRACT_VERSION,
    EssayBlindAdjudicationDecision,
    EssayBlindAdjudicationRequest,
)
from app.domains.essay_intelligence.multi_model_grading_repository import (
    EssayAdjudicationAudit,
)
from app.domains.essay_intelligence.review_checkpoint import (
    EssayReviewCheckpointRepository,
)
from app.platform.jobs import BackgroundJob


ADJUDICATION_PROMPT_VERSION = "essay-blind-adjudicator-v1"
ADJUDICATION_SCHEMA_VERSION = "essay-blind-adjudication-decision-v1"
_RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEADER_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")


class BlindAdjudicatorConfigurationError(RuntimeError):
    pass


class BlindAdjudicatorError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BlindAdjudicatorMeteredError(BlindAdjudicatorError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        usage: ModelTokenUsage,
        output_fingerprint: str,
        latency_ms: int,
    ) -> None:
        super().__init__(code, message, retryable=False)
        self.usage = usage
        self.output_fingerprint = output_fingerprint
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class BlindAdjudicatorSettings:
    base_url: str
    api_key: str
    provider_release_id: str
    model: str
    idempotency_key_header: str
    request_id_header: str
    idempotency_capability: str
    maximum_input_bytes: int
    maximum_output_bytes: int

    @classmethod
    def from_environment(cls) -> "BlindAdjudicatorSettings":
        return cls(
            base_url=os.getenv("AI_ESSAY_ADJUDICATOR_BASE_URL", ""),
            api_key=os.getenv("AI_ESSAY_ADJUDICATOR_API_KEY", ""),
            provider_release_id=os.getenv(
                "AI_ESSAY_ADJUDICATOR_PROVIDER_RELEASE_ID", ""
            ),
            model=os.getenv("AI_ESSAY_ADJUDICATOR_MODEL", ""),
            idempotency_key_header=os.getenv(
                "AI_ESSAY_ADJUDICATOR_IDEMPOTENCY_KEY_HEADER",
                "Idempotency-Key",
            ),
            request_id_header=os.getenv(
                "AI_ESSAY_ADJUDICATOR_REQUEST_ID_HEADER", ""
            ),
            idempotency_capability=os.getenv(
                "AI_ESSAY_ADJUDICATOR_IDEMPOTENCY_CAPABILITY", ""
            ),
            maximum_input_bytes=int(
                os.getenv("AI_ESSAY_ADJUDICATOR_MAX_INPUT_BYTES", "0")
            ),
            maximum_output_bytes=int(
                os.getenv("AI_ESSAY_ADJUDICATOR_MAX_OUTPUT_BYTES", "0")
            ),
        )


@dataclass(frozen=True)
class BlindAdjudicatorProviderResult:
    decision: EssayBlindAdjudicationDecision
    usage: ModelTokenUsage
    output_fingerprint: str
    latency_ms: int
    provider_request_id_header: str | None
    provider_request_id_fingerprint: str | None


class OpenAICompatibleBlindAdjudicatorProvider:
    """Brand-blind adjudicator; its request contains no candidate provider identity."""

    def __init__(
        self,
        settings: BlindAdjudicatorSettings | None = None,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._settings = settings or BlindAdjudicatorSettings.from_environment()
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(90.0, connect=15.0))
        )
        self._validate_configuration()

    @property
    def provider_name(self) -> str:
        return f"openai-compatible-adjudicator@{self._settings.provider_release_id}"

    @property
    def provider_release_id(self) -> str:
        return self._settings.provider_release_id

    @property
    def model(self) -> str:
        return self._settings.model

    @property
    def endpoint(self) -> str:
        return f"{self._settings.base_url.rstrip('/')}/chat/completions"

    def adjudicate(
        self,
        *,
        request: EssayBlindAdjudicationRequest,
        idempotency_key: str,
    ) -> BlindAdjudicatorProviderResult:
        request_document = request.model_dump(by_alias=True, mode="json")
        serialized_request = json.dumps(
            request_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        lowered = serialized_request.casefold()
        if any(token in lowered for token in ("providerreleaseid", '"model"', '"slotid"')):
            raise BlindAdjudicatorError(
                "brand_blinding_failed",
                "Adjudication request leaked candidate provider identity",
                retryable=False,
            )
        output_schema = EssayBlindAdjudicationDecision.model_json_schema(by_alias=True)
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是国考申论评分争议裁决器。候选已匿名，不得猜测模型来源。"
                        "不得平均分数，只能选择证据更充分的一份候选。理由必须引用该候选"
                        "已有的评分点、答案证据和材料证据；不得新造引用。只返回 JSON。"
                    ),
                },
                {"role": "user", "content": serialized_request},
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "essay_blind_adjudication",
                    "strict": True,
                    "schema": output_schema,
                },
            },
        }
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self._settings.maximum_input_bytes:
            raise BlindAdjudicatorError(
                "model_context_capacity_exceeded",
                "Blind adjudication input exceeds configured capacity",
                retryable=False,
            )
        started = time.monotonic()
        try:
            with self._client_factory() as client:
                response = client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._settings.api_key}",
                        "Content-Type": "application/json",
                        self._settings.idempotency_key_header: idempotency_key,
                    },
                    content=encoded,
                )
            latency_ms = max(0, round((time.monotonic() - started) * 1000))
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BlindAdjudicatorError(
                "provider_transport_failed",
                "Blind adjudicator transport failed",
                retryable=provider_failure_is_retryable(
                    idempotency_capability=self._settings.idempotency_capability
                ),
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise BlindAdjudicatorError(
                "provider_http_failed",
                f"Blind adjudicator returned HTTP {exc.response.status_code}",
                retryable=provider_failure_is_retryable(
                    idempotency_capability=self._settings.idempotency_capability,
                    status_code=exc.response.status_code,
                ),
            ) from exc
        usage: ModelTokenUsage | None = None
        try:
            envelope = response.json()
            if envelope.get("model") is not None and envelope["model"] != self.model:
                raise ValueError("adjudicator model drifted")
            usage = _usage(envelope["usage"])
            content = envelope["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content:
                raise ValueError("adjudicator content is empty")
            encoded_content = content.encode("utf-8")
            if len(encoded_content) > self._settings.maximum_output_bytes:
                raise ValueError("adjudicator output exceeds configured capacity")
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            if usage is not None:
                raise BlindAdjudicatorMeteredError(
                    "adjudication_envelope_invalid",
                    "Blind adjudicator returned a billable invalid envelope",
                    usage=usage,
                    output_fingerprint=sha256(response.content).hexdigest(),
                    latency_ms=latency_ms,
                ) from exc
            raise BlindAdjudicatorError(
                "adjudication_envelope_invalid",
                "Blind adjudicator returned an invalid response envelope",
                retryable=False,
            ) from exc
        output_fingerprint = sha256(encoded_content).hexdigest()
        try:
            document = parse_provider_json_object(content)
            decision = EssayBlindAdjudicationDecision.model_validate_json(
                json.dumps(document, ensure_ascii=False)
            )
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise BlindAdjudicatorMeteredError(
                "adjudication_response_invalid",
                "Blind adjudicator returned an invalid decision",
                usage=usage,
                output_fingerprint=output_fingerprint,
                latency_ms=latency_ms,
            ) from exc
        audit_header, request_id_fingerprint = provider_request_audit(
            response=response,
            envelope=envelope,
            configured_source=self._settings.request_id_header,
        )
        return BlindAdjudicatorProviderResult(
            decision=decision,
            usage=usage,
            output_fingerprint=output_fingerprint,
            latency_ms=latency_ms,
            provider_request_id_header=audit_header,
            provider_request_id_fingerprint=request_id_fingerprint,
        )

    def _validate_configuration(self) -> None:
        parsed = urlsplit(self._settings.base_url)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise BlindAdjudicatorConfigurationError(
                "Blind adjudicator requires a credential-free HTTPS base URL"
            )
        if not self._settings.api_key:
            raise BlindAdjudicatorConfigurationError(
                "AI_ESSAY_ADJUDICATOR_API_KEY is required"
            )
        if _RELEASE_PATTERN.fullmatch(self._settings.provider_release_id) is None:
            raise BlindAdjudicatorConfigurationError(
                "AI_ESSAY_ADJUDICATOR_PROVIDER_RELEASE_ID is invalid"
            )
        if not self._settings.model.strip():
            raise BlindAdjudicatorConfigurationError(
                "AI_ESSAY_ADJUDICATOR_MODEL is required"
            )
        header = self._settings.idempotency_key_header.strip()
        if (
            _HEADER_PATTERN.fullmatch(header) is None
            or header.casefold() in {"authorization", "content-type", "host"}
        ):
            raise BlindAdjudicatorConfigurationError(
                "Blind adjudicator idempotency header is invalid or reserved"
            )
        if min(
            self._settings.maximum_input_bytes,
            self._settings.maximum_output_bytes,
        ) <= 0:
            raise BlindAdjudicatorConfigurationError(
                "Blind adjudicator input and output capacities are required"
            )


class LedgeredEssayBlindAdjudicator:
    def __init__(
        self,
        *,
        session: Session,
        app_settings: Settings,
        job: BackgroundJob,
        provider: OpenAICompatibleBlindAdjudicatorProvider,
        price_card: ModelPriceCard,
        evidence_refs: tuple[EvidenceReference, ...],
        ledger: ModelCallLedgerRepository | None = None,
        checkpoints: EssayReviewCheckpointRepository | None = None,
    ) -> None:
        self._session = session
        self._app_settings = app_settings
        self._job = job
        self._provider = provider
        self._price_card = price_card
        self._evidence_refs = evidence_refs
        self._ledger = ledger or ModelCallLedgerRepository()
        self._checkpoints = checkpoints or EssayReviewCheckpointRepository.from_settings(
            app_settings
        )

    def adjudicate(
        self,
        *,
        grading_run_id: UUID,
        request: EssayBlindAdjudicationRequest,
    ) -> EssayAdjudicationAudit:
        input_fingerprint = sha256(
            json.dumps(
                request.model_dump(by_alias=True, mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        checkpointed = self._load_checkpoint(
            grading_run_id=grading_run_id,
            request=request,
            input_fingerprint=input_fingerprint,
        )
        if checkpointed is not None:
            return checkpointed
        call_key = f"essay-grading:{grading_run_id}:adjudication"
        quota = self._job.payload["quotaReservation"]
        existing_before_dispatch = self._ledger.get_existing(
            self._session,
            call_key=call_key,
            user_id=str(self._job.payload["userId"]),
            job_id=self._job.job_id,
            feature="essay_review",
            operation="essay_blind_adjudication",
            input_fingerprint=input_fingerprint,
        )
        record = self._ledger.begin(
            self._session,
            call_key=call_key,
            user_id=str(self._job.payload["userId"]),
            job_id=self._job.job_id,
            feature="essay_review",
            operation="essay_blind_adjudication",
            provider=self._provider.provider_name,
            model=self._provider.model,
            prompt_version=ADJUDICATION_PROMPT_VERSION,
            schema_version=ADJUDICATION_SCHEMA_VERSION,
            price_card=self._price_card,
            input_fingerprint=input_fingerprint,
            attribution=AIGovernanceRepository().attribution(
                self._session,
                environment=self._app_settings.environment,
                scenario_key="essay_grounded_review",
                operation="essay_blind_adjudication",
                provider=self._provider.provider_name,
                model=self._provider.model,
                prompt_version=ADJUDICATION_PROMPT_VERSION,
                schema_version=ADJUDICATION_SCHEMA_VERSION,
                evidence_refs=self._evidence_refs,
                retrieval_bundle_id=None,
                reservation_id=UUID(str(quota["reservationId"])),
                reservation_source=str(quota.get("source", "catalog")),
            ),
            processor_release_id=self._provider.provider_release_id,
            processor_endpoint_fingerprint=endpoint_fingerprint(
                self._provider.endpoint
            ),
            processing_purpose="essay_blind_adjudication",
            processing_data_categories=(
                "essay_answer",
                "public_exam_material",
                "grading_rubric",
                "model_candidate_output",
            ),
        )
        self._session.commit()
        if existing_before_dispatch is not None and record.status == "running":
            record = self._ledger.fail(
                self._session,
                call_id=record.call_id,
                error_code="adjudication_outcome_unknown_after_interruption",
                latency_ms=0,
            )
        if existing_before_dispatch is not None and record.status == "succeeded":
            code = "adjudication_checkpoint_missing_after_completed_call"
            self._save_failure_checkpoint(record=record, error_code=code)
            raise BlindAdjudicatorError(code, code, retryable=False)
        if record.status == "failed":
            code = record.error_code or "adjudication_failed"
            self._save_failure_checkpoint(
                record=record,
                error_code=code,
            )
            raise BlindAdjudicatorError(code, code, retryable=False)
        try:
            result = self._provider.adjudicate(
                request=request,
                idempotency_key=call_key,
            )
        except BlindAdjudicatorMeteredError as exc:
            if (
                record.status == "succeeded"
                and record.output_fingerprint != exc.output_fingerprint
            ):
                raise BlindAdjudicatorError(
                    "provider_idempotency_violation",
                    "Adjudicator changed a rejected immutable response",
                    retryable=False,
                ) from exc
            if record.status == "running":
                record = self._ledger.succeed(
                    self._session,
                    call_id=record.call_id,
                    usage=exc.usage,
                    output_fingerprint=exc.output_fingerprint,
                    latency_ms=exc.latency_ms,
                )
            self._save_failure_checkpoint(record=record, error_code=exc.code)
            raise
        except BlindAdjudicatorError as exc:
            if record.status == "succeeded":
                raise BlindAdjudicatorError(
                    "provider_idempotency_violation",
                    "Adjudicator replaced a completed immutable response with an error",
                    retryable=False,
                ) from exc
            retryable = exc.retryable and self._job.attempt_count < self._job.max_attempts
            if record.status == "running" and not retryable:
                record = self._ledger.fail(
                    self._session,
                    call_id=record.call_id,
                    error_code=exc.code,
                    latency_ms=0,
                )
                self._save_failure_checkpoint(record=record, error_code=exc.code)
            if retryable:
                raise
            raise BlindAdjudicatorError(
                exc.code,
                str(exc),
                retryable=False,
            ) from exc
        if (
            record.status == "succeeded"
            and record.output_fingerprint != result.output_fingerprint
        ):
            raise BlindAdjudicatorError(
                "provider_idempotency_violation",
                "Adjudicator changed output for an immutable call key",
                retryable=False,
            )
        if record.status == "running":
            record = self._ledger.succeed(
                self._session,
                call_id=record.call_id,
                usage=result.usage,
                output_fingerprint=result.output_fingerprint,
                latency_ms=result.latency_ms,
            )
        if not _provider_request_audit_pair_valid(
            result.provider_request_id_header,
            result.provider_request_id_fingerprint,
        ):
            self._save_failure_checkpoint(
                record=record,
                error_code="provider_request_id_contract_invalid",
            )
            raise BlindAdjudicatorError(
                "provider_request_id_contract_invalid",
                "Adjudicator request ID audit fields must appear together",
                retryable=False,
            )
        audit = EssayAdjudicationAudit(
            adjudication_id=uuid5(
                NAMESPACE_URL,
                f"{ADJUDICATION_CONTRACT_VERSION}:{grading_run_id}",
            ),
            provider_release_id=self._provider.provider_release_id,
            model=self._provider.model,
            prompt_version=ADJUDICATION_PROMPT_VERSION,
            schema_version=ADJUDICATION_SCHEMA_VERSION,
            model_call_id=record.call_id,
            blind_request=request,
            decision=result.decision,
            output_fingerprint=result.output_fingerprint,
        )
        self._save_success_checkpoint(
            record=record,
            audit=audit,
            provider_request_id_header=result.provider_request_id_header,
            provider_request_id_fingerprint=result.provider_request_id_fingerprint,
        )
        return audit

    def _load_checkpoint(
        self,
        *,
        grading_run_id: UUID,
        request: EssayBlindAdjudicationRequest,
        input_fingerprint: str,
    ) -> EssayAdjudicationAudit | None:
        saved = self._checkpoints.load(
            self._session,
            job_id=self._job.job_id,
            phase="adjudication",
        )
        if saved is None or saved.payload is None:
            return None
        record = self._ledger.terminal_for_checkpoint(
            self._session,
            call_id=saved.call_id,
            job_id=self._job.job_id,
            input_fingerprint=input_fingerprint,
        )
        document = saved.payload
        status = document.get("status")
        if status == "failed":
            code = document.get("errorCode")
            if not isinstance(code, str) or not code:
                raise ValueError("Adjudication failure checkpoint is invalid")
            expected_fingerprint = (
                record.output_fingerprint
                if record.status == "succeeded"
                else _checkpoint_hash(document)
            )
            if saved.output_fingerprint != expected_fingerprint:
                raise ValueError("Adjudication failure checkpoint ledger mismatch")
            raise BlindAdjudicatorError(code, code, retryable=False)
        if status != "succeeded" or record.status != "succeeded":
            raise ValueError("Adjudication success checkpoint is invalid")
        if record.output_fingerprint != saved.output_fingerprint:
            raise ValueError("Adjudication checkpoint output does not match its ledger")
        try:
            blind_request = EssayBlindAdjudicationRequest.model_validate_json(
                json.dumps(document["blindRequest"], ensure_ascii=False)
            )
            decision = EssayBlindAdjudicationDecision.model_validate_json(
                json.dumps(document["decision"], ensure_ascii=False)
            )
            audit = EssayAdjudicationAudit(
                adjudication_id=UUID(str(document["adjudicationId"])),
                provider_release_id=str(document["providerReleaseId"]),
                model=str(document["model"]),
                prompt_version=str(document["promptVersion"]),
                schema_version=str(document["schemaVersion"]),
                model_call_id=saved.call_id,
                blind_request=blind_request,
                decision=decision,
                output_fingerprint=saved.output_fingerprint,
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ValueError("Adjudication checkpoint payload is invalid") from exc
        immutable = (
            audit.adjudication_id
            == uuid5(NAMESPACE_URL, f"{ADJUDICATION_CONTRACT_VERSION}:{grading_run_id}"),
            audit.provider_release_id == self._provider.provider_release_id,
            audit.model == self._provider.model,
            audit.prompt_version == ADJUDICATION_PROMPT_VERSION,
            audit.schema_version == ADJUDICATION_SCHEMA_VERSION,
            audit.blind_request == request,
        )
        if not all(immutable):
            raise ValueError("Adjudication checkpoint belongs to another release")
        return audit

    def _save_success_checkpoint(
        self,
        *,
        record: ModelCallRecord,
        audit: EssayAdjudicationAudit,
        provider_request_id_header: str | None,
        provider_request_id_fingerprint: str | None,
    ) -> None:
        if record.output_fingerprint is None:
            raise ValueError("Successful adjudication ledger lacks an output fingerprint")
        document = {
            "status": "succeeded",
            "adjudicationId": str(audit.adjudication_id),
            "providerReleaseId": audit.provider_release_id,
            "model": audit.model,
            "promptVersion": audit.prompt_version,
            "schemaVersion": audit.schema_version,
            "blindRequest": audit.blind_request.model_dump(by_alias=True, mode="json"),
            "decision": audit.decision.model_dump(by_alias=True, mode="json"),
        }
        self._checkpoints.save(
            self._session,
            job_id=self._job.job_id,
            phase="adjudication",
            call_id=record.call_id,
            payload=document,
            output_fingerprint=record.output_fingerprint,
            provider_request_id_header=provider_request_id_header,
            provider_request_id_fingerprint=provider_request_id_fingerprint,
        )
        self._session.commit()

    def _save_failure_checkpoint(
        self,
        *,
        record: ModelCallRecord,
        error_code: str,
    ) -> None:
        document = {"status": "failed", "errorCode": error_code}
        output_fingerprint = record.output_fingerprint or _checkpoint_hash(document)
        self._checkpoints.save(
            self._session,
            job_id=self._job.job_id,
            phase="adjudication",
            call_id=record.call_id,
            payload=document,
            output_fingerprint=output_fingerprint,
        )
        self._session.commit()


def _usage(value: Any) -> ModelTokenUsage:
    if not isinstance(value, dict):
        raise ValueError("usage must be an object")
    input_tokens = value.get("prompt_tokens")
    output_tokens = value.get("completion_tokens")
    details = value.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in (input_tokens, output_tokens, cached)
    ):
        raise ValueError("usage token fields must be non-negative integers")
    return ModelTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
    )


def _checkpoint_hash(document: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _provider_request_audit_pair_valid(
    header: object,
    fingerprint: object,
) -> bool:
    if header is None and fingerprint is None:
        return True
    return (
        isinstance(header, str)
        and bool(header.strip())
        and isinstance(fingerprint, str)
        and len(fingerprint) == 64
        and all(character in "0123456789abcdef" for character in fingerprint)
    )
