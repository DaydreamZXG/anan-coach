"""契约增量规格 v1：AnswerCheckpoint 及其子结构的向后兼容扩展。

覆盖：毫秒时钟 atMs 非降序、sessionAtMs 会话时间线、笔画逐点 t 时序、
erasedAtMs 擦除墓碑、material_scrolled/question_entered/question_left 事件、
actionEventsTruncated 溢出标记；旧格式（无任何新字段）必须依旧合法。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.domains.xingce_learning.real_attempt.schemas import (
    AnnotationPoint,
    AnnotationStroke,
    AnswerCheckpoint,
    AttemptActionEvent,
)


def _legacy_answer_payload() -> dict[str, Any]:
    return {
        "questionId": "question-1",
        "selectedOptionIds": ["A"],
        "eliminatedOptionIds": ["B"],
        "confidence": "medium",
        "marked": False,
        "skipped": False,
        "elapsedSeconds": 30,
        "draftText": "旧版草稿",
        "materialPosition": None,
        "annotations": [
            {
                "strokeId": "stroke-1",
                "tool": "pen",
                "width": 0.01,
                "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}],
                "atMs": 1_000,
            }
        ],
        "actionEvents": [
            {"atMs": 500, "type": "option_selected", "optionId": "A"},
            {"atMs": 900, "type": "annotation_added"},
        ],
    }


def test_legacy_payload_without_new_fields_is_still_valid() -> None:
    answer = AnswerCheckpoint.model_validate(_legacy_answer_payload())

    assert answer.action_events_truncated is None
    assert answer.annotations[0].erased_at_ms is None
    assert all(point.t is None for point in answer.annotations[0].points)
    assert all(event.session_at_ms is None for event in answer.action_events)


def test_legacy_payload_round_trip_serialization_adds_no_new_keys() -> None:
    payload = _legacy_answer_payload()
    answer = AnswerCheckpoint.model_validate(payload)

    dumped = answer.model_dump(by_alias=True, mode="json", exclude_none=True)

    assert "actionEventsTruncated" not in dumped
    assert "erasedAtMs" not in dumped["annotations"][0]
    assert all("t" not in point for point in dumped["annotations"][0]["points"])
    assert all("sessionAtMs" not in event for event in dumped["actionEvents"])


def test_action_events_allow_equal_at_ms() -> None:
    payload = _legacy_answer_payload()
    payload["actionEvents"] = [
        {"atMs": 700, "type": "option_selected", "optionId": "A"},
        {"atMs": 700, "type": "confidence_set"},
        {"atMs": 700, "type": "marked"},
    ]

    answer = AnswerCheckpoint.model_validate(payload)

    assert [event.at_ms for event in answer.action_events] == [700, 700, 700]


def test_action_events_reject_decreasing_at_ms() -> None:
    payload = _legacy_answer_payload()
    payload["actionEvents"] = [
        {"atMs": 700, "type": "option_selected", "optionId": "A"},
        {"atMs": 699, "type": "confidence_set"},
    ]

    with pytest.raises(ValidationError, match="ordered by atMs"):
        AnswerCheckpoint.model_validate(payload)


def test_new_event_types_and_session_timeline_are_accepted() -> None:
    payload = _legacy_answer_payload()
    payload["actionEvents"] = [
        {"atMs": 0, "sessionAtMs": 12_000, "type": "question_entered"},
        {
            "atMs": 2_100,
            "sessionAtMs": 14_100,
            "type": "material_scrolled",
            "detail": "material-anchor-3",
        },
        {"atMs": 5_000, "sessionAtMs": 17_000, "type": "question_left"},
    ]

    answer = AnswerCheckpoint.model_validate(payload)

    assert [event.type for event in answer.action_events] == [
        "question_entered",
        "material_scrolled",
        "question_left",
    ]
    assert [event.session_at_ms for event in answer.action_events] == [
        12_000,
        14_100,
        17_000,
    ]


def test_session_at_ms_must_be_non_decreasing_when_present() -> None:
    payload = _legacy_answer_payload()
    payload["actionEvents"] = [
        {"atMs": 0, "sessionAtMs": 15_000, "type": "question_entered"},
        {"atMs": 100, "type": "option_selected", "optionId": "A"},
        {"atMs": 200, "sessionAtMs": 14_999, "type": "question_left"},
    ]

    with pytest.raises(ValidationError, match="ordered by sessionAtMs"):
        AnswerCheckpoint.model_validate(payload)


def test_session_at_ms_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AttemptActionEvent.model_validate(
            {"atMs": 0, "sessionAtMs": 86_400_001, "type": "question_entered"}
        )
    with pytest.raises(ValidationError):
        AttemptActionEvent.model_validate(
            {"atMs": 0, "sessionAtMs": -1, "type": "question_entered"}
        )


def test_stroke_point_time_offsets_accept_non_decreasing_sequence() -> None:
    stroke = AnnotationStroke.model_validate(
        {
            "strokeId": "stroke-timed",
            "tool": "highlighter",
            "width": 0.02,
            "points": [
                {"x": 0.1, "y": 0.1, "t": 0},
                {"x": 0.2, "y": 0.2, "t": 16},
                {"x": 0.3, "y": 0.3, "t": 16},
                {"x": 0.4, "y": 0.4, "t": 48},
            ],
            "atMs": 3_000,
        }
    )

    assert [point.t for point in stroke.points] == [0, 16, 16, 48]


def test_stroke_point_time_offsets_reject_decreasing_sequence() -> None:
    with pytest.raises(ValidationError, match="non-decreasing"):
        AnnotationStroke.model_validate(
            {
                "strokeId": "stroke-bad-t",
                "tool": "pen",
                "width": 0.01,
                "points": [
                    {"x": 0.1, "y": 0.1, "t": 100},
                    {"x": 0.2, "y": 0.2, "t": 99},
                ],
                "atMs": 3_000,
            }
        )


def test_stroke_point_time_offset_rejects_negative_value() -> None:
    with pytest.raises(ValidationError):
        AnnotationPoint.model_validate({"x": 0.5, "y": 0.5, "t": -1})


def test_erased_stroke_tombstone_is_accepted_within_bounds() -> None:
    stroke = AnnotationStroke.model_validate(
        {
            "strokeId": "stroke-erased",
            "tool": "pen",
            "width": 0.01,
            "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}],
            "atMs": 1_000,
            "erasedAtMs": 86_400_000,
        }
    )

    assert stroke.erased_at_ms == 86_400_000


def test_erased_at_ms_out_of_range_is_rejected() -> None:
    base = {
        "strokeId": "stroke-erased-bad",
        "tool": "pen",
        "width": 0.01,
        "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}],
        "atMs": 1_000,
    }

    with pytest.raises(ValidationError):
        AnnotationStroke.model_validate({**base, "erasedAtMs": 86_400_001})
    with pytest.raises(ValidationError):
        AnnotationStroke.model_validate({**base, "erasedAtMs": -1})


def test_action_events_truncated_flag_round_trips() -> None:
    payload = _legacy_answer_payload()
    payload["actionEventsTruncated"] = True

    answer = AnswerCheckpoint.model_validate(payload)
    dumped = answer.model_dump(by_alias=True, mode="json", exclude_none=True)

    assert answer.action_events_truncated is True
    assert dumped["actionEventsTruncated"] is True


def test_tombstoned_strokes_still_count_against_stroke_cap() -> None:
    payload = _legacy_answer_payload()
    payload["annotations"] = [
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
        AnswerCheckpoint.model_validate(payload)
