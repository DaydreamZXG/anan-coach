from __future__ import annotations

from typing import Any

from app.domains.xingce_learning.cause_agent.tools import CauseAgentTools


class _Repository:
    def attempt_evidence(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            _attempt_row("answered-wrong", "incorrect", selected=["B"]),
            _attempt_row("auto-unanswered", "unanswered", selected=[]),
        ]

    def session_checkpoint(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "checkpoint": {
                "answers": [
                    {
                        "questionId": "answered-wrong",
                        "selectedOptionIds": ["B"],
                        "actionEvents": [
                            {"type": "question_entered", "atMs": 0},
                            {"type": "option_selected", "atMs": 5_000, "optionId": "B"},
                        ],
                        "annotations": [],
                    },
                    {
                        "questionId": "auto-unanswered",
                        "selectedOptionIds": [],
                        "skipped": True,
                        "actionEvents": [{"type": "skipped", "atMs": 0}],
                        "annotations": [],
                    },
                ]
            }
        }

    def session_bundle(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "safe_bundle": {
                "questions": [
                    {
                        "questionId": "answered-wrong",
                        "materialId": "material-1",
                        "stem": "下列说法正确的是？",
                        "options": [
                            {"optionId": "A", "label": "A", "text": "正确选项"},
                            {"optionId": "B", "label": "B", "text": "错误选项"},
                        ],
                    },
                    {
                        "questionId": "auto-unanswered",
                        "materialId": None,
                        "stem": "未作答题不应进入错因分析",
                        "options": [],
                    },
                ],
                "materials": [
                    {
                        "materialId": "material-1",
                        "title": "共享材料",
                        "text": "甲地公共服务投入持续增长。",
                        "content": [
                            {
                                "type": "paragraph",
                                "text": "甲地公共服务投入持续增长。",
                            }
                        ],
                    }
                ],
            },
            "scoring_bundle": {},
        }

    def ink_formula_evidence(
        self, *_args: Any, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        return []


def test_auto_finalized_unanswered_items_do_not_enter_cause_model_evidence() -> None:
    tools = CauseAgentTools(
        session=object(),  # type: ignore[arg-type]
        repository=_Repository(),  # type: ignore[arg-type]
        user_id="user-1",
        session_id="session-1",
        reflection_version_id="reflection-1",
    )
    scope = {
        "userId": "user-1",
        "sessionId": "session-1",
        "reflectionVersionId": "reflection-1",
    }

    attempt = tools.load_attempt_evidence(scope)
    trace = tools.load_action_trace(scope)

    assert [item["questionId"] for item in attempt["items"]] == ["answered-wrong"]
    assert [item["questionId"] for item in trace["items"]] == ["answered-wrong"]
    assert "annotations" not in attempt["items"][0]["answer"]
    assert "actionEvents" not in attempt["items"][0]["answer"]
    assert attempt["items"][0]["question"] == {
        "stem": "下列说法正确的是？",
        "materialId": "material-1",
        "options": [
            {"optionId": "A", "label": "A", "text": "正确选项"},
            {"optionId": "B", "label": "B", "text": "错误选项"},
        ],
    }
    assert attempt["materials"] == [
        {
            "materialId": "material-1",
            "title": "共享材料",
            "text": "甲地公共服务投入持续增长。",
            "content": [
                {"type": "paragraph", "text": "甲地公共服务投入持续增长。"}
            ],
        }
    ]
    assert attempt["evidenceRefs"] == [
        "assessment:answered-wrong",
        "content-version:content-answered-wrong",
    ]


def _attempt_row(
    question_id: str,
    result_code: str,
    *,
    selected: list[str],
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "result_code": result_code,
        "result_snapshot": {
            "resultCode": result_code,
            "selectedOptionIds": selected,
            "correctOptionIds": ["A"],
            "objectiveVerdict": "incorrect" if selected else "unanswered",
            "disposition": "answered" if selected else "active_abandon",
            "confidenceEvidence": "unreported",
            "timingEvidence": {
                "status": "within_advisory",
                "elapsedSeconds": 5,
                "advisorySeconds": 60,
            },
            "explanation": "This large field is deliberately excluded from the model input.",
        },
        "answer_payload": {
            "questionId": question_id,
            "selectedOptionIds": selected,
            "eliminatedOptionIds": [],
            "confidence": None,
            "marked": False,
            "skipped": not selected,
            "elapsedSeconds": 5,
            "draftText": "",
            "annotations": [{"points": [{"x": 0.1, "y": 0.2}]}],
            "actionEvents": [{"type": "option_selected", "atMs": 5_000}],
        },
        "content_version_id": f"content-{question_id}",
        "assessment_id": question_id,
        "disputed": False,
    }
