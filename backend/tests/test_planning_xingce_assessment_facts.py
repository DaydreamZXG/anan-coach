from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.planning.schemas import TodayDashboard
from app.domains.planning.service import PlanningService
from tests.factories import (
    FrozenKnowledgeNodeSeed,
    FrozenQuestionSeed,
    seed_canonical_frozen_release,
)
from tests.test_planning_routes import (
    NOW,
    _seed_learner_and_published_essay_content,
    _seed_planning_learner,
)


def test_next_targeted_plan_uses_user_isolated_assessment_weakest_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        include_objective_questions=False,
    )
    other_user_id = _seed_planning_learner(db_session_factory)
    planning = PlanningService()

    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update learner.profiles set daily_minutes = 180 "
                "where user_id in (cast(:user_id as uuid), cast(:other_user_id as uuid))"
            ),
            {"user_id": user_id, "other_user_id": other_user_id},
        )
        quantity_ids = _insert_objective_questions(
            session,
            prefix="assessment-quantity",
            module_name="数量关系",
            count=9,
        )
        language_alias_ids = _insert_objective_questions(
            session,
            prefix="assessment-language-alias",
            module_name="言语理解",
            count=6,
        )
        language_canonical_ids = _insert_objective_questions(
            session,
            prefix="assessment-language-canonical",
            module_name="言语理解与表达",
            count=6,
        )
        language_ids = [*language_alias_ids, *language_canonical_ids]
        assessed_language_ids = [
            *language_alias_ids[:3],
            *language_canonical_ids[:3],
        ]
        _insert_active_knowledge_node(
            session,
            module_name="言语理解与表达",
            tag_value="中心理解",
            question_ids=language_ids,
            now=NOW,
        )
        data_ids = _insert_objective_questions(
            session,
            prefix="assessment-data",
            module_name="资料分析",
            count=6,
        )
        initial = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
        other_initial = planning.get_or_create_today(
            session,
            user_id=other_user_id,
            now=NOW,
            local_date=NOW.date(),
        )
        _insert_assessment_facts(
            session,
            user_id=user_id,
            plan=initial,
            question_results=[
                *zip(quantity_ids[:6], [True, True, True, True, True, False]),
                *zip(
                    assessed_language_ids,
                    [True, False, False, False, False, False],
                ),
            ],
        )
        _insert_assessment_facts(
            session,
            user_id=other_user_id,
            plan=other_initial,
            question_results=[*zip(data_ids, [False] * 6)],
        )
        _insert_complete_ability_estimate(session, user_id=user_id)

        assert (
            session.execute(
                text(
                    "select count(*) from learner.practice_answers "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )

    next_day = NOW + timedelta(days=1)
    with db_session_factory.begin() as session:
        plan = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=next_day,
            local_date=next_day.date(),
        )

    targeted = next(task for task in plan.tasks if task.stage == "objective_practice")
    assert targeted.title == "行测提分练习：言语理解与表达"
    assert "言语理解与表达" in targeted.reason
    assert "资料分析" not in targeted.title


def test_completed_question_group_stops_repeating_baseline_without_formal_dual_track_estimate(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        include_objective_questions=False,
    )
    planning = PlanningService()

    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update learner.profiles set daily_minutes = 180 "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        )
        question_ids = _insert_objective_questions(
            session,
            prefix="completed-baseline-language",
            module_name="言语理解与表达",
            count=36,
        )
        _insert_active_knowledge_node(
            session,
            module_name="言语理解与表达",
            tag_value="中心理解",
            question_ids=question_ids,
            now=NOW,
        )
        initial = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
        assert any(task.stage == "objective_baseline" for task in initial.tasks)
        _insert_assessment_facts(
            session,
            user_id=user_id,
            plan=initial,
            question_results=[
                (question_id, index % 4 != 0) for index, question_id in enumerate(question_ids[:30])
            ],
        )

    next_day = NOW + timedelta(days=1)
    with db_session_factory.begin() as session:
        next_plan = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=next_day,
            local_date=next_day.date(),
        )

    assert all(task.stage != "objective_baseline" for task in next_plan.tasks)
    assert any(task.stage == "objective_practice" for task in next_plan.tasks)
    assert next_plan.input_versions["abilityEstimate"] == "unavailable"


def _insert_objective_questions(
    session: Session,
    *,
    prefix: str,
    module_name: str,
    count: int,
) -> list[str]:
    question_ids: list[str] = []
    for index in range(count):
        question_id = f"{prefix}-{index + 1}"
        question_ids.append(question_id)
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id, domain, status, module_name, question_type,
                  stem, answer, shared_context_required, visual_required,
                  occurrence_count, latest_year
                ) values (
                  :question_id, 'objective', 'canonical', :module_name,
                  'single_choice', :stem, 'A', false, false,
                  :occurrence_count, 2026
                )
                """
            ),
            {
                "question_id": question_id,
                "module_name": module_name,
                "stem": f"{module_name}真实作答事实题 {index + 1}",
                "occurrence_count": 200 - index,
            },
        )
        for option_index, label in enumerate(("A", "B", "C", "D"), start=1):
            session.execute(
                text(
                    """
                    insert into product.question_options (
                      question_id, option_index, label, option_text
                    ) values (
                      :question_id, :option_index, :label, :option_text
                    )
                    """
                ),
                {
                    "question_id": question_id,
                    "option_index": option_index,
                    "label": label,
                    "option_text": f"选项 {label}",
                },
            )
    return question_ids


def _insert_assessment_facts(
    session: Session,
    *,
    user_id: str,
    plan: TodayDashboard,
    question_results: list[tuple[str, bool]],
) -> None:
    plan_version_id = str(plan.plan_version_id)
    plan_task = next(task for task in plan.tasks if task.track == "xingce")
    for group_index in range(0, len(question_results), 3):
        group = question_results[group_index : group_index + 3]
        session_id = str(uuid4())
        attempt_group_id = str(uuid4())
        command_id = f"assessment-submit-{user_id[:8]}-{group_index // 3}"
        created_at = NOW - timedelta(days=1, minutes=group_index)
        session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  user_id, command_id, command_type, target_id,
                  request_fingerprint, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :command_id, 'attempt_submit',
                  cast(:session_id as uuid), :fingerprint, '{}'::jsonb, :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "session_id": session_id,
                "fingerprint": _digest(f"command:{session_id}"),
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_task_sessions (
                  session_id, user_id, source_plan_task_id, source_plan_version_id,
                  local_date, work_item_key, mode, state, attempt_group_id,
                  revision, bundle_version_id, safe_bundle, scoring_bundle,
                  current_question_id, checkpoint, total_elapsed_seconds,
                  created_at, updated_at, submitted_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  cast(:plan_task_id as uuid), cast(:plan_version_id as uuid),
                  :local_date, :work_item_key, 'baseline', 'submitted',
                  cast(:attempt_group_id as uuid), 2, :bundle_version_id,
                  cast(:safe_bundle as jsonb), '{}'::jsonb, :current_question_id,
                  '{"answers":[]}'::jsonb, 90, :created_at, :created_at, :created_at
                )
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "plan_task_id": str(plan_task.id),
                "plan_version_id": plan_version_id,
                "local_date": plan.local_date,
                "work_item_key": _digest(f"work-item:{session_id}"),
                "attempt_group_id": attempt_group_id,
                "bundle_version_id": _digest(f"bundle:{session_id}"),
                "safe_bundle": json.dumps(
                    {"questions": [{"questionId": item[0]} for item in group]}
                ),
                "current_question_id": group[0][0],
                "created_at": created_at,
            },
        )
        for question_id, correct in group:
            _insert_assessment(
                session,
                user_id=user_id,
                session_id=session_id,
                attempt_group_id=attempt_group_id,
                command_id=command_id,
                question_id=question_id,
                correct=correct,
                created_at=created_at,
            )


def _insert_assessment(
    session: Session,
    *,
    user_id: str,
    session_id: str,
    attempt_group_id: str,
    command_id: str,
    question_id: str,
    correct: bool,
    created_at: datetime,
) -> None:
    answer_version_id = str(uuid4())
    attempt_record_id = str(uuid4())
    payload = {"questionId": question_id, "selectedOption": "A" if correct else "B"}
    session.execute(
        text(
            """
            insert into learning.xingce_answer_versions (
              answer_version_id, command_id, user_id, session_id, question_id,
              version_no, payload, payload_sha256, created_at
            ) values (
              cast(:answer_version_id as uuid), :command_id,
              cast(:user_id as uuid), cast(:session_id as uuid), :question_id,
              1, cast(:payload as jsonb), :payload_sha256, :created_at
            )
            """
        ),
        {
            "answer_version_id": answer_version_id,
            "command_id": command_id,
            "user_id": user_id,
            "session_id": session_id,
            "question_id": question_id,
            "payload": json.dumps(payload),
            "payload_sha256": _digest(json.dumps(payload, sort_keys=True)),
            "created_at": created_at,
        },
    )
    session.execute(
        text(
            """
            insert into learning.xingce_attempt_records (
              attempt_record_id, attempt_group_id, item_attempt_id,
              submit_command_id, user_id, session_id, question_id,
              session_revision, answer_version_id, content_version_id,
              scoring_policy_version, timing_evidence_ref, timing_evidence,
              submission_hash, source_lineage_refs, created_at
            ) values (
              cast(:attempt_record_id as uuid), cast(:attempt_group_id as uuid),
              :item_attempt_id, :command_id, cast(:user_id as uuid),
              cast(:session_id as uuid), :question_id, 2,
              cast(:answer_version_id as uuid), :content_version_id,
              'test-scoring-v1', :timing_evidence_ref,
              cast(:timing_evidence as jsonb), :submission_hash,
              cast(:source_lineage_refs as jsonb), :created_at
            )
            """
        ),
        {
            "attempt_record_id": attempt_record_id,
            "attempt_group_id": attempt_group_id,
            "item_attempt_id": _digest(f"item:{user_id}:{session_id}:{question_id}"),
            "command_id": command_id,
            "user_id": user_id,
            "session_id": session_id,
            "question_id": question_id,
            "answer_version_id": answer_version_id,
            "content_version_id": _digest(f"content:{question_id}"),
            "timing_evidence_ref": _digest(f"timing:{session_id}:{question_id}"),
            "timing_evidence": json.dumps({"elapsedSeconds": 30}),
            "submission_hash": _digest(f"submission:{session_id}:{question_id}"),
            "source_lineage_refs": json.dumps(
                [
                    f"session:{session_id}",
                    f"answer:{answer_version_id}",
                    f"question:{question_id}",
                ]
            ),
            "created_at": created_at,
        },
    )
    verdict = "correct" if correct else "incorrect"
    session.execute(
        text(
            """
            insert into learning.xingce_assessments (
              assessment_id, attempt_record_id, submit_command_id,
              user_id, session_id, question_id, answer_version_id,
              content_version_id, scoring_policy_version, assessment_key_hash,
              result_code, correct, result_snapshot, created_at
            ) values (
              cast(:assessment_id as uuid), cast(:attempt_record_id as uuid),
              :command_id, cast(:user_id as uuid), cast(:session_id as uuid),
              :question_id, cast(:answer_version_id as uuid),
              :content_version_id, 'test-scoring-v1', :assessment_key_hash,
              :result_code, :correct, cast(:result_snapshot as jsonb), :created_at
            )
            """
        ),
        {
            "assessment_id": str(uuid4()),
            "attempt_record_id": attempt_record_id,
            "command_id": command_id,
            "user_id": user_id,
            "session_id": session_id,
            "question_id": question_id,
            "answer_version_id": answer_version_id,
            "content_version_id": _digest(f"content:{question_id}"),
            "assessment_key_hash": _digest(f"assessment:{user_id}:{session_id}:{question_id}"),
            "result_code": verdict,
            "correct": correct,
            "result_snapshot": json.dumps(
                {
                    "objectiveVerdict": verdict,
                    "timingEvidence": {"elapsedSeconds": 30},
                }
            ),
            "created_at": created_at,
        },
    )


def _insert_complete_ability_estimate(session: Session, *, user_id: str) -> None:
    session.execute(
        text(
            """
            insert into learning.ability_estimate_versions (
              estimate_version_id, user_id, exam_code, exam_year, status,
              xingce_lower, xingce_recommended, xingce_upper,
              shenlun_lower, shenlun_recommended, shenlun_upper,
              total_lower, total_recommended, total_upper,
              confidence, source_evidence_ids, evidence_summary,
              algorithm_version, input_fingerprint,
              generated_at, valid_until
            ) values (
              cast(:estimate_id as uuid), cast(:user_id as uuid),
              'guokao', 2026, 'complete', 58, 62, 66, 52, 56, 60,
              110, 118, 126, 'medium', '[]'::jsonb,
              cast(:evidence_summary as jsonb), 'test-estimate-v1',
              :fingerprint, :generated_at, :valid_until
            )
            """
        ),
        {
            "estimate_id": str(uuid4()),
            "user_id": user_id,
            "fingerprint": _digest(f"estimate:{user_id}"),
            "evidence_summary": json.dumps({"evidenceCount": 12}),
            "generated_at": NOW,
            "valid_until": NOW + timedelta(days=30),
        },
    )


def _insert_active_knowledge_node(
    session: Session,
    *,
    module_name: str,
    tag_value: str,
    question_ids: list[str],
    now: datetime,
) -> None:
    question_rows = list(
        session.execute(
            text(
                """
                select
                  question_id, module_name, stem, answer,
                  occurrence_count, latest_year
                from product.questions
                where question_id = any(cast(:question_ids as text[]))
                order by array_position(
                  cast(:question_ids as text[]),
                  question_id
                )
                """
            ),
            {"question_ids": question_ids},
        ).mappings()
    )
    questions = tuple(
        FrozenQuestionSeed(
            question_id=str(row["question_id"]),
            module_name=str(row["module_name"]),
            stem=str(row["stem"]),
            answer=str(row["answer"]),
            node_id="assessment-facts-language-node",
            occurrence_count=int(row["occurrence_count"]),
            latest_year=int(row["latest_year"]),
        )
        for row in question_rows
    )
    seed_canonical_frozen_release(
        session,
        release_key="assessment-facts-targeted",
        questions=questions,
        nodes=(
            FrozenKnowledgeNodeSeed(
                node_id="assessment-facts-language-node",
                module_name=module_name,
                tag_value=tag_value,
                near5_weight=0.8,
                near5_occurrence_count=12,
            ),
        ),
        reference_time=now,
    )


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
