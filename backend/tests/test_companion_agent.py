import pytest
from pydantic import ValidationError

from app.domains.ai.governance.contracts import IntentDecision
from app.domains.companion.agent import (
    CompanionActionRequest,
    execute_companion_actions,
    is_capacity_change_request,
    requested_weekly_schedule,
)
from app.domains.companion.service import _allowed_actions, _requested_plan_minutes


def planner_decision() -> IntentDecision:
    return IntentDecision.model_validate(
        {
            "scenarioKey": "planning_explanation",
            "respondingAgent": "planner",
            "answerType": "plan_explanation",
            "modelTier": "small",
            "policyDecision": "answer",
            "companionIntent": "thinking",
            "reasonCodes": ["test"],
        }
    )


def test_extracts_explicit_same_day_capacity_without_guessing() -> None:
    assert _requested_plan_minutes("今晚只能学1小时，帮我重新安排") == 60
    assert _requested_plan_minutes("今天只有90分钟") == 90
    assert _requested_plan_minutes("今天只能学1小时30分钟") == 90
    assert _requested_plan_minutes("今天只能学1小时半") == 90
    assert _requested_plan_minutes("今天休息，不要安排任务") == 0
    assert _requested_plan_minutes("帮我调整一下今天的计划") is None
    assert _requested_plan_minutes("我有8项任务") is None
    assert _requested_plan_minutes("今天学25小时") is None


def test_plan_action_carries_only_the_explicit_capacity_to_preview() -> None:
    action = _allowed_actions(
        decision=planner_decision(),
        question_text="今晚只能学1个半小时，重新安排",
    )[0]

    assert action.model_dump(by_alias=True) == {
        "kind": "adjust_plan_preview",
        "label": "预览调整为 90 分钟",
        "targetRef": "/pages/day-adjustment/index?source=anan&availableMinutes=90",
        "requiresConfirmation": True,
    }


def test_plan_action_keeps_manual_preview_when_no_capacity_is_stated() -> None:
    action = _allowed_actions(
        decision=planner_decision(),
        question_text="帮我调整一下今天的计划",
    )[0]

    assert action.target_ref == "/pages/day-adjustment/index"


def test_model_requested_capacity_action_must_match_the_user_value() -> None:
    outcome = execute_companion_actions(
        requests=[
            CompanionActionRequest(
                name="preview_daily_capacity",
                arguments={"availableMinutes": 90},
            )
        ],
        question_text="今晚只能学 90 分钟，帮我重新安排",
        fallback_actions=[],
    )

    assert outcome.succeeded_count == 1
    assert outcome.actions[0].target_ref == (
        "/pages/day-adjustment/index?source=anan&availableMinutes=90"
    )
    assert any(event["eventType"] == "succeeded" for event in outcome.events)


def test_model_cannot_invent_capacity_and_server_keeps_safe_fallback() -> None:
    fallback = _allowed_actions(
        decision=planner_decision(),
        question_text="今晚只能学 90 分钟，帮我重新安排",
    )
    outcome = execute_companion_actions(
        requests=[
            CompanionActionRequest(
                name="preview_daily_capacity",
                arguments={"availableMinutes": 600},
            )
        ],
        question_text="今晚只能学 90 分钟，帮我重新安排",
        fallback_actions=fallback,
    )

    assert outcome.succeeded_count == 0
    assert outcome.actions == tuple(fallback)
    assert any(event["eventType"] == "failed" for event in outcome.events)


def test_inferred_capacity_is_stripped_when_user_did_not_state_a_duration() -> None:
    outcome = execute_companion_actions(
        requests=[
            CompanionActionRequest(
                name="preview_daily_capacity",
                arguments={"availableMinutes": 60},
            )
        ],
        question_text="今天行测想学资料分析",
        fallback_actions=[],
    )

    assert outcome.succeeded_count == 1
    assert outcome.actions[0].target_ref == "/pages/day-adjustment/index"
    assert outcome.actions[0].label == "预览今日计划调整"
    assert not any(event["eventType"] == "failed" for event in outcome.events)


def test_action_registry_exposes_private_library_without_accepting_arguments() -> None:
    outcome = execute_companion_actions(
        requests=[CompanionActionRequest(name="open_private_library", arguments={})],
        question_text="打开我的资料库",
        fallback_actions=[],
    )

    assert outcome.actions[0].target_ref == "/pages/private-knowledge/index"
    assert outcome.actions[0].requires_confirmation is False


def test_capacity_mutation_intent_does_not_treat_explanation_as_a_write() -> None:
    assert is_capacity_change_request("今晚只能学一小时，帮我重新安排")
    assert is_capacity_change_request("今天休息，不要排任务")
    assert not is_capacity_change_request("为什么今天只有一小时？")


def test_provider_cannot_name_an_unpublished_action_tool() -> None:
    with pytest.raises(ValidationError):
        CompanionActionRequest.model_validate(
            {"name": "write_qualification", "arguments": {"eligible": True}}
        )


def test_extracts_only_explicit_weekly_schedule_days() -> None:
    request = requested_weekly_schedule("把周六安排成模考日，周日休息")

    assert request is not None
    assert request.mock_exam_day == "saturday"
    assert request.fixed_rest_day == "sunday"
    compact = requested_weekly_schedule("周六模考周日休息")
    assert compact is not None
    assert compact.mock_exam_day == "saturday"
    assert compact.fixed_rest_day == "sunday"
    assert requested_weekly_schedule("帮我重新安排这周") is None
    assert requested_weekly_schedule("周六既模考又休息") is None


def test_weekly_schedule_tool_opens_confirmed_prefill_only_for_user_days() -> None:
    outcome = execute_companion_actions(
        requests=[
            CompanionActionRequest(
                name="preview_weekly_schedule",
                arguments={"mockExamDay": "saturday", "fixedRestDay": "sunday"},
            )
        ],
        question_text="把周六安排成模考日，周日休息",
        fallback_actions=[],
    )

    assert outcome.succeeded_count == 1
    assert outcome.actions[0].requires_confirmation is True
    assert outcome.actions[0].target_ref == (
        "/pages/study-preference/index?source=anan&mockExamDay=saturday&fixedRestDay=sunday"
    )


def test_weekly_schedule_tool_rejects_a_model_invented_day() -> None:
    outcome = execute_companion_actions(
        requests=[
            CompanionActionRequest(
                name="preview_weekly_schedule",
                arguments={"mockExamDay": "friday", "fixedRestDay": "sunday"},
            )
        ],
        question_text="把周六安排成模考日，周日休息",
        fallback_actions=[],
    )

    assert outcome.succeeded_count == 0
    assert outcome.actions == ()


def test_position_goal_tool_requires_the_exact_id_in_the_current_message() -> None:
    position_id = "00000000-0000-0000-0000-000000009901"
    accepted = execute_companion_actions(
        requests=[
            CompanionActionRequest(
                name="open_position_goal",
                arguments={"positionId": position_id},
            )
        ],
        question_text=f"把岗位 {position_id} 设为目标岗位",
        fallback_actions=[],
    )
    rejected = execute_companion_actions(
        requests=[
            CompanionActionRequest(
                name="open_position_goal",
                arguments={"positionId": position_id},
            )
        ],
        question_text="帮我选一个目标岗位",
        fallback_actions=[],
    )

    assert accepted.actions[0].target_ref == (
        f"/pages/positions/goal/index?positionId={position_id}"
    )
    assert accepted.actions[0].requires_confirmation is True
    assert rejected.actions == ()


def test_profile_weekly_plan_and_evidence_tools_reuse_existing_action_contract() -> None:
    outcome = execute_companion_actions(
        requests=[
            CompanionActionRequest(name="open_profile_archive", arguments={}),
            CompanionActionRequest(name="open_weekly_plan", arguments={}),
            CompanionActionRequest(name="view_learning_evidence", arguments={}),
        ],
        question_text="查看我的档案、周计划和学习证据",
        fallback_actions=[],
    )

    assert [action.kind for action in outcome.actions] == [
        "supply_information",
        "supply_information",
        "view_evidence",
    ]
