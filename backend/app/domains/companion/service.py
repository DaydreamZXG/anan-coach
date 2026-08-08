from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeVar
from uuid import NAMESPACE_URL, uuid5

from httpx import HTTPStatusError, NetworkError, TimeoutException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.ai.audit import AIAuditRepository
from app.domains.ai.governance.contracts import IntentDecision
from app.domains.ai.governance.repository import (
    AIGovernanceReleaseError,
    AIGovernanceRepository,
)
from app.domains.ai.governance.retrieval import (
    ObjectAuthorizationError,
    RetrievalBundle,
)
from app.domains.ai.governance.routing import GlobalAskPolicyError, route_global_question
from app.domains.ai.model_cost import (
    ModelCallLedgerError,
    ModelCallLedgerRepository,
    ModelCallRecord,
    ModelCostConfigurationError,
    ModelPriceCard,
)
from app.domains.ai.external_processing import endpoint_fingerprint
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
from app.domains.companion.agent import (
    COMPANION_AGENT_DEFINITION_RELEASE,
    default_companion_actions,
)
from app.domains.companion.context import (
    CompanionContextAssembler,
    _build_persona_prompt,
    _context_payload as _context_payload,
    _context_retrieval_candidates as _context_retrieval_candidates,
    _high_impact_evidence_ready,
    _selected_citations,
)
from app.domains.companion.model_reply import CompanionModelReply
from app.domains.companion.provider_context import CompanionProviderContextAssembler
from app.domains.companion.repository import (
    CompanionRepository,
    KnowledgeMapCandidateRecord,
)
from app.domains.companion.schemas import (
    CompanionAskRequest,
    CompanionAskResponse,
    CompanionAllowedAction,
    CompanionCitation,
    CompanionHighImpactDecision,
    ExternalQuestionConfirmRequest,
    ExternalQuestionConfirmResponse,
    ExternalQuestionResolveRequest,
    ExternalQuestionResolveResponse,
    KnowledgeMapCandidate,
)
from app.domains.companion.short_term import ShortTermConversationMessage
from app.domains.essay.schemas import AIQuotaState
from app.platform.outbox import OutboxRepository

COMPANION_ASK_FEATURE = "companion_ask"
EXTERNAL_WRONG_INTAKE_FEATURE = "external_wrong_intake"
MIN_EXTRACTION_CONFIDENCE = 0.70
MIN_NODE_CONFIDENCE = 0.75
COMPANION_ASK_REQUEST_CONTRACT = "global-ask-command@v2"
COMPANION_ASK_RESPONSE_CONTRACT = "grounded-answer@v2"
COMPANION_ASK_EXECUTION_CONTRACT = "companion-ask-execution@v2"
EXTERNAL_RESOLVE_REQUEST_CONTRACT = "external-question-resolve-command-v2"
EXTERNAL_RESOLVE_RESPONSE_CONTRACT = "external-question-resolve-response-v1"
EXTERNAL_RESOLVE_EXECUTION_CONTRACT = "external-question-resolve-execution-v2"


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
        short_term_history: tuple[ShortTermConversationMessage, ...] = (),
    ) -> CompanionAskResponse:
        request_payload = request.model_dump(by_alias=True, mode="json")
        try:
            decision = route_global_question(
                question=request.question_text,
                context=request.context,
            )
        except GlobalAskPolicyError as exc:
            raise CompanionCommandFailure(
                status_code=403,
                detail="This page is not authorized to request that AI context",
                error_code=exc.code,
            ) from exc
        if decision.policy_decision != "answer":
            return self._policy_response(
                user_id=user_id,
                idempotency_key=idempotency_key,
                request=request,
                decision=decision,
            )
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
                node_id=None,
            )
            context_assembler = CompanionContextAssembler(self._session)
            context_snapshot = context_assembler.snapshot(
                context,
                fallback_state_version=request.context.learner_state_version,
            )
            current_state_version = context_snapshot.state_version
            if current_state_version != request.context.learner_state_version:
                return self._stale_context_response(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request=request,
                    decision=decision,
                    current_state_version=current_state_version,
                )
            try:
                context_bundle = context_assembler.retrieve(
                    snapshot=context_snapshot,
                    context=context,
                    user_id=user_id,
                    question_text=request.question_text,
                    request_context=request.context,
                    decision=decision,
                )
            except ObjectAuthorizationError as exc:
                self._session.rollback()
                raise CompanionCommandFailure(
                    status_code=403,
                    detail="Referenced AI context is not owned by this account",
                    error_code="context_reference_forbidden",
                ) from exc
            retrieval = context_bundle.retrieval
            if not retrieval.sources or not _high_impact_evidence_ready(
                decision=decision,
                retrieval=retrieval,
            ):
                self._session.commit()
                return self._evidence_insufficient_response(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request=request,
                    decision=decision,
                    retrieval=retrieval,
                )
            provider_request = CompanionProviderContextAssembler(self._ai_provider).prepare(
                decision=decision,
                question_text=request.question_text,
                context_payload=context_bundle.provider_payload,
                retrieval=retrieval,
                persona=_build_persona_prompt(),
                short_term_history=short_term_history,
            )
            execution_payload = {
                "contractVersion": COMPANION_ASK_EXECUTION_CONTRACT,
                "request": request_payload,
                "routeDecision": decision.model_dump(by_alias=True, mode="json"),
                "providerRequest": provider_request,
                "context": context_bundle.provider_payload,
                "learnerStateVersion": current_state_version,
                "retrievalBundle": retrieval.model_dump(by_alias=True, mode="json"),
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
        route_decision = IntentDecision.model_validate(command.execution_payload["routeDecision"])
        command = self._commands.begin_provider_attempt(
            self._session,
            command_id=command.command_id,
        )
        try:
            result = CompanionModelReply(self._ai_provider).answer(
                prepared=provider_request,
                idempotency_key=command.provider_idempotency_key,
                retrieval_payload=command.execution_payload["retrievalBundle"],
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
                    scenario=route_decision.scenario_key,
                    error_code="provider_execution_configuration_changed",
                )
                raise AssertionError("uncertain provider failure must raise") from exc
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
                scenario=route_decision.scenario_key,
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
                scenario=route_decision.scenario_key,
                status_code=502,
                detail="AI companion answer failed",
                error_code=exc.code,
                latency_ms=exc.latency_ms,
                quota_consumed=True,
                provider_request_id_header=exc.provider_request_id_header,
                provider_request_id_fingerprint=(exc.provider_request_id_fingerprint),
            )
            raise AssertionError("terminal provider failure must raise") from exc
        except HTTPStatusError as exc:
            provider_status = exc.response.status_code
            if provider_status == 408 or provider_status == 429 or provider_status >= 500:
                self._provider_uncertain(
                    command=command,
                    learner_key=learner_key,
                    request_summary=request.question_text[:200],
                    scenario=route_decision.scenario_key,
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
                scenario=route_decision.scenario_key,
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
                scenario=route_decision.scenario_key,
                error_code="provider_transport_uncertain",
            )
            raise AssertionError("uncertain provider failure must raise") from exc
        except Exception as exc:
            self._provider_uncertain(
                command=command,
                learner_key=learner_key,
                request_summary=request.question_text[:200],
                scenario=route_decision.scenario_key,
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
        citations = _selected_citations(
            command.execution_payload["retrievalBundle"],
            result.source_refs,
        )
        allowed_actions = _allowed_actions(
            decision=route_decision,
            question_text=request.question_text,
        )
        self._audit_repository.log_interaction(
            log_id=f"companion_command_{command.command_id.hex}",
            learner_key=learner_key,
            feature=COMPANION_ASK_FEATURE,
            provider=self._ai_provider.provider_name,
            model=str(provider_request["model"]),
            status="succeeded",
            request_summary=request.question_text[:200],
            response_summary=result.response_summary,
            latency_ms=result.latency_ms,
            metadata={
                "scenario": route_decision.scenario_key,
                "respondingAgent": route_decision.responding_agent,
                "retrievalBundleId": command.execution_payload["retrievalBundle"]["bundleId"],
                "learnerStateVersion": command.execution_payload["learnerStateVersion"],
                "commandId": str(command.command_id),
                "agentDefinitionReleaseId": COMPANION_AGENT_DEFINITION_RELEASE,
                "fallbackActionCount": len(allowed_actions),
                "fallbackActions": [
                    action.model_dump(by_alias=True, mode="json")
                    for action in allowed_actions
                ],
            },
            commit=False,
        )
        response = CompanionAskResponse(
            answerId=str(command.command_id),
            semanticState="ready",
            scenarioKey=route_decision.scenario_key,
            respondingAgent=route_decision.responding_agent,
            answerType=route_decision.answer_type,
            policyDecision=route_decision.policy_decision,
            companionIntent=route_decision.companion_intent,
            answer=result.answer,
            citations=citations,
            confidence=result.confidence,
            limitations=result.limitations,
            knowledgeFreshness=command.execution_payload["learnerStateVersion"],
            allowedActions=allowed_actions,
            highImpactDecision=_high_impact_decision(
                decision=route_decision,
                citations=citations,
            ),
            quota=_quota_state(quota),
        )
        response_payload = response.model_dump(by_alias=True, mode="json")
        succeeded = self._commands.succeed(
            self._session,
            command_id=command.command_id,
            response_payload=response_payload,
            provider_request_id_header=result.provider_request_id_header,
            provider_request_id_fingerprint=(result.provider_request_id_fingerprint),
        )
        self._append_completion_event(
            command=succeeded,
            user_id=user_id,
            response_fingerprint=payload_fingerprint(response_payload),
        )
        self._session.commit()
        return response

    def _policy_response(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request: CompanionAskRequest,
        decision: IntentDecision,
    ) -> CompanionAskResponse:
        if decision.policy_decision == "security_blocked":
            semantic_state = "security_blocked"
            answer = "这条问题包含试图改变系统权限或读取内部指令的内容，系统没有把它交给模型。"
            limitations = ["未调用模型，也未读取或修改任何学习事实。"]
        else:
            semantic_state = "permission_denied"
            answer = (
                "当前仍在作答阶段，只能解释界面和作答规则；提交后才能讨论答案、方法或参考要点。"
            )
            limitations = ["作答前不会泄露答案、排除结论、范文或定向改写。"]
        return CompanionAskResponse(
            answerId=_deterministic_answer_id(user_id, idempotency_key),
            semanticState=semantic_state,
            scenarioKey=decision.scenario_key,
            respondingAgent=decision.responding_agent,
            answerType=decision.answer_type,
            policyDecision=decision.policy_decision,
            companionIntent=decision.companion_intent,
            answer=answer,
            citations=[],
            confidence="high",
            limitations=limitations,
            knowledgeFreshness=request.context.learner_state_version,
            allowedActions=[],
            highImpactDecision=None,
            quota=self._current_quota_state(user_id=user_id),
        )

    def _evidence_insufficient_response(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request: CompanionAskRequest,
        decision: IntentDecision,
        retrieval: RetrievalBundle,
    ) -> CompanionAskResponse:
        reason = (
            "可用资料中的指令性内容已被安全策略隔离，当前没有可用于回答的证据。"
            if retrieval.security_decision == "all_sources_blocked"
            else "当前学习大脑和已确认资料里没有足够证据支持这个结论。"
        )
        retrieval_payload = retrieval.model_dump(by_alias=True, mode="json")
        citations = _selected_citations(
            retrieval_payload,
            [item.reference.source_id for item in retrieval.sources],
        )
        return CompanionAskResponse(
            answerId=_deterministic_answer_id(user_id, idempotency_key),
            semanticState="evidence_insufficient",
            scenarioKey=decision.scenario_key,
            respondingAgent=decision.responding_agent,
            answerType=decision.answer_type,
            policyDecision="evidence_required",
            companionIntent="reminder",
            answer=reason,
            citations=citations,
            confidence="low",
            limitations=["系统没有让模型补齐缺失事实，也没有消耗本次 AI 额度。"],
            knowledgeFreshness=request.context.learner_state_version,
            allowedActions=[_information_action(decision)],
            highImpactDecision=_high_impact_decision(
                decision=decision,
                citations=citations,
                status="evidence_insufficient",
            ),
            quota=self._current_quota_state(user_id=user_id),
        )

    def _stale_context_response(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request: CompanionAskRequest,
        decision: IntentDecision,
        current_state_version: str,
    ) -> CompanionAskResponse:
        return CompanionAskResponse(
            answerId=_deterministic_answer_id(user_id, idempotency_key),
            semanticState="evidence_insufficient",
            scenarioKey=decision.scenario_key,
            respondingAgent=decision.responding_agent,
            answerType=decision.answer_type,
            policyDecision="evidence_required",
            companionIntent="reminder",
            answer="你的学习证据在打开页面后发生了变化，请刷新后再问。",
            citations=[],
            confidence="high",
            limitations=["本次未调用模型，也未消耗 AI 额度。"],
            knowledgeFreshness=current_state_version,
            allowedActions=[
                CompanionAllowedAction(
                    kind="refresh_context",
                    label="刷新学习证据",
                    targetRef=None,
                    requiresConfirmation=False,
                )
            ],
            highImpactDecision=None,
            quota=self._current_quota_state(user_id=user_id),
        )

    def _current_quota_state(self, *, user_id: str) -> AIQuotaState:
        entitlement = self._commerce_service.get_entitlement(user_id=user_id)
        quota = next(
            item for item in entitlement.feature_quotas if item.feature == COMPANION_ASK_FEATURE
        )
        self._session.commit()
        return AIQuotaState(
            feature=quota.feature,
            used=quota.used,
            access_mode=quota.access_mode,
            limit=quota.limit,
            date=entitlement.server_time.date().isoformat(),
        )

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
            selected_knowledge_release_id: str | None = None
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
                candidates, candidate_release_id = self._resolve_candidates(request)
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
                    selected_knowledge_release_id = candidate_release_id
                    selected_node_id = selected.node_id
                    selected_confidence = selected.confidence
                    answer_preview = _answer_preview(selected)
            execution_payload = {
                "contractVersion": EXTERNAL_RESOLVE_EXECUTION_CONTRACT,
                "request": request_payload,
                "resolution": {
                    "status": status,
                    "reason": reason,
                    "candidates": [
                        item.model_dump(by_alias=True, mode="json") for item in candidates
                    ],
                    "selectedKnowledgeReleaseId": selected_knowledge_release_id,
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
            KnowledgeMapCandidate.model_validate(item) for item in resolution["candidates"]
        ]
        intake_id = self._repository.create_external_intake(
            user_id=user_id,
            stem=stored_request["stem"],
            options=stored_request["options"],
            selected_answer=stored_request.get("selectedAnswer"),
            correct_answer=stored_request.get("correctAnswer"),
            extracted_tags=stored_request["extractedTags"],
            candidate_nodes=resolution["candidates"],
            knowledge_release_id=resolution["selectedKnowledgeReleaseId"],
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
                resolution["reason"] or resolution["answerPreview"] or "pending confirmation"
            ),
            latency_ms=0,
            metadata={
                "intake_id": intake_id,
                "candidate_count": len(stored_candidates),
                "knowledge_release_id": resolution["selectedKnowledgeReleaseId"],
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
            requires_user_confirmation=(resolution["status"] == "pending_confirmation"),
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
                    str(provider_request["model"]),
                    str(provider_request["promptVersion"]),
                    str(provider_request["schemaVersion"]),
                )
                if (
                    model_call is not None
                    and (
                        model_call.provider,
                        model_call.model,
                        model_call.prompt_version,
                        model_call.schema_version,
                    )
                    != provider_identity
                ):
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
                    price_card = self._price_card or ModelPriceCard.from_settings(self._settings)
                    attribution = _companion_attribution(
                        session=self._session,
                        settings=self._settings,
                        provider_identity=provider_identity,
                        execution_payload=record.execution_payload,
                        reservation=reservation,
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
                        attribution=attribution,
                        processor_release_id=str(
                            provider_request.get("providerReleaseId")
                            or self._settings.ai_companion_provider_release_id
                            or "local-unreleased"
                        ),
                        processor_endpoint_fingerprint=str(
                            provider_request.get("providerEndpointFingerprint")
                            or endpoint_fingerprint(self._settings.ai_provider_base_url)
                        ),
                        processing_purpose="companion_answer",
                        processing_data_categories=(
                            "learner_question",
                            "learning_evidence",
                        ),
                    )
                except (
                    AIGovernanceReleaseError,
                    ModelCostConfigurationError,
                    ModelCallLedgerError,
                ) as exc:
                    self._preflight_provider_failure(
                        record=record,
                        reservation=reservation,
                        model_call=model_call,
                        user_id=user_id,
                        error_code="model_cost_configuration_invalid",
                    )
                    raise AssertionError("model preflight failure must raise") from exc
            else:
                try:
                    attribution = _companion_attribution(
                        session=self._session,
                        settings=self._settings,
                        provider_identity=provider_identity,
                        execution_payload=record.execution_payload,
                        reservation=reservation,
                    )
                    AIGovernanceRepository().insert_model_call_attribution(
                        self._session,
                        call_id=model_call.call_id,
                        attribution=attribution,
                    )
                except AIGovernanceReleaseError as exc:
                    self._preflight_provider_failure(
                        record=record,
                        reservation=reservation,
                        model_call=model_call,
                        user_id=user_id,
                        error_code="ai_release_not_evaluated",
                    )
                    raise AssertionError("AI release gate failure must raise") from exc
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
        if isinstance(retention, bool) or not isinstance(retention, int) or retention <= 0:
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
                f"companion_uncertain_{command.command_id.hex}_{uncertain.provider_attempt_count}"
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
        if (
            intake.status != "pending_confirmation"
            or intake.knowledge_release_id is None
            or intake.selected_node_id is None
        ):
            raise ExternalQuestionConfirmationError(
                "Only pending resolved intakes can be confirmed"
            )
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
    ) -> tuple[list[KnowledgeMapCandidate], str | None]:
        vocabulary = self._repository.current_knowledge_vocabulary()
        if vocabulary is None:
            return [], None
        candidates: list[KnowledgeMapCandidate] = []
        seen: set[str] = set()
        for tag in request.extracted_tags:
            record = self._repository.resolve_candidate(
                module_name=tag.module_name,
                tag_type=tag.tag_type,
                tag_value=tag.tag_value,
                vocabulary=vocabulary,
            )
            if record is None or record.node_id in seen:
                continue
            seen.add(record.node_id)
            candidates.append(_candidate_response(record, tag.confidence))
        return candidates, vocabulary.release_id


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
        raise CompanionCommandStateError("Succeeded Companion command has an invalid status code")
    return response_model.model_validate(payload)


def _companion_provider_request(
    command: CompanionCommandRecord,
) -> dict[str, Any]:
    execution = command.execution_payload
    provider_request = execution.get("providerRequest")
    if (
        execution.get("contractVersion") != COMPANION_ASK_EXECUTION_CONTRACT
        or not isinstance(execution.get("request"), dict)
        or not isinstance(execution.get("context"), dict)
        or not isinstance(execution.get("routeDecision"), dict)
        or not isinstance(execution.get("retrievalBundle"), dict)
        or not isinstance(execution.get("learnerStateVersion"), str)
        or not execution["learnerStateVersion"]
        or not isinstance(provider_request, dict)
        or command.provider_idempotency_key is None
    ):
        raise CompanionCommandStateError("Stored Companion execution checkpoint is invalid")
    return dict(provider_request)


def _companion_attribution(
    *,
    session: Session,
    settings: Settings,
    provider_identity: tuple[str, str, str, str],
    execution_payload: dict[str, Any],
    reservation: FeatureUsageReservation,
):
    decision = IntentDecision.model_validate(execution_payload["routeDecision"])
    bundle = RetrievalBundle.model_validate(execution_payload["retrievalBundle"])
    return AIGovernanceRepository().attribution(
        session,
        environment=settings.environment,
        scenario_key=decision.scenario_key,
        operation="companion_answer",
        provider=provider_identity[0],
        model=provider_identity[1],
        prompt_version=provider_identity[2],
        schema_version=provider_identity[3],
        evidence_refs=tuple(item.reference for item in bundle.sources),
        retrieval_bundle_id=bundle.bundle_id,
        reservation_id=reservation.reservation_id,
        reservation_source=reservation.source,
        # 岸岸是个人测试阶段的交互入口。保留完整调用归因，但不能因为缺少
        # 企业式评测发布证明而阻断真实用户提问。
        enforce_evaluated_deployment=False,
    )


def _allowed_actions(
    *,
    decision: IntentDecision,
    question_text: str | None = None,
) -> list[CompanionAllowedAction]:
    return default_companion_actions(
        answer_type=decision.answer_type,
        question_text=question_text or "",
    )


def _information_action(decision: IntentDecision) -> CompanionAllowedAction:
    if decision.responding_agent == "position":
        label = "补充岗位与资格信息"
        target_ref = "/pages/positions/index"
    elif decision.responding_agent == "planner":
        label = "去完成今日学习任务"
        target_ref = "/pages/today/index"
    else:
        label = "拍题或导入学习资料"
        target_ref = "/pages/capture-import/index"
    return CompanionAllowedAction(
        kind="supply_information",
        label=label,
        targetRef=target_ref,
        requiresConfirmation=False,
    )


def _high_impact_decision(
    *,
    decision: IntentDecision,
    citations: list[CompanionCitation],
    status: Literal["explain_only", "evidence_insufficient"] = "explain_only",
) -> CompanionHighImpactDecision | None:
    if decision.answer_type not in {
        "position_explanation",
        "strategic_deprioritization",
    }:
        return None
    decision_type = (
        "position_explanation"
        if decision.answer_type == "position_explanation"
        else "strategic_deprioritization"
    )
    prohibited = (
        ["qualification", "primary_goal"]
        if decision_type == "position_explanation"
        else ["strategy", "primary_goal"]
    )
    return CompanionHighImpactDecision(
        decisionType=decision_type,
        status=status,
        evidenceRefs=[item.source_id for item in citations],
        requiresConfirmation=True,
        prohibitedMutations=prohibited,
    )


def _deterministic_answer_id(user_id: str, idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"companion-answer:{user_id}:{idempotency_key}"))


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
        access_mode=quota.access_mode,
        limit=quota.limit if quota.access_mode == "entitlement_quota" else None,
        date=quota.usage_date.isoformat(),
    )
