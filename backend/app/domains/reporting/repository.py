from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class ReportingRepository:
    def lock_user(self, session: Session, *, user_id: str) -> None:
        owner = session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise LookupError("report owner does not exist")

    def confirmed_printed_recognition(
        self,
        session: Session,
        *,
        user_id: str,
        recognition_revision_id: str,
        artifact_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select recognition.*, artifact.content_sha256, artifact.mime_type,
                       artifact.source_kind
                from learning.recognition_revisions recognition
                join learning.source_artifact_versions artifact
                  on artifact.artifact_version_id = recognition.artifact_version_id
                 and artifact.owner_user_id = recognition.owner_user_id
                where recognition.recognition_revision_id = cast(:recognition_id as uuid)
                  and recognition.artifact_version_id = cast(:artifact_id as uuid)
                  and recognition.owner_user_id = cast(:user_id as uuid)
                  and recognition.recognition_kind = 'printed_ocr'
                  and recognition.state = 'confirmed'
                  and artifact.state = 'available'
                  and artifact.source_kind in ('camera_image', 'image_import')
                  and artifact.mime_type like 'image/%'
                  and not exists (
                    select 1 from learning.recognition_revisions successor
                    where successor.supersedes_recognition_revision_id =
                          recognition.recognition_revision_id
                  )
                """
            ),
            {
                "recognition_id": recognition_revision_id,
                "artifact_id": artifact_version_id,
                "user_id": user_id,
            },
        ).mappings().one_or_none()

    def report_for_command(
        self, session: Session, *, user_id: str, idempotency_key: str
    ) -> tuple[RowMapping, RowMapping] | None:
        command = session.execute(
            text(
                """
                select * from learning.external_mock_report_commands
                where user_id = cast(:user_id as uuid)
                  and idempotency_key = :idempotency_key
                """
            ),
            {"user_id": user_id, "idempotency_key": idempotency_key},
        ).mappings().one_or_none()
        if command is None:
            return None
        report = self.report(
            session,
            user_id=user_id,
            report_version_id=str(command["report_version_id"]),
        )
        assert report is not None
        return command, report

    def current_for_recognition(
        self,
        session: Session,
        *,
        user_id: str,
        recognition_revision_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select report.*
                from learning.external_mock_report_versions report
                where report.user_id = cast(:user_id as uuid)
                  and report.recognition_revision_id = cast(:recognition_id as uuid)
                  and not exists (
                    select 1 from learning.external_mock_report_versions successor
                    where successor.supersedes_report_version_id = report.report_version_id
                  )
                order by report.version_no desc
                limit 1
                """
            ),
            {"user_id": user_id, "recognition_id": recognition_revision_id},
        ).mappings().one_or_none()

    def insert_report(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.external_mock_report_versions (
                  report_version_id, report_id, command_id, user_id, version_no,
                  state, artifact_version_id, recognition_revision_id,
                  platform_name, exam_name, occurred_at, timed_status,
                  duration_minutes, total_score, xingce_score, shenlun_score,
                  module_scores, evidence_grade, score_evidence_id,
                  estimate_version_id, input_fingerprint,
                  supersedes_report_version_id, confirmed_at, created_at
                ) values (
                  cast(:report_version_id as uuid), cast(:report_id as uuid),
                  :command_id, cast(:user_id as uuid), :version_no, :state,
                  cast(:artifact_version_id as uuid),
                  cast(:recognition_revision_id as uuid),
                  :platform_name, :exam_name, :occurred_at, :timed_status,
                  :duration_minutes, :total_score, :xingce_score, :shenlun_score,
                  cast(:module_scores as jsonb), :evidence_grade,
                  cast(:score_evidence_id as uuid), cast(:estimate_version_id as uuid),
                  :input_fingerprint, cast(:supersedes_report_version_id as uuid),
                  :confirmed_at, :created_at
                )
                returning *
                """
            ),
            {
                **values,
                "module_scores": json.dumps(
                    values.get("module_scores", {}), ensure_ascii=False, sort_keys=True
                ),
            },
        ).mappings().one()

    def insert_command(
        self,
        session: Session,
        *,
        user_id: str,
        idempotency_key: str,
        command_type: str,
        request_fingerprint: str,
        report_version_id: str,
        response_payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.external_mock_report_commands (
                  user_id, idempotency_key, command_type, request_fingerprint,
                  report_version_id, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :idempotency_key, :command_type,
                  :request_fingerprint, cast(:report_version_id as uuid),
                  cast(:response_payload as jsonb), :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "idempotency_key": idempotency_key,
                "command_type": command_type,
                "request_fingerprint": request_fingerprint,
                "report_version_id": report_version_id,
                "response_payload": json.dumps(
                    response_payload, ensure_ascii=False, sort_keys=True
                ),
                "created_at": created_at,
            },
        )

    def report(
        self,
        session: Session,
        *,
        user_id: str,
        report_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.external_mock_report_versions
                where report_version_id = cast(:report_version_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {"report_version_id": report_version_id, "user_id": user_id},
        ).mappings().one_or_none()

    def confirmed_for_artifact(
        self,
        session: Session,
        *,
        user_id: str,
        artifact_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.external_mock_report_versions
                where user_id = cast(:user_id as uuid)
                  and artifact_version_id = cast(:artifact_version_id as uuid)
                  and state = 'confirmed'
                limit 1
                """
            ),
            {"user_id": user_id, "artifact_version_id": artifact_version_id},
        ).mappings().one_or_none()

    def latest_confirmed_external(
        self, session: Session, *, user_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.external_mock_report_versions
                where user_id = cast(:user_id as uuid) and state = 'confirmed'
                order by occurred_at desc, confirmed_at desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def external_history(self, session: Session, *, user_id: str) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select * from learning.external_mock_report_versions
                    where user_id = cast(:user_id as uuid) and state = 'confirmed'
                    order by occurred_at, report_version_id
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )

    def mock_sessions(self, session: Session, *, user_id: str) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select session.session_id, session.status, session.started_at,
                           session.submitted_at, session.review_payload,
                           session.paper_version_id, session.scoring_policy_version_id,
                           paper.title, paper.duration_minutes, paper.question_ids,
                           policy.metric_name, policy.metric_unit, policy.definition
                    from learner.mock_exam_sessions session
                    join product.mock_exam_paper_versions paper
                      on paper.paper_version_id = session.paper_version_id
                    join product.mock_exam_scoring_policy_versions policy
                      on policy.scoring_policy_version_id = session.scoring_policy_version_id
                    where session.user_id = cast(:user_id as uuid)
                    order by session.created_at desc
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )

    def latest_mock_session(
        self, session: Session, *, user_id: str
    ) -> RowMapping | None:
        rows = self.mock_sessions(session, user_id=user_id)
        return rows[0] if rows else None

    def ability_history(self, session: Session, *, user_id: str) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select * from learning.ability_estimate_versions
                    where user_id = cast(:user_id as uuid)
                    order by generated_at, estimate_version_id
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )

    def current_goal(self, session: Session, *, user_id: str) -> RowMapping | None:
        return session.execute(
            text(
                """
                select goal.*, position.name as position_name,
                       position.department_full as department_name
                from learner.position_goal_versions goal
                join product.positions position on position.position_id = goal.position_id
                where goal.user_id = cast(:user_id as uuid)
                  and goal.role = 'primary' and goal.status = 'confirmed'
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def recent_set_summaries(
        self, session: Session, *, user_id: str, limit: int = 12
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select assessment.session_id,
                           min(assessment.created_at) as occurred_at,
                           count(*) as total_count,
                           count(*) filter (where assessment.correct) as correct_count,
                           coalesce(sum((attempt.timing_evidence->>'questionElapsedSeconds')::numeric), 0)
                             as elapsed_seconds
                    from learning.xingce_assessments assessment
                    join learning.xingce_attempt_records attempt
                      on attempt.attempt_record_id = assessment.attempt_record_id
                    where assessment.user_id = cast(:user_id as uuid)
                    group by assessment.session_id
                    order by min(assessment.created_at) desc
                    limit :limit
                    """
                ),
                {"user_id": user_id, "limit": limit},
            ).mappings()
        )

    def weekly_execution_summary(
        self, session: Session, *, user_id: str
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    with current_execution as (
                      select execution.*
                      from learning.task_execution_versions execution
                      where execution.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1 from learning.task_execution_versions successor
                          where successor.supersedes_execution_version_id =
                                execution.execution_version_id
                        )
                    )
                    select task.track, task.kind,
                           count(*) as planned_count,
                           count(execution.execution_version_id) filter (
                             where execution.status = 'complete'
                           ) as completed_count,
                           coalesce(sum(execution.elapsed_minutes) filter (
                             where execution.status = 'complete'
                           ), 0) as completed_minutes
                    from learning.daily_plan_tasks task
                    left join current_execution execution
                      on execution.task_id = task.task_id
                    where task.user_id = cast(:user_id as uuid)
                      and task.local_date >= current_date - 6
                      and task.local_date <= current_date
                    group by task.track, task.kind
                    order by task.track, task.kind
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )

    def projection_by_fingerprint(
        self,
        session: Session,
        *,
        user_id: str,
        report_kind: str,
        subject_ref: str,
        input_fingerprint: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.report_projection_versions
                where user_id = cast(:user_id as uuid)
                  and report_kind = :report_kind
                  and subject_ref = :subject_ref
                  and input_fingerprint = :input_fingerprint
                """
            ),
            {
                "user_id": user_id,
                "report_kind": report_kind,
                "subject_ref": subject_ref,
                "input_fingerprint": input_fingerprint,
            },
        ).mappings().one_or_none()

    def insert_projection(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.report_projection_versions (
                  projection_version_id, user_id, report_kind, subject_ref,
                  chart_contract_version, input_fingerprint, payload,
                  evidence_refs, next_plan_evidence_refs, generated_at
                ) values (
                  cast(:projection_version_id as uuid), cast(:user_id as uuid),
                  :report_kind, :subject_ref, 'report-chart@v1', :input_fingerprint,
                  cast(:payload as jsonb), cast(:evidence_refs as jsonb),
                  cast(:next_plan_evidence_refs as jsonb), :generated_at
                ) returning *
                """
            ),
            {
                **values,
                "payload": json.dumps(values["payload"], ensure_ascii=False, sort_keys=True),
                "evidence_refs": json.dumps(
                    values["evidence_refs"], ensure_ascii=False, sort_keys=True
                ),
                "next_plan_evidence_refs": json.dumps(
                    values["next_plan_evidence_refs"], ensure_ascii=False, sort_keys=True
                ),
            },
        ).mappings().one()
