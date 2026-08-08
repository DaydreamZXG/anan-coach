from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import re
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
    AuthorizedHybridRetriever,
    ObjectAuthorizationError,
    RetrievalBundle,
    context_candidate,
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
    execute_companion_actions,
)
from app.domains.companion.repository import (
    CompanionContextRecord,
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
from app.domains.companion.short_term import (
    ShortTermConversationMessage,
    short_term_history_payload,
)
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
        if decision.companion_intent in {"rest", "encouragement"}:
            context = self._repository.get_context(user_id=user_id, node_id=None)
            current_state_version = _context_state_version(
                context,
                fallback=request.context.learner_state_version,
            )
            if current_state_version != request.context.learner_state_version:
                return self._stale_context_response(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request=request,
                    decision=decision,
                    current_state_version=current_state_version,
                )
            return self._support_response(
                user_id=user_id,
                idempotency_key=idempotency_key,
                request=request,
                decision=decision,
                context=context,
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
            current_state_version = _context_state_version(
                context,
                fallback=request.context.learner_state_version,
            )
            if current_state_version != request.context.learner_state_version:
                return self._stale_context_response(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request=request,
                    decision=decision,
                    current_state_version=current_state_version,
                )
            context_payload = _context_payload(context)
            try:
                retrieval = AuthorizedHybridRetriever(self._session).retrieve(
                    user_id=user_id,
                    question=request.question_text,
                    context=request.context,
                    context_sources=_context_retrieval_candidates(context),
                )
            except ObjectAuthorizationError as exc:
                self._session.rollback()
                raise CompanionCommandFailure(
                    status_code=403,
                    detail="Referenced AI context is not owned by this account",
                    error_code="context_reference_forbidden",
                ) from exc
            AIGovernanceRepository().record_retrieval_bundle(
                self._session,
                user_id=user_id,
                scenario_key=decision.scenario_key,
                route_id=request.context.route_id,
                workspace=request.context.workspace,
                bundle=retrieval,
            )
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
            persona = _build_persona_prompt()
            provider_kwargs: dict[str, Any] = {
                "route_decision": decision.model_dump(by_alias=True, mode="json"),
                "question_text": request.question_text,
                "context": context_payload,
                "sources": _provider_sources(retrieval),
                "persona": persona,
            }
            if short_term_history:
                provider_kwargs["conversation_history"] = short_term_history_payload(
                    short_term_history
                )
            provider_request = self._ai_provider.prepare_companion_question(
                **provider_kwargs
            )
            execution_payload = {
                "contractVersion": COMPANION_ASK_EXECUTION_CONTRACT,
                "request": request_payload,
                "routeDecision": decision.model_dump(by_alias=True, mode="json"),
                "providerRequest": provider_request,
                "context": context_payload,
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
            result = self._ai_provider.answer_prepared_companion_question(
                prepared=provider_request,
                idempotency_key=command.provider_idempotency_key,
            )
            _validate_provider_source_refs(
                result.source_refs,
                command.execution_payload["retrievalBundle"],
                result=result,
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
        fallback_actions = _allowed_actions(
            decision=route_decision,
            question_text=request.question_text,
        )
        agent_outcome = execute_companion_actions(
            # Provider actions are advisory output and are deliberately not an
            # execution authority. The server derives navigation/preview
            # actions from the classified intent and the user's own text.
            requests=(),
            question_text=request.question_text,
            fallback_actions=fallback_actions,
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
                "fallbackActionCount": len(fallback_actions),
                "fallbackActions": [
                    action.model_dump(by_alias=True, mode="json")
                    for action in agent_outcome.actions
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
            allowedActions=list(agent_outcome.actions),
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

    def _support_response(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request: CompanionAskRequest,
        decision: IntentDecision,
        context: CompanionContextRecord,
    ) -> CompanionAskResponse:
        progress = (
            f"今天已经留下 {context.today_practice_answers} 道作答记录，"
            if context.today_practice_answers > 0
            else ""
        )
        if decision.companion_intent == "rest":
            answer = (
                progress
                + "先停一下，累的时候不用靠硬扛证明自己。离开屏幕休息十分钟，"
                "回来后只做今天最短的一项；如果还是很累，就把今天收成复盘和休息。"
            )
        else:
            answer = (
                progress
                + "这次进步值得记下来。先别急着加量，把刚才做对的方法留成一句话，"
                "再按原计划完成下一项就好。"
            )
        response = CompanionAskResponse(
            answerId=_deterministic_answer_id(user_id, idempotency_key),
            semanticState="ready",
            scenarioKey=decision.scenario_key,
            respondingAgent=decision.responding_agent,
            answerType=decision.answer_type,
            policyDecision=decision.policy_decision,
            companionIntent=decision.companion_intent,
            answer=answer,
            citations=[],
            confidence="high",
            limitations=["这条回复不会自动修改你的计划。"],
            knowledgeFreshness=request.context.learner_state_version,
            allowedActions=[],
            highImpactDecision=None,
            quota=self._current_quota_state(user_id=user_id),
        )
        request_payload = request.model_dump(by_alias=True, mode="json")
        command = self._commands.claim(
            self._session,
            user_id=user_id,
            feature=COMPANION_ASK_FEATURE,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint(
                user_id=user_id,
                feature=COMPANION_ASK_FEATURE,
                contract_version=COMPANION_ASK_REQUEST_CONTRACT,
                payload=request_payload,
            ),
            request_contract_version=COMPANION_ASK_REQUEST_CONTRACT,
            execution_payload={
                "contractVersion": "companion-support-v1",
                "request": request_payload,
                "routeDecision": decision.model_dump(by_alias=True, mode="json"),
                "learnerStateVersion": request.context.learner_state_version,
            },
            response_contract_version=COMPANION_ASK_RESPONSE_CONTRACT,
            reservation_key=stable_command_key(
                user_id=user_id,
                feature=COMPANION_ASK_FEATURE,
                idempotency_key=idempotency_key,
            ),
            # The existing command ledger requires a stable provider key for
            # companion_ask rows even when this deterministic branch performs
            # no external call. Keeping the key preserves replay/conflict
            # semantics without consuming quota or model cost.
            provider_idempotency_key=stable_provider_key(
                user_id=user_id,
                feature=COMPANION_ASK_FEATURE,
                idempotency_key=idempotency_key,
            ),
        )
        replay = _terminal_response(command, CompanionAskResponse)
        if replay is not None:
            self._session.commit()
            return replay
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


def _context_payload(context: CompanionContextRecord) -> dict[str, object]:
    return {
        "targetExam": context.target_exam,
        "positionGroup": context.position_group,
        "targetScore": context.target_score,
        "baselineXingceScore": context.baseline_xingce_score,
        "baselineShenlunScore": context.baseline_shenlun_score,
        "practiceAnswers": context.practice_answers,
        "practiceAnswersScope": "historical_total",
        "todayPracticeAnswers": context.today_practice_answers,
        "wrongAnswers": context.wrong_answers,
        "todayWrongAnswers": context.today_wrong_answers,
        "mockExams": context.mock_exams,
        "latestMockScore": context.latest_mock_score,
        "topWrongModules": context.top_wrong_modules,
        "methodCards": context.method_cards,
        "todayPlan": {
            "tasks": context.today_plan_tasks,
        },
        "essay": {
            "currentAbilityEstimate": context.current_shenlun_estimate,
            "attemptCount": context.essay_attempts,
            "groundedReviewCount": context.grounded_essay_reviews,
            "latestGroundedReview": context.latest_essay_review,
            "latestStructuredReflection": context.latest_essay_reflection,
            "todayPlanTask": context.today_essay_plan,
            "todayOmissionReason": context.essay_plan_omission_reason,
        },
        "learningBrain": context.learning_brain_profile,
        "rank": {
            "level": context.rank_level,
            "name": context.rank_name,
            "fame": context.fame,
            "consecutiveDays": context.consecutive_days,
            "xp": context.xp,
        },
    }


def _context_state_version(
    context: CompanionContextRecord,
    *,
    fallback: str,
) -> str:
    if context.learning_brain_profile is None:
        return fallback
    return str(context.learning_brain_profile["profileVersionId"])


def _build_persona_prompt() -> str:
    """Return the product-owned 岸岸 persona without legacy role-play prompts."""
    return (
        "你是公考学习搭子岸岸，表达直接、亲切、克制，不使用官场角色扮演、古风称呼或夸张鼓励。"
        "一切建言必须且只能基于传入的 learner_context，"
        "不得编造课程、分数、岗位、错题或方法卡；若 learner_context 数据不足，须直说数据不足并给出采集动作。"
        "learningBrain 中的能力、模块、错误模式与计划结论都带内部版本；版本只用于系统追溯，"
        "对用户只说明可理解的事实和证据，不得输出 JSON、英文键名、枚举值、UUID、算法名、版本号或内部来源标识。"
        "learningBrain 的 strategyDirective 记录本周策略：active 为已生效安排，pending 为待确认候选；"
        "用户问及「为什么这样排」等排期原因时，引用对应 rationale 解释；pending 存在且用户询问学习安排时，"
        "可主动请其确认，确认前不得当作已生效，也不得把候选写成新的能力事实。"
        "不得把本次回答、用户情绪或未确认候选写成新的能力事实。"
        "practiceAnswers 和 wrongAnswers 是历史累计，todayPracticeAnswers 和 todayWrongAnswers "
        "才是上海时区的今日数据；不得把历史累计说成今天或昨天完成。"
        "你同时负责行测和申论：申论建议必须优先引用 grounded review、结构化复盘和今日申论计划；"
        "system_compiled/unverified 评分规程下的单题分数只能称为低置信 AI 训练估分，"
        "不得冒充官方分数，也不得直接据此推断岗位总分差。"
        "回答先给结论，再说明证据与局限，最后给出一到三个可执行动作。"
    )


def _display_number(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"{float(value):g}"


def _activity_citation_summary(context: CompanionContextRecord) -> str:
    parts = [
        (
            f"历史累计已记录 {context.practice_answers} 道行测练习，"
            f"其中 {context.wrong_answers} 道错题"
        ),
        (
            f"今日已记录 {context.today_practice_answers} 道作答，"
            f"其中 {context.today_wrong_answers} 道错题"
        ),
        f"已完成 {context.mock_exams} 次模考",
    ]
    if context.latest_mock_score is not None:
        parts.append(f"最近一次模考记录为 {context.latest_mock_score:g} 分")
    module_names = [
        str(item.get("module_name", "")).strip()
        for item in context.top_wrong_modules
        if str(item.get("module_name", "")).strip()
    ]
    if module_names:
        parts.append(f"目前错题较多的模块包括{'、'.join(module_names)}")
    return "；".join(parts) + "。"


def _learning_brain_citation_summary(profile: dict[str, object]) -> str:
    ability = profile.get("ability")
    ability_map = ability if isinstance(ability, dict) else {}
    estimate = ability_map.get("estimate")
    estimate_map = estimate if isinstance(estimate, dict) else {}
    total = estimate_map.get("total")
    total_map = total if isinstance(total, dict) else {}
    xingce = estimate_map.get("xingce")
    xingce_map = xingce if isinstance(xingce, dict) else {}
    shenlun = estimate_map.get("shenlun")
    shenlun_map = shenlun if isinstance(shenlun, dict) else {}
    evidence = profile.get("evidenceSummary")
    evidence_map = evidence if isinstance(evidence, dict) else {}
    decision = profile.get("decisionSummary")
    decision_map = decision if isinstance(decision, dict) else {}

    parts: list[str] = []
    total_score = _display_number(total_map.get("recommended"))
    xingce_score = _display_number(xingce_map.get("recommended"))
    shenlun_score = _display_number(shenlun_map.get("recommended"))
    if total_score is not None:
        score_parts = []
        if xingce_score is not None:
            score_parts.append(f"行测约 {xingce_score} 分")
        if shenlun_score is not None:
            score_parts.append(f"申论约 {shenlun_score} 分")
        detail = f"（{'、'.join(score_parts)}）" if score_parts else ""
        parts.append(f"当前综合能力参考值约 {total_score} 分{detail}")

    evidence_count = sum(
        int(evidence_map.get(key, 0))
        for key in (
            "scoreEvidenceCount",
            "xingceAttemptCount",
            "xingceMasteryCount",
            "essayMasteryCount",
            "taskExecutionCount",
            "externalAnswerCount",
        )
        if isinstance(evidence_map.get(key, 0), int)
    )
    if evidence_count > 0:
        parts.append(f"本次判断汇总了 {evidence_count} 条已记录学习证据")

    review_due = decision_map.get("reviewDueCount")
    if isinstance(review_due, int):
        parts.append(f"当前有 {review_due} 项待复测" if review_due > 0 else "当前没有待复测项目")
    if not parts:
        return "已读取当前学习档案中的能力、错因与计划记录。"
    return "；".join(parts) + "。"


def _target_position_citation_summary(context: CompanionContextRecord) -> str:
    exam_labels = {"guokao": "国考", "national": "国考"}
    exam_label = exam_labels.get(
        (context.target_exam or "").strip().casefold(),
        (context.target_exam or "当前考试").strip(),
    )
    target_label = (
        f"当前主目标岗位为「{context.position_group}」"
        if context.position_group
        else "尚未确认具体主目标岗位"
    )
    score_label = (
        f"，目标总分为 {context.target_score:g} 分" if context.target_score is not None else ""
    )
    return f"{exam_label}：{target_label}{score_label}。"


def _context_retrieval_candidates(context: CompanionContextRecord) -> tuple[Any, ...]:
    candidates: list[Any] = []
    activity = {
        "practiceAnswers": context.practice_answers,
        "practiceAnswersScope": "historical_total",
        "todayPracticeAnswers": context.today_practice_answers,
        "wrongAnswers": context.wrong_answers,
        "todayWrongAnswers": context.today_wrong_answers,
        "mockExams": context.mock_exams,
        "latestMockScore": context.latest_mock_score,
        "topWrongModules": context.top_wrong_modules,
    }
    if any((context.practice_answers, context.wrong_answers, context.mock_exams)):
        candidates.append(
            context_candidate(
                kind="learner_activity",
                source_id="learner-activity-summary",
                version=_stable_payload_version(activity),
                anchor="current-summary",
                title="行测与模考学习记录",
                text_value=json.dumps(activity, ensure_ascii=False, sort_keys=True),
                trust_scope="learner_activity",
                source_weight=0.9,
                display_summary=_activity_citation_summary(context),
            )
        )
    if context.learning_brain_profile is not None:
        profile = context.learning_brain_profile
        candidates.append(
            context_candidate(
                kind="learning_brain",
                source_id=str(profile["profileVersionId"]),
                version=str(profile["versionNo"]),
                anchor="profile-evidence",
                title="当前学习档案",
                text_value=json.dumps(profile, ensure_ascii=False, sort_keys=True),
                trust_scope="learning_brain",
                source_weight=1.0,
                display_summary=_learning_brain_citation_summary(profile),
            )
        )
    if context.today_plan_tasks:
        today_plan = {"tasks": context.today_plan_tasks}
        candidates.append(
            context_candidate(
                kind="daily_plan",
                source_id="daily-plan-current",
                version=_stable_payload_version(today_plan),
                anchor="current-tasks",
                title="今日计划概览",
                text_value=json.dumps(today_plan, ensure_ascii=False, sort_keys=True),
                trust_scope="daily_plan",
                source_weight=1.0,
                display_summary=_today_plan_citation_summary(context),
            )
        )
    if _has_essay_context(context):
        essay = {
            "estimate": context.current_shenlun_estimate,
            "review": context.latest_essay_review,
            "reflection": context.latest_essay_reflection,
            "todayPlan": context.today_essay_plan,
            "omissionReason": context.essay_plan_omission_reason,
            "summary": _essay_citation_summary(context),
        }
        candidates.append(
            context_candidate(
                kind="essay_learning",
                source_id="essay-learning-current",
                version=_stable_payload_version(essay),
                anchor="grounded-review-reflection-plan",
                title="申论学习证据",
                text_value=json.dumps(essay, ensure_ascii=False, sort_keys=True),
                trust_scope="essay_learning",
                source_weight=0.95,
                display_summary=_essay_citation_summary(context),
            )
        )
    if context.position_group or context.target_score is not None:
        position = {
            "targetExam": context.target_exam,
            "positionGroup": context.position_group,
            "targetScore": context.target_score,
            "baselineXingceScore": context.baseline_xingce_score,
            "baselineShenlunScore": context.baseline_shenlun_score,
        }
        candidates.append(
            context_candidate(
                kind="target_position",
                source_id="target-position-current",
                version=_stable_payload_version(position),
                anchor="selected-goal",
                title=context.position_group or "目标岗位",
                text_value=json.dumps(position, ensure_ascii=False, sort_keys=True),
                trust_scope="target_position",
                source_weight=1.0,
                display_summary=_target_position_citation_summary(context),
            )
        )
    for card in context.method_cards:
        candidates.append(
            context_candidate(
                kind="method_card",
                source_id=str(card["id"]),
                version=_stable_payload_version(dict(card)),
                anchor="fix-action",
                title=str(card["title"]),
                text_value=f"{card['title']}：{card['fix_action']}；{card['tag_path']}",
                trust_scope="published_method_card",
                source_weight=0.9,
                display_summary=f"{card['title']}：{card['fix_action']}。",
            )
        )
    return tuple(candidates)


def _provider_sources(bundle: RetrievalBundle) -> list[dict[str, Any]]:
    return [
        {
            "sourceRef": item.reference.source_id,
            "kind": item.reference.kind,
            "title": item.reference.title,
            "version": item.reference.version,
            "anchor": item.reference.anchor,
            "trustScope": item.trust_scope,
            "untrustedInput": item.untrusted_input,
            "content": item.excerpt,
        }
        for item in bundle.sources
    ]


def _validate_provider_source_refs(
    source_refs: list[str],
    retrieval_payload: dict[str, Any],
    *,
    result: Any,
) -> None:
    bundle = RetrievalBundle.model_validate(retrieval_payload)
    allowed = {item.reference.source_id for item in bundle.sources}
    if set(source_refs) - allowed:
        raise AIProviderResponseError(
            "AI provider cited evidence outside the authorized retrieval bundle",
            code="provider_grounding_invalid",
            usage=result.usage,
            latency_ms=result.latency_ms,
            output_fingerprint=result.output_fingerprint,
            provider_request_id_header=result.provider_request_id_header,
            provider_request_id_fingerprint=result.provider_request_id_fingerprint,
        )


def _selected_citations(
    retrieval_payload: dict[str, Any],
    source_refs: list[str],
) -> list[CompanionCitation]:
    bundle = RetrievalBundle.model_validate(retrieval_payload)
    selected = {item.reference.source_id: item for item in bundle.sources}
    return [
        CompanionCitation(
            kind=selected[source_ref].reference.kind,
            title=selected[source_ref].reference.title,
            sourceId=source_ref,
            version=selected[source_ref].reference.version,
            anchor=selected[source_ref].reference.anchor,
            summary=selected[source_ref].display_summary or selected[source_ref].excerpt,
        )
        for source_ref in source_refs
    ]


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


def _high_impact_evidence_ready(
    *,
    decision: IntentDecision,
    retrieval: RetrievalBundle,
) -> bool:
    kinds = {item.reference.kind for item in retrieval.sources}
    if decision.answer_type == "position_explanation":
        return "target_position" in kinds
    if decision.answer_type == "strategic_deprioritization":
        return {"learning_brain", "learner_activity"}.issubset(kinds)
    return True


def _stable_payload_version(payload: Any) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _deterministic_answer_id(user_id: str, idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"companion-answer:{user_id}:{idempotency_key}"))


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


def _today_plan_citation_summary(context: CompanionContextRecord) -> str:
    tasks = context.today_plan_tasks
    if not tasks:
        return "今日暂未安排学习任务。"
    track_labels = {"xingce": "行测", "essay": "申论", "mock": "模考", "recovery": "复盘"}
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for item in tasks:
        title = re.sub(
            r"\s*[（(]第\s*\d+\s*组[）)]\s*$",
            "",
            str(item.get("title") or "学习任务").strip(),
        )
        track = track_labels.get(str(item.get("track") or ""), "学习")
        minutes = item.get("estimatedMinutes")
        key = (track, title)
        group = groups.setdefault(
            key,
            {
                "track": track,
                "title": title,
                "count": 0,
                "total_minutes": 0,
                "minutes_complete": True,
                "completed_count": 0,
            },
        )
        group["count"] = int(group["count"]) + 1
        if isinstance(minutes, int):
            group["total_minutes"] = int(group["total_minutes"]) + minutes
        else:
            group["minutes_complete"] = False
        if item.get("status") == "complete":
            group["completed_count"] = int(group["completed_count"]) + 1

    total_minutes = sum(
        int(item.get("estimatedMinutes") or 0)
        for item in tasks
        if isinstance(item.get("estimatedMinutes"), int)
    )
    all_minutes_known = all(isinstance(item.get("estimatedMinutes"), int) for item in tasks)
    headline = f"今日共 {len(tasks)} 项"
    if all_minutes_known:
        headline += f"，预计 {total_minutes} 分钟"

    group_parts: list[str] = []
    visible_groups = list(groups.values())
    if len(visible_groups) > 4:
        hidden_count = len(visible_groups) - 3
        visible_groups = visible_groups[:3]
    else:
        hidden_count = 0
    for group in visible_groups:
        track = str(group["track"])
        title = str(group["title"])
        count = int(group["count"])
        completed_count = int(group["completed_count"])
        if count > 1:
            part = f"{track}「{title}」{count} 组"
            if bool(group["minutes_complete"]):
                part += f"，共 {int(group['total_minutes'])} 分钟"
            if completed_count > 0:
                part += f"，{completed_count} 组已完成"
        else:
            part = f"{track}「{title}」"
            if bool(group["minutes_complete"]):
                part += f"，{int(group['total_minutes'])} 分钟"
            if completed_count > 0:
                part += "，已完成"
        group_parts.append(part)
    if hidden_count > 0:
        group_parts.append(f"另有 {hidden_count} 类任务，完整安排在今日计划中")
    return headline + "：" + "；".join(group_parts) + "。"


def _essay_citation_summary(context: CompanionContextRecord) -> str:
    parts = [f"{context.essay_attempts} 次作答，{context.grounded_essay_reviews} 次可验证 AI 评阅"]
    review = context.latest_essay_review
    if review is not None:
        confidence = {
            "low": "依据较少",
            "medium": "依据一般",
            "high": "依据充分",
        }.get(str(review["confidence"]), "依据待补充")
        parts.append(
            "最近训练估分 "
            f"{review['recommendedScore']:g}/{review['maximumScore']:g}"
            f"（{confidence}）"
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
        access_mode=quota.access_mode,
        limit=quota.limit if quota.access_mode == "entitlement_quota" else None,
        date=quota.usage_date.isoformat(),
    )
