from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class QuestionQualityRepository:
    def lock_question(self, session: Session, *, question_id: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"question-quality:{question_id}"},
        )

    def command(self, session: Session, *, command_id: str) -> RowMapping | None:
        return session.execute(
            text(
                "select * from product.question_quality_commands "
                "where command_id = :command_id"
            ),
            {"command_id": command_id},
        ).mappings().one_or_none()

    def current_content(self, session: Session, *, question_id: str) -> RowMapping | None:
        return session.execute(
            text(
                """
                select question.question_id, question.status, head.content_version_id
                from product.questions question
                join product.question_content_heads head
                  on head.question_id = question.question_id
                where question.question_id = :question_id
                for update of question
                """
            ),
            {"question_id": question_id},
        ).mappings().one_or_none()

    def disputes(
        self,
        session: Session,
        *,
        dispute_ids: list[str],
        question_id: str,
        content_version_id: str,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select dispute_id, category
                    from learning.xingce_question_disputes
                    where dispute_id = any(cast(:dispute_ids as uuid[]))
                      and question_id = :question_id
                      and content_version_id = :content_version_id
                    order by dispute_id
                    """
                ),
                {
                    "dispute_ids": dispute_ids,
                    "question_id": question_id,
                    "content_version_id": content_version_id,
                },
            ).mappings().all()
        )

    def head(
        self, session: Session, *, question_id: str, content_version_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select head.*, version.version_no, version.actor_ref
                from product.question_quality_heads head
                join product.question_quality_decision_versions version
                  on version.decision_version_id = head.decision_version_id
                where head.question_id = :question_id
                  and head.content_version_id = :content_version_id
                for update of head
                """
            ),
            {"question_id": question_id, "content_version_id": content_version_id},
        ).mappings().one_or_none()

    def decision(
        self, session: Session, *, decision_version_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from product.question_quality_decision_versions "
                "where decision_version_id = cast(:decision_version_id as uuid)"
            ),
            {"decision_version_id": decision_version_id},
        ).mappings().one_or_none()

    def insert_decision(
        self, session: Session, *, values: dict[str, Any]
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into product.question_quality_decision_versions (
                  decision_version_id, decision_id, question_id, content_version_id,
                  version_no, decision, reason_code, rationale, authority_version,
                  actor_ref, evidence_refs, supersedes_decision_version_id, created_at
                ) values (
                  cast(:decision_version_id as uuid), cast(:decision_id as uuid),
                  :question_id, :content_version_id, :version_no, :decision,
                  :reason_code, :rationale, :authority_version, :actor_ref,
                  cast(:evidence_refs as jsonb),
                  cast(:supersedes_decision_version_id as uuid), :created_at
                ) returning *
                """
            ),
            {
                **values,
                "evidence_refs": json.dumps(
                    values["evidence_refs"], ensure_ascii=False, sort_keys=True
                ),
            },
        ).mappings().one()

    def bind_disputes(
        self,
        session: Session,
        *,
        decision_version_id: str,
        dispute_ids: list[str],
        created_at: datetime,
    ) -> None:
        for dispute_id in dispute_ids:
            session.execute(
                text(
                    """
                    insert into product.question_quality_decision_disputes (
                      decision_version_id, dispute_id, created_at
                    ) values (
                      cast(:decision_version_id as uuid), cast(:dispute_id as uuid),
                      :created_at
                    )
                    """
                ),
                {
                    "decision_version_id": decision_version_id,
                    "dispute_id": dispute_id,
                    "created_at": created_at,
                },
            )

    def move_head(
        self,
        session: Session,
        *,
        question_id: str,
        content_version_id: str,
        decision_id: str,
        decision_version_id: str,
        decision: str,
        updated_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into product.question_quality_heads (
                  question_id, content_version_id, decision_id,
                  decision_version_id, decision, updated_at
                ) values (
                  :question_id, :content_version_id, cast(:decision_id as uuid),
                  cast(:decision_version_id as uuid), :decision, :updated_at
                )
                on conflict (question_id, content_version_id) do update
                set decision_id = excluded.decision_id,
                    decision_version_id = excluded.decision_version_id,
                    decision = excluded.decision,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "question_id": question_id,
                "content_version_id": content_version_id,
                "decision_id": decision_id,
                "decision_version_id": decision_version_id,
                "decision": decision,
                "updated_at": updated_at,
            },
        )

    def invalidate_question(self, session: Session, *, question_id: str) -> None:
        session.execute(
            text(
                "update product.questions set status = 'invalidated' "
                "where question_id = :question_id and status in ('active', 'canonical')"
            ),
            {"question_id": question_id},
        )

    def insert_command(
        self,
        session: Session,
        *,
        command_id: str,
        actor_ref: str,
        request_fingerprint: str,
        decision_version_id: str,
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into product.question_quality_commands (
                  command_id, actor_ref, request_fingerprint,
                  decision_version_id, created_at
                ) values (
                  :command_id, :actor_ref, :request_fingerprint,
                  cast(:decision_version_id as uuid), :created_at
                )
                """
            ),
            {
                "command_id": command_id,
                "actor_ref": actor_ref,
                "request_fingerprint": request_fingerprint,
                "decision_version_id": decision_version_id,
                "created_at": created_at,
            },
        )

    def decision_dispute_ids(
        self, session: Session, *, decision_version_id: str
    ) -> list[str]:
        return [
            str(row["dispute_id"])
            for row in session.execute(
                text(
                    "select dispute_id from product.question_quality_decision_disputes "
                    "where decision_version_id = cast(:decision_version_id as uuid) "
                    "order by dispute_id"
                ),
                {"decision_version_id": decision_version_id},
            ).mappings()
        ]
