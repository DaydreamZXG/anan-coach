from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class EssayMasteryRepository:
    def by_command_prefix(
        self,
        session: Session,
        *,
        user_id: str,
        command_prefix: str,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    "select * from learning.essay_mastery_state_versions "
                    "where user_id = cast(:user_id as uuid) "
                    "and left(command_id, length(:prefix) + 1) = :prefix || ':' "
                    "order by finding_id"
                ),
                {"user_id": user_id, "prefix": command_prefix},
            ).mappings().all()
        )

    def by_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.essay_mastery_state_versions "
                "where user_id = cast(:user_id as uuid) and command_id = :command_id"
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def current_for_finding(
        self,
        session: Session,
        *,
        user_id: str,
        source_review_version_id: str,
        finding_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        lock = "for update" if for_update else ""
        return session.execute(
            text(
                f"""
                select item.*
                from learning.essay_mastery_state_versions item
                where item.user_id = cast(:user_id as uuid)
                  and item.source_review_version_id = cast(:review_id as uuid)
                  and item.finding_id = :finding_id
                  and not exists (
                    select 1 from learning.essay_mastery_state_versions successor
                    where successor.supersedes_mastery_version_id = item.mastery_version_id
                  )
                order by item.version_no desc
                limit 1
                {lock}
                """
            ),
            {
                "user_id": user_id,
                "review_id": source_review_version_id,
                "finding_id": finding_id,
            },
        ).mappings().one_or_none()

    def current_for_decision(
        self,
        session: Session,
        *,
        user_id: str,
        decision_artifact_version_id: str,
        for_update: bool = False,
    ) -> list[RowMapping]:
        lock = "for update of item" if for_update else ""
        return list(
            session.execute(
                text(
                    f"""
                    select item.*
                    from learning.essay_mastery_state_versions item
                    where item.user_id = cast(:user_id as uuid)
                      and item.finding_decision_artifact_version_id =
                          cast(:decision_id as uuid)
                      and not exists (
                        select 1 from learning.essay_mastery_state_versions successor
                        where successor.supersedes_mastery_version_id = item.mastery_version_id
                      )
                    order by item.finding_id
                    {lock}
                    """
                ),
                {"user_id": user_id, "decision_id": decision_artifact_version_id},
            ).mappings().all()
        )

    def current_for_user(self, session: Session, *, user_id: str) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select item.*
                    from learning.essay_mastery_state_versions item
                    where item.user_id = cast(:user_id as uuid)
                      and not exists (
                        select 1 from learning.essay_mastery_state_versions successor
                        where successor.supersedes_mastery_version_id = item.mastery_version_id
                      )
                    order by item.created_at desc, item.mastery_id
                    """
                ),
                {"user_id": user_id},
            ).mappings().all()
        )

    def insert(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_mastery_state_versions (
                  mastery_version_id, mastery_id, command_id, user_id, finding_id,
                  version_no, state, due_at, source_task_version_id,
                  source_attempt_id, source_answer_version_id, source_review_version_id,
                  finding_decision_artifact_version_id, rewrite_version_id,
                  transfer_verification_version_id, evidence_refs, policy_version,
                  input_fingerprint, supersedes_mastery_version_id, created_at
                ) values (
                  cast(:mastery_version_id as uuid), cast(:mastery_id as uuid),
                  :command_id, cast(:user_id as uuid), :finding_id, :version_no,
                  :state, :due_at, cast(:source_task_version_id as uuid),
                  cast(:source_attempt_id as uuid),
                  cast(:source_answer_version_id as uuid),
                  cast(:source_review_version_id as uuid),
                  cast(:finding_decision_artifact_version_id as uuid),
                  cast(:rewrite_version_id as uuid),
                  cast(:transfer_verification_version_id as uuid),
                  cast(:evidence_refs as jsonb), :policy_version, :input_fingerprint,
                  cast(:supersedes_mastery_version_id as uuid), :created_at
                )
                returning *
                """
            ),
            values,
        ).mappings().one()
