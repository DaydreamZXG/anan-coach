from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.domains.xingce_learning.mastery.repository import XingceMasteryRepository
from tests.test_xingce_planning_closure import _submitted_attempt


def test_item_interpretation_requires_current_answered_objective_error_and_replays(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-item-interpretation-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    items = submitted["result"]["items"]
    wrong_question = items[0]["questionId"]
    correct_question = items[1]["questionId"]
    assert submitted["result"]["groupSummary"][
        "methodCorrectCalculationError"
    ] == 0

    duplicate = _reflect(
        client,
        auth,
        review,
        key="interpretation-duplicate",
        interpretations=[
            _interpretation(wrong_question),
            _interpretation(wrong_question),
        ],
    )
    assert duplicate.status_code == 409

    too_many = _reflect(
        client,
        auth,
        review,
        key="interpretation-too-many",
        interpretations=[
            _interpretation(item["questionId"])
            for item in items
        ]
        + [_interpretation("xingce-route-question-4")],
    )
    assert too_many.status_code == 409

    unsupported = _reflect(
        client,
        auth,
        review,
        key="interpretation-unsupported",
        interpretations=[
            {
                "questionId": wrong_question,
                "interpretation": "knowledge_gap",
            }
        ],
    )
    assert unsupported.status_code == 409

    cross_session = _reflect(
        client,
        auth,
        review,
        key="interpretation-cross-session",
        interpretations=[_interpretation("xingce-route-question-4")],
    )
    assert cross_session.status_code == 409

    not_applicable = _reflect(
        client,
        auth,
        review,
        key="interpretation-correct-item",
        interpretations=[_interpretation(correct_question)],
    )
    assert not_applicable.status_code == 409

    payload = [_interpretation(wrong_question)]
    accepted = _reflect(
        client,
        auth,
        review,
        key="interpretation-accepted",
        interpretations=payload,
    )
    replay = _reflect(
        client,
        auth,
        review,
        key="interpretation-accepted",
        interpretations=payload,
    )
    assert accepted.status_code == 200, accepted.text
    assert replay.status_code == 200, replay.text
    data = accepted.json()["data"]
    assert replay.json()["data"] == data
    assert data["review"]["reflection"]["itemInterpretations"] == payload
    assert data["result"]["groupSummary"][
        "methodCorrectCalculationError"
    ] == 1
    assert data["result"]["groupSummary"]["pendingReflection"] is False
    assert data["result"]["groupSummary"]["pendingCause"] is True

    with db_session_factory() as session:
        row = session.execute(
            text(
                "select reflection_version_id, user_id::text, session_id::text, payload "
                "from learning.xingce_reflection_versions"
            )
        ).mappings().one()
        assert row["payload"]["itemInterpretations"] == payload
        evidence = XingceMasteryRepository().verification_evidence(
            session,
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            reflection_version_id=str(row["reflection_version_id"]),
        )
        assert [item["method_correct_calculation_error"] for item in evidence] == [
            True,
            False,
            False,
        ]
        assert {str(item["disposition"]) for item in evidence} == {"answered"}
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text(
                    "update learning.xingce_reflection_versions "
                    "set payload = '{}'::jsonb "
                    "where reflection_version_id = :reflection_version_id"
                ),
                {"reflection_version_id": row["reflection_version_id"]},
            )


def test_tags_never_materialize_method_correct_calculation_error_without_item_fact(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-tags-not-facts-token",
        choices=["D", "B", "C"],
    )
    response = _reflect(
        client,
        auth,
        submitted["review"],
        key="tags-not-facts-reflection",
        interpretations=[],
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["review"]["reflection"]["tags"] == ["方法正确", "计算错误"]
    assert data["result"]["groupSummary"][
        "methodCorrectCalculationError"
    ] == 0


def _reflect(
    client,
    auth: dict[str, str],
    review: dict,
    *,
    key: str,
    interpretations: list[dict],
):
    return client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": key},
        json={
            "expectedRevision": review["revision"],
            "rawText": "我的方法是对的，但这道题的计算结果写错了。",
            "tags": ["方法正确", "计算错误"],
            "strategyNote": "下次独立复核计算结果。",
            "doubtNote": None,
            "voiceArtifactVersionIds": [],
            "itemInterpretations": interpretations,
        },
    )


def _interpretation(question_id: str) -> dict[str, str]:
    return {
        "questionId": question_id,
        "interpretation": "method_correct_calculation_error",
    }
