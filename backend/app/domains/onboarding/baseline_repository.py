from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.onboarding.progress_repository import OnboardingProgressError
from app.domains.onboarding.v2_schemas import AbilityBaselineProfile


class OnboardingBaselineRepository:
    def lock_owner(self, session: Session, *, user_id: str) -> None:
        owner = session.execute(
            text(
                """
                select user_id
                from learner.users
                where user_id = cast(:user_id as uuid)
                for update
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise OnboardingProgressError(
                "learner_missing",
                "当前备考账户不存在。",
            )

    def current_row(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> Mapping[str, Any] | None:
        return session.execute(
            text(
                """
                select declaration.*, evidence.xingce_score, evidence.shenlun_score
                from learner.ability_baseline_declaration_versions declaration
                left join learning.score_evidence evidence
                  on evidence.evidence_id = declaration.score_evidence_id
                where declaration.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from learner.ability_baseline_declaration_versions successor
                    where successor.supersedes_baseline_version_id =
                          declaration.baseline_version_id
                  )
                order by declaration.version_no desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def by_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> Mapping[str, Any] | None:
        return session.execute(
            text(
                """
                select declaration.*, evidence.xingce_score, evidence.shenlun_score
                from learner.ability_baseline_declaration_versions declaration
                left join learning.score_evidence evidence
                  on evidence.evidence_id = declaration.score_evidence_id
                where declaration.user_id = cast(:user_id as uuid)
                  and declaration.command_id = :command_id
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def append(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        expected_baseline_version: int,
        mode: str,
        score_evidence_id: str | None,
        input_fingerprint: str,
        now: datetime,
    ) -> AbilityBaselineProfile:
        current = self.current_row(session, user_id=user_id)
        current_version = int(current["version_no"]) if current is not None else 0
        if current_version != expected_baseline_version:
            raise OnboardingProgressError(
                "ability_baseline_version_conflict",
                "能力起点已更新，请刷新后再保存。",
                details={
                    "expectedVersion": expected_baseline_version,
                    "currentVersion": current_version,
                },
            )
        baseline_version_id = str(uuid4())
        row = session.execute(
            text(
                """
                insert into learner.ability_baseline_declaration_versions (
                  baseline_version_id, command_id, user_id, version_no, mode,
                  score_evidence_id, source_type, input_fingerprint,
                  supersedes_baseline_version_id, created_at
                ) values (
                  cast(:baseline_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :version_no, :mode,
                  cast(:score_evidence_id as uuid), :source_type,
                  :input_fingerprint, cast(:supersedes_id as uuid), :created_at
                )
                returning *
                """
            ),
            {
                "baseline_version_id": baseline_version_id,
                "command_id": command_id,
                "user_id": user_id,
                "version_no": current_version + 1,
                "mode": mode,
                "score_evidence_id": score_evidence_id,
                "source_type": (
                    "user_revised" if current is not None else "user_confirmed"
                ),
                "input_fingerprint": input_fingerprint,
                "supersedes_id": (
                    str(current["baseline_version_id"])
                    if current is not None
                    else None
                ),
                "created_at": now,
            },
        ).mappings().one()
        joined = dict(row)
        joined["xingce_score"] = None
        joined["shenlun_score"] = None
        if score_evidence_id is not None:
            evidence = session.execute(
                text(
                    """
                    select xingce_score, shenlun_score
                    from learning.score_evidence
                    where evidence_id = cast(:evidence_id as uuid)
                    """
                ),
                {"evidence_id": score_evidence_id},
            ).mappings().one()
            joined.update(evidence)
        return ability_baseline_from_row(joined)


def ability_baseline_from_row(row: Mapping[str, Any]) -> AbilityBaselineProfile:
    return AbilityBaselineProfile(
        baselineVersionId=str(row["baseline_version_id"]),
        baselineVersion=int(row["version_no"]),
        mode=str(row["mode"]),
        xingceScore=(
            float(row["xingce_score"])
            if row.get("xingce_score") is not None
            else None
        ),
        shenlunScore=(
            float(row["shenlun_score"])
            if row.get("shenlun_score") is not None
            else None
        ),
        scoreEvidenceId=(
            str(row["score_evidence_id"])
            if row.get("score_evidence_id") is not None
            else None
        ),
        sourceType=str(row["source_type"]),
        createdAt=row["created_at"],
        supersedesBaselineVersionId=(
            str(row["supersedes_baseline_version_id"])
            if row.get("supersedes_baseline_version_id") is not None
            else None
        ),
    )
