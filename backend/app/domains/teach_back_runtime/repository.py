from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class TeachBackRuntimeRepository:
    def by_command(
        self, session: Session, *, user_id: str, command_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.xingce_teach_back_session_versions
                where user_id = cast(:user_id as uuid) and command_id = :command_id
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def current(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update of current" if for_update else ""
        return session.execute(
            text(
                """
                select current.*
                from learning.xingce_teach_back_session_versions current
                where current.user_id = cast(:user_id as uuid)
                  and current.session_id = cast(:session_id as uuid)
                  and not exists (
                    select 1 from learning.xingce_teach_back_session_versions successor
                    where successor.supersedes_session_version_id = current.session_version_id
                  )
                """ + suffix
            ),
            {"user_id": user_id, "session_id": session_id},
        ).mappings().one_or_none()

    def insert_initial(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.xingce_teach_back_session_versions (
                  session_version_id, session_id, command_id, request_fingerprint,
                  user_id, revision, state, review_id, source_question_id,
                  package_fingerprint, package_authority, user_explanation,
                  student_output, grounding_output, call_audits,
                  transfer_selected_option_ids, transfer_confidence, transfer_correct,
                  mastery_version_id, supersedes_session_version_id, created_at
                ) values (
                  cast(:session_version_id as uuid), cast(:session_id as uuid),
                  :command_id, :request_fingerprint, cast(:user_id as uuid), 1,
                  :state, cast(:review_id as uuid), :source_question_id,
                  :package_fingerprint, cast(:package_authority as jsonb),
                  :user_explanation, cast(:student_output as jsonb),
                  cast(:grounding_output as jsonb), cast(:call_audits as jsonb),
                  null, null, null, null, null, :created_at
                ) returning *
                """
            ),
            _json_values(values, "package_authority", "student_output", "grounding_output", "call_audits"),
        ).mappings().one()

    def insert_completed(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.xingce_teach_back_session_versions (
                  session_version_id, session_id, command_id, request_fingerprint,
                  user_id, revision, state, review_id, source_question_id,
                  package_fingerprint, package_authority, user_explanation,
                  student_output, grounding_output, call_audits,
                  transfer_selected_option_ids, transfer_confidence, transfer_correct,
                  mastery_version_id, supersedes_session_version_id, created_at
                ) values (
                  cast(:session_version_id as uuid), cast(:session_id as uuid),
                  :command_id, :request_fingerprint, cast(:user_id as uuid),
                  :revision, 'completed', cast(:review_id as uuid), :source_question_id,
                  :package_fingerprint, cast(:package_authority as jsonb),
                  :user_explanation, cast(:student_output as jsonb),
                  cast(:grounding_output as jsonb), cast(:call_audits as jsonb),
                  cast(:transfer_selected_option_ids as jsonb), :transfer_confidence,
                  :transfer_correct, cast(:mastery_version_id as uuid),
                  cast(:supersedes_session_version_id as uuid), :created_at
                ) returning *
                """
            ),
            _json_values(
                values,
                "package_authority",
                "student_output",
                "grounding_output",
                "call_audits",
                "transfer_selected_option_ids",
            ),
        ).mappings().one()

    def xingce_command(
        self, session: Session, *, user_id: str, command_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.xingce_commands "
                "where user_id = cast(:user_id as uuid) and command_id = :command_id"
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def insert_xingce_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        session_id: str,
        request_fingerprint: str,
        response_payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  user_id, command_id, command_type, target_id,
                  request_fingerprint, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :command_id, 'teach_back_transfer',
                  cast(:session_id as uuid), :request_fingerprint,
                  cast(:response_payload as jsonb), :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "session_id": session_id,
                "request_fingerprint": request_fingerprint,
                "response_payload": json.dumps(
                    response_payload, ensure_ascii=False, sort_keys=True
                ),
                "created_at": created_at,
            },
        )

    def mastery_version(
        self, session: Session, *, user_id: str, mastery_version_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.xingce_mastery_state_versions
                where user_id = cast(:user_id as uuid)
                  and mastery_version_id = cast(:mastery_version_id as uuid)
                """
            ),
            {"user_id": user_id, "mastery_version_id": mastery_version_id},
        ).mappings().one_or_none()


def _json_values(values: dict[str, Any], *keys: str) -> dict[str, Any]:
    encoded = dict(values)
    for key in keys:
        encoded[key] = json.dumps(encoded[key], ensure_ascii=False, sort_keys=True)
    return encoded
