from __future__ import annotations

import ast
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.planning.repository import PlanningRepository
from app.domains.planning.candidate_input_repository import (
    CandidateInputRepositoryMixin,
)
from app.domains.planning.cause_training import direction_from_codes
from app.domains.planning.service import PlanningService
from app.domains.knowledge.module_vocabulary import ReleasedModuleVocabulary
from app.domains.calibration.repository import CalibrationRepository
from app.domains.calibration.service import project_knowledge_point_weaknesses
from app.domains.task_runtime.xingce_attempt.contracts import (
    XingceSelectionIntentError,
    freeze_selection_intent,
    validate_selection_intent,
)
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from tests.test_planning_routes import NOW
from tests.test_planning_xingce_assessment_facts import _insert_assessment_facts
from tests.test_xingce_cause_agent_workflow import _reflect, _run_one
from tests.test_xingce_planning_closure import _submitted_attempt
from tests.test_xingce_question_cause_candidates import (
    _PerQuestionProvider,
    _session_owner,
)
from tests.test_xingce_targeted_knowledge_selection import (
    _node,
    _seed_targeted_learner,
    test_targeted_selection_is_user_isolated_and_does_not_repeat_exposed_questions as _assert_legacy_user_exposure_isolation,
)


def test_planning_freezes_sample_sufficient_weakness_and_known_difficulty(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = NOW
    user_id = _seed_targeted_learner(
        db_session_factory,
        token="learning-loop-selection-token",
        now=now,
        nodes=(
            _node(
                "weak-knowledge-node",
                "knowledge_point",
                "knowledge_point",
                "中心理解",
                0.92,
                20,
            ),
        ),
        tags={"中心理解": tuple(range(1, 21))},
    )
    planning = PlanningService()
    with db_session_factory.begin() as session:
        initial = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=now,
            local_date=now.date(),
        )
        for index in range(1, 21):
            session.execute(
                text(
                    """
                    insert into product.question_tags (
                      question_tag_id, question_id, tag_type, tag_value,
                      sort_order, evidence_link_count
                    ) values (
                      :tag_id, :question_id, 'knowledge_point', '中心理解', 1, 1
                    )
                    """
                ),
                {
                    "tag_id": f"selection-knowledge-tag-{index}",
                    "question_id": f"planning-shared-question-{index}",
                },
            )
        _insert_assessment_facts(
            session,
            user_id=user_id,
            plan=initial,
            question_results=[
                (f"planning-shared-question-{index}", index <= 2)
                for index in range(1, 11)
            ],
        )
        shadow_id = str(uuid4())
        session.execute(
            text(
                """
                insert into learning.ability_estimate_v2_shadow (
                  shadow_estimate_id, user_id, subject, theta, theta_se,
                  ci_lower, ci_upper, sample_size, insufficient_sample,
                  strata, mapped_v1_xingce_score, v1_snapshot, divergence,
                  algorithm_version, computed_at
                ) values (
                  cast(:shadow_id as uuid), cast(:user_id as uuid), 'xingce',
                  0, 0.5, -0.98, 0.98, 10, false, '{}'::jsonb, 55,
                  null, '{}'::jsonb, 'ability-estimate-v2-shadow-1', :now
                )
                """
            ),
            {"shadow_id": shadow_id, "user_id": user_id, "now": now},
        )
        for index in range(11, 21):
            session.execute(
                text(
                    """
                    insert into product.question_occurrences (
                      occurrence_id, question_id, occurrence_correct_ratio,
                      occurrence_sample_size
                    ) values (
                      :occurrence_id, :question_id, :ratio, 100
                    )
                    on conflict (occurrence_id) do update
                       set occurrence_correct_ratio = excluded.occurrence_correct_ratio,
                           occurrence_sample_size = excluded.occurrence_sample_size
                    """
                ),
                {
                    "occurrence_id": f"selection-occurrence-{index}",
                    "question_id": f"planning-shared-question-{index}",
                    "ratio": 0.5 if index == 11 else 0.9,
                },
            )

    next_day = now + timedelta(days=1)
    with db_session_factory.begin() as session:
        repository = PlanningRepository()
        goal = repository.goal_and_ability(session, user_id=user_id)
        snapshot = repository.objective_snapshot(
            session,
            user_id=user_id,
            goal=goal,
            now=next_day,
        )
        assert snapshot.selection_intent is not None
        intent = validate_selection_intent(snapshot.selection_intent)
        assert intent["targetModule"] == "言语理解与表达"
        assert intent["targetKnowledgeNode"]["nodeId"] == "weak-knowledge-node"
        assert intent["difficultyPolicy"] == {
            "state": "known",
            "targetTheta": 0.0,
            "sourceVersionId": shadow_id,
            "algorithmVersion": "ability-estimate-v2-shadow-1",
        }
        selection = XingceAttemptRepository().select_frozen_questions_for_intent(
            session,
            intent=intent,
            limit=3,
            user_id=user_id,
            now=next_day,
        )
        assert selection is not None
        assert selection.selection_result is not None
        assert selection.selection_result["hitTier"] == "same_knowledge"
        assert selection.selection_result["questionIds"][0] == (
            "planning-shared-question-11"
        )
        assert selection.selection_result["questionRankingEvidence"][0][
            "distanceToTargetTheta"
        ] == 0.0
        assert {
            str(item["question_id"]) for item in selection.questions
        }.isdisjoint(
            {f"planning-shared-question-{index}" for index in range(1, 11)}
        )
        exposure_count = session.execute(
            text(
                """
                select count(*)
                from learning.xingce_question_exposures
                where user_id = cast(:user_id as uuid)
                  and question_id = any(cast(:question_ids as text[]))
                """
            ),
            {
                "user_id": user_id,
                "question_ids": [
                    f"planning-shared-question-{index}" for index in range(1, 11)
                ],
            },
        ).scalar_one()
        assert int(exposure_count) == 10
        replay = XingceAttemptRepository().select_frozen_questions_for_intent(
            session,
            intent=intent,
            limit=3,
            user_id=user_id,
            now=next_day,
        )
        assert replay == selection
        verification_supply = XingceAttemptRepository().select_frozen_questions(
            session,
            module_name="言语理解与表达",
            limit=3,
            user_id=user_id,
            excluded_question_ids=[
                str(item["question_id"]) for item in selection.questions
            ],
        )
        assert {
            str(item["question_id"]) for item in verification_supply
        }.isdisjoint(
            {str(item["question_id"]) for item in selection.questions}
        )

        persisted = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=next_day,
            local_date=next_day.date(),
        )
        stored = session.execute(
            text(
                """
                select selection_intent
                from learning.daily_plan_tasks
                where plan_version_id = cast(:plan_version_id as uuid)
                  and stage = 'objective_practice'
                order by task_order
                limit 1
                """
            ),
            {"plan_version_id": str(persisted.plan_version_id)},
        ).scalar_one()
        assert stored == intent


def test_real_confirmed_question_candidate_drives_exact_selection_intent(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="learning-loop-grounded-cause-token",
        choices=["D", "B", "C"],
    )
    session_id = str(submitted["sessionId"])
    question_ids = [
        str(item["questionId"]) for item in submitted["bundle"]["questions"]
    ]
    first_question_id = question_ids[0]
    review = submitted["review"]
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learning.xingce_task_sessions task_session
                   set safe_bundle = jsonb_set(
                     task_session.safe_bundle,
                     '{questions}',
                     (
                       select jsonb_agg(question.item - 'questionType'
                                        order by question.position)
                       from jsonb_array_elements(
                         task_session.safe_bundle -> 'questions'
                       ) with ordinality question(item, position)
                     )
                   )
                 where task_session.session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": session_id},
        )
    _reflect(client, auth, review, key="learning-loop-grounded-cause-reflection")
    _run_one(
        db_session_factory,
        _PerQuestionProvider({first_question_id: "stem_misread"}),
    )
    candidate_view = client.get(
        f"/api/v2/xingce/task-sessions/{session_id}",
        headers=auth,
    ).json()["data"]
    candidate = candidate_view["review"]["aiCandidate"]
    assert candidate["questionCandidates"] == [
        {
            "questionId": first_question_id,
            "status": "candidate",
            "causeCode": "stem_misread",
            "confidence": "medium",
            "evidenceRefs": candidate["questionCandidates"][0]["evidenceRefs"],
            "rationale": "依据该题作答证据与复盘原话的逐题判断。",
        }
    ]
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "learning-loop-grounded-cause-decision"},
        json={
            "expectedRevision": candidate_view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text
    user_id = _session_owner(db_session_factory, session_id=session_id)

    with db_session_factory.begin() as session:
        session.execute(text("set local session_replication_role = 'replica'"))
        release = session.execute(
            text(
                """
                select release_id::text
                from product.knowledge_map_releases
                where status = 'active'
                """
            )
        ).scalar_one()
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id, node_id, exam_scope, track, module_name,
                  node_type, tag_type, tag_value, display_path,
                  support_status, near5_window_start_year,
                  near5_window_end_year, near5_weight,
                  near5_occurrence_count, stable_for_old_questions,
                  method_card_ids, status
                ) values (
                  cast(:release_id as uuid), 'grounded-cause-micro-skill',
                  'guokao', 'xingce', '言语理解与表达',
                  'micro_skill', 'micro_skill', '限定词定位',
                  '言语理解与表达/限定词定位', 'text_supported',
                  2022, 2026, 0.95, 60, true, '[]'::jsonb, 'published'
                )
                """
            ),
            {"release_id": release},
        )
        # 复用发布内已冻结的题目内容绑定，只增加同发布的 micro-skill 映射。
        session.execute(
            text(
                """
                insert into product.knowledge_map_question_bindings (
                  release_id, node_id, question_id, content_version_id,
                  module_code, canonical_module, binding_kind,
                  source_tag_type, source_tag_value, delivery_eligible,
                  visual_required, shared_context_required, latest_year,
                  occurrence_count, latest_recent_exam_year,
                  recent_occurrence_count, latest_old_exam_year,
                  old_occurrence_count, binding_fingerprint, created_at
                )
                select
                  release_id, 'grounded-cause-micro-skill', question_id,
                  content_version_id, module_code, canonical_module, 'direct',
                  'micro_skill', '限定词定位', delivery_eligible,
                  visual_required, shared_context_required, latest_year,
                  occurrence_count, latest_recent_exam_year,
                  recent_occurrence_count, latest_old_exam_year,
                  old_occurrence_count, repeat('a', 64), created_at
                from product.knowledge_map_question_bindings
                where release_id = cast(:release_id as uuid)
                  and node_id <> 'grounded-cause-micro-skill'
                """
            ),
            {"release_id": release},
        )
        manifest = session.execute(
            text(
                """
                select binding_count, binding_fingerprint
                from product.knowledge_map_question_binding_manifest(
                  cast(:release_id as uuid)
                )
                """
            ),
            {"release_id": release},
        ).mappings().one()
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                   set question_binding_count = :binding_count,
                       question_binding_fingerprint = :binding_fingerprint
                 where release_id = cast(:release_id as uuid)
                """
            ),
            {
                "release_id": release,
                "binding_count": manifest["binding_count"],
                "binding_fingerprint": manifest["binding_fingerprint"],
            },
        )
        session.execute(
            text(
                """
                insert into product.question_tags (
                  question_tag_id, question_id, tag_type, tag_value,
                  sort_order, evidence_link_count
                )
                select 'grounded-knowledge-' || question_id, question_id,
                       'knowledge_point', '言语理解基础', 1, 1
                from product.questions
                where question_id like 'xingce-route-question-%'
                on conflict do nothing
                """
            )
        )
        planning = PlanningService()
        current_plan = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=datetime.now(UTC),
            local_date=datetime.now(UTC).date(),
        )
        _insert_assessment_facts(
            session,
            user_id=user_id,
            plan=current_plan,
            question_results=[
                (f"xingce-route-question-{index}", index <= 2)
                for index in range(1, 11)
            ],
        )

    with db_session_factory.begin() as session:
        repository = PlanningRepository()
        goal = repository.goal_and_ability(session, user_id=user_id)
        snapshot = repository.objective_snapshot(
            session,
            user_id=user_id,
            goal=goal,
            now=datetime.now(UTC) + timedelta(days=1),
        )
        intent = validate_selection_intent(snapshot.selection_intent)
        assert intent["targetKnowledgeNode"]["nodeId"] == "canonical-verbal-practice"
        grounded = intent["confirmedCauseFocus"]
        assert grounded["questionType"] == "single_choice"
        assert intent["targetMicroSkillNode"]["nodeId"] == (
            "grounded-cause-micro-skill"
        )
        assert grounded["candidateVersionId"] == candidate["candidateVersionId"]
        selection = XingceAttemptRepository().select_frozen_questions_for_intent(
            session,
            intent=intent,
            limit=3,
            user_id=user_id,
            now=datetime.now(UTC) + timedelta(days=1),
        )
        assert selection is not None
        assert selection.selection_result["hitTier"] == (
            "exact_knowledge_and_cause"
        )


def test_grounded_cause_tier_is_fixed_and_rich_evidence_never_uses_high_frequency() -> None:
    intent = _intent(
        knowledge_node="knowledge-node",
        micro_node="micro-node",
        question_type="single_choice",
        fallback_reason=None,
    )
    exact = _RecordingRepository(pool_sizes=[3])
    selection = exact.select_frozen_questions_for_intent(
        object(),
        intent=intent,
        limit=3,
        user_id="00000000-0000-0000-0000-000000000001",
        now=NOW,
    )
    assert selection is not None
    assert selection.selection_result is not None
    assert selection.selection_result["hitTier"] == "exact_knowledge_and_cause"
    assert exact.calls[0]["requiredNodeIds"] == (
        "knowledge-node",
        "micro-node",
    )
    assert exact.calls[0]["questionType"] == "single_choice"
    assert exact.calls[0]["selectionIdentity"] == intent["intentId"]

    exhausted = _RecordingRepository(pool_sizes=[0, 0, 0, 0])
    assert exhausted.select_frozen_questions_for_intent(
        object(),
        intent=intent,
        limit=3,
        user_id="00000000-0000-0000-0000-000000000001",
        now=NOW,
    ) is None
    assert len(exhausted.calls) == 4
    assert all(call["canonicalModules"] == ("言语理解与表达",) for call in exhausted.calls)


def test_cold_start_high_frequency_stays_inside_frozen_target_module() -> None:
    intent = _intent(
        knowledge_node=None,
        micro_node=None,
        question_type=None,
        fallback_reason="cold_start_no_grounded_evidence",
    )
    repository = _RecordingRepository(pool_sizes=[3])
    selection = repository.select_frozen_questions_for_intent(
        object(),
        intent=intent,
        limit=3,
        user_id="00000000-0000-0000-0000-000000000002",
        now=NOW,
    )
    assert selection is not None
    assert selection.selection_result is not None
    assert selection.selection_result["hitTier"] == "high_frequency_cold_start"
    assert repository.calls == [
        {
            "nodeId": None,
            "requiredNodeIds": None,
            "canonicalModules": ("言语理解与表达",),
            "questionType": None,
            "selectionIdentity": intent["intentId"],
        }
    ]


def test_missing_published_knowledge_node_falls_back_micro_then_module() -> None:
    intent = _intent(
        knowledge_node=None,
        micro_node="micro-node",
        question_type=None,
        fallback_reason="knowledge_node_unavailable",
    )
    repository = _RecordingRepository(pool_sizes=[0, 3])
    selection = repository.select_frozen_questions_for_intent(
        object(),
        intent=intent,
        limit=3,
        user_id="00000000-0000-0000-0000-000000000004",
        now=NOW,
    )
    assert selection is not None
    assert selection.selection_result["hitTier"] == "module"
    assert [call["nodeId"] for call in repository.calls] == ["micro-node", None]
    assert all(
        call["canonicalModules"] == ("言语理解与表达",)
        for call in repository.calls
    )


def test_grounded_question_type_changes_module_selection_before_explicit_fallback() -> None:
    intent = _intent(
        knowledge_node=None,
        micro_node=None,
        question_type="data_analysis",
        fallback_reason=None,
    )
    repository = _RecordingRepository(pool_sizes=[0, 3])
    selection = repository.select_frozen_questions_for_intent(
        object(),
        intent=intent,
        limit=3,
        user_id="00000000-0000-0000-0000-000000000005",
        now=NOW,
    )
    assert selection is not None
    assert [call["questionType"] for call in repository.calls] == [
        "data_analysis",
        None,
    ]
    assert selection.selection_result["hitTier"] == "module"
    assert selection.selection_result["skippedTiers"] == [
        {
            "tier": "module",
            "reason": "cause_question_type_supply_insufficient",
            "eligibleCount": 0,
        }
    ]


def test_grounded_cause_skips_cross_module_candidate_then_uses_matching_one() -> None:
    vocabulary = ReleasedModuleVocabulary(
        release_id="00000000-0000-0000-0000-000000000010",
        release_version="release-v1",
        source_data_version="source-v1",
        vocab_version="vocab-v1",
        fresh_until=datetime(2026, 8, 20, tzinfo=UTC),
        _canonical_by_raw_name=MappingProxyType(
            {
                "言语理解与表达": "言语理解与表达",
                "资料分析": "资料分析",
            }
        ),
        _raw_names_by_canonical=MappingProxyType(
            {
                "言语理解与表达": ("言语理解与表达",),
                "资料分析": ("资料分析",),
            }
        ),
    )
    direction = direction_from_codes("stem_misread", None)
    assert direction is not None
    session = _GroundedCauseSession()
    grounded = CandidateInputRepositoryMixin()._grounded_cause_selector(
        session,  # type: ignore[arg-type]
        user_id="00000000-0000-0000-0000-000000000001",
        cause_row={
            "confirmed_cause_version_id": "confirmed-1",
            "decision_version_id": "decision-1",
            "candidate_version_id": "candidate-1",
            "session_id": "00000000-0000-0000-0000-000000000002",
            "replacement_code": None,
            "candidate_payload": {
                "questionCandidates": [
                    {
                        "questionId": "cross-module-first",
                        "status": "candidate",
                        "causeCode": "stem_misread",
                    },
                    {
                        "questionId": "matching-second",
                        "status": "candidate",
                        "causeCode": "stem_misread",
                    },
                ]
            },
        },
        direction=direction,
        vocabulary=vocabulary,
        cause_module="言语理解与表达",
    )
    assert session.binding_queries == [
        "cross-module-first",
        "matching-second",
    ]
    assert grounded["questionType"] == "single_choice"
    assert grounded["microSkillNode"]["nodeId"] == "matching-micro"


def test_nine_samples_remain_insufficient_and_publish_no_weakness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        CalibrationRepository,
        "knowledge_point_accuracy",
        lambda _self, *, user_id: [
            {
                "module_name": "言语理解与表达",
                "tag_value": "中心理解",
                "sample_size": 9,
                "correct_count": 0,
            }
        ],
    )
    weaknesses, fingerprint = project_knowledge_point_weaknesses(
        object(),  # type: ignore[arg-type]
        user_id="00000000-0000-0000-0000-000000000003",
    )
    assert weaknesses == []
    assert len(fingerprint) == 64


def test_legacy_answered_exposed_and_cross_user_isolation_remain_locked(
    db_session_factory: sessionmaker[Session],
) -> None:
    _assert_legacy_user_exposure_isolation(db_session_factory)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(targetModule=""),
        lambda value: value.update(evidenceRefs=[]),
        lambda value: value.update(fallbackReason="invented"),
        lambda value: value.update(difficultyPolicy={"state": "known"}),
        lambda value: value.update(intentId="0" * 64),
        lambda value: value.update(extra="not-allowed"),
    ],
)
def test_selection_intent_malformed_payloads_fail_closed(mutate: Any) -> None:
    value = deepcopy(_intent(None, None, None, "cold_start_no_grounded_evidence"))
    mutate(value)
    with pytest.raises(XingceSelectionIntentError):
        validate_selection_intent(value)


def test_planning_does_not_import_learning_brain() -> None:
    root = Path(__file__).parents[1] / "app" / "domains" / "planning"
    imported: list[str] = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
    assert not any(name.startswith("app.domains.learning_brain") for name in imported)


class _RecordingRepository(XingceAttemptRepository):
    def __init__(self, *, pool_sizes: list[int]) -> None:
        self.pool_sizes = list(pool_sizes)
        self.calls: list[dict[str, Any]] = []

    def _select_eligible_questions(self, _session: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "nodeId": kwargs["node_id"],
                "requiredNodeIds": kwargs["required_node_ids"],
                "canonicalModules": kwargs["canonical_modules"],
                "questionType": kwargs["question_type"],
                "selectionIdentity": kwargs["selection_identity"],
            }
        )
        size = self.pool_sizes.pop(0)
        return [{"question_id": f"question-{index}"} for index in range(1, size + 1)]

    def _rank_intent_candidates(self, _session: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected = list(kwargs["questions"][: kwargs["limit"]])
        return selected, [
            {
                "questionId": item["question_id"],
                "rank": index,
                "anchorState": "unknown",
                "difficultyB": None,
                "distanceToTargetTheta": None,
                "stablePoolRank": index - 1,
            }
            for index, item in enumerate(selected, start=1)
        ]

    @staticmethod
    def _current_module_vocabulary(
        _session: Any,
        *,
        now: datetime,
    ) -> ReleasedModuleVocabulary:
        assert now == NOW
        canonical = "言语理解与表达"
        return ReleasedModuleVocabulary(
            release_id="00000000-0000-0000-0000-000000000010",
            release_version="release-v1",
            source_data_version="source-v1",
            vocab_version="vocab-v1",
            fresh_until=datetime(2026, 8, 20, tzinfo=UTC),
            _canonical_by_raw_name=MappingProxyType({canonical: canonical}),
            _raw_names_by_canonical=MappingProxyType({canonical: (canonical,)}),
        )


class _GroundedCauseResult:
    def __init__(self, *, scalar: Any = None, mapping: Any = None) -> None:
        self.scalar = scalar
        self.mapping = mapping

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def mappings(self) -> "_GroundedCauseResult":
        return self

    def one_or_none(self) -> Any:
        return self.mapping


class _GroundedCauseSession:
    def __init__(self) -> None:
        self.binding_queries: list[str] = []

    def execute(self, _statement: Any, params: dict[str, Any]) -> _GroundedCauseResult:
        question_id = params.get("question_id")
        if question_id is None:
            return _GroundedCauseResult(
                scalar={
                    "questions": [
                        {
                            "questionId": "cross-module-first",
                            "questionType": "data_analysis",
                        },
                        {
                            "questionId": "matching-second",
                            "questionType": "single_choice",
                        },
                    ]
                }
            )
        self.binding_queries.append(str(question_id))
        module = (
            "资料分析"
            if question_id == "cross-module-first"
            else "言语理解与表达"
        )
        return _GroundedCauseResult(
            mapping={
                "canonical_module": module,
                "node_id": (
                    "cross-micro"
                    if question_id == "cross-module-first"
                    else "matching-micro"
                ),
                "node_type": "micro_skill",
                "tag_type": "micro_skill",
                "tag_value": "stem-reading",
            }
        )


def _intent(
    knowledge_node: str | None,
    micro_node: str | None,
    question_type: str | None,
    fallback_reason: str | None,
) -> dict[str, Any]:
    release = {
        "releaseId": "00000000-0000-0000-0000-000000000010",
        "releaseVersion": "release-v1",
        "sourceDataVersion": "source-v1",
        "vocabVersion": "vocab-v1",
        "freshUntil": datetime(2026, 8, 20, tzinfo=UTC).isoformat(),
    }

    def node(node_id: str, node_type: str, tag_type: str) -> dict[str, str]:
        return {
            "releaseId": release["releaseId"],
            "nodeId": node_id,
            "nodeType": node_type,
            "tagType": tag_type,
            "tagValue": node_id,
        }

    return freeze_selection_intent(
        {
            "contractVersion": "xingce-selection-intent@v1",
            "knowledgeRelease": release,
            "targetModule": "言语理解与表达",
            "targetKnowledgeNode": (
                node(knowledge_node, "knowledge_point", "knowledge_point")
                if knowledge_node is not None
                else None
            ),
            "targetMicroSkillNode": (
                node(micro_node, "micro_skill", "micro_skill")
                if micro_node is not None
                else None
            ),
            "confirmedCauseFocus": (
                {
                    "causeCode": "knowledge_gap",
                    "focus": "reasoning",
                    "questionType": question_type,
                    "confirmedCauseVersionId": "cause-version-1",
                    "decisionVersionId": "decision-version-1",
                    "candidateVersionId": "candidate-version-1",
                }
                if micro_node is not None or question_type is not None
                else None
            ),
            "difficultyPolicy": {
                "state": "unknown",
                "reason": "missing_shadow_estimate",
                "sourceVersionId": None,
                "algorithmVersion": "ability-estimate-v2-shadow-1",
            },
            "noveltyPolicy": {
                "answered": "exclude",
                "exposed": "exclude",
                "scope": "current_user",
            },
            "fallbackReason": fallback_reason,
            "evidenceRefs": ["selection-policy:xingce-selection-intent@v1"],
        }
    )
