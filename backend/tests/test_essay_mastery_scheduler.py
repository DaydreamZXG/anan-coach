from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.domains.essay_intelligence.learning_schemas import (
    ReviewAcknowledgementPayload,
)
from tests.test_essay_closure_writers import _post_decision
from tests.test_essay_learning_artifacts import (
    _client,
    _content_ids,
    _insert_completed_review,
    _seed_answer_without_review,
)
from tests.test_planning_routes import _seed_learner_and_published_essay_content


def test_finding_decisions_require_explanations_for_disagreement_and_uncertainty() -> None:
    common = {
        "schemaType": "review_acknowledgement",
        "contractVersion": "essay-finding-decision@v1",
        "priorityFindingIds": [],
        "learnerUnderstanding": "逐条核对",
    }
    for decision in (
        {"findingId": "f-1", "decision": "rejected"},
        {
            "findingId": "f-1",
            "decision": "uncertain",
            "learnerReason": "证据不足",
        },
    ):
        try:
            ReviewAcknowledgementPayload.model_validate(
                {**common, "decisions": [decision]}
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid finding decision was accepted")


def test_unresolved_rewrite_remains_due_and_never_becomes_stable(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-mastery-unresolved-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    answer_id, attempt_id = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    review_id = _insert_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
        answer_id=answer_id,
    )
    client = _client(db_session_factory)
    headers = {"Authorization": f"Bearer {token}"}
    decision_id = _post_decision(
        client,
        headers,
        key="essay-unresolved-decision",
        ids=ids,
        review_id=review_id,
        decision="accepted",
    )
    response = client.post(
        "/api/v2/essay-intelligence/rewrites",
        headers={**headers, "Idempotency-Key": "essay-unresolved-rewrite"},
        json={
            "attemptId": attempt_id,
            "baseReviewVersionId": review_id,
            "baseAnswerVersionId": answer_id,
            "findingDecisionArtifactVersionId": decision_id,
            "basedOnAnswerVersionId": answer_id,
            "basedOnRewriteVersionId": None,
            "sourceType": "typed",
            "answerText": "基层治理仍存在执行问题。",
            "thinking": None,
            "resolutions": [
                {
                    "findingId": "finding-1",
                    "status": "unresolved",
                    "learnerEvidence": "尚未形成可复用的上位概括",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["rewrite"]["status"] == "needs_revision"
    assert {item["state"] for item in data["masteryVersions"]} == {
        "rewrite_due"
    }
    projection = client.get(
        "/api/v2/essay-intelligence/mastery",
        headers=headers,
    ).json()["data"]
    assert projection["stableCount"] == 0
    assert projection["dueCount"] == 1
