from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_essay_closure_writers import _post_decision
from tests.test_essay_learning_artifacts import (
    _client,
    _content_ids,
    _insert_completed_review,
    _seed_answer_without_review,
)
from tests.test_planning_routes import _seed_learner_and_published_essay_content


def test_v2_rewrite_does_not_write_score_evidence_or_formal_ability(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-score-isolation-token"
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
        key="essay-score-isolation-decision",
        ids=ids,
        review_id=review_id,
        decision="accepted",
    )
    response = client.post(
        "/api/v2/essay-intelligence/rewrites",
        headers={**headers, "Idempotency-Key": "essay-score-isolation-rewrite"},
        json={
            "attemptId": attempt_id,
            "baseReviewVersionId": review_id,
            "baseAnswerVersionId": answer_id,
            "findingDecisionArtifactVersionId": decision_id,
            "basedOnAnswerVersionId": answer_id,
            "basedOnRewriteVersionId": None,
            "sourceType": "typed",
            "answerText": "将材料事实归纳为责任与协同问题。",
            "thinking": None,
            "resolutions": [
                {
                    "findingId": "finding-1",
                    "status": "resolved",
                    "learnerEvidence": "结构与概括方式已重写",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    rewrite_id = response.json()["data"]["rewrite"]["rewriteVersionId"]

    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learning.score_evidence "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 0
        assert session.execute(
            text(
                "select count(*) from learning.ability_estimate_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 0
        isolation = session.execute(
            text(
                "select analysis_payload->'scoreIsolation' "
                "from learning.essay_rewrite_versions "
                "where rewrite_version_id = cast(:rewrite_id as uuid)"
            ),
            {"rewrite_id": rewrite_id},
        ).scalar_one()
        assert isolation == {
            "writesScoreEvidence": False,
            "writesFormalAbility": False,
        }
