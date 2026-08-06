from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from threading import Lock
import time
from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
import httpx
from httpx import TimeoutException
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.session import get_db_session
from app.domains.ai.audit import AIAuditRepository
from app.domains.ai.governance.contracts import IntentDecision
from app.domains.ai.model_cost import ModelPriceCard, ModelTokenUsage
from app.domains.ai.provider import (
    AICompanionAnswerResult,
    AIProvider,
    AIProviderConfigurationError,
    AIProviderResponseError,
    _validate_production_security,
)
from app.domains.auth.repository import token_hash_for_tests
from app.domains.commerce.app_store import (
    StaticAppStoreTransactionVerifier,
    VerifiedAppStoreTransaction,
)
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.companion.commands import CompanionCommandRepository
from app.domains.companion.repository import CompanionContextRecord, CompanionRepository
from app.domains.companion.router import get_companion_service
from app.domains.companion.schemas import CompanionAskRequest
from app.domains.companion.service import (
    CompanionCommandFailure,
    CompanionProviderUncertain,
    CompanionService,
    _allowed_actions,
    _build_persona_prompt,
    _context_retrieval_candidates,
    _context_payload,
    _information_action,
)
from app.domains.memory.repository import LearnerMemoryRepository
from app.domains.method_cards.repository import MethodCardRecord, MethodCardRepository
from app.main import create_app
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


TEST_PRICE_CARD = ModelPriceCard(
    version="companion-test-v1",
    input_cny_per_million=Decimal("1"),
    cached_input_cny_per_million=Decimal("0.1"),
    output_cny_per_million=Decimal("2"),
)


class FakeCompanionAIProvider:
    def __init__(self) -> None:
        self._lock = Lock()
        self._responses: dict[str, AICompanionAnswerResult] = {}
        self.attempt_keys: list[str] = []
        self.billing_count = 0

    @property
    def provider_name(self) -> str:
        return "fake_companion_provider"

    @property
    def model(self) -> str:
        return "fake-companion-model"

    @property
    def prompt_version(self) -> str:
        return "fake-companion-prompt-v1"

    @property
    def schema_version(self) -> str:
        return "fake-companion-schema-v1"

    def prepare_companion_question(
        self,
        *,
        route_decision: dict[str, object],
        question_text: str,
        context: dict[str, object],
        sources: list[dict[str, object]],
        persona: str | None = None,
    ) -> dict[str, object]:
        return {
            "contractVersion": "fake-companion-input-v1",
            "promptVersion": self.prompt_version,
            "schemaVersion": self.schema_version,
            "providerReplayRetentionSeconds": 604_800,
            "modelTier": route_decision["modelTier"],
            "model": self.model,
            "routeDecision": route_decision,
            "questionText": question_text,
            "context": context,
            "sources": sources,
            "persona": persona,
        }

    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        assert idempotency_key is not None
        with self._lock:
            self.attempt_keys.append(idempotency_key)
            cached = self._responses.get(idempotency_key)
            if cached is not None:
                return cached
            context = prepared["context"]
            assert isinstance(context, dict)
            sources = prepared["sources"]
            assert isinstance(sources, list) and sources
            source_ref = sources[0]["sourceRef"]
            assert isinstance(source_ref, str)
            answer = f"基于 {context['practiceAnswers']} 次练习，先修复资料分析增长率。"
            result = AICompanionAnswerResult(
                answer=answer,
                source_refs=[source_ref],
                confidence="medium",
                limitations=["仅基于当前已确认学习证据。"],
                latency_ms=12,
                response_summary=('{"answer":"ok","nextActions":["回看增长率方法卡"]}'),
                usage=ModelTokenUsage(
                    input_tokens=120,
                    cached_input_tokens=0,
                    output_tokens=30,
                ),
                output_fingerprint=sha256(answer.encode("utf-8")).hexdigest(),
                provider_request_id_header="x-request-id",
                provider_request_id_fingerprint=sha256(
                    f"request:{idempotency_key}".encode("utf-8")
                ).hexdigest(),
            )
            self.billing_count += 1
            self._responses[idempotency_key] = result
            return result

    def validate_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> None:
        assert prepared["contractVersion"] == "fake-companion-input-v1"
        assert prepared["providerReplayRetentionSeconds"] == 604_800
        assert idempotency_key


class OvereagerActionProvider(FakeCompanionAIProvider):
    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        # The provider contract no longer carries model-proposed actions; the
        # server derives navigation actions from the classified intent. The
        # subclass keeps the base behavior so ask:409 stays an explicit
        # requests=() drop of any model action output.
        return super().answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )

class TimeoutAfterAcceptOnceProvider(FakeCompanionAIProvider):
    def __init__(self) -> None:
        super().__init__()
        self._timed_out = False

    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        result = super().answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )
        with self._lock:
            if not self._timed_out:
                self._timed_out = True
                raise TimeoutException("provider accepted before timeout")
        return result


class SlowCompanionAIProvider(FakeCompanionAIProvider):
    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        time.sleep(0.15)
        return super().answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )


class ReplayConfigurationUnavailableProvider(FakeCompanionAIProvider):
    def validate_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> None:
        raise AIProviderConfigurationError("approved replay provider unavailable")


class ZeroRetentionTimeoutProvider(TimeoutAfterAcceptOnceProvider):
    def prepare_companion_question(
        self,
        *,
        route_decision: dict[str, object],
        question_text: str,
        context: dict[str, object],
        sources: list[dict[str, object]],
        persona: str | None = None,
    ) -> dict[str, object]:
        prepared = super().prepare_companion_question(
            route_decision=route_decision,
            question_text=question_text,
            context=context,
            sources=sources,
            persona=persona,
        )
        prepared["providerReplayRetentionSeconds"] = 0
        return prepared

    def validate_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> None:
        assert prepared["providerReplayRetentionSeconds"] == 0
        assert idempotency_key


class RuntimeConfigurationFailureProvider(FakeCompanionAIProvider):
    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        raise AIProviderConfigurationError("runtime configuration invalid")


class CrashBeforeProviderCommandRepository(CompanionCommandRepository):
    def lock_for_execution(
        self,
        session: Session,
        *,
        user_id: str,
        feature: str,
        idempotency_key: str,
    ):
        raise RuntimeError("simulated crash before provider dispatch")


def test_companion_brain_carries_essay_review_reflection_and_plan_without_raw_answer() -> None:
    context = CompanionContextRecord(
        target_exam="国考",
        position_group="中央机关综合管理岗",
        target_score=132.5,
        baseline_xingce_score=61.0,
        baseline_shenlun_score=55.0,
        practice_answers=12,
        wrong_answers=4,
        mock_exams=1,
        latest_mock_score=63.0,
        top_wrong_modules=[],
        method_cards=[],
        current_shenlun_estimate={
            "lower": 52.0,
            "recommended": 56.0,
            "upper": 60.0,
            "confidence": "medium",
        },
        essay_attempts=3,
        grounded_essay_reviews=2,
        latest_essay_review={
            "recommendedScore": 13.0,
            "maximumScore": 20.0,
            "confidence": "low",
            "scoreMeaning": "unverified_ai_rubric_training_estimate",
            "priorityFindings": [
                {
                    "type": "abstraction",
                    "severity": "high",
                    "diagnosis": "概括层级偏低",
                    "action": "先合并同义事实再提炼上位概念",
                }
            ],
        },
        latest_essay_reflection={
            "causeCodes": ["wrong_abstraction"],
            "nextChange": "先归类再下笔",
            "verificationPlan": "下一题检查概括层级",
        },
        today_essay_plan={
            "stage": "essay_drafting",
            "title": "依据材料与提纲完成申论作答",
            "reason": "完成本题闭环",
            "estimatedMinutes": 35,
            "status": "pending",
        },
        essay_plan_omission_reason=None,
    )

    payload = _context_payload(context)

    assert payload["essay"] == {
        "currentAbilityEstimate": context.current_shenlun_estimate,
        "attemptCount": 3,
        "groundedReviewCount": 2,
        "latestGroundedReview": context.latest_essay_review,
        "latestStructuredReflection": context.latest_essay_reflection,
        "todayPlanTask": context.today_essay_plan,
        "todayOmissionReason": None,
    }
    assert "answerText" not in str(payload)
    assert [item.reference.kind for item in _context_retrieval_candidates(context)] == [
        "learner_activity",
        "essay_learning",
        "target_position",
    ]
    persona = _build_persona_prompt()
    assert "岸岸" in persona
    assert "同时负责行测和申论" in persona
    assert "不得直接据此推断岗位总分差" in persona
    assert "沈砚" not in persona
    assert "主公" not in persona


def test_companion_context_uses_only_current_canonical_essay_evidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(
        db_session_factory,
        token="companion-canonical-evidence-token",
    )
    evidence = _seed_companion_canonical_evidence(
        db_session_factory,
        user_id=user_id,
    )

    context = _read_companion_context(db_session_factory, user_id=user_id)
    assert context.current_shenlun_estimate is None
    assert context.latest_essay_review is not None
    assert context.latest_essay_review["recommendedScore"] == 12.0
    assert context.latest_essay_reflection is not None
    assert context.latest_essay_reflection["nextChange"] == "current-answer-v1"
    assert evidence["raw_answer_v1"] not in str(_context_payload(context))

    _insert_current_shenlun_estimate(
        db_session_factory,
        user_id=user_id,
        exam_year=2027,
        recommended=61,
        supersedes_estimate_id=evidence["expired_current_estimate"],
    )
    context = _read_companion_context(db_session_factory, user_id=user_id)
    assert context.current_shenlun_estimate is not None
    assert context.current_shenlun_estimate["recommended"] == 61.0

    answer_v2 = _insert_companion_answer(
        db_session_factory,
        evidence=evidence,
        version_no=2,
        supersedes_answer_id=evidence["answer_v1"],
        answer_text="RAW_CURRENT_ANSWER_V2_MUST_NOT_ENTER_COMPANION",
    )
    _insert_companion_reflection(
        db_session_factory,
        evidence=evidence,
        answer_id=answer_v2,
        material_id=evidence["other_material"],
        rubric_id=evidence["other_rubric"],
        marker="wrong-content-tuple",
    )

    context = _read_companion_context(db_session_factory, user_id=user_id)
    assert context.latest_essay_review is None
    assert context.latest_essay_reflection is None

    _insert_companion_review(
        db_session_factory,
        evidence=evidence,
        answer_id=answer_v2,
        marker="expired-current-answer-review",
        generated_at=datetime.now(UTC) - timedelta(days=2),
        valid_until=datetime.now(UTC) - timedelta(days=1),
    )
    context = _read_companion_context(db_session_factory, user_id=user_id)
    assert context.latest_essay_review is None
    assert context.latest_essay_reflection is None

    _insert_companion_review(
        db_session_factory,
        evidence=evidence,
        answer_id=answer_v2,
        marker="valid-current-answer-review",
        generated_at=datetime.now(UTC),
        valid_until=datetime.now(UTC) + timedelta(days=30),
    )
    _insert_companion_reflection(
        db_session_factory,
        evidence=evidence,
        answer_id=answer_v2,
        material_id=evidence["material"],
        rubric_id=evidence["rubric"],
        marker="current-answer-v2",
    )
    context = _read_companion_context(db_session_factory, user_id=user_id)
    assert context.latest_essay_review is not None
    priority_findings = context.latest_essay_review["priorityFindings"]
    assert isinstance(priority_findings, list)
    assert priority_findings[0]["diagnosis"] == "valid-current-answer-review"
    assert context.latest_essay_reflection is not None
    assert context.latest_essay_reflection["nextChange"] == "current-answer-v2"
    assert "RAW_CURRENT_ANSWER_V2_MUST_NOT_ENTER_COMPANION" not in str(_context_payload(context))

    _insert_companion_orphan_attempt(
        db_session_factory,
        evidence=evidence,
        submitted_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    context = _read_companion_context(db_session_factory, user_id=user_id)
    assert context.latest_essay_review is None
    assert context.latest_essay_reflection is None


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"ai_companion_provider_release_id": ""}, "RELEASE_ID"),
        ({"ai_companion_provider_https_host_allowlist": ""}, "ALLOWLIST"),
        ({"ai_companion_provider_idempotency_capability": ""}, "replayable"),
        ({"ai_companion_provider_idempotency_key_header": ""}, "KEY_HEADER"),
        ({"ai_companion_provider_request_id_header": ""}, "REQUEST_ID_HEADER"),
        (
            {"ai_companion_provider_replay_retention_seconds": 0},
            "REPLAY_RETENTION_SECONDS",
        ),
        ({"ai_companion_max_input_bytes": 0}, "MAX_INPUT_BYTES"),
        ({"ai_companion_max_output_bytes": 0}, "MAX_OUTPUT_BYTES"),
    ],
)
def test_production_companion_provider_fails_closed_without_verified_replay_contract(
    override: dict[str, object],
    message: str,
) -> None:
    provider = AIProvider(replace(_production_companion_settings(), **override))
    prepared = provider.prepare_companion_question(
        route_decision=_provider_route_decision(),
        question_text="如何提升？",
        context={"practiceAnswers": 1},
        sources=_grounding_sources(),
    )

    with pytest.raises(AIProviderConfigurationError, match=message):
        provider.answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key="verified-command-key-1",
        )


def test_production_companion_does_not_require_enterprise_dpa_attestation() -> None:
    settings = replace(
        _production_companion_settings(),
        ai_companion_provider_dpa_confirmed=False,
    )

    _validate_production_security(settings)


def test_production_provider_replay_uses_identical_key_and_request_bytes() -> None:
    requests: list[tuple[str | None, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.headers.get("X-Idempotency-Key"), request.content))
        return httpx.Response(
            200,
            headers={"X-Request-Id": "provider-request-1"},
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"按方法卡练习","nextActions":["做三道题"],'
                                '"sourceRefs":["test-source"],"confidence":"medium",'
                                '"limitations":[]}'
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    transport = httpx.MockTransport(handler)
    provider = AIProvider(
        _production_companion_settings(),
        client_factory=lambda: httpx.Client(transport=transport),
    )
    prepared = provider.prepare_companion_question(
        route_decision=_provider_route_decision(),
        question_text="资料分析怎么补？",
        context={"practiceAnswers": 3},
        sources=_grounding_sources(),
    )
    assert prepared["body"]["response_format"]["type"] == "json_schema"

    first = provider.answer_prepared_companion_question(
        prepared=prepared,
        idempotency_key="stable-provider-command-1",
    )
    replay = provider.answer_prepared_companion_question(
        prepared=prepared,
        idempotency_key="stable-provider-command-1",
    )

    assert first.answer == replay.answer
    assert first.source_refs == replay.source_refs
    assert first.usage == replay.usage
    assert first.output_fingerprint == replay.output_fingerprint
    assert requests[0] == requests[1]
    assert requests[0][0] == "stable-provider-command-1"


def test_deepseek_companion_uses_indexed_json_and_maps_authorized_sources() -> None:
    observed_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"X-Request-Id": "deepseek-request-1"},
            json={
                "id": "deepseek-request-1",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"可以，先完成今天的基线任务。",'
                                '"sourceIndexes":[1],"confidence":"medium",'
                                '"limitations":[]}'
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    settings = replace(
        _production_companion_settings(),
        ai_model="deepseek-v4-flash",
        ai_small_model="deepseek-v4-flash",
        ai_high_capability_model="deepseek-v4-flash",
    )
    provider = AIProvider(
        settings,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    prepared = provider.prepare_companion_question(
        route_decision=_provider_route_decision(),
        question_text="我可以做资料分析吗？",
        context={"practiceAnswers": 3},
        sources=_grounding_sources(),
    )

    assert prepared["outputAdapter"] == "deepseek-indexed-json@v1"
    assert prepared["sourceRefMap"] == ["test-source"]
    assert prepared["body"]["response_format"] == {"type": "json_object"}
    assert prepared["body"]["stream"] is False
    assert prepared["body"]["thinking"] == {"type": "disabled"}

    result = provider.answer_prepared_companion_question(
        prepared=prepared,
        idempotency_key="deepseek-provider-command-1",
    )

    provider_prompt = json.loads(observed_body["messages"][1]["content"])
    assert provider_prompt["retrieved_sources"][0]["sourceIndex"] == 1
    assert "sourceRef" not in provider_prompt["retrieved_sources"][0]
    assert provider_prompt["output_example"]["sourceIndexes"] == [1]
    assert result.source_refs == ["test-source"]
    assert result.answer == "可以，先完成今天的基线任务。"


def test_deepseek_companion_rejects_source_index_outside_authorized_map() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Request-Id": "deepseek-request-invalid-source"},
            json={
                "id": "deepseek-request-invalid-source",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"先练习。","sourceIndexes":[2],"confidence":"low"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            },
        )

    settings = replace(
        _production_companion_settings(),
        ai_model="deepseek-v4-flash",
        ai_small_model="deepseek-v4-flash",
        ai_high_capability_model="deepseek-v4-flash",
    )
    provider = AIProvider(
        settings,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    prepared = provider.prepare_companion_question(
        route_decision=_provider_route_decision(),
        question_text="怎么练？",
        context={"practiceAnswers": 1},
        sources=_grounding_sources(),
    )

    with pytest.raises(AIProviderResponseError) as exc_info:
        provider.answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key="deepseek-provider-command-invalid-source",
        )
    assert getattr(exc_info.value, "code", None) == "provider_content_invalid"


def test_companion_ask_is_quota_gated_and_audited(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="companion-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    client = _make_client(
        db_session_factory,
        provider=OvereagerActionProvider(),
    )

    response = client.post(
        "/api/v1/companion/ask",
        headers={
            "Authorization": "Bearer companion-token",
            "Idempotency-Key": "companion-ask-audit-1",
        },
        json={
            "questionText": "资料分析增长率怎么补？",
            "context": {
                "routeId": "pages/companion/index",
                "workspace": "training",
                "objectRefs": [],
                "learnerStateVersion": "test-state-v1",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["quota"] == {
        "feature": "companion_ask",
        "used": 1,
        "accessMode": "entitlement_quota",
        "limit": 3,
        "date": datetime.now(UTC).date().isoformat(),
    }
    assert "资料分析增长率" in body["answer"]
    assert body["semanticState"] == "ready"
    assert body["respondingAgent"] == "tutor"
    assert body["citations"][0]["version"]
    assert body["citations"][0]["anchor"]
    assert body["allowedActions"] == [
        {
            "kind": "open_training",
            "label": "查看今日训练安排",
            "targetRef": "/pages/today/index",
            "requiresConfirmation": False,
        }
    ]
    with db_session_factory() as session:
        log = (
            session.execute(
                text(
                    """
                select feature, provider, status, metadata
                from ai_audit.interaction_logs
                where learner_key = :learner_key
                """
                ),
                {"learner_key": user_id},
            )
            .mappings()
            .one()
        )
        assert log["feature"] == "companion_ask"
        assert log["provider"] == "fake_companion_provider"
        assert log["status"] == "succeeded"
        assert log["metadata"]["scenario"] == "tutoring_explanation"
        assert log["metadata"]["fallbackActionCount"] == 1
        assert log["metadata"]["fallbackActions"] == [
            {
                "kind": "open_training",
                "label": "查看今日训练安排",
                "targetRef": "/pages/today/index",
                "requiresConfirmation": False,
            }
        ]
        assert "context" not in log["metadata"]


def test_companion_buttons_use_deterministic_executable_targets() -> None:
    planner = _intent_decision("planner", "plan_explanation")
    position = _intent_decision("position", "position_explanation")
    tutor = _intent_decision("tutor", "learning_explanation")

    assert _allowed_actions(decision=planner)[0].model_dump(by_alias=True) == {
        "kind": "adjust_plan_preview",
        "label": "预览今日计划调整",
        "targetRef": "/plans/day-adjustment/index",
        "requiresConfirmation": True,
    }
    assert _allowed_actions(decision=position)[0].target_ref == "/pages/positions/index"
    assert _information_action(tutor).model_dump(by_alias=True) == {
        "kind": "supply_information",
        "label": "拍题或导入学习资料",
        "targetRef": "/pages/capture-import/index",
        "requiresConfirmation": False,
    }


def test_external_wrong_question_requires_existing_supported_map_node(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="external-token")
    _seed_retired_duplicate_knowledge_map(db_session_factory)
    _seed_knowledge_map(db_session_factory)
    client = _make_client(db_session_factory)

    resolved = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers={
            "Authorization": "Bearer external-token",
            "Idempotency-Key": "external-resolve-supported-1",
        },
        json={
            "stem": "某市 2025 年产值同比增长 12%，求增长率相关判断。",
            "options": ["A", "B", "C", "D"],
            "selectedAnswer": "A",
            "correctAnswer": "B",
            "extractedTags": [
                {
                    "moduleName": "资科分析",
                    "tagType": "knowledge_point",
                    "tagValue": "增长率",
                    "confidence": 0.91,
                }
            ],
            "containsImage": False,
            "extractionConfidence": 0.88,
        },
    )

    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "pending_confirmation"
    assert body["requiresUserConfirmation"] is True
    assert body["candidates"][0]["nodeId"] == "km_growth"
    assert body["candidates"][0]["displayPath"] == "行测/资料分析/增长率"

    with db_session_factory() as session:
        intake = (
            session.execute(
                text(
                    """
                select knowledge_release_id, selected_node_id
                from learner.external_question_intakes
                where intake_id = cast(:intake_id as uuid)
                """
                ),
                {"intake_id": body["intakeId"]},
            )
            .mappings()
            .one()
        )
        assert str(intake["knowledge_release_id"]) == ("11111111-1111-4111-8111-111111111111")
        assert intake["selected_node_id"] == "km_growth"

    _switch_active_knowledge_map_release(db_session_factory)

    confirmed = client.post(
        f"/api/v1/companion/external-questions/{body['intakeId']}/confirm",
        headers={"Authorization": "Bearer external-token"},
        json={"userConfirmed": True, "errorSummary": "把同比增长率和增长量混了。"},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["memorySource"] == "external"

    with db_session_factory() as session:
        row = (
            session.execute(
                text(
                    """
                select knowledge_release_id, node_id, source, error_summary
                from learner.external_wrong_questions
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert str(row["knowledge_release_id"]) == ("11111111-1111-4111-8111-111111111111")
        assert row["node_id"] == "km_growth"
        assert row["source"] == "external"
        assert row["error_summary"] == "把同比增长率和增长量混了。"

        sections = LearnerMemoryRepository(session).list_workbench_sections(user_id=user_id)
        external_section = next(section for section in sections if section.id == "external_wrong")
        assert len(external_section.objects) == 1
        assert external_section.objects[0].payload["displayPath"] == "行测/资料分析/增长率"
        assert external_section.objects[0].payload["knowledgeReleaseId"] == (
            "11111111-1111-4111-8111-111111111111"
        )
        assert external_section.objects[0].payload["knowledgeReleaseVersion"] == (
            "knowledge-map@companion-tests-current"
        )


def test_external_wrong_question_rejects_free_tag_and_does_not_write_memory(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="free-tag-token")
    _seed_knowledge_map(db_session_factory)
    client = _make_client(db_session_factory)

    response = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers={
            "Authorization": "Bearer free-tag-token",
            "Idempotency-Key": "free-tag-resolve-1",
        },
        json={
            "stem": "这是一道模型试图乱标知识点的题干，文本足够长。",
            "options": [],
            "extractedTags": [
                {
                    "moduleName": "资料分析",
                    "tagType": "knowledge_point",
                    "tagValue": "AI自由发明tag",
                    "confidence": 0.99,
                }
            ],
            "containsImage": False,
            "extractionConfidence": 0.95,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert "知识点地图" in body["reason"]

    with db_session_factory() as session:
        count = session.execute(
            text(
                """
                select count(*)
                from learner.external_wrong_questions
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        assert count == 0


def test_companion_knowledge_release_expires_during_the_same_transaction(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="cross-expiry-token")
    _seed_knowledge_map(
        db_session_factory,
        fresh_until=datetime.now(UTC) + timedelta(seconds=1),
    )

    with db_session_factory() as session:
        session.execute(text("select pg_sleep(1.1)"))
        repository = CompanionRepository(session)

        assert (
            repository.resolve_candidate(
                module_name="资科分析",
                tag_type="knowledge_point",
                tag_value="增长率",
            )
            is None
        )
        assert (
            repository.get_context(
                user_id=user_id,
                node_id="km_growth",
            ).method_cards
            == []
        )


def test_companion_shared_method_card_exact_replay_preserves_context(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="method-card-replay-token")
    _seed_knowledge_map(db_session_factory)

    with db_session_factory() as session:
        before_timestamps = session.execute(
            text(
                """
                select created_at, updated_at
                from product.method_cards
                where id = 'mc_growth'
                """
            )
        ).one()
        before_context = (
            CompanionRepository(session)
            .get_context(
                user_id=user_id,
                node_id="km_growth",
            )
            .method_cards
        )

        assert MethodCardRepository(session).upsert_cards([_companion_shared_method_card()]) == 1

        after_timestamps = session.execute(
            text(
                """
                select created_at, updated_at
                from product.method_cards
                where id = 'mc_growth'
                """
            )
        ).one()
        after_context = (
            CompanionRepository(session)
            .get_context(
                user_id=user_id,
                node_id="km_growth",
            )
            .method_cards
        )
        release_status = session.execute(
            text(
                """
                select status
                from product.knowledge_map_releases
                where release_id =
                      '11111111-1111-4111-8111-111111111111'::uuid
                """
            )
        ).scalar_one()

    assert before_context == [
        {
            "id": "mc_growth",
            "title": "增长率三步法",
            "fix_action": "先写公式再代数。",
            "tag_path": "增长率",
        }
    ]
    assert after_context == before_context
    assert after_timestamps == before_timestamps
    assert release_status == "active"


def test_external_wrong_question_rejects_image_unsupported_node(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="image-token")
    _seed_knowledge_map(db_session_factory)
    client = _make_client(db_session_factory)

    response = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers={
            "Authorization": "Bearer image-token",
            "Idempotency-Key": "image-resolve-1",
        },
        json={
            "stem": "给出一组图形，问下一个图形应当选择哪一个。",
            "options": ["A", "B", "C", "D"],
            "extractedTags": [
                {
                    "moduleName": "判断推理",
                    "tagType": "knowledge_point",
                    "tagValue": "图形推理",
                    "confidence": 0.96,
                }
            ],
            "containsImage": False,
            "extractionConfidence": 0.96,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert "暂不支持" in body["reason"]


def test_external_wrong_question_quota_is_enforced(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="quota-token")
    _seed_knowledge_map(db_session_factory)
    client = _make_client(db_session_factory)
    payload = {
        "stem": "某市 2025 年产值同比增长 12%，求增长率相关判断。",
        "options": ["A", "B", "C", "D"],
        "extractedTags": [
            {
                "moduleName": "资料分析",
                "tagType": "knowledge_point",
                "tagValue": "增长率",
                "confidence": 0.91,
            }
        ],
        "containsImage": False,
        "extractionConfidence": 0.88,
    }

    first = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers={
            "Authorization": "Bearer quota-token",
            "Idempotency-Key": "quota-resolve-1",
        },
        json=payload,
    )
    second = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers={
            "Authorization": "Bearer quota-token",
            "Idempotency-Key": "quota-resolve-2",
        },
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_paid_companion_commands_require_idempotency_key(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="required-key-token")
    provider = FakeCompanionAIProvider()
    client = _make_client(db_session_factory, provider=provider)

    ask = client.post(
        "/api/v1/companion/ask",
        headers={"Authorization": "Bearer required-key-token"},
        json=_ask_body(),
    )
    external = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers={"Authorization": "Bearer required-key-token"},
        json={
            "stem": "这是一道需要记录的外部文本题目。",
            "options": [],
            "extractedTags": [],
            "containsImage": False,
            "extractionConfidence": 0.9,
        },
    )

    assert ask.status_code == 422
    assert external.status_code == 422
    assert provider.attempt_keys == []


def test_companion_ask_replays_full_response_and_rejects_key_reuse(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="ask-replay-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    provider = FakeCompanionAIProvider()
    client = _make_client(db_session_factory, provider=provider)
    headers = {
        "Authorization": "Bearer ask-replay-token",
        "Idempotency-Key": "ask-replay-command-1",
    }

    first = client.post(
        "/api/v1/companion/ask",
        headers=headers,
        json=_ask_body(),
    )
    replay = client.post(
        "/api/v1/companion/ask",
        headers=headers,
        json=_ask_body(),
    )
    conflict_body = _ask_body()
    conflict_body["questionText"] = "用同一命令键问另一个问题"
    conflict = client.post(
        "/api/v1/companion/ask",
        headers=headers,
        json=conflict_body,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert provider.billing_count == 1
    assert len(provider.attempt_keys) == 1

    with db_session_factory() as session:
        command = (
            session.execute(
                text(
                    """
                select status, response_payload, response_fingerprint,
                       provider_attempt_count
                  from platform.companion_paid_commands
                 where user_id = cast(:user_id as uuid)
                   and feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert command["status"] == "succeeded"
        assert command["response_payload"] == first.json()
        assert len(command["response_fingerprint"]) == 64
        assert command["provider_attempt_count"] == 1
        assert (
            session.execute(
                text(
                    """
                select used_count
                  from commerce.entitlement_usage_daily
                 where user_id = cast(:user_id as uuid)
                   and feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
        event = (
            session.execute(
                text(
                    """
                select event_type, payload
                  from platform.outbox_events
                 where aggregate_type = 'companion_paid_command'
                   and payload->>'userId' = :user_id
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert event["event_type"] == "companion.command.succeeded"
        assert event["payload"]["status"] == "succeeded"
        assert (
            session.execute(
                text(
                    """
                select count(*) from ai_audit.model_call_ledger
                 where user_id = cast(:user_id as uuid)
                   and feature = 'companion_ask'
                   and status = 'succeeded'
                """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )


def test_external_resolve_replay_creates_one_intake_and_consumes_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="external-replay-token")
    _seed_knowledge_map(db_session_factory)
    client = _make_client(db_session_factory)
    headers = {
        "Authorization": "Bearer external-replay-token",
        "Idempotency-Key": "external-replay-command-1",
    }
    payload = {
        "stem": "某市 2025 年产值同比增长 12%，求增长率相关判断。",
        "options": ["A", "B", "C", "D"],
        "selectedAnswer": "A",
        "correctAnswer": "B",
        "extractedTags": [
            {
                "moduleName": "资料分析",
                "tagType": "knowledge_point",
                "tagValue": "增长率",
                "confidence": 0.91,
            }
        ],
        "containsImage": False,
        "extractionConfidence": 0.88,
    }

    first = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        "/api/v1/companion/external-questions/resolve",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learner.external_question_intakes "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
        command = (
            session.execute(
                text(
                    """
                select request_contract_version, execution_payload
                from platform.companion_paid_commands
                where user_id = cast(:user_id as uuid)
                  and feature = 'external_wrong_intake'
                  and idempotency_key = 'external-replay-command-1'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert command["request_contract_version"] == ("external-question-resolve-command-v2")
        assert command["execution_payload"]["contractVersion"] == (
            "external-question-resolve-execution-v2"
        )
        assert (
            command["execution_payload"]["resolution"]["selectedKnowledgeReleaseId"]
            == "11111111-1111-4111-8111-111111111111"
        )
        intake_release_id = session.execute(
            text(
                """
                select knowledge_release_id
                from learner.external_question_intakes
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        assert str(intake_release_id) == ("11111111-1111-4111-8111-111111111111")
        assert (
            session.execute(
                text(
                    "select used_count from commerce.entitlement_usage_daily "
                    "where user_id = cast(:user_id as uuid) "
                    "and feature = 'external_wrong_intake'"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )


def test_concurrent_companion_retries_execute_provider_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="concurrent-ask-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    provider = SlowCompanionAIProvider()
    client = _make_client(db_session_factory, provider=provider)
    headers = {
        "Authorization": "Bearer concurrent-ask-token",
        "Idempotency-Key": "concurrent-ask-command-1",
    }

    def send() -> tuple[int, dict[str, object]]:
        response = client.post(
            "/api/v1/companion/ask",
            headers=headers,
            json=_ask_body(),
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: send(), range(2)))

    assert [status_code for status_code, _ in responses] == [200, 200]
    assert responses[0][1] == responses[1][1]
    assert provider.billing_count == 1
    assert len(provider.attempt_keys) == 1


def test_provider_timeout_replays_same_accepted_request_without_second_charge(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="timeout-replay-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    provider = TimeoutAfterAcceptOnceProvider()
    client = _make_client(db_session_factory, provider=provider)
    headers = {
        "Authorization": "Bearer timeout-replay-token",
        "Idempotency-Key": "timeout-replay-command-1",
    }

    uncertain = client.post(
        "/api/v1/companion/ask",
        headers=headers,
        json=_ask_body(),
    )
    recovered = client.post(
        "/api/v1/companion/ask",
        headers=headers,
        json=_ask_body(),
    )

    assert uncertain.status_code == 502
    assert recovered.status_code == 200
    assert provider.billing_count == 1
    assert len(provider.attempt_keys) == 2
    assert provider.attempt_keys[0] == provider.attempt_keys[1]
    with db_session_factory() as session:
        command = (
            session.execute(
                text(
                    "select status, delivery_count, provider_attempt_count "
                    "from platform.companion_paid_commands "
                    "where user_id = cast(:user_id as uuid) "
                    "and feature = 'companion_ask'"
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert command["status"] == "succeeded"
        assert command["delivery_count"] == 2
        assert command["provider_attempt_count"] == 2
        assert (
            session.execute(
                text(
                    "select used_count from commerce.entitlement_usage_daily "
                    "where user_id = cast(:user_id as uuid) "
                    "and feature = 'companion_ask'"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )


def test_expired_provider_replay_window_never_dispatches_a_second_unknown_charge(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="expired-replay-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    provider = ZeroRetentionTimeoutProvider()
    client = _make_client(db_session_factory, provider=provider)
    headers = {
        "Authorization": "Bearer expired-replay-token",
        "Idempotency-Key": "expired-replay-command-1",
    }

    uncertain = client.post(
        "/api/v1/companion/ask",
        headers=headers,
        json=_ask_body(),
    )
    expired = client.post(
        "/api/v1/companion/ask",
        headers=headers,
        json=_ask_body(),
    )

    assert uncertain.status_code == 502
    assert expired.status_code == 503
    assert provider.billing_count == 1
    assert len(provider.attempt_keys) == 1
    with db_session_factory() as session:
        state = (
            session.execute(
                text(
                    """
                select command.status, command.provider_attempt_count,
                       reservation.status as reservation_status
                  from platform.companion_paid_commands command
                  join commerce.feature_usage_reservations reservation
                    on reservation.reservation_key = command.reservation_key
                 where command.user_id = cast(:user_id as uuid)
                   and command.feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert dict(state) == {
            "status": "provider_uncertain",
            "provider_attempt_count": 1,
            "reservation_status": "reserved",
        }


def test_uncertain_replay_reuses_original_price_snapshot_after_config_drift(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="price-drift-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    provider = TimeoutAfterAcceptOnceProvider()
    request = CompanionAskRequest.model_validate(_ask_body())

    with db_session_factory() as initial_session:
        with pytest.raises(CompanionProviderUncertain):
            _companion_service(
                session=initial_session,
                provider=provider,
            ).ask(
                user_id=user_id,
                learner_key=user_id,
                idempotency_key="price-drift-command-1",
                request=request,
            )

    with db_session_factory() as retry_session:
        recovered = _companion_service(
            session=retry_session,
            provider=provider,
            price_card=None,
            settings=Settings(),
        ).ask(
            user_id=user_id,
            learner_key=user_id,
            idempotency_key="price-drift-command-1",
            request=request,
        )

    assert "资料分析增长率" in recovered.answer
    assert provider.billing_count == 1
    assert provider.attempt_keys[0] == provider.attempt_keys[1]
    with db_session_factory() as session:
        call = (
            session.execute(
                text(
                    """
                select status, price_version,
                       input_rate_cny_per_million,
                       output_rate_cny_per_million
                  from ai_audit.model_call_ledger
                 where user_id = cast(:user_id as uuid)
                   and feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert call["status"] == "succeeded"
        assert call["price_version"] == TEST_PRICE_CARD.version
        assert Decimal(call["input_rate_cny_per_million"]) == Decimal("1")
        assert Decimal(call["output_rate_cny_per_million"]) == Decimal("2")


def test_initial_runtime_provider_configuration_failure_refunds_and_terminates(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="runtime-config-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    client = _make_client(
        db_session_factory,
        provider=RuntimeConfigurationFailureProvider(),
    )

    response = client.post(
        "/api/v1/companion/ask",
        headers={
            "Authorization": "Bearer runtime-config-token",
            "Idempotency-Key": "runtime-config-command-1",
        },
        json=_ask_body(),
    )

    assert response.status_code == 503
    with db_session_factory() as session:
        state = (
            session.execute(
                text(
                    """
                select command.status, command.provider_attempt_count,
                       reservation.status as reservation_status,
                       model_call.status as model_call_status,
                       usage.used_count
                  from platform.companion_paid_commands command
                  join commerce.feature_usage_reservations reservation
                    on reservation.reservation_key = command.reservation_key
                  join ai_audit.model_call_ledger model_call
                    on model_call.call_key = command.provider_idempotency_key
                  join commerce.entitlement_usage_daily usage
                    on usage.user_id = command.user_id
                   and usage.feature = command.feature
                 where command.user_id = cast(:user_id as uuid)
                   and command.feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert dict(state) == {
            "status": "failed",
            "provider_attempt_count": 1,
            "reservation_status": "refunded",
            "model_call_status": "failed",
            "used_count": 0,
        }


def test_initial_preflight_configuration_failure_refunds_without_dispatch(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="preflight-config-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    request = CompanionAskRequest.model_validate(_ask_body())
    unconfigured_provider = AIProvider(
        Settings(
            ai_provider_base_url="https://provider.example.com/v1",
            ai_model="companion-model",
            ai_api_key="",
            ai_companion_provider_release_id="companion-release",
        )
    )

    with db_session_factory() as session:
        with pytest.raises(CompanionCommandFailure) as failure:
            _companion_service(
                session=session,
                provider=unconfigured_provider,
            ).ask(
                user_id=user_id,
                learner_key=user_id,
                idempotency_key="preflight-config-command-1",
                request=request,
            )
        assert failure.value.status_code == 503

    with db_session_factory() as session:
        state = (
            session.execute(
                text(
                    """
                select command.status, command.provider_attempt_count,
                       reservation.status as reservation_status,
                       usage.used_count,
                       (select count(*) from ai_audit.model_call_ledger
                         where call_key = command.provider_idempotency_key)
                         as model_call_count
                  from platform.companion_paid_commands command
                  join commerce.feature_usage_reservations reservation
                    on reservation.reservation_key = command.reservation_key
                  join commerce.entitlement_usage_daily usage
                    on usage.user_id = command.user_id
                   and usage.feature = command.feature
                 where command.user_id = cast(:user_id as uuid)
                   and command.feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert dict(state) == {
            "status": "failed",
            "provider_attempt_count": 0,
            "reservation_status": "refunded",
            "used_count": 0,
            "model_call_count": 0,
        }


def test_crash_before_durable_provider_dispatch_allows_safe_drift_refund(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="pre-dispatch-crash-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    request = CompanionAskRequest.model_validate(_ask_body())
    initial_provider = AIProvider(
        Settings(
            ai_provider_base_url="https://provider-a.example.com/v1",
            ai_model="companion-model-a",
            ai_api_key="test-key-a",
            ai_companion_provider_release_id="companion-release-a",
        )
    )

    with db_session_factory() as crashed_session:
        with pytest.raises(RuntimeError, match="before provider dispatch"):
            _companion_service(
                session=crashed_session,
                provider=initial_provider,
                command_repository=CrashBeforeProviderCommandRepository(),
            ).ask(
                user_id=user_id,
                learner_key=user_id,
                idempotency_key="pre-dispatch-crash-command-1",
                request=request,
            )

    drifted_provider = AIProvider(
        Settings(
            ai_provider_base_url="https://provider-b.example.com/v1",
            ai_model="companion-model-b",
            ai_api_key="test-key-b",
            ai_companion_provider_release_id="companion-release-b",
        )
    )
    with db_session_factory() as retry_session:
        with pytest.raises(CompanionCommandFailure) as failure:
            _companion_service(
                session=retry_session,
                provider=drifted_provider,
            ).ask(
                user_id=user_id,
                learner_key=user_id,
                idempotency_key="pre-dispatch-crash-command-1",
                request=request,
            )
        assert failure.value.status_code == 503

    with db_session_factory() as session:
        state = (
            session.execute(
                text(
                    """
                select command.status, command.provider_attempt_count,
                       reservation.status as reservation_status,
                       model_call.status as model_call_status
                  from platform.companion_paid_commands command
                  join commerce.feature_usage_reservations reservation
                    on reservation.reservation_key = command.reservation_key
                  join ai_audit.model_call_ledger model_call
                    on model_call.call_key = command.provider_idempotency_key
                 where command.user_id = cast(:user_id as uuid)
                   and command.feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert dict(state) == {
            "status": "failed",
            "provider_attempt_count": 0,
            "reservation_status": "refunded",
            "model_call_status": "failed",
        }


def test_provider_success_then_db_commit_failure_replays_same_provider_key(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="commit-replay-token")
    _seed_profile_and_context(db_session_factory, user_id=user_id)
    provider = FakeCompanionAIProvider()
    request = CompanionAskRequest.model_validate(_ask_body())

    with db_session_factory() as failed_session:
        service = _companion_service(session=failed_session, provider=provider)
        real_commit = failed_session.commit
        commit_count = 0

        def fail_final_commit() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                failed_session.rollback()
                raise RuntimeError("simulated final database commit failure")
            real_commit()

        failed_session.commit = fail_final_commit  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="final database commit failure"):
            service.ask(
                user_id=user_id,
                learner_key=user_id,
                idempotency_key="db-commit-replay-command-1",
                request=request,
            )

    with db_session_factory() as unavailable_session:
        with pytest.raises(
            CompanionProviderUncertain,
            match="replay configuration",
        ):
            _companion_service(
                session=unavailable_session,
                provider=ReplayConfigurationUnavailableProvider(),
            ).ask(
                user_id=user_id,
                learner_key=user_id,
                idempotency_key="db-commit-replay-command-1",
                request=request,
            )
    with db_session_factory() as checkpoint_session:
        checkpoint = (
            checkpoint_session.execute(
                text(
                    """
                select command.status, command.provider_attempt_count,
                       reservation.status as reservation_status
                  from platform.companion_paid_commands command
                  join commerce.feature_usage_reservations reservation
                    on reservation.reservation_key = command.reservation_key
                 where command.user_id = cast(:user_id as uuid)
                   and command.feature = 'companion_ask'
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert dict(checkpoint) == {
            "status": "provider_uncertain",
            "provider_attempt_count": 1,
            "reservation_status": "reserved",
        }

    with db_session_factory() as retry_session:
        recovered = _companion_service(
            session=retry_session,
            provider=provider,
        ).ask(
            user_id=user_id,
            learner_key=user_id,
            idempotency_key="db-commit-replay-command-1",
            request=request,
        )

    assert "资料分析增长率" in recovered.answer
    assert provider.billing_count == 1
    assert len(provider.attempt_keys) == 2
    assert provider.attempt_keys[0] == provider.attempt_keys[1]
    with db_session_factory() as session:
        command = (
            session.execute(
                text(
                    "select status, response_payload from platform.companion_paid_commands "
                    "where user_id = cast(:user_id as uuid) "
                    "and feature = 'companion_ask'"
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert command["status"] == "succeeded"
        assert command["response_payload"]["answer"] == recovered.answer
        assert (
            session.execute(
                text(
                    "select used_count from commerce.entitlement_usage_daily "
                    "where user_id = cast(:user_id as uuid) "
                    "and feature = 'companion_ask'"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )


def _make_client(
    db_session_factory: sessionmaker[Session],
    *,
    provider: FakeCompanionAIProvider | None = None,
) -> TestClient:
    app = create_app()
    companion_provider = provider or FakeCompanionAIProvider()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    def override_companion_service(
        session: Annotated[Session, Depends(get_db_session)],
    ) -> CompanionService:
        return _companion_service(
            session=session,
            provider=companion_provider,
        )

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_companion_service] = override_companion_service
    return TestClient(app)


def _companion_service(
    *,
    session: Session,
    provider: FakeCompanionAIProvider | AIProvider,
    command_repository: CompanionCommandRepository | None = None,
    price_card: ModelPriceCard | None = TEST_PRICE_CARD,
    settings: Settings | None = None,
) -> CompanionService:
    return CompanionService(
        repository=CompanionRepository(session),
        audit_repository=AIAuditRepository(session),
        commerce_service=CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(_verified_transaction()),
            commercial_mode="paid",
            usage_access_mode="entitlement_quota",
        ),
        ai_provider=provider,  # type: ignore[arg-type]
        session=session,
        command_repository=command_repository,
        price_card=price_card,
        settings=settings,
    )


def _ask_body() -> dict[str, object]:
    return {
        "questionText": "资料分析增长率怎么补？",
        "context": {
            "routeId": "pages/companion/index",
            "workspace": "training",
            "objectRefs": [],
            "learnerStateVersion": "test-state-v1",
        },
    }


def _provider_route_decision() -> dict[str, object]:
    return {
        "scenarioKey": "tutoring_explanation",
        "respondingAgent": "tutor",
        "answerType": "learning_explanation",
        "modelTier": "small",
        "policyDecision": "answer",
        "companionIntent": "thinking",
        "reasonCodes": ["test"],
    }


def _intent_decision(responding_agent: str, answer_type: str) -> IntentDecision:
    return IntentDecision.model_validate(
        {
            "scenarioKey": (
                "position_explanation"
                if responding_agent == "position"
                else "planning_explanation"
                if responding_agent == "planner"
                else "tutoring_explanation"
            ),
            "respondingAgent": responding_agent,
            "answerType": answer_type,
            "modelTier": "small",
            "policyDecision": "answer",
            "companionIntent": "thinking",
            "reasonCodes": ["test"],
        }
    )


def _grounding_sources() -> list[dict[str, object]]:
    return [
        {
            "sourceRef": "test-source",
            "kind": "learner_activity",
            "title": "学习记录",
            "version": "test-v1",
            "anchor": "summary",
            "trustScope": "learner_activity",
            "untrustedInput": False,
            "content": "已完成 3 次练习。",
        }
    ]


def _production_companion_settings() -> Settings:
    return Settings(
        environment="production",
        ai_provider_base_url="https://provider.example.com/v1",
        ai_model="approved-companion-model",
        ai_small_model="approved-companion-small-model",
        ai_high_capability_model="approved-companion-high-model",
        ai_api_key="test-api-key",
        ai_companion_provider_release_id="companion-provider-2026-07-13",
        ai_companion_provider_https_host_allowlist="provider.example.com",
        ai_companion_provider_dpa_confirmed=True,
        ai_companion_provider_idempotency_capability="replayable_queryable",
        ai_companion_provider_idempotency_key_header="X-Idempotency-Key",
        ai_companion_provider_request_id_header="X-Request-Id",
        ai_companion_provider_replay_retention_seconds=604_800,
        ai_companion_max_input_bytes=100_000,
        ai_companion_max_output_bytes=20_000,
    )


def _seed_user(db_session_factory: sessionmaker[Session], *, token: str) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '学习者')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id,
                  user_id,
                  session_token_hash,
                  device_id,
                  expires_at
                ) values (
                  cast(:session_id as uuid),
                  cast(:user_id as uuid),
                  :session_token_hash,
                  'ios-test',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "session_token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.commit()
    return user_id


def _verified_transaction() -> VerifiedAppStoreTransaction:
    return VerifiedAppStoreTransaction(
        product_id="gongkao_ai_pro_monthly",
        transaction_id="tx_unused",
        original_transaction_id="otx_unused",
        web_order_line_item_id=None,
        environment="Sandbox",
        purchase_date=datetime.now(UTC),
        expires_date=datetime.now(UTC) + timedelta(days=30),
        revocation_date=None,
        app_account_token=None,
        raw_payload={},
    )


def _seed_knowledge_map(
    db_session_factory: sessionmaker[Session],
    *,
    fresh_until: datetime | None = None,
) -> None:
    with db_session_factory() as session:
        MethodCardRepository(session).upsert_cards([_companion_shared_method_card()])
        seed_released_module_vocabulary(
            session,
            version="vocab@companion-tests-current",
            aliases={
                "言语理解": "verbal_comprehension",
                "逻辑判断": "judgment_reasoning",
                "资科分析": "data_analysis",
            },
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id,
                  release_version,
                  exam_scope,
                  track,
                  source_data_version,
                  vocab_version,
                  generator_version,
                  source_fingerprint,
                  generated_at,
                  fresh_until,
                  status,
                  node_count,
                  activated_at,
                  retired_at
                ) values (
                  '11111111-1111-4111-8111-111111111111'::uuid,
                  'knowledge-map@companion-tests-current',
                  'guokao',
                  'xingce',
                  'questions@companion-tests-current',
                  'vocab@companion-tests-current',
                  'knowledge-map-generator@companion-tests',
                  repeat('a', 64),
                  now() - interval '1 day',
                  cast(:fresh_until as timestamptz),
                  'draft',
                  0,
                  null,
                  null
                )
                """
            ),
            {
                "fresh_until": fresh_until or datetime.now(UTC) + timedelta(days=7),
            },
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id,
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status
                ) values
                  (
                    '11111111-1111-4111-8111-111111111111'::uuid,
                    'km_growth',
                    'guokao',
                    'xingce',
                    '资料分析',
                    'knowledge_point',
                    'knowledge_point',
                    '增长率',
                    '行测/资料分析/增长率',
                    'text_supported',
                    2022,
                    2026,
                    0.42,
                    30,
                    true,
                    '["mc_growth"]'::jsonb,
                    '{}'::jsonb,
                    'published'
                  ),
                  (
                    '11111111-1111-4111-8111-111111111111'::uuid,
                    'km_graphic',
                    'guokao',
                    'xingce',
                    '判断推理',
                    'knowledge_point',
                    'knowledge_point',
                    '图形推理',
                    '行测/判断推理/图形推理',
                    'image_unsupported',
                    2022,
                    2026,
                    0.2,
                    12,
                    true,
                    '[]'::jsonb,
                    '{}'::jsonb,
                    'published'
                  )
                on conflict (release_id, node_id) do nothing
                """
            )
        )
        seed_minimal_release_binding(
            session,
            release_id="11111111-1111-4111-8111-111111111111",
            node_id="km_growth",
            question_id="q-companion-current-growth",
        )
        activate_knowledge_release(
            session,
            release_id="11111111-1111-4111-8111-111111111111",
            activated_at=datetime.now(UTC) - timedelta(hours=12),
        )
        session.commit()


def _companion_shared_method_card() -> MethodCardRecord:
    return MethodCardRecord(
        id="mc_growth",
        exam_scope="guokao",
        track="xingce",
        module_name="资料分析",
        tag_path="增长率",
        title="增长率三步法",
        recognition="看到同比/增长率",
        applicability={},
        method_steps=["定位基期", "计算增长"],
        error_signatures=[],
        fix_action="先写公式再代数。",
        time_budget_sec=90,
        accuracy_rule="公式正确且单位一致",
        pitfall=None,
        author_note="test",
        validation={},
        priority=5,
        status="published",
    )


def _seed_retired_duplicate_knowledge_map(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as session:
        MethodCardRepository(session).upsert_cards([_companion_shared_method_card()])
        seed_released_module_vocabulary(
            session,
            version="vocab@companion-tests-retired",
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id,
                  release_version,
                  exam_scope,
                  track,
                  source_data_version,
                  vocab_version,
                  generator_version,
                  source_fingerprint,
                  generated_at,
                  fresh_until,
                  status,
                  node_count,
                  activated_at,
                  retired_at
                ) values (
                  '22222222-2222-4222-8222-222222222222'::uuid,
                  'knowledge-map@companion-tests-retired',
                  'guokao',
                  'xingce',
                  'questions@companion-tests-retired',
                  'vocab@companion-tests-retired',
                  'knowledge-map-generator@companion-tests',
                  repeat('b', 64),
                  now() - interval '3 days',
                  now() - interval '2 days',
                  'draft',
                  0,
                  null,
                  null
                )
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id,
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status
                ) values (
                  '22222222-2222-4222-8222-222222222222'::uuid,
                  'km_growth',
                  'guokao',
                  'xingce',
                  '资料分析',
                  'knowledge_point',
                  'knowledge_point',
                  '增长率',
                  '历史发布/资料分析/增长率',
                  'text_supported',
                  2021,
                  2025,
                  0.99,
                  999,
                  true,
                  '["mc_growth"]'::jsonb,
                  '{}'::jsonb,
                  'published'
                )
                """
            )
        )
        seed_minimal_release_binding(
            session,
            release_id="22222222-2222-4222-8222-222222222222",
            node_id="km_growth",
            question_id="q-companion-retired-growth",
        )
        activate_knowledge_release(
            session,
            release_id="22222222-2222-4222-8222-222222222222",
            activated_at=datetime.now(UTC)
            - timedelta(
                days=2,
                hours=12,
            ),
        )
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'retired', retired_at = now() - interval '2 days'
                where release_id =
                      '22222222-2222-4222-8222-222222222222'::uuid
                  and status = 'active'
                """
            )
        )
        session.commit()


def _switch_active_knowledge_map_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as session:
        seed_released_module_vocabulary(
            session,
            version="vocab@companion-tests-next",
            aliases={"资科分析": "data_analysis"},
        )
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set
                  status = 'retired',
                  retired_at = statement_timestamp()
                where release_id =
                      '11111111-1111-4111-8111-111111111111'::uuid
                  and status = 'active';

                insert into product.knowledge_map_releases (
                  release_id,
                  release_version,
                  exam_scope,
                  track,
                  source_data_version,
                  vocab_version,
                  generator_version,
                  source_fingerprint,
                  generated_at,
                  fresh_until,
                  status,
                  node_count,
                  activated_at,
                  retired_at
                ) values (
                  '33333333-3333-4333-8333-333333333333'::uuid,
                  'knowledge-map@companion-tests-next',
                  'guokao',
                  'xingce',
                  'questions@companion-tests-next',
                  'vocab@companion-tests-next',
                  'knowledge-map-generator@companion-tests',
                  repeat('c', 64),
                  statement_timestamp() - interval '1 hour',
                  statement_timestamp() + interval '7 days',
                  'draft',
                  0,
                  null,
                  null
                );

                insert into product.knowledge_map_nodes (
                  release_id,
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status
                ) values (
                  '33333333-3333-4333-8333-333333333333'::uuid,
                  'km_growth',
                  'guokao',
                  'xingce',
                  '资料分析',
                  'knowledge_point',
                  'knowledge_point',
                  '增长率',
                  '新发布/资料分析/增长率（不得重解释旧错题）',
                  'text_supported',
                  2022,
                  2026,
                  0.95,
                  300,
                  true,
                  '["mc_growth"]'::jsonb,
                  '{"test":"next-release"}'::jsonb,
                  'published'
                );

                """
            )
        )
        seed_minimal_release_binding(
            session,
            release_id="33333333-3333-4333-8333-333333333333",
            node_id="km_growth",
            question_id="q-companion-next-growth",
        )
        activate_knowledge_release(
            session,
            release_id="33333333-3333-4333-8333-333333333333",
        )
        session.commit()


def _seed_profile_and_context(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    _seed_knowledge_map(db_session_factory)
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id,
                  target_exam,
                  position_group,
                  baseline_source,
                  baseline_xingce_score,
                  baseline_shenlun_score,
                  onboarding_complete
                ) values (
                  cast(:user_id as uuid),
                  '国考',
                  '中央机关综合管理岗',
                  'self_report',
                  61.0,
                  55.0,
                  true
                )
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id,
                  domain,
                  status,
                  module_name,
                  stem,
                  answer
                ) values (
                  'q_growth_wrong',
                  'objective',
                  'active',
                  '资料分析',
                  '增长率题',
                  'B'
                )
                """
            )
        )
        session.execute(
            text(
                """
                insert into learner.practice_answers (
                  answer_id,
                  user_id,
                  task_id,
                  question_id,
                  selected_answer,
                  correct_answer,
                  correct,
                  answered_at
                ) values (
                  gen_random_uuid(),
                  cast(:user_id as uuid),
                  'task-growth',
                  'q_growth_wrong',
                  'A',
                  'B',
                  false,
                  now()
                )
                """
            ),
            {"user_id": user_id},
        )
        session.commit()


def _read_companion_context(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> CompanionContextRecord:
    with factory() as session:
        return CompanionRepository(session).get_context(
            user_id=user_id,
            node_id=None,
        )


def _seed_companion_canonical_evidence(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> dict[str, str]:
    task_id = str(uuid4())
    material_id = str(uuid4())
    rubric_id = str(uuid4())
    other_material_id = str(uuid4())
    other_rubric_id = str(uuid4())
    attempt_id = str(uuid4())
    answer_id = str(uuid4())
    expired_current_estimate_id = str(uuid4())
    position_release_id = str(uuid4())
    raw_answer = "RAW_ANSWER_V1_MUST_NOT_ENTER_COMPANION"
    now = datetime.now(UTC)
    evidence = {
        "user": user_id,
        "task": task_id,
        "material": material_id,
        "rubric": rubric_id,
        "other_material": other_material_id,
        "other_rubric": other_rubric_id,
        "attempt": attempt_id,
        "answer_v1": answer_id,
        "expired_current_estimate": expired_current_estimate_id,
        "raw_answer_v1": raw_answer,
    }
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status
                ) values (
                  cast(:release_id as uuid), 'guokao', 2027,
                  'companion-rules-v1', 'companion-positions-v1',
                  'draft'
                )
                """
            ),
            {"release_id": position_release_id},
        )
        session.execute(
            text(
                """
                insert into product.position_release_windows (
                  window_id, release_id, window_kind, sequence_no,
                  opens_at, closes_at, official_source_url,
                  official_source_title, official_source_published_at,
                  official_source_received_at, official_source_sha256,
                  official_source_receipt
                ) values (
                  gen_random_uuid(), cast(:release_id as uuid), 'primary', 1,
                  :opens_at, :closes_at,
                  'https://example.gov.cn/guokao/2027/announcement',
                  '2027年度考试录用公务员公告',
                  :published_at, :received_at, repeat('d', 64),
                  'companion-fixture-primary'
                )
                """
            ),
            {
                "release_id": position_release_id,
                "opens_at": now - timedelta(days=1),
                "closes_at": now + timedelta(days=30),
                "published_at": now - timedelta(days=3),
                "received_at": now - timedelta(days=2),
            },
        )
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'active', activated_at = :activated_at
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": position_release_id, "activated_at": now},
        )
        session.execute(
            text(
                """
                insert into learning.ability_estimate_versions (
                  estimate_version_id, user_id, exam_code, exam_year, status,
                  shenlun_lower, shenlun_recommended, shenlun_upper,
                  confidence, source_evidence_ids, evidence_summary,
                  algorithm_version, input_fingerprint, generated_at, valid_until
                ) values
                  (
                    cast(:old_estimate_id as uuid), cast(:user_id as uuid),
                    'guokao', 2026, 'partial', 40, 45, 50, 'medium',
                    '[]'::jsonb, '{}'::jsonb, 'companion-test-v1',
                    :old_fingerprint, :now, :future
                  ),
                  (
                    cast(:expired_estimate_id as uuid), cast(:user_id as uuid),
                    'guokao', 2027, 'partial', 50, 55, 60, 'medium',
                    '[]'::jsonb, '{}'::jsonb, 'companion-test-v1',
                    :expired_fingerprint, :expired_generated, :expired_until
                  )
                """
            ),
            {
                "old_estimate_id": str(uuid4()),
                "expired_estimate_id": expired_current_estimate_id,
                "user_id": user_id,
                "old_fingerprint": _companion_test_hash("old-estimate"),
                "expired_fingerprint": _companion_test_hash("expired-current-estimate"),
                "now": now,
                "future": now + timedelta(days=30),
                "expired_generated": now - timedelta(days=2),
                "expired_until": now - timedelta(days=1),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type, prompt,
                  prompt_sha256, word_limit, maximum_score, state
                ) values (
                  cast(:task_id as uuid), :task_key, 1, 'summary',
                  '请根据材料概括主要问题。', :prompt_sha, 200, 20, 'published'
                )
                """
            ),
            {
                "task_id": task_id,
                "task_key": f"companion-task:{task_id}",
                "prompt_sha": _companion_test_hash("companion-prompt"),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_material_bundle_versions (
                  material_bundle_version_id, task_version_id, task_key,
                  version_no, completeness, source_kind, content_sha256
                ) values
                  (
                    cast(:material_id as uuid), cast(:task_id as uuid),
                    :task_key, 1, 'complete', 'official', :material_sha
                  ),
                  (
                    cast(:other_material_id as uuid), cast(:task_id as uuid),
                    :task_key, 2, 'complete', 'official', :other_material_sha
                  )
                """
            ),
            {
                "material_id": material_id,
                "other_material_id": other_material_id,
                "task_id": task_id,
                "task_key": f"companion-task:{task_id}",
                "material_sha": _companion_test_hash("companion-material"),
                "other_material_sha": _companion_test_hash("companion-other-material"),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key,
                  material_bundle_version_id, version_no, source_kind,
                  verification_status, state, total_maximum, criteria, points,
                  content_sha256
                ) values
                  (
                    cast(:rubric_id as uuid), cast(:task_id as uuid), :task_key,
                    cast(:material_id as uuid), 1, 'official', 'verified',
                    'published', 20, '[{}]'::jsonb, '[{}]'::jsonb, :rubric_sha
                  ),
                  (
                    cast(:other_rubric_id as uuid), cast(:task_id as uuid),
                    :task_key, cast(:other_material_id as uuid), 2, 'official',
                    'verified', 'published', 20, '[{}]'::jsonb, '[{}]'::jsonb,
                    :other_rubric_sha
                  )
                """
            ),
            {
                "rubric_id": rubric_id,
                "other_rubric_id": other_rubric_id,
                "task_id": task_id,
                "task_key": f"companion-task:{task_id}",
                "material_id": material_id,
                "other_material_id": other_material_id,
                "rubric_sha": _companion_test_hash("companion-rubric"),
                "other_rubric_sha": _companion_test_hash("companion-other-rubric"),
            },
        )
        session.execute(
            text(
                """
                insert into learning.essay_attempts (
                  attempt_id, command_id, user_id, task_version_id, task_key,
                  material_bundle_version_id, rubric_version_id, source_context,
                  timed_status, input_fingerprint, submitted_at
                ) values (
                  cast(:attempt_id as uuid), :command_id, cast(:user_id as uuid),
                  cast(:task_id as uuid), :task_key, cast(:material_id as uuid),
                  cast(:rubric_id as uuid), 'in_program', 'untimed',
                  :input_fingerprint, :submitted_at
                )
                """
            ),
            {
                "attempt_id": attempt_id,
                "command_id": f"companion-attempt:{attempt_id}",
                "user_id": user_id,
                "task_id": task_id,
                "task_key": f"companion-task:{task_id}",
                "material_id": material_id,
                "rubric_id": rubric_id,
                "input_fingerprint": _companion_test_hash("companion-attempt-v1"),
                "submitted_at": now - timedelta(minutes=10),
            },
        )
        session.execute(
            text(
                """
                insert into learning.essay_answer_versions (
                  answer_version_id, command_id, attempt_id, user_id,
                  task_version_id, version_no, source_type, answer_text,
                  answer_sha256, input_fingerprint, created_at
                ) values (
                  cast(:answer_id as uuid), :command_id,
                  cast(:attempt_id as uuid), cast(:user_id as uuid),
                  cast(:task_id as uuid), 1, 'typed', :answer_text,
                  :answer_sha, :input_fingerprint, :created_at
                )
                """
            ),
            {
                "answer_id": answer_id,
                "command_id": f"companion-answer:{answer_id}",
                "attempt_id": attempt_id,
                "user_id": user_id,
                "task_id": task_id,
                "answer_text": raw_answer,
                "answer_sha": _companion_test_hash(raw_answer),
                "input_fingerprint": _companion_test_hash("companion-answer-v1"),
                "created_at": now - timedelta(minutes=9),
            },
        )
    _insert_companion_review(
        factory,
        evidence=evidence,
        answer_id=answer_id,
        marker="valid-current-answer-v1",
        generated_at=now - timedelta(minutes=8),
        valid_until=now + timedelta(days=30),
    )
    _insert_companion_reflection(
        factory,
        evidence=evidence,
        answer_id=answer_id,
        material_id=material_id,
        rubric_id=rubric_id,
        marker="current-answer-v1",
    )
    return evidence


def _insert_current_shenlun_estimate(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    exam_year: int,
    recommended: int,
    supersedes_estimate_id: str | None = None,
) -> str:
    estimate_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.ability_estimate_versions (
                  estimate_version_id, user_id, exam_code, exam_year, status,
                  shenlun_lower, shenlun_recommended, shenlun_upper,
                  confidence, source_evidence_ids, evidence_summary,
                  algorithm_version, input_fingerprint,
                  supersedes_estimate_version_id, generated_at, valid_until
                ) values (
                  cast(:estimate_id as uuid), cast(:user_id as uuid),
                  'guokao', :exam_year, 'partial', :lower, :recommended, :upper,
                  'medium', '[]'::jsonb, '{}'::jsonb, 'companion-test-v1',
                  :fingerprint, cast(:supersedes_id as uuid), :generated_at,
                  :valid_until
                )
                """
            ),
            {
                "estimate_id": estimate_id,
                "user_id": user_id,
                "exam_year": exam_year,
                "lower": recommended - 5,
                "recommended": recommended,
                "upper": recommended + 5,
                "fingerprint": _companion_test_hash(f"current-estimate:{estimate_id}"),
                "supersedes_id": supersedes_estimate_id,
                "generated_at": datetime.now(UTC),
                "valid_until": datetime.now(UTC) + timedelta(days=30),
            },
        )
    return estimate_id


def _insert_companion_answer(
    factory: sessionmaker[Session],
    *,
    evidence: dict[str, str],
    version_no: int,
    supersedes_answer_id: str,
    answer_text: str,
) -> str:
    answer_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.essay_answer_versions (
                  answer_version_id, command_id, attempt_id, user_id,
                  task_version_id, version_no, source_type, answer_text,
                  answer_sha256, input_fingerprint,
                  supersedes_answer_version_id, created_at
                ) values (
                  cast(:answer_id as uuid), :command_id,
                  cast(:attempt_id as uuid), cast(:user_id as uuid),
                  cast(:task_id as uuid), :version_no, 'typed', :answer_text,
                  :answer_sha, :input_fingerprint,
                  cast(:supersedes_answer_id as uuid), :created_at
                )
                """
            ),
            {
                "answer_id": answer_id,
                "command_id": f"companion-answer:{answer_id}",
                "attempt_id": evidence["attempt"],
                "user_id": evidence["user"],
                "task_id": evidence["task"],
                "version_no": version_no,
                "answer_text": answer_text,
                "answer_sha": _companion_test_hash(answer_text),
                "input_fingerprint": _companion_test_hash(f"companion-answer:{answer_id}"),
                "supersedes_answer_id": supersedes_answer_id,
                "created_at": datetime.now(UTC),
            },
        )
    return answer_id


def _insert_companion_review(
    factory: sessionmaker[Session],
    *,
    evidence: dict[str, str],
    answer_id: str,
    marker: str,
    generated_at: datetime,
    valid_until: datetime,
) -> str:
    review_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.essay_review_versions (
                  review_version_id, command_id, attempt_id, user_id,
                  task_version_id, answer_version_id, material_bundle_version_id,
                  rubric_version_id, status, review_scope,
                  thinking_evidence_available, readiness, score_lower,
                  score_recommended, score_upper, score_maximum, score_confidence,
                  score_confidence_reasons, grounding_contract_version,
                  grounding_report, dimensions, point_coverage, claim_evidence,
                  findings, rewrite_plan, contract_version,
                  provider_schema_version, rubric_contract_version,
                  prompt_version, provider, model, input_fingerprint,
                  execution_fingerprint, execution_attempt_no,
                  output_fingerprint, generated_at, valid_until
                ) values (
                  cast(:review_id as uuid), :command_id,
                  cast(:attempt_id as uuid), cast(:user_id as uuid),
                  cast(:task_id as uuid), cast(:answer_id as uuid),
                  cast(:material_id as uuid), cast(:rubric_id as uuid),
                  'completed', 'grounded_full', false, '{}'::jsonb,
                  10, 12, 14, 20, 'medium', '["grounded"]'::jsonb,
                  'essay-grounding-v2',
                  '{"verified": true, "eligibleForLearning": true}'::jsonb,
                  '[{}]'::jsonb, '[{}]'::jsonb, '[]'::jsonb,
                  jsonb_build_array(jsonb_build_object(
                    'type', 'structure', 'severity', 'high',
                    'diagnosis', cast(:marker as text), 'action', '按结构重写',
                    'attribution', 'answer'
                  )),
                  '{}'::jsonb, 'essay-review-v2', 'provider-schema-v1',
                  'rubric-v1', 'prompt-v1', 'provider', 'model',
                  :input_fingerprint, :execution_fingerprint, 1,
                  :output_fingerprint, :generated_at, :valid_until
                )
                """
            ),
            {
                "review_id": review_id,
                "command_id": f"companion-review:{review_id}",
                "attempt_id": evidence["attempt"],
                "user_id": evidence["user"],
                "task_id": evidence["task"],
                "answer_id": answer_id,
                "material_id": evidence["material"],
                "rubric_id": evidence["rubric"],
                "marker": marker,
                "input_fingerprint": _companion_test_hash(f"companion-review-input:{review_id}"),
                "execution_fingerprint": _companion_test_hash(
                    f"companion-review-execution:{review_id}"
                ),
                "output_fingerprint": _companion_test_hash(f"companion-review-output:{review_id}"),
                "generated_at": generated_at,
                "valid_until": valid_until,
            },
        )
    return review_id


def _insert_companion_reflection(
    factory: sessionmaker[Session],
    *,
    evidence: dict[str, str],
    answer_id: str,
    material_id: str,
    rubric_id: str,
    marker: str,
) -> str:
    artifact_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.essay_learning_artifact_versions (
                  artifact_version_id, command_id, user_id, artifact_type,
                  scope_key, task_version_id, material_bundle_version_id,
                  rubric_version_id, answer_version_id, version_no, payload,
                  payload_sha256, input_fingerprint, created_at
                ) values (
                  cast(:artifact_id as uuid), :command_id,
                  cast(:user_id as uuid), 'reflection', :scope_key,
                  cast(:task_id as uuid), cast(:material_id as uuid),
                  cast(:rubric_id as uuid), cast(:answer_id as uuid), 1,
                  jsonb_build_object(
                    'schemaType', 'reflection',
                    'causeCodes', jsonb_build_array('test_cause'),
                    'whatIWillChange', cast(:marker as text),
                    'verificationPlan', '下一题验证'
                  ),
                  :payload_sha, :input_fingerprint, :created_at
                )
                """
            ),
            {
                "artifact_id": artifact_id,
                "command_id": f"companion-reflection:{artifact_id}",
                "user_id": evidence["user"],
                "scope_key": _companion_test_hash(f"companion-reflection-scope:{artifact_id}"),
                "task_id": evidence["task"],
                "material_id": material_id,
                "rubric_id": rubric_id,
                "answer_id": answer_id,
                "marker": marker,
                "payload_sha": _companion_test_hash(f"companion-reflection-payload:{artifact_id}"),
                "input_fingerprint": _companion_test_hash(
                    f"companion-reflection-input:{artifact_id}"
                ),
                "created_at": datetime.now(UTC),
            },
        )
    return artifact_id


def _insert_companion_orphan_attempt(
    factory: sessionmaker[Session],
    *,
    evidence: dict[str, str],
    submitted_at: datetime,
) -> str:
    attempt_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.essay_attempts (
                  attempt_id, command_id, user_id, task_version_id, task_key,
                  material_bundle_version_id, rubric_version_id, source_context,
                  timed_status, input_fingerprint, submitted_at
                ) values (
                  cast(:attempt_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:task_id as uuid), :task_key,
                  cast(:material_id as uuid), cast(:rubric_id as uuid),
                  'in_program', 'untimed', :input_fingerprint, :submitted_at
                )
                """
            ),
            {
                "attempt_id": attempt_id,
                "command_id": f"companion-orphan-attempt:{attempt_id}",
                "user_id": evidence["user"],
                "task_id": evidence["task"],
                "task_key": f"companion-task:{evidence['task']}",
                "material_id": evidence["material"],
                "rubric_id": evidence["rubric"],
                "input_fingerprint": _companion_test_hash(f"companion-orphan-attempt:{attempt_id}"),
                "submitted_at": submitted_at,
            },
        )
    return attempt_id


def _companion_test_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
