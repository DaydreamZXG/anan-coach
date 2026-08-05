from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.planning.cause_training import (
    CAUSE_TO_TRAINING_FOCUS,
    FOCUS_DISPLAY_NAME,
    direction_from_codes,
    leading_confirmed_cause_index,
    pick_leading_confirmed_cause,
    resolve_cause_training_focus,
)
from app.domains.planning.repository import PlanningRepository
from app.domains.planning.service import PlanningService
from tests.test_planning_routes import _seed_learner_and_published_essay_content
from tests.test_planning_xingce_assessment_facts import (
    _insert_active_knowledge_node,
    _insert_assessment_facts,
    _insert_objective_questions,
)
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _reflect, _run_one
from tests.test_xingce_planning_closure import _submitted_attempt
from tests.test_xingce_question_cause_candidates import _session_owner
from tests.test_xingce_targeted_knowledge_selection import _insert_complete_ability_estimate


def test_eight_cause_codes_each_resolve_to_a_training_direction() -> None:
    """8 类错因码都有确定的方向与展示名。"""

    assert set(CAUSE_TO_TRAINING_FOCUS) == {
        "stem_misread",
        "knowledge_gap",
        "method_selection",
        "calculation_error",
        "time_pressure",
        "guessing",
        "active_abandon",
        "other",
    }
    expected = {
        "stem_misread": ("stem_reading", "审题"),
        "knowledge_gap": ("knowledge_base", "知识点基础"),
        "method_selection": ("method_application", "方法应用"),
        "calculation_error": ("calculation", "计算"),
        "time_pressure": ("time_bounded", "限时"),
        "guessing": ("careful_selection", "稳选"),
        "active_abandon": ("core_drilling", "核心打底"),
        "other": ("balanced_drilling", "均衡"),
    }
    for code, (focus, display) in expected.items():
        assert resolve_cause_training_focus(code, None) == focus
        assert FOCUS_DISPLAY_NAME[focus] == display
        direction = direction_from_codes(code, None)
        assert direction is not None
        assert direction.focus == focus
        assert direction.display == display
    # 无结构化码的 user_replacement 不产生方向（回退模块级）。
    assert resolve_cause_training_focus("user_replacement", None) is None
    assert resolve_cause_training_focus(None, None) is None
    assert resolve_cause_training_focus("unknown_code", None) is None


def test_user_replacement_structured_code_resolves_and_unknown_falls_back() -> None:
    """结构化改写码同样解析方向；未选码改写不参与错因选题。"""

    assert resolve_cause_training_focus("user_replacement", "calculation_error") == "calculation"
    assert direction_from_codes("user_replacement", "method_selection") is not None
    assert direction_from_codes("user_replacement", None) is None
    # replacement_code 优先：即使 cause_code 是 other，方向仍取改写的计算方向。
    assert direction_from_codes("other", "calculation_error").focus == "calculation"


def test_leading_cause_prefers_specific_direction_then_latest() -> None:
    """多条错因时按方向顺序取主导；同方向取最新；全不可识别时回退。"""

    # 方向更靠前（更具体）优先于更靠后的兜底方向。
    assert pick_leading_confirmed_cause(
        [("other", None), ("calculation_error", None)]
    ).focus == "calculation"
    # 同方向：取最新一条（下标更大的那一条）。
    assert (
        leading_confirmed_cause_index([("stem_misread", None), ("stem_misread", None)]) == 1
    )
    # user_replacement（无可识别方向）在有明确方向时被忽略。
    assert (
        leading_confirmed_cause_index(
            [("user_replacement", None), ("calculation_error", None)]
        )
        == 1
    )
    # 全部无可识别方向 -> 不覆盖（回退模块级）。
    assert pick_leading_confirmed_cause([("user_replacement", None)]) is None
    assert pick_leading_confirmed_cause([]) is None


def test_confirmed_cause_selects_cause_module_over_statistical_weakest_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    """有已确认错因时，针对性训练按错因所在模块选题，而不是统计最弱模块。

    步骤：
    1. 完成一次真实行测（route 题组，言语理解模块题目），走完整复盘 + 错因
       确认，落库一条 replacement_code=calculation_error 的已确认错因；
    2. 造一批统计上更弱的「数量关系」错题事实（最近 60 天正确率低）；
    3. objective_snapshot 必须把 weakest_module 选成错因所在模块
       （言语理解与表达），并携带 caused_training_direction=calculation。
    """

    now = datetime.now(UTC).replace(hour=6, minute=0, second=0, microsecond=0)
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="caused-training-module-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="caused-training-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    ready = current.json()["data"]
    assert ready["state"] == "cause_decision_required"
    candidate_id = ready["review"]["aiCandidate"]["candidateVersionId"]
    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "caused-training-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "replace",
            "replacementCause": "数量关系这题是跳步算错了。",
            "replacementCode": "calculation_error",
        },
    )
    assert decided.status_code == 200, decided.text
    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])

    with db_session_factory.begin() as session:
        _insert_complete_ability_estimate(session, user_id=user_id, now=now)
        row = session.execute(
            text(
                "select cause_code, replacement_code "
                "from learning.xingce_confirmed_cause_versions"
            )
        ).mappings().one()
        assert dict(row) == {
            "cause_code": "user_replacement",
            "replacement_code": "calculation_error",
        }

    next_day = now + timedelta(days=1)
    with db_session_factory.begin() as session:
        planning = PlanningService()
        plan = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=next_day,
            local_date=next_day.date(),
        )
        # 造统计上更弱的「数量关系」事实：5 对 1 错。
        quantity_history = _insert_objective_questions(
            session,
            prefix="caused-weak-quantity",
            module_name="数量关系",
            count=6,
        )
        _insert_assessment_facts(
            session,
            user_id=user_id,
            plan=plan,
            question_results=[*zip(quantity_history, [True, False, False, False, False, False])],
        )

    with db_session_factory() as session:
        repository = PlanningRepository()
        goal = repository.goal_and_ability(session, user_id=user_id)
        snapshot = repository.objective_snapshot(
            session,
            user_id=user_id,
            goal=goal,
            now=next_day + timedelta(minutes=1),
        )

    assert snapshot.caused_training_direction is not None
    assert snapshot.caused_training_direction.focus == "calculation"
    assert snapshot.caused_training_direction.display == "计算"
    assert snapshot.caused_training_direction.cause_code == "user_replacement"
    # 错因所在模块（本次 route 题组为言语理解）优先于统计最弱的数量关系。
    assert snapshot.weakest_module == "言语理解与表达"


def test_no_confirmed_cause_falls_back_to_statistical_weakest_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    """没有已确认错因时，objective_snapshot 完全走原统计最弱模块逻辑。

    统计最弱模块的完整选择链已由 test_xingce_targeted_knowledge_selection
    覆盖；这里聚焦新增的错因方向：没有错因时必须返回 None，模块仍为统计最弱。
    """

    now = datetime.now(UTC).replace(hour=6, minute=0, second=0, microsecond=0)
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token="caused-fallback-token",
        include_objective_questions=False,
    )
    with db_session_factory.begin() as session:
        _insert_complete_ability_estimate(session, user_id=user_id, now=now)
        language_history = _insert_objective_questions(
            session,
            prefix="caused-fallback-language",
            module_name="言语理解",
            count=6,
        )
        _insert_active_knowledge_node(
            session,
            module_name="言语理解与表达",
            tag_value="中心理解",
            question_ids=language_history,
            now=now,
        )
        planning = PlanningService()
        plan = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=now,
            local_date=now.date(),
        )
        assert any(task.track == "xingce" for task in plan.tasks), [
            task.stage for task in plan.tasks
        ]
        _insert_assessment_facts(
            session,
            user_id=user_id,
            plan=plan,
            question_results=[*zip(language_history, [True, False, False, False, False, False])],
        )

    with db_session_factory() as session:
        repository = PlanningRepository()
        goal = repository.goal_and_ability(session, user_id=user_id)
        snapshot = repository.objective_snapshot(
            session,
            user_id=user_id,
            goal=goal,
            now=now + timedelta(days=1),
        )

    assert snapshot.caused_training_direction is None
    assert snapshot.weakest_module == "言语理解与表达"


def test_confirmed_cause_direction_surfaces_in_plan_candidates(
    db_session_factory: sessionmaker[Session],
) -> None:
    """已确认错因方向传导到 xingce-practice 候选（标题/理由/方向 key）。

    通过 objective_snapshot + build_dual_track_candidates 验证 DB → 快照 →
    候选的完整链路；候选层面不依赖日计划调度引擎（验证任务会先占位）。
    """

    now = datetime.now(UTC).replace(hour=6, minute=0, second=0, microsecond=0)
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="caused-training-plan-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="caused-training-plan-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    ready = current.json()["data"]
    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "caused-training-plan-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": ready["review"]["aiCandidate"]["candidateVersionId"],
            "decision": "replace",
            "replacementCause": "这次言语题是时间仓促蒙的。",
            "replacementCode": "guessing",
        },
    )
    assert decided.status_code == 200, decided.text
    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])

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

    from app.domains.planning.candidates import (
        build_dual_track_candidates,
        EssayPlanningSnapshot,
        GoalPlanningSnapshot,
    )

    candidates = build_dual_track_candidates(
        goal=GoalPlanningSnapshot(
            readiness="complete",
            positive_gap=True,
            goal_version_id="goal-v1",
            ability_estimate_version_id="ability-v1",
        ),
        objective=snapshot,
        essay=EssayPlanningSnapshot("content_unavailable", None),
        available_minutes=60,
    )
    practice = next(
        (
            item
            for item in candidates.candidates
            if item.stage == "objective_practice"
        ),
        None,
    )
    assert practice is not None, [item.stage for item in candidates.candidates]
    # 计划任务仍以模块为核心（_task_scope 兼容），但 title 带错因方向。
    assert practice.candidate_key.startswith("xingce-practice:")
    assert "稳选" in practice.title
    assert "稳选" in practice.reason
    assert practice.strategy_question_type == "careful_selection"
    assert practice.strategy_module_keys and practice.strategy_module_keys[0] == "言语理解与表达"
