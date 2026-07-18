from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class RetakeProgramRecord:
    program_version_id: UUID
    source_score_evidence_id: UUID
    source_exam_year: int
    target_exam_year: int
    state: str
    loss_structure: dict[str, object]
    created_at: datetime


class RetakeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_owner(self, *, user_id: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"retake-program:{user_id}"},
        )

    def current_program(self, *, user_id: str) -> RetakeProgramRecord | None:
        row = self._session.execute(
            text(
                """
                select *
                from learning.retake_program_versions
                where user_id = cast(:user_id as uuid)
                  and state = 'active'
                order by version_no desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        return _program(row) if row is not None else None

    def program_by_command(
        self,
        *,
        user_id: str,
        command_key: str,
    ) -> Mapping[str, Any] | None:
        return self._session.execute(
            text(
                """
                select *
                from learning.retake_program_versions
                where user_id = cast(:user_id as uuid)
                  and command_key = :command_key
                """
            ),
            {"user_id": user_id, "command_key": command_key},
        ).mappings().one_or_none()

    def eligible_score_evidence(
        self,
        *,
        user_id: str,
        target_exam_year: int,
    ) -> list[Mapping[str, Any]]:
        return list(
            self._session.execute(
                text(
                    """
                    select evidence.*
                    from learning.score_evidence evidence
                    where evidence.user_id = cast(:user_id as uuid)
                      and evidence.exam_code = 'guokao'
                      and evidence.exam_year < :target_exam_year
                      and evidence.verification_status in (
                        'user_confirmed', 'system_verified'
                      )
                      and (
                        evidence.source_type = 'official_exam'
                        or (
                          evidence.source_type = 'self_report'
                          and evidence.reported_context = 'official_exam'
                        )
                      )
                      and not exists (
                        select 1 from learning.score_evidence successor
                        where successor.supersedes_evidence_id = evidence.evidence_id
                      )
                    order by evidence.exam_year desc, evidence.occurred_at desc,
                             evidence.evidence_id
                    """
                ),
                {"user_id": user_id, "target_exam_year": target_exam_year},
            ).mappings()
        )

    def eligible_score_evidence_by_id(
        self,
        *,
        user_id: str,
        evidence_id: UUID,
        target_exam_year: int,
    ) -> Mapping[str, Any] | None:
        return next(
            (
                row
                for row in self.eligible_score_evidence(
                    user_id=user_id,
                    target_exam_year=target_exam_year,
                )
                if UUID(str(row["evidence_id"])) == evidence_id
            ),
            None,
        )

    def insert_program(
        self,
        *,
        user_id: str,
        command_key: str,
        evidence: Mapping[str, Any],
        target_exam_year: int,
        loss_structure: dict[str, object],
        now: datetime,
    ) -> RetakeProgramRecord:
        program_version_id = uuid4()
        program_id = uuid4()
        evidence_id = UUID(str(evidence["evidence_id"]))
        payload = {
            "sourceScoreEvidenceId": str(evidence_id),
            "targetExamYear": target_exam_year,
            "lossStructure": loss_structure,
        }
        row = self._session.execute(
            text(
                """
                insert into learning.retake_program_versions (
                  program_version_id, program_id, command_key, user_id,
                  source_score_evidence_id, source_exam_year, target_exam_year,
                  version_no, state, loss_structure, score_use_boundary,
                  evidence_refs, input_fingerprint, created_at
                ) values (
                  cast(:program_version_id as uuid), cast(:program_id as uuid),
                  :command_key, cast(:user_id as uuid), cast(:evidence_id as uuid),
                  :source_year, :target_year, 1, 'active',
                  cast(:loss_structure as jsonb), cast(:score_boundary as jsonb),
                  cast(:evidence_refs as jsonb), :input_fingerprint, :created_at
                ) returning *
                """
            ),
            {
                "program_version_id": str(program_version_id),
                "program_id": str(program_id),
                "command_key": command_key,
                "user_id": user_id,
                "evidence_id": str(evidence_id),
                "source_year": int(evidence["exam_year"]),
                "target_year": target_exam_year,
                "loss_structure": _json(loss_structure),
                "score_boundary": _json(
                    {"refund": False, "cashReward": False, "training": True}
                ),
                "evidence_refs": _json([f"score-evidence:{evidence_id}"]),
                "input_fingerprint": _fingerprint(payload),
                "created_at": now,
            },
        ).mappings().one()
        return _program(row)


def _program(row: Mapping[str, Any]) -> RetakeProgramRecord:
    return RetakeProgramRecord(
        program_version_id=UUID(str(row["program_version_id"])),
        source_score_evidence_id=UUID(str(row["source_score_evidence_id"])),
        source_exam_year=int(row["source_exam_year"]),
        target_exam_year=int(row["target_exam_year"]),
        state=str(row["state"]),
        loss_structure=dict(row["loss_structure"]),
        created_at=row["created_at"],
    )


def _fingerprint(payload: object) -> str:
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
