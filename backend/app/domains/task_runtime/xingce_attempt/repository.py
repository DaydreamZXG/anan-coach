from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class XingceTaskRuntimeRepository:
    """Owns the mutable session and immutable answer/attempt runtime facts."""

    def lock_user(self, session: Session, *, user_id: str) -> None:
        owner = session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise LookupError("learner_not_found")

    def command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    "select * from learning.xingce_commands "
                    "where command_id = :command_id and user_id = cast(:user_id as uuid)"
                ),
                {"command_id": command_id, "user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )

    def record_command(
        self,
        session: Session,
        *,
        command_id: str,
        user_id: str,
        command_type: str,
        target_id: str | None,
        request_fingerprint: str,
        response_payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  command_id, user_id, command_type, target_id,
                  request_fingerprint, response_payload, created_at
                ) values (
                  :command_id, cast(:user_id as uuid), :command_type,
                  cast(:target_id as uuid), :request_fingerprint,
                  cast(:response_payload as jsonb), :created_at
                )
                """
            ),
            {
                "command_id": command_id,
                "user_id": user_id,
                "command_type": command_type,
                "target_id": target_id,
                "request_fingerprint": request_fingerprint,
                "response_payload": _json(response_payload),
                "created_at": created_at,
            },
        )

    def current_plan_task(
        self,
        session: Session,
        *,
        user_id: str,
        plan_task_id: str,
        now: datetime,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select task.*, plan.valid_until
                from learning.daily_plan_tasks task
                join learning.daily_plan_versions plan
                  on plan.plan_version_id = task.plan_version_id
                 and plan.user_id = task.user_id
                 and plan.local_date = task.local_date
                 and plan.version_no = task.plan_version_no
                where task.task_id = cast(:task_id as uuid)
                  and task.user_id = cast(:user_id as uuid)
                  and task.track = 'xingce'
                  and task.stage in (
                    'objective_baseline', 'objective_practice',
                    'objective_verification'
                  )
                  and plan.valid_until > :now
                  and not exists (
                    select 1 from learning.daily_plan_versions newer
                    where newer.user_id = plan.user_id
                      and newer.local_date = plan.local_date
                      and newer.version_no > plan.version_no
                  )
                  and not exists (
                    select 1 from learning.task_execution_versions execution
                    where execution.user_id = task.user_id
                      and execution.work_item_key = task.work_item_key
                      and execution.status in ('complete', 'abandoned')
                      and not exists (
                        select 1 from learning.task_execution_versions successor
                        where successor.supersedes_execution_version_id =
                              execution.execution_version_id
                      )
                  )
                for update of task
                """
                ),
                {"task_id": plan_task_id, "user_id": user_id, "now": now},
            )
            .mappings()
            .one_or_none()
        )

    def session_by_work_item(
        self,
        session: Session,
        *,
        user_id: str,
        work_item_key: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    "select * from learning.xingce_task_sessions "
                    "where user_id = cast(:user_id as uuid) and work_item_key = :work_item_key"
                ),
                {"user_id": user_id, "work_item_key": work_item_key},
            )
            .mappings()
            .one_or_none()
        )

    def task_session(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update" if for_update else ""
        return (
            session.execute(
                text(
                    "select * from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid) "
                    "and user_id = cast(:user_id as uuid)" + suffix
                ),
                {"session_id": session_id, "user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )

    def insert_task_session(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_task_sessions (
                  session_id, user_id, source_plan_task_id, source_plan_version_id,
                  local_date, work_item_key, mode, state, revision,
                  bundle_version_id, safe_bundle, scoring_bundle,
                  source_mastery_id, source_mastery_version_id,
                  current_question_id, checkpoint, total_elapsed_seconds,
                  created_at, updated_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  cast(:source_plan_task_id as uuid), cast(:source_plan_version_id as uuid),
                  :local_date, :work_item_key, :mode, 'active', 1,
                  :bundle_version_id, cast(:safe_bundle as jsonb),
                  cast(:scoring_bundle as jsonb),
                  cast(:source_mastery_id as uuid),
                  cast(:source_mastery_version_id as uuid), :current_question_id,
                  cast(:checkpoint as jsonb), 0, :created_at, :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def update_checkpoint(
        self,
        session: Session,
        *,
        session_id: str,
        user_id: str,
        expected_revision: int,
        current_question_id: str,
        checkpoint: dict[str, Any],
        total_elapsed_seconds: int,
        updated_at: datetime,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                update learning.xingce_task_sessions
                set revision = revision + 1,
                    current_question_id = :current_question_id,
                    checkpoint = cast(:checkpoint as jsonb),
                    total_elapsed_seconds = :total_elapsed_seconds,
                    updated_at = :updated_at
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and revision = :expected_revision
                  and state = 'active'
                returning *
                """
                ),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "expected_revision": expected_revision,
                    "current_question_id": current_question_id,
                    "checkpoint": _json(checkpoint),
                    "total_elapsed_seconds": total_elapsed_seconds,
                    "updated_at": updated_at,
                },
            )
            .mappings()
            .one_or_none()
        )

    def latest_answer_version(
        self,
        session: Session,
        *,
        session_id: str,
        question_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select answer.*
                from learning.xingce_answer_versions answer
                where answer.session_id = cast(:session_id as uuid)
                  and answer.question_id = :question_id
                  and not exists (
                    select 1 from learning.xingce_answer_versions successor
                    where successor.supersedes_answer_version_id = answer.answer_version_id
                  )
                """
                ),
                {"session_id": session_id, "question_id": question_id},
            )
            .mappings()
            .one_or_none()
        )

    def answer_versions(
        self,
        session: Session,
        *,
        session_id: str,
        question_id: str,
    ) -> list[RowMapping]:
        """按版本号升序返回一题的全部不可变作答版本（改答事实的来源）。"""
        return list(
            session.execute(
                text(
                    """
                    select answer.version_no, answer.payload
                    from learning.xingce_answer_versions answer
                    where answer.session_id = cast(:session_id as uuid)
                      and answer.question_id = :question_id
                    order by answer.version_no asc
                    """
                ),
                {"session_id": session_id, "question_id": question_id},
            )
            .mappings()
            .all()
        )

    def insert_answer_version(self, session: Session, *, values: dict[str, Any]) -> None:
        session.execute(
            text(
                """
                insert into learning.xingce_answer_versions (
                  answer_version_id, command_id, user_id, session_id, question_id,
                  version_no, payload, payload_sha256, supersedes_answer_version_id,
                  created_at
                ) values (
                  cast(:answer_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:session_id as uuid), :question_id,
                  :version_no, cast(:payload as jsonb), :payload_sha256,
                  cast(:supersedes_answer_version_id as uuid), :created_at
                )
                """
            ),
            values,
        )

    def insert_attempt_record(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_attempt_records (
                  attempt_record_id, attempt_group_id, item_attempt_id,
                  submit_command_id, user_id, session_id, question_id,
                  session_revision, answer_version_id, content_version_id,
                  scoring_policy_version, timing_evidence_ref, timing_evidence,
                  submission_hash, source_lineage_refs,
                  scratch_evidence_projection, scratch_evidence_fingerprint,
                  created_at
                ) values (
                  cast(:attempt_record_id as uuid), cast(:attempt_group_id as uuid),
                  :item_attempt_id, :submit_command_id, cast(:user_id as uuid),
                  cast(:session_id as uuid), :question_id, :session_revision,
                  cast(:answer_version_id as uuid), :content_version_id,
                  :scoring_policy_version, :timing_evidence_ref,
                  cast(:timing_evidence as jsonb), :submission_hash,
                  cast(:source_lineage_refs as jsonb),
                  cast(:scratch_evidence_projection as jsonb),
                  :scratch_evidence_fingerprint, :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def formula_versions_for_question(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        question_id: str,
    ) -> list[RowMapping]:
        """Return only version-chain heads frozen at submit time.

        A later user confirmation creates a successor and therefore cannot
        retroactively change an already persisted scratch projection.
        """

        return list(
            session.execute(
                text(
                    """
                    select formula.*, command.recognition_metadata
                    from learning.math_formula_versions formula
                    join learning.math_ink_commands command
                      on command.request_id = formula.request_id
                     and command.owner_user_id = formula.owner_user_id
                    where formula.owner_user_id = cast(:user_id as uuid)
                      and formula.session_id = cast(:session_id as uuid)
                      and formula.question_id = :question_id
                      and formula.state <> 'rejected'
                      and not exists (
                        select 1 from learning.math_formula_versions successor
                        where successor.supersedes_formula_version_id =
                              formula.formula_version_id
                      )
                    order by formula.created_at, formula.formula_version_id
                    """
                ),
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                },
            )
            .mappings()
            .all()
        )

    def scratch_projection_refs(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> list[str]:
        rows = session.execute(
            text(
                """
                select attempt_record_id, scratch_evidence_fingerprint
                from learning.xingce_attempt_records
                where user_id = cast(:user_id as uuid)
                  and session_id = cast(:session_id as uuid)
                  and scratch_evidence_projection is not null
                order by question_id
                """
            ),
            {"user_id": user_id, "session_id": session_id},
        ).mappings()
        return [
            "scratch-evidence-projection:"
            f"{row['attempt_record_id']}:{row['scratch_evidence_fingerprint']}"
            for row in rows
        ]

    def attempt_records(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select * from learning.xingce_attempt_records
                    where user_id = cast(:user_id as uuid)
                      and session_id = cast(:session_id as uuid)
                    order by question_id
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).mappings()
        )

    def submit_session(
        self,
        session: Session,
        *,
        session_id: str,
        user_id: str,
        expected_revision: int,
        attempt_group_id: str,
        total_elapsed_seconds: int,
        submitted_at: datetime,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                update learning.xingce_task_sessions
                set revision = revision + 1, state = 'submitted',
                    attempt_group_id = cast(:attempt_group_id as uuid),
                    total_elapsed_seconds = :total_elapsed_seconds,
                    submitted_at = :submitted_at, updated_at = :submitted_at
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and revision = :expected_revision
                  and state = 'active'
                returning *
                """
                ),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "expected_revision": expected_revision,
                    "attempt_group_id": attempt_group_id,
                    "total_elapsed_seconds": total_elapsed_seconds,
                    "submitted_at": submitted_at,
                },
            )
            .mappings()
            .one_or_none()
        )

    def settle_session(
        self,
        session: Session,
        *,
        session_id: str,
        user_id: str,
        updated_at: datetime,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                update learning.xingce_task_sessions
                set revision = revision + 1, state = 'settled',
                    updated_at = :updated_at,
                    completed_at = :updated_at
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and state = 'submitted'
                returning *
                """
                ),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "updated_at": updated_at,
                },
            )
            .mappings()
            .one_or_none()
        )

    def completion_payload(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        value = session.execute(
            text(
                """
                select command.response_payload->'completion'
                from learning.xingce_commands command
                where command.user_id = cast(:user_id as uuid)
                  and command.command_type in (
                    'attempt_submit', 'reflection_submit', 'cause_decision'
                  )
                  and command.response_payload->>'sessionId' = :session_id
                  and command.response_payload->'completion' <> 'null'::jsonb
                order by command.created_at desc
                limit 1
                """
            ),
            {"user_id": user_id, "session_id": session_id},
        ).scalar_one_or_none()
        return dict(value) if isinstance(value, dict) else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
