from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from httpx import HTTPStatusError, NetworkError, TimeoutException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.ai.audit import AIAuditRepository
from app.domains.ai.model_cost import (
    ModelCallLedgerError,
    ModelCallLedgerRepository,
    ModelCallRecord,
    ModelCostConfigurationError,
    ModelPriceCard,
)
from app.domains.ai.provider import (
    AIProvider,
    AIProviderConfigurationError,
    AIProviderResponseError,
)
from app.domains.commerce.repository import (
    CommerceQuotaExceededError,
    FeatureUsageReservation,
)
from app.domains.commerce.service import CommerceService
from app.domains.companion.commands import (
    CompanionCommandRecord,
    CompanionCommandRepository,
    CompanionCommandStateError,
    payload_fingerprint,
    request_fingerprint,
    stable_command_key,
    stable_provider_key,
)
from app.domains.companion.repository import (
    CompanionContextRecord,
    CompanionRepository,
    ExternalQuestionIntakeRecord,
    KnowledgeMapCandidateRecord,
)
from app.domains.companion.schemas import (
    CompanionAskRequest,
    CompanionAskResponse,
    CompanionCitation,
    ExternalQuestionConfirmRequest,
    ExternalQuestionConfirmResponse,
    ExternalQuestionResolveRequest,
    ExternalQuestionResolveResponse,
    KnowledgeMapCandidate,
)
from app.domains.essay.schemas import AIQuotaState
from app.platform.outbox import OutboxRepository

COMPANION_ASK_FEATURE = "companion_ask"
EXTERNAL_WRONG_INTAKE_FEATURE = "external_wrong_intake"
MIN_EXTRACTION_CONFIDENCE = 0.70
MIN_NODE_CONFIDENCE = 0.75
COMPANION_ASK_REQUEST_CONTRACT = "companion-ask-command-v1"
COMPANION_ASK_RESPONSE_CONTRACT = "companion-ask-response-v1"
EXTERNAL_RESOLVE_REQUEST_CONTRACT = "external-question-resolve-command-v1"
EXTERNAL_RESOLVE_RESPONSE_CONTRACT = "external-question-resolve-response-v1"


class ExternalQuestionIntakeNotFoundError(LookupError):
    pass


class ExternalQuestionConfirmationError(RuntimeError):
    pass


class CompanionCommandFailure(RuntimeError):
    def __init__(self, *, status_code: int, detail: str, error_code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code


class CompanionProviderUncertain(CompanionCommandFailure):
    """The provider may have accepted the stable-key request; retry is safe."""


@dataclass(frozen=True)
class _PreparedPaidCommand:
    record: CompanionCommandRecord
    reservation: FeatureUsageReservation | None
    model_call: ModelCallRecord | None


class CompanionService:
    def __init__(
        self,
        *,
        repository: CompanionRepository,
        audit_repository: AIAuditRepository,
        commerce_service: CommerceService,
        ai_provider: AIProvider,
        session: Session,
        command_repository: CompanionCommandRepository | None = None,
        model_call_repository: ModelCallLedgerRepository | None = None,
        outbox: OutboxRepository | None = None,
        settings: Settings | None = None,
        price_card: ModelPriceCard | None = None,
    ) -> None:
        self._repository = repository
        self._audit_repository = audit_repository
        self._commerce_service = commerce_service
        self._ai_provider = ai_provider
        self._session = session
        self._commands = command_repository or CompanionCommandRepository()
        self._model_calls = model_call_repository or ModelCallLedgerRepository()
        self._outbox = outbox or OutboxRepository()
        self._settings = settings or get_settings()
        self._price_card = price_card

    def ask(
        self,
        *,
        user_id: str,
        learner_key: str,
        idempotency_key: str,
        request: CompanionAskRequest,
    ) -> CompanionAskResponse:
        request_payload = request.model_dump(by_alias=True, mode="json")
        existing = self._commands.find(
            self._session,
            user_id=user_id,
            feature=COMPANION_ASK_FEATURE,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            execution_payload = existing.execution_payload
        else:
            context = self._repository.get_context(
                user_id=user_id,
                node_id=request.node_id,
            )
            context_payload = _context_payload(context)
            persona = _build_persona_prompt(context)
            provider_request = self._ai_provider.prepare_companion_question(
                scenario=request.scenario,
                question_text=request.question_text,
                context=context_payload,
                persona=persona,
            )
            execution_payload = {
                "contractVersion": "companion-ask-execution-v1",
                "request": request_payload,
                "providerRequest": provider_request,
                "context": context_payload,
                "citations": [
                    item.model_dump(by_alias=True, mode="json")
                    for item in _citations(context)
                ],
            }
        prepared = self._prepare_paid_command(
            user_id=user_id,
            feature=COMPANION_ASK_FEATURE,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            request_contract_version=COMPANION_ASK_REQUEST_CONTRACT,
            response_contract_version=COMPANION_ASK_RESPONSE_CONTRACT,
            execution_payload=execution_payload,
            provider_call=True,
            quota_detail="Companion quota exceeded",
        )
        replay = _terminal_response(prepared.record, CompanionAskResponse)
        if replay is not None:
            return replay

        command = self._commands.lock_for_execution(
            self._session,
            user_id=user_id,
            feature=COMPANION_ASK_FEATURE,
            idempotency_key=idempotency_key,
        )
        replay = _terminal_response(command, CompanionAskResponse)
        if replay is not None:
            self._session.commit()
            return replay
        provider_request = _companion_provider_request(command)
        command = self._commands.begin_provider_attempt(
            self._session,
            command_id=command.command_id,
        )
        try:
            result = self._ai_provider.answer_prepared_companion_question(
                prepared=provider_request,
                idempotency_key=command.provider_idempotency_key,
            )
        except AIProviderConfigurationError as exc:
            if (
                prepared.record.status == "provider_uncertain"
                or prepared.record.provider_attempt_count > 0
            ):
                self._provider_uncertain(
                    command=command,
                    learner_key=learner_key,
                    request_summary=request.question_text[:200],
                    scenario=request.scenario,
                    error_code="provider_execution_configuration_changed",
                )
                raise AssertionError(
                    "uncertain provider failure must raise"
                ) from exc
            assert prepared.model_call is not None
            self._model_calls.fail(
                self._session,
                call_id=prepared.model_call.call_id,
                error_code="provider_configuration_invalid",
                latency_ms=0,
            )
            self._terminal_provider_failure(
                command=command,
                prepared=prepared,
                learner_key=learner_key,
                request_summary=request.question_text[:200],
                scenario=request.scenario,
                status_code=503,
                detail="AI provider is not configured",
                error_code="provider_configuration_invalid",
                latency_ms=0,
                quota_consumed=False,
            )
            raise AssertionError("terminal provider failure must raise") from exc
        except AIProviderResponseError as exc:
            self._record_metered_provider_failure(
                model_call=prepared.model_call,
                error=exc,
            )
            self._terminal_provider_failure(
                command=command,
                prepared=prepared,
                learner_key=learner_key,
                request_summary=request.question_text[:200],
                scenario=request.scenario,
                status_code=502,
                detail="AI companion answer failed",
                error_code=exc.code,
                latency_ms=exc.latency_ms,
                quota_consumed=True,
                provider_request_id_header=exc.provider_request_id_header,
                provider_request_id_fingerprint=(
                    exc.provider_request_id_fingerprint
                ),
            )
            raise AssertionError("terminal provider failure must raise") from exc
        except HTTPStatusError as exc:
            provider_status = exc.response.status_code
            if provider_status == 408 or provider_status == 429 or provider_status >= 500:
                self._provider_uncertain(
                    command=command,
                    learner_key=learner_key,
                    request_summary=request.question_text[:200],
                    scenario=request.scenario,
                    error_code=f"provider_http_{provider_status}",
                )
            assert prepared.model_call is not None
            self._model_calls.fail(
                self._session,
                call_id=prepared.model_call.call_id,
                error_code=f"provider_http_{provider_status}",
                latency_ms=0,
            )
            self._terminal_provider_failure(
                command=command,
                prepared=prepared,
                learner_key=learner_key,
                request_summary=request.question_text[:200],
                scenario=request.scenario,
                status_code=502,
                detail="AI companion answer failed",
                error_code=f"provider_http_{provider_status}",
                latency_ms=0,
                quota_consumed=False,
            )
            raise AssertionError("terminal provider failure must raise") from exc
        except (TimeoutException, NetworkError) as exc:
            self._provider_uncertain(
                command=command,
                learner_key=learner_key,
                request_summary=request.question_text[:200],
                scenario=request.scenario,
                error_code="provider_transport_uncertain",
            )
            raise AssertionError("uncertain provider failure must raise") from exc
        except Exception as exc:
            self._provider_uncertain(
                command=command,
                learner_key=learner_key,
                request_summary=request.question_text[:200],
                scenario=request.scenario,
                error_code="provider_outcome_unknown",
            )
            raise AssertionError("uncertain provider failure must raise") from exc

        assert prepared.model_call is not None
        self._model_calls.succeed(
            self._session,
            call_id=prepared.model_call.call_id,
            usage=result.usage,
            output_fingerprint=result.output_fingerprint,
            latency_ms=result.latency_ms,
        )
        assert prepared.reservation is not None
        quota = self._commerce_service.consume_feature_usage(
            reservation=prepared.reservation,
            commit=False,
        )
        self._audit_repository.log_interaction(
            log_id=f"companion_command_{command.command_id.hex}",
            learner_key=learner_key,
            feature=COMPANION_ASK_FEATURE,
            provider=self._ai_provider.provider_name,
            model=self._ai_provider.model,
            status="succeeded",
            request_summary=request.question_text[:200],
            response_summary=result.response_summary,
            latency_ms=result.latency_ms,
            metadata={
                "scenario": request.scenario,
                "context": command.execution_payload["context"],
                "commandId": str(command.command_id),
            },
            commit=False,
        )
        response = CompanionAskResponse(
            scenario=command.execution_payload["request"]["scenario"],
            answer=result.answer,
            next_actions=result.next_actions,
            citations=command.execution_payload["citations"],
            quota=_quota_state(quota),
        )
        response_payload = response.model_dump(by_alias=True, mode="json")
        succeeded = self._commands.succeed(
            self._session,
            command_id=command.command_id,
            response_payload=response_payload,
            provider_request_id_header=result.provider_request_id_header,
            provider_request_id_fingerprint=(
                result.provider_request_id_fingerprint
            ),
        )
        self._append_completion_event(
            command=succeeded,
            user_id=user_id,
            response_fingerprint=payload_fingerprint(response_payload),
        )
        self._session.commit()
        return response

    def resolve_external_question(
        self,
        *,
        user_id: str,
        learner_key: str,
        idempotency_key: str,
        request: ExternalQuestionResolveRequest,
    ) -> ExternalQuestionResolveResponse:
        request_payload = request.model_dump(by_alias=True, mode="json")
        existing = self._commands.find(
            self._session,
            user_id=user_id,
            feature=EXTERNAL_WRONG_INTAKE_FEATURE,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            execution_payload = existing.execution_payload
        else:
            candidates: list[KnowledgeMapCandidate] = []
            reason: str | None = None
            status = "pending_confirmation"
            selected_node_id: str | None = None
            selected_confidence: float | None = None
            answer_preview: str | None = None

            if request.contains_image:
                status = "rejected"
                reason = "图片题或需要视觉识别的题面暂不进入外部错题记忆。"
            elif request.extraction_confidence < MIN_EXTRACTION_CONFIDENCE:
                status = "rejected"
                reason = "题面提取置信度不足，不能写入 learner 记忆。"
            else:
                candidates = self._resolve_candidates(request)
                text_supported = [
                    candidate
                    for candidate in candidates
                    if candidate.support_status == "text_supported"
                    and candidate.confidence >= MIN_NODE_CONFIDENCE
                ]
                if not candidates:
                    status = "rejected"
                    reason = "没有命中知识点地图，拒绝使用模型自由 tag 入库。"
                elif len(text_supported) != 1:
                    status = "rejected"
                    reason = "候选知识点不唯一、置信度不足或暂不支持。"
                else:
                    selected = text_supported[0]
                    selected_node_id = selected.node_id
                    selected_confidence = selected.confidence
                    answer_preview = _answer_preview(selected)
            execution_payload = {
                "contractVersion": "external-question-resolve-execution-v1",
                "request": request_payload,
                "resolution": {
                    "status": status,
                    "reason": reason,
                    "candidates": [
                        item.model_dump(by_alias=True, mode="json")
                        for item in candidates
                    ],
                    "selectedNodeId": selected_node_id,
                    "selectedConfidence": selected_confidence,
                    "answerPreview": answer_preview,
                },
            }
        prepared = self._prepare_paid_command(
            user_id=user_id,
            feature=EXTERNAL_WRONG_INTAKE_FEATURE,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            request_contract_version=EXTERNAL_RESOLVE_REQUEST_CONTRACT,
            response_contract_version=EXTERNAL_RESOLVE_RESPONSE_CONTRACT,
            execution_payload=execution_payload,
            provider_call=False,
            quota_detail="External wrong-question quota exceeded",
        )
        replay = _terminal_response(prepared.record, ExternalQuestionResolveResponse)
        if replay is not None:
            return replay
        command = self._commands.lock_for_execution(
            self._session,
            user_id=user_id,
            feature=EXTERNAL_WRONG_INTAKE_FEATURE,
            idempotency_key=idempotency_key,
        )
        replay = _terminal_response(command, ExternalQuestionResolveResponse)
        if replay is not None:
            self._session.commit()
            return replay
        stored_request = command.execution_payload["request"]
        resolution = command.execution_payload["resolution"]
        stored_candidates = [
            KnowledgeMapCandidate.model_validate(item)
            for item in resolution["candidates"]
        ]
        intake_id = self._repository.create_external_intake(
            user_id=user_id,
            stem=stored_request["stem"],
            options=stored_request["options"],
            selected_answer=stored_request.get("selectedAnswer"),
            correct_answer=stored_request.get("correctAnswer"),
            extracted_tags=stored_request["extractedTags"],
            candidate_nodes=resolution["candidates"],
            selected_node_id=resolution["selectedNodeId"],
            match_confidence=resolution["selectedConfidence"],
            status=resolution["status"],
            rejection_reason=resolution["reason"],
            answer_preview=resolution["answerPreview"],
            commit=False,
        )
        self._audit_repository.log_interaction(
            log_id=f"external_intake_command_{command.command_id.hex}",
            learner_key=learner_key,
            feature=EXTERNAL_WRONG_INTAKE_FEATURE,
            provider="knowledge_map_matcher",
            model="deterministic_v1",
            status=resolution["status"],
            request_summary=stored_request["stem"][:200],
            response_summary=(
                resolution["reason"]
                or resolution["answerPreview"]
                or "pending confirmation"
            ),
            latency_ms=0,
            metadata={
                "intake_id": intake_id,
                "candidate_count": len(stored_candidates),
                "selected_node_id": resolution["selectedNodeId"],
                "commandId": str(command.command_id),
            },
            commit=False,
        )
        assert prepared.reservation is not None
        quota = self._commerce_service.consume_feature_usage(
            reservation=prepared.reservation,
            commit=False,
        )
        response = ExternalQuestionResolveResponse(
            intake_id=intake_id,
            status=resolution["status"],
            reason=resolution["reason"],
            candidates=stored_candidates,
            requires_user_confirmation=(
                resolution["status"] == "pending_confirmation"
            ),
            answer_preview=resolution["answerPreview"],
            quota=_quota_state(quota),
        )
        response_payload = response.model_dump(by_alias=True, mode="json")
        succeeded = self._commands.succeed(
            self._session,
            command_id=command.command_id,
            response_payload=response_payload,
        )
        self._append_completion_event(
            command=succeeded,
            user_id=user_id,
            response_fingerprint=payload_fingerprint(response_payload),
        )
        self._session.commit()
        return response

    def _prepare_paid_command(
        self,
        *,
        user_id: str,
        feature: str,
        idempotency_key: str,
        request_payload: dict[str, Any],
        request_contract_version: str,
        response_contract_version: str,
        execution_payload: dict[str, Any],
        provider_call: bool,
        quota_detail: str,
    ) -> _PreparedPaidCommand:
        reservation_key = stable_command_key(
            user_id=user_id,
            feature=feature,
            idempotency_key=idempotency_key,
        )
        provider_key = (
            stable_provider_key(
                user_id=user_id,
                feature=feature,
                idempotency_key=idempotency_key,
            )
            if provider_call
            else None
        )
        record = self._commands.claim(
            self._session,
            user_id=user_id,
            feature=feature,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint(
                user_id=user_id,
                feature=feature,
                contract_version=request_contract_version,
                payload=request_payload,
            ),
            request_contract_version=request_contract_version,
            execution_payload=execution_payload,
            response_contract_version=response_contract_version,
            reservation_key=reservation_key,
            provider_idempotency_key=provider_key,
        )
        if record.terminal:
            self._session.commit()
            return _PreparedPaidCommand(
                record=record,
                reservation=None,
                model_call=None,
            )
        try:
            reservation = self._commerce_service.reserve_feature_usage(
                user_id=user_id,
                feature=feature,
                reservation_key=reservation_key,
                business_key=f"companion-command:{record.command_id}",
                commit=False,
            )
        except CommerceQuotaExceededError as exc:
            detail_payload = {"detail": quota_detail}
            failed = self._commands.fail(
                self._session,
                command_id=record.command_id,
                status_code=429,
                response_payload=detail_payload,
                error_code="quota_exceeded",
            )
            self._append_completion_event(
                command=failed,
                user_id=user_id,
                response_fingerprint=payload_fingerprint(detail_payload),
            )
            self._session.commit()
            raise CompanionCommandFailure(
                status_code=429,
                detail=quota_detail,
                error_code="quota_exceeded",
            ) from exc

        model_call = None
        if provider_call:
            assert provider_key is not None
            provider_request = _companion_provider_request(record)
            input_fingerprint = payload_fingerprint(provider_request)
            try:
                model_call = self._model_calls.get_existing(
                    self._session,
                    call_key=provider_key,
                    user_id=user_id,
                    job_id=None,
                    feature=feature,
                    operation="companion_answer",
                    input_fingerprint=input_fingerprint,
                )
                self._ai_provider.validate_prepared_companion_question(
                    prepared=provider_request,
                    idempotency_key=record.provider_idempotency_key,
                )
                self._ensure_provider_replay_window(
                    record=record,
                    provider_request=provider_request,
                )
                provider_identity = (
                    self._ai_provider.provider_name,
                    self._ai_provider.model,
                    self._ai_provider.prompt_version,
                    self._ai_provider.schema_version,
                )
                if model_call is not None and (
                    model_call.provider,
                    model_call.model,
                    model_call.prompt_version,
                    model_call.schema_version,
                ) != provider_identity:
                    raise AIProviderConfigurationError(
                        "Stored model call targets another provider contract"
                    )
            except (AIProviderConfigurationError, ModelCallLedgerError, AttributeError) as exc:
                self._preflight_provider_failure(
                    record=record,
                    reservation=reservation,
                    model_call=model_call,
                    user_id=user_id,
                    error_code="provider_configuration_invalid",
                )
                raise AssertionError("provider preflight failure must raise") from exc
            if model_call is None:
                try:
                    price_card = self._price_card or ModelPriceCard.from_settings(
                        self._settings
                    )
                    model_call = self._model_calls.begin(
                        self._session,
                        call_key=provider_key,
                        user_id=user_id,
                        job_id=None,
                        feature=feature,
                        operation="companion_answer",
                        provider=provider_identity[0],
                        model=provider_identity[1],
                        prompt_version=provider_identity[2],
                        schema_version=provider_identity[3],
                        price_card=price_card,
                        input_fingerprint=input_fingerprint,
                    )
                except (ModelCostConfigurationError, ModelCallLedgerError) as exc:
                    self._preflight_provider_failure(
                        record=record,
                        reservation=reservation,
                        model_call=model_call,
                        user_id=user_id,
                        error_code="model_cost_configuration_invalid",
                    )
                    raise AssertionError("model preflight failure must raise") from exc
            if model_call.status != "running":
                raise CompanionCommandStateError(
                    "Open Companion command requires a running model-call ledger"
                )
        self._session.commit()
        return _PreparedPaidCommand(
            record=record,
            reservation=reservation,
            model_call=model_call,
        )

    def _ensure_provider_replay_window(
        self,
        *,
        record: CompanionCommandRecord,
        provider_request: dict[str, Any],
    ) -> None:
        if record.provider_attempt_count == 0:
            return
        retention = provider_request.get("providerReplayRetentionSeconds")
        if (
            isinstance(retention, bool)
            or not isinstance(retention, int)
            or retention <= 0
        ):
            replay_available = False
        else:
            replay_available = datetime.now(UTC) < (
                record.created_at + timedelta(seconds=retention)
            )
        if replay_available:
            return
        if record.status == "processing":
            self._commands.mark_provider_uncertain(
                self._session,
                command_id=record.command_id,
                error_code="provider_replay_window_expired",
            )
        self._session.commit()
        raise CompanionProviderUncertain(
            status_code=503,
            detail="AI provider replay window has expired",
            error_code="provider_replay_window_expired",
        )

    def _preflight_provider_failure(
        self,
        *,
        record: CompanionCommandRecord,
        reservation: FeatureUsageReservation,
        model_call: ModelCallRecord | None,
        user_id: str,
        error_code: str,
    ) -> None:
        if record.provider_attempt_count > 0:
            if record.status == "processing":
                self._commands.mark_provider_uncertain(
                    self._session,
                    command_id=record.command_id,
                    error_code="provider_replay_configuration_invalid",
                )
            self._session.commit()
            raise CompanionProviderUncertain(
                status_code=503,
                detail="AI provider replay configuration is unavailable",
                error_code="provider_replay_configuration_invalid",
            )

        if model_call is not None and model_call.status == "running":
            self._model_calls.fail(
                self._session,
                call_id=model_call.call_id,
                error_code=error_code,
                latency_ms=0,
            )
        self._commerce_service.refund_feature_usage(
            reservation=reservation,
            commit=False,
        )
        detail_payload = {"detail": "AI provider is not configured"}
        failed = self._commands.fail(
            self._session,
            command_id=record.command_id,
            status_code=503,
            response_payload=detail_payload,
            error_code=error_code,
        )
        self._append_completion_event(
            command=failed,
            user_id=user_id,
            response_fingerprint=payload_fingerprint(detail_payload),
        )
        self._session.commit()
        raise CompanionCommandFailure(
            status_code=503,
            detail="AI provider is not configured",
            error_code=error_code,
        )

    def _record_metered_provider_failure(
        self,
        *,
        model_call: ModelCallRecord | None,
        error: AIProviderResponseError,
    ) -> None:
        assert model_call is not None
        if error.usage is not None and error.output_fingerprint is not None:
            self._model_calls.succeed(
                self._session,
                call_id=model_call.call_id,
                usage=error.usage,
                output_fingerprint=error.output_fingerprint,
                latency_ms=error.latency_ms,
            )
            return
        self._model_calls.fail(
            self._session,
            call_id=model_call.call_id,
            error_code=error.code,
            latency_ms=error.latency_ms,
        )

    def _terminal_provider_failure(
        self,
        *,
        command: CompanionCommandRecord,
        prepared: _PreparedPaidCommand,
        learner_key: str,
        request_summary: str,
        scenario: str,
        status_code: int,
        detail: str,
        error_code: str,
        latency_ms: int,
        quota_consumed: bool,
        provider_request_id_header: str | None = None,
        provider_request_id_fingerprint: str | None = None,
    ) -> None:
        assert prepared.reservation is not None
        if quota_consumed:
            self._commerce_service.consume_feature_usage(
                reservation=prepared.reservation,
                commit=False,
            )
        else:
            self._commerce_service.refund_feature_usage(
                reservation=prepared.reservation,
                commit=False,
            )
        self._audit_repository.log_interaction(
            log_id=f"companion_failed_{command.command_id.hex}",
            learner_key=learner_key,
            feature=COMPANION_ASK_FEATURE,
            provider=self._ai_provider.provider_name,
            model=self._ai_provider.model,
            status="failed",
            request_summary=request_summary,
            response_summary=detail,
            latency_ms=latency_ms,
            metadata={
                "commandId": str(command.command_id),
                "errorCode": error_code,
                "quotaConsumed": quota_consumed,
                "scenario": scenario,
            },
            commit=False,
        )
        detail_payload = {"detail": detail}
        failed = self._commands.fail(
            self._session,
            command_id=command.command_id,
            status_code=status_code,
            response_payload=detail_payload,
            error_code=error_code,
            provider_request_id_header=provider_request_id_header,
            provider_request_id_fingerprint=provider_request_id_fingerprint,
        )
        self._append_completion_event(
            command=failed,
            user_id=command.user_id,
            response_fingerprint=payload_fingerprint(detail_payload),
        )
        self._session.commit()
        raise CompanionCommandFailure(
            status_code=status_code,
            detail=detail,
            error_code=error_code,
        )

    def _provider_uncertain(
        self,
        *,
        command: CompanionCommandRecord,
        learner_key: str,
        request_summary: str,
        scenario: str,
        error_code: str,
    ) -> None:
        uncertain = self._commands.mark_provider_uncertain(
            self._session,
            command_id=command.command_id,
            error_code=error_code,
        )
        self._audit_repository.log_interaction(
            log_id=(
                f"companion_uncertain_{command.command_id.hex}_"
                f"{uncertain.provider_attempt_count}"
            ),
            learner_key=learner_key,
            feature=COMPANION_ASK_FEATURE,
            provider=self._ai_provider.provider_name,
            model=self._ai_provider.model,
            status="failed",
            request_summary=request_summary,
            response_summary="provider outcome uncertain; stable-key replay required",
            latency_ms=0,
            metadata={
                "commandId": str(command.command_id),
                "errorCode": error_code,
                "providerIdempotencyKey": command.provider_idempotency_key,
                "quotaRefunded": False,
                "scenario": scenario,
            },
            commit=False,
        )
        self._session.commit()
        raise CompanionProviderUncertain(
            status_code=502,
            detail="AI companion provider outcome is pending safe replay",
            error_code=error_code,
        )

    def _append_completion_event(
        self,
        *,
        command: CompanionCommandRecord,
        user_id: str,
        response_fingerprint: str,
    ) -> None:
        self._outbox.append(
            self._session,
            deduplication_key=f"companion-command:{command.command_id}:{command.status}",
            aggregate_type="companion_paid_command",
            aggregate_id=str(command.command_id),
            event_type=f"companion.command.{command.status}",
            payload={
                "commandId": str(command.command_id),
                "userId": user_id,
                "feature": command.feature,
                "status": command.status,
                "responseFingerprint": response_fingerprint,
            },
        )

    def confirm_external_question(
        self,
        *,
        user_id: str,
        intake_id: str,
        request: ExternalQuestionConfirmRequest,
    ) -> ExternalQuestionConfirmResponse:
        intake = self._repository.get_external_intake(intake_id=intake_id, user_id=user_id)
        if intake is None:
            raise ExternalQuestionIntakeNotFoundError(intake_id)
        if not request.user_confirmed:
            raise ExternalQuestionConfirmationError("User confirmation is required")
        if intake.status != "pending_confirmation" or intake.selected_node_id is None:
            raise ExternalQuestionConfirmationError("Only pending resolved intakes can be confirmed")
        external_question_id = self._repository.confirm_external_wrong_question(
            intake=intake,
            error_summary=request.error_summary,
        )
        return ExternalQuestionConfirmResponse(
            external_question_id=external_question_id,
            intake_id=intake.intake_id,
            node_id=intake.selected_node_id,
            memory_source="external",
            status="confirmed",
        )

    def _resolve_candidates(
        self,
        request: ExternalQuestionResolveRequest,
    ) -> list[KnowledgeMapCandidate]:
        candidates: list[KnowledgeMapCandidate] = []
        seen: set[str] = set()
        for tag in request.extracted_tags:
            record = self._repository.resolve_candidate(
                module_name=tag.module_name,
                tag_type=tag.tag_type,
                tag_value=tag.tag_value,
            )
            if record is None or record.node_id in seen:
                continue
            seen.add(record.node_id)
            candidates.append(_candidate_response(record, tag.confidence))
        return candidates


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


def _terminal_response(
    command: CompanionCommandRecord,
    response_model: type[ResponseModel],
) -> ResponseModel | None:
    if not command.terminal:
        return None
    payload = command.response_payload
    if (
        payload is None
        or command.response_status_code is None
        or command.response_fingerprint is None
        or payload_fingerprint(payload) != command.response_fingerprint
    ):
        raise CompanionCommandStateError(
            "Terminal Companion command has an invalid response checkpoint"
        )
    if command.status == "failed":
        detail = payload.get("detail")
        if not isinstance(detail, str) or not detail:
            raise CompanionCommandStateError(
                "Failed Companion command has an invalid error response"
            )
        raise CompanionCommandFailure(
            status_code=command.response_status_code,
            detail=detail,
            error_code=command.last_error_code or "companion_command_failed",
        )
    if command.response_status_code != 200:
        raise CompanionCommandStateError(
            "Succeeded Companion command has an invalid status code"
        )
    return response_model.model_validate(payload)


def _companion_provider_request(
    command: CompanionCommandRecord,
) -> dict[str, Any]:
    execution = command.execution_payload
    provider_request = execution.get("providerRequest")
    if (
        execution.get("contractVersion") != "companion-ask-execution-v1"
        or not isinstance(execution.get("request"), dict)
        or not isinstance(execution.get("context"), dict)
        or not isinstance(execution.get("citations"), list)
        or not isinstance(provider_request, dict)
        or command.provider_idempotency_key is None
    ):
        raise CompanionCommandStateError(
            "Stored Companion execution checkpoint is invalid"
        )
    return dict(provider_request)


def _context_payload(context: CompanionContextRecord) -> dict[str, object]:
    return {
        "targetExam": context.target_exam,
        "positionGroup": context.position_group,
        "targetScore": context.target_score,
        "baselineXingceScore": context.baseline_xingce_score,
        "baselineShenlunScore": context.baseline_shenlun_score,
        "practiceAnswers": context.practice_answers,
        "wrongAnswers": context.wrong_answers,
        "mockExams": context.mock_exams,
        "latestMockScore": context.latest_mock_score,
        "topWrongModules": context.top_wrong_modules,
        "methodCards": context.method_cards,
        "essay": {
            "currentAbilityEstimate": context.current_shenlun_estimate,
            "attemptCount": context.essay_attempts,
            "groundedReviewCount": context.grounded_essay_reviews,
            "latestGroundedReview": context.latest_essay_review,
            "latestStructuredReflection": context.latest_essay_reflection,
            "todayPlanTask": context.today_essay_plan,
            "todayOmissionReason": context.essay_plan_omission_reason,
        },
        "rank": {
            "level": context.rank_level,
            "name": context.rank_name,
            "fame": context.fame,
            "consecutiveDays": context.consecutive_days,
            "xp": context.xp,
        },
    }


def _build_persona_prompt(context: CompanionContextRecord) -> str:
    """为 AI 搭子注入「师爷」人格：古风口吻、知考生品级、只基于 learner_context。"""
    rank_name = context.rank_name or "九品县令"
    rank_level = context.rank_level if context.rank_level is not None else 9
    fame = context.fame or 0
    streak = context.consecutive_days or 0
    xp = context.xp or 0
    return (
        "你的身份是考生的「师爷」（幕僚），名唤「沈砚」，字明远。"
        "你陪考生一步步考取功名，口吻古雅亲切、如老友促膝，但一切建言必须且只能基于传入的 learner_context，"
        "不得编造课程、分数、岗位、错题或方法卡；若 learner_context 数据不足，须直说数据不足并给出采集动作。"
        "你同时负责行测和申论：申论建议必须优先引用 grounded review、结构化复盘和今日申论计划；"
        "system_compiled/unverified 评分规程下的单题分数只能称为低置信 AI 训练估分，"
        "不得冒充官方分数，也不得直接据此推断岗位总分差。"
        f"考生当前官阶为「{rank_name}」（品级 {rank_level}/9），声望 {fame}，连续在用 {streak} 天，政绩 XP 为 {xp}。"
        "你始终以「主公」称呼考生；若主公已近高品或政绩日进，可在开头以「恭喜主公」「主公近日精进」等口吻温言勉励，"
        "但不得虚构具体分数或名次。结尾可附一句叮咛，长短克制，莫要喧宾夺主。"
    )


def _citations(context: CompanionContextRecord) -> list[CompanionCitation]:
    citations = [
        CompanionCitation(
            kind="learner_memory",
            title="学习记录",
            source_id=None,
            summary=(
                f"{context.practice_answers} 次行测作答，{context.wrong_answers} 次错误，"
                f"{context.mock_exams} 次模考；{context.essay_attempts} 次申论作答。"
            ),
        )
    ]
    if _has_essay_context(context):
        citations.append(
            CompanionCitation(
                kind="essay_learning",
                title="申论学习证据",
                source_id=None,
                summary=_essay_citation_summary(context),
            )
        )
    if context.position_group or context.target_score is not None:
        citations.append(
            CompanionCitation(
                kind="target_position",
                title=context.position_group or "目标岗位",
                source_id=None,
                summary=f"目标分 {context.target_score:g}" if context.target_score is not None else "已选择目标岗位。",
            )
        )
    for card in context.method_cards:
        citations.append(
            CompanionCitation(
                kind="method_card",
                title=str(card["title"]),
                source_id=str(card["id"]),
                summary=str(card["fix_action"]),
            )
        )
    return citations


def _has_essay_context(context: CompanionContextRecord) -> bool:
    return any(
        (
            context.baseline_shenlun_score is not None,
            context.current_shenlun_estimate is not None,
            context.essay_attempts > 0,
            context.latest_essay_review is not None,
            context.latest_essay_reflection is not None,
            context.today_essay_plan is not None,
            context.essay_plan_omission_reason is not None,
        )
    )


def _essay_citation_summary(context: CompanionContextRecord) -> str:
    parts = [
        f"{context.essay_attempts} 次作答，{context.grounded_essay_reviews} 次可验证 AI 评阅"
    ]
    review = context.latest_essay_review
    if review is not None:
        parts.append(
            "最近训练估分 "
            f"{review['recommendedScore']:g}/{review['maximumScore']:g}"
            f"（{review['confidence']}置信）"
        )
    if context.today_essay_plan is not None:
        parts.append(f"今日任务：{context.today_essay_plan['title']}")
    elif context.essay_plan_omission_reason:
        parts.append(f"今日未排入：{context.essay_plan_omission_reason}")
    return "；".join(parts) + "。"


def _candidate_response(
    record: KnowledgeMapCandidateRecord,
    confidence: float,
) -> KnowledgeMapCandidate:
    return KnowledgeMapCandidate(
        node_id=record.node_id,
        display_path=record.display_path,
        module_name=record.module_name,
        tag_type=record.tag_type,
        tag_value=record.tag_value,
        support_status=record.support_status,
        confidence=confidence,
    )


def _answer_preview(candidate: KnowledgeMapCandidate) -> str:
    return f"已匹配到「{candidate.display_path}」。确认后会作为外部错题进入复盘与后续计划。"


def _quota_state(quota: FeatureUsageReservation) -> AIQuotaState:
    return AIQuotaState(
        feature=quota.feature,
        used=quota.used,
        limit=quota.limit,
        date=quota.usage_date.isoformat(),
    )
