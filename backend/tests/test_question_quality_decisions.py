from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.domains.task_content.quality.schemas import (
    RecordQuestionQualityDecisionCommand,
)
from app.domains.task_content.quality.service import (
    QuestionQualityDecisionError,
    QuestionQualityDecisionService,
)
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository


NOW = datetime(2026, 7, 16, 4, tzinfo=UTC)


def test_confirmed_invalid_dispute_is_versioned_and_removed_from_delivery(
    db_session_factory: sessionmaker[Session],
) -> None:
    seeded = _seed_quality_case(db_session_factory)
    service = QuestionQualityDecisionService()
    command = RecordQuestionQualityDecisionCommand(
        questionId=seeded["question_id"],
        contentVersionId=seeded["content_version_id"],
        decision="confirmed_invalid",
        disputeIds=[seeded["dispute_id"]],
        reasonCode="answer_key_incorrect",
        rationale="复核原题与官方答案后，确认当前答案键错误。",
        authorityVersion="content-moderation-policy@v1",
        evidenceRefs=["official-answer-review:fixture-v1"],
    )

    with db_session_factory() as session:
        result = service.record(
            session,
            command_id="quality-decision-command-1",
            actor_ref="moderator:fixture-1",
            command=command,
            now=NOW,
        )
        session.commit()

    assert result.version_no == 1
    assert result.decision == "confirmed_invalid"
    assert [str(item) for item in result.dispute_ids] == [seeded["dispute_id"]]

    with db_session_factory() as session:
        replay = service.record(
            session,
            command_id="quality-decision-command-1",
            actor_ref="moderator:fixture-1",
            command=command,
            now=NOW,
        )
        status = session.execute(
            text("select status from product.questions where question_id = :question_id"),
            {"question_id": seeded["question_id"]},
        ).scalar_one()
        selectable = XingceAttemptRepository().select_frozen_questions(
            session,
            module_name="资料分析",
            limit=3,
        )
        session.rollback()

    assert replay.decision_version_id == result.decision_version_id
    assert status == "invalidated"
    assert selectable == []

    with db_session_factory() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "update product.question_quality_decision_versions "
                    "set rationale = 'tampered' "
                    "where decision_version_id = cast(:decision_version_id as uuid)"
                ),
                {"decision_version_id": str(result.decision_version_id)},
            )


def test_adjudication_rejects_cross_version_evidence_and_idempotency_reuse(
    db_session_factory: sessionmaker[Session],
) -> None:
    seeded = _seed_quality_case(db_session_factory)
    service = QuestionQualityDecisionService()
    base = RecordQuestionQualityDecisionCommand(
        questionId=seeded["question_id"],
        contentVersionId=seeded["content_version_id"],
        decision="needs_more_evidence",
        disputeIds=[seeded["dispute_id"]],
        reasonCode="needs_official_source",
        rationale="现有报告不足以确认答案键，需要补官方来源。",
        authorityVersion="content-moderation-policy@v1",
        evidenceRefs=["moderation-queue:fixture-v1"],
    )
    with db_session_factory() as session:
        first = service.record(
            session,
            command_id="quality-decision-command-2",
            actor_ref="moderator:fixture-2",
            command=base,
            now=NOW,
        )
        session.commit()
    assert first.decision == "needs_more_evidence"

    with db_session_factory() as session:
        with pytest.raises(QuestionQualityDecisionError, match="reused"):
            service.record(
                session,
                command_id="quality-decision-command-2",
                actor_ref="moderator:another",
                command=base,
                now=NOW,
            )

    wrong_version = RecordQuestionQualityDecisionCommand(
        **{
            **base.model_dump(by_alias=True, mode="json"),
            "contentVersionId": "f" * 64,
        }
    )
    with db_session_factory() as session:
        with pytest.raises(QuestionQualityDecisionError, match="current published"):
            service.record(
                session,
                command_id="quality-decision-command-3",
                actor_ref="moderator:fixture-2",
                command=wrong_version,
                now=NOW,
            )


def _seed_quality_case(db_session_factory: sessionmaker[Session]) -> dict[str, str]:
    user_id = str(uuid4())
    question_id = f"quality-question-{uuid4()}"
    session_id = str(uuid4())
    dispute_id = str(uuid4())
    content_version_id = "a" * 63 + "1"
    with db_session_factory() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), 'quality-user')"
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id, domain, status, module_name, question_type,
                  stem, answer, visual_required
                ) values (
                  :question_id, 'objective', 'active', '资料分析', 'single_choice',
                  '测试题干', 'A', false
                )
                """
            ),
            {"question_id": question_id},
        )
        session.execute(
            text(
                """
                insert into product.question_options (
                  question_id, option_index, label, option_text
                ) values (:question_id, 0, 'A', '选项A')
                """
            ),
            {"question_id": question_id},
        )
        session.execute(
            text(
                """
                insert into product.question_content_versions (
                  content_version_id, question_id, track, snapshot_payload,
                  normalized_search_text, created_at
                ) values (
                  :content_version_id, :question_id, 'xingce', '{}'::jsonb,
                  '测试题干 选项A', :created_at
                )
                """
            ),
            {
                "content_version_id": content_version_id,
                "question_id": question_id,
                "created_at": NOW,
            },
        )
        session.execute(
            text(
                """
                insert into product.question_content_heads (
                  question_id, content_version_id, updated_at
                ) values (:question_id, :content_version_id, :created_at)
                """
            ),
            {
                "content_version_id": content_version_id,
                "question_id": question_id,
                "created_at": NOW,
            },
        )
        session.execute(
            text(
                """
                insert into product.mock_exam_paper_versions (
                  paper_version_id, paper_key, version_no, state, exam_scope,
                  track, title, duration_minutes, question_ids, blueprint,
                  learner_bundle_version_id, scoring_policy_version_id,
                  exposure_policy, equivalence_metadata, content_fingerprint,
                  published_at, created_at
                ) values (
                  '00000000-0000-4000-8000-000000000633',
                  'quality-dispute-fixture', 1, 'retired', 'guokao', 'xingce',
                  '质量争议测试卷', 120, '[]'::jsonb, '[]'::jsonb,
                  'fixture-bundle', 'mock-objective-exact-v1', '{}'::jsonb,
                  '{}'::jsonb, repeat('b', 64), :created_at, :created_at
                )
                """
            ),
            {"created_at": NOW},
        )
        session.execute(
            text(
                """
                insert into learner.mock_exam_sessions (
                  session_id, user_id, exam_scope, track, status,
                  question_ids, blueprint, paper_version_id,
                  scoring_policy_version_id, created_at, updated_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid), 'guokao',
                  'xingce', 'assigned', '[]'::jsonb, '{}'::jsonb,
                  '00000000-0000-4000-8000-000000000633',
                  'mock-objective-exact-v1',
                  :created_at, :created_at
                )
                """
            ),
            {"session_id": session_id, "user_id": user_id, "created_at": NOW},
        )
        session.execute(
            text(
                """
                insert into learning.xingce_question_disputes (
                  dispute_id, user_id, session_kind, session_id, question_id,
                  content_version_id, category, description, evidence_context,
                  idempotency_key, created_at
                ) values (
                  cast(:dispute_id as uuid), cast(:user_id as uuid), 'mock_exam',
                  cast(:session_id as uuid), :question_id, :content_version_id,
                  'answer_error', '答案键与官方题不一致',
                  cast(:evidence_context as jsonb),
                  'quality-dispute-fixture', :created_at
                )
                """
            ),
            {
                "dispute_id": dispute_id,
                "user_id": user_id,
                "session_id": session_id,
                "question_id": question_id,
                "content_version_id": content_version_id,
                "evidence_context": json.dumps(
                    {
                        "sessionRevision": 1,
                        "bundleVersionId": "fixture",
                        "currentQuestionId": question_id,
                    }
                ),
                "created_at": NOW,
            },
        )
        session.commit()
    return {
        "user_id": user_id,
        "question_id": question_id,
        "session_id": session_id,
        "dispute_id": dispute_id,
        "content_version_id": content_version_id,
    }
