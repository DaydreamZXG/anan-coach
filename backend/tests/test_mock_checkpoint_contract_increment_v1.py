"""契约增量规格 v1：模考检查点接入批注/动作事实采集。

MockExamAnswerCheckpoint 通过 import 复用 real_attempt 的
AnnotationStroke / AttemptActionEvent 定义（不复制），并镜像
AnswerCheckpoint 的排序与唯一性校验；旧格式（无任何新字段）必须依旧合法。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.domains.mock_exam.schemas import MockExamAnswerCheckpoint
from app.domains.xingce_learning.real_attempt.schemas import (
    AnnotationStroke,
    AttemptActionEvent,
)
from tests.task_editing_helpers import claim_task_edit_headers
from tests.test_mock_exam_calibration import (
    _make_client,
    _seed_mock_qbank,
    _seed_user,
)


def _legacy_answer_payload() -> dict[str, Any]:
    return {
        "questionId": "question-1",
        "selectedAnswer": "B",
        "eliminatedOptionIds": ["A"],
        "marked": True,
        "draftText": "旧版草稿",
        "materialPosition": None,
    }


def test_mock_checkpoint_reuses_real_attempt_definitions() -> None:
    assert (
        MockExamAnswerCheckpoint.model_fields["annotations"].annotation
        == list[AnnotationStroke]
    )
    assert (
        MockExamAnswerCheckpoint.model_fields["action_events"].annotation
        == list[AttemptActionEvent]
    )


def test_legacy_payload_without_new_fields_is_still_valid() -> None:
    answer = MockExamAnswerCheckpoint.model_validate(_legacy_answer_payload())

    assert answer.annotations == []
    assert answer.action_events == []
    assert answer.action_events_truncated is None


def test_mock_checkpoint_accepts_full_contract_increment_payload() -> None:
    payload = {
        **_legacy_answer_payload(),
        "annotations": [
            {
                "strokeId": "stroke-1",
                "tool": "pen",
                "width": 0.01,
                "points": [
                    {"x": 0.1, "y": 0.1, "t": 0},
                    {"x": 0.2, "y": 0.2, "t": 16},
                ],
                "atMs": 1_000,
                "erasedAtMs": 2_000,
            }
        ],
        "actionEvents": [
            {"atMs": 0, "sessionAtMs": 12_000, "type": "question_entered"},
            {"atMs": 300, "sessionAtMs": 12_300, "type": "option_selected", "optionId": "B"},
            {
                "atMs": 2_100,
                "sessionAtMs": 14_100,
                "type": "material_scrolled",
                "detail": "material-anchor-3",
            },
            {"atMs": 5_000, "sessionAtMs": 17_000, "type": "question_left"},
        ],
        "actionEventsTruncated": True,
    }

    answer = MockExamAnswerCheckpoint.model_validate(payload)
    dumped = answer.model_dump(by_alias=True, mode="json")

    assert [event.type for event in answer.action_events] == [
        "question_entered",
        "option_selected",
        "material_scrolled",
        "question_left",
    ]
    assert answer.annotations[0].erased_at_ms == 2_000
    assert answer.action_events_truncated is True
    assert dumped["actionEvents"][2]["detail"] == "material-anchor-3"
    assert dumped["actionEventsTruncated"] is True


def test_mock_checkpoint_rejects_decreasing_at_ms() -> None:
    payload = {
        **_legacy_answer_payload(),
        "actionEvents": [
            {"atMs": 700, "type": "option_selected", "optionId": "B"},
            {"atMs": 699, "type": "marked"},
        ],
    }

    with pytest.raises(ValidationError, match="ordered by atMs"):
        MockExamAnswerCheckpoint.model_validate(payload)


def test_mock_checkpoint_rejects_decreasing_session_at_ms() -> None:
    payload = {
        **_legacy_answer_payload(),
        "actionEvents": [
            {"atMs": 0, "sessionAtMs": 15_000, "type": "question_entered"},
            {"atMs": 100, "type": "option_selected", "optionId": "B"},
            {"atMs": 200, "sessionAtMs": 14_999, "type": "question_left"},
        ],
    }

    with pytest.raises(ValidationError, match="ordered by sessionAtMs"):
        MockExamAnswerCheckpoint.model_validate(payload)


def test_mock_checkpoint_rejects_duplicate_stroke_ids() -> None:
    stroke = {
        "strokeId": "stroke-dup",
        "tool": "pen",
        "width": 0.01,
        "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}],
        "atMs": 1_000,
    }
    payload = {**_legacy_answer_payload(), "annotations": [stroke, dict(stroke)]}

    with pytest.raises(ValidationError, match="unique"):
        MockExamAnswerCheckpoint.model_validate(payload)


def test_mock_checkpoint_enforces_annotation_and_event_caps() -> None:
    over_cap_strokes = [
        {
            "strokeId": f"stroke-{index}",
            "tool": "pen",
            "width": 0.01,
            "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}],
            "atMs": index,
            "erasedAtMs": index + 1,
        }
        for index in range(121)
    ]
    with pytest.raises(ValidationError):
        MockExamAnswerCheckpoint.model_validate(
            {**_legacy_answer_payload(), "annotations": over_cap_strokes}
        )

    over_cap_events = [{"atMs": index, "type": "marked"} for index in range(401)]
    with pytest.raises(ValidationError):
        MockExamAnswerCheckpoint.model_validate(
            {**_legacy_answer_payload(), "actionEvents": over_cap_events}
        )


def test_mock_checkpoint_round_trips_action_facts_over_http(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-contract-increment-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-contract-increment-token"}

    created_response = client.post("/api/v1/mock-exams/weekly", headers=auth)
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    auth = {
        **auth,
        **claim_task_edit_headers(
            client,
            auth,
            resource_type="mock_exam_session",
            resource_id=created["sessionId"],
            revision=created["revision"],
        ),
    }
    question_id = created["questions"][0]["questionId"]

    action_events = [
        {"atMs": 0, "sessionAtMs": 1_000, "type": "question_entered"},
        {"atMs": 450, "sessionAtMs": 1_450, "type": "option_selected", "optionId": "B"},
        {
            "atMs": 2_450,
            "sessionAtMs": 3_450,
            "type": "material_scrolled",
            "detail": "anchor-material-1",
        },
    ]
    saved_response = client.put(
        f"/api/v1/mock-exams/{created['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "mock-contract-increment-cp-001"},
        json={
            "expectedRevision": created["revision"],
            "currentQuestionId": question_id,
            "answers": [
                {
                    "questionId": question_id,
                    "selectedAnswer": "B",
                    "eliminatedOptionIds": [],
                    "marked": False,
                    "draftText": "",
                    "annotations": [],
                    "actionEvents": action_events,
                    "actionEventsTruncated": True,
                }
            ],
        },
    )
    assert saved_response.status_code == 200, saved_response.text
    saved_answer = saved_response.json()["checkpoint"]["answers"][0]
    echoed_events = [
        {key: value for key, value in event.items() if value is not None}
        for event in saved_answer["actionEvents"]
    ]
    assert echoed_events == action_events
    assert saved_answer["annotations"] == []
    assert saved_answer["actionEventsTruncated"] is True

    # 旧客户端不带新字段依旧可以保存（向后兼容），并保持缺省语义。
    legacy_response = client.put(
        f"/api/v1/mock-exams/{created['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "mock-contract-increment-cp-002"},
        json={
            "expectedRevision": saved_response.json()["revision"],
            "currentQuestionId": question_id,
            "answers": [
                {
                    "questionId": question_id,
                    "selectedAnswer": "C",
                    "eliminatedOptionIds": [],
                    "marked": False,
                    "draftText": "",
                }
            ],
        },
    )
    assert legacy_response.status_code == 200, legacy_response.text
    legacy_answer = legacy_response.json()["checkpoint"]["answers"][0]
    assert legacy_answer["selectedAnswer"] == "C"
    assert legacy_answer["actionEvents"] == []
    assert legacy_answer.get("actionEventsTruncated") in (None, False)
