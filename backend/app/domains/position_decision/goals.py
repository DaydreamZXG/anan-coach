from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.position_decision.schemas import (
    PositionGoal,
    PrimaryGoalPlanImpact,
    PrimaryGoalPreview,
    TargetScoreRange,
)
from app.domains.positions.target_scores import TargetScoreExpiredError
from app.platform.outbox import OutboxRepository


class PositionGoalContractError(RuntimeError):
    pass


PRIMARY_GOAL_PREVIEW_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class ConfirmPrimaryGoalCommand:
    command_id: str
    preview_id: str
    user_id: str
    position_id: str
    eligibility_snapshot_id: str
    expected_previous_goal_version_id: str | None
    learner_profile_version: int
    position_data_version: str
    target_score: TargetScoreRange
    change_reason: str
    plan_impact: PrimaryGoalPlanImpact


class PositionGoalService:
    def __init__(self, outbox: OutboxRepository | None = None) -> None:
        self._outbox = outbox or OutboxRepository()

    def preview_plan_impact(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
        target_score: TargetScoreRange,
    ) -> PrimaryGoalPlanImpact:
        current = session.execute(
            text(
                """
                select position_id::text as position_id, target_score_recommended
                from learner.position_goal_versions
                where user_id = cast(:user_id as uuid)
                  and role = 'primary'
                  and status = 'confirmed'
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        previous_score = (
            float(current["target_score_recommended"]) if current is not None else None
        )
        return PrimaryGoalPlanImpact(
            requires_plan_rebuild=True,
            change_type="goal_changed" if current is not None else "goal_selected",
            position_changed=current is None or current["position_id"] != position_id,
            previous_target_score=previous_score,
            next_target_score=target_score.recommended,
            target_score_delta=(
                round(target_score.recommended - previous_score, 2)
                if previous_score is not None
                else None
            ),
            invalidates=["daily_plan", "weekly_plan", "score_gap"],
        )

    def create_primary_goal_preview(
        self,
        session: Session,
        *,
        request_id: str,
        user_id: str,
        position_id: str,
        eligibility_snapshot_id: str,
        learner_profile_version: int,
        position_data_version: str,
        target_score: TargetScoreRange,
        now: datetime | None = None,
    ) -> PrimaryGoalPreview:
        if not request_id.strip():
            raise PositionGoalContractError("Preview request ID is required")
        created_at = now or datetime.now(UTC)
        if target_score.valid_until <= created_at:
            raise TargetScoreExpiredError("Target-score evidence has expired")
        if target_score.evidence_level == "unavailable":
            raise PositionGoalContractError(
                "A primary goal preview requires an evidence-backed target score"
            )
        request_payload = {
            "userId": user_id,
            "positionId": position_id,
            "eligibilitySnapshotId": eligibility_snapshot_id,
        }
        existing = session.execute(
            text(
                """
                select *
                from learner.position_goal_previews
                where request_id = :request_id
                """
            ),
            {"request_id": request_id},
        ).mappings().one_or_none()
        if existing is not None:
            if existing["request_payload"] != request_payload:
                raise PositionGoalContractError(
                    "An existing preview request ID refers to different goal inputs"
                )
            return _preview_from_row(existing)

        snapshot = session.execute(
            text(
                """
                select snapshot.status, profile.profile_version
                from learner.eligibility_snapshots snapshot
                join learner.profiles profile on profile.user_id = snapshot.user_id
                where snapshot.snapshot_id = cast(:snapshot_id as uuid)
                  and snapshot.user_id = cast(:user_id as uuid)
                  and snapshot.position_id = cast(:position_id as uuid)
                  and snapshot.learner_profile_version = :learner_profile_version
                  and snapshot.position_data_version = :position_data_version
                """
            ),
            {
                "snapshot_id": eligibility_snapshot_id,
                "user_id": user_id,
                "position_id": position_id,
                "learner_profile_version": learner_profile_version,
                "position_data_version": position_data_version,
            },
        ).mappings().one_or_none()
        if snapshot is None or snapshot["status"] != "eligible":
            raise PositionGoalContractError(
                "Only an owned, confirmed eligible snapshot can be previewed"
            )
        if int(snapshot["profile_version"]) != learner_profile_version:
            raise PositionGoalContractError(
                "Learner profile changed; reevaluate eligibility"
            )

        current = session.execute(
            text(
                """
                select goal_version_id::text as goal_version_id
                from learner.position_goal_versions
                where user_id = cast(:user_id as uuid)
                  and role = 'primary'
                  and status = 'confirmed'
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        current_goal_version_id = str(current) if current is not None else None
        plan_impact = self.preview_plan_impact(
            session,
            user_id=user_id,
            position_id=position_id,
            target_score=target_score,
        )
        expires_at = created_at + PRIMARY_GOAL_PREVIEW_TTL
        row = session.execute(
            text(
                """
                insert into learner.position_goal_previews (
                  preview_id, request_id, user_id, position_id,
                  eligibility_snapshot_id, from_goal_version_id,
                  learner_profile_version, position_data_version,
                  target_score, plan_impact, request_payload,
                  expires_at, created_at
                ) values (
                  cast(:preview_id as uuid), :request_id, cast(:user_id as uuid),
                  cast(:position_id as uuid), cast(:eligibility_snapshot_id as uuid),
                  cast(:from_goal_version_id as uuid), :learner_profile_version,
                  :position_data_version, cast(:target_score as jsonb),
                  cast(:plan_impact as jsonb), cast(:request_payload as jsonb),
                  :expires_at, :created_at
                )
                on conflict (request_id) do nothing
                returning *
                """
            ),
            {
                "preview_id": str(uuid4()),
                "request_id": request_id,
                "user_id": user_id,
                "position_id": position_id,
                "eligibility_snapshot_id": eligibility_snapshot_id,
                "from_goal_version_id": current_goal_version_id,
                "learner_profile_version": learner_profile_version,
                "position_data_version": position_data_version,
                "target_score": json.dumps(
                    target_score.model_dump(by_alias=True, mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "plan_impact": json.dumps(
                    plan_impact.model_dump(by_alias=True, mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "request_payload": json.dumps(
                    request_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "expires_at": expires_at,
                "created_at": created_at,
            },
        ).mappings().one_or_none()
        if row is None:
            row = session.execute(
                text(
                    """
                    select *
                    from learner.position_goal_previews
                    where request_id = :request_id
                    """
                ),
                {"request_id": request_id},
            ).mappings().one()
            if row["request_payload"] != request_payload:
                raise PositionGoalContractError(
                    "An existing preview request ID refers to different goal inputs"
                )
        return _preview_from_row(row)

    def get_primary_goal_preview(
        self,
        session: Session,
        *,
        user_id: str,
        preview_id: str,
    ) -> PrimaryGoalPreview:
        row = session.execute(
            text(
                """
                select *
                from learner.position_goal_previews
                where preview_id = cast(:preview_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {"preview_id": preview_id, "user_id": user_id},
        ).mappings().one_or_none()
        if row is None:
            raise PositionGoalContractError("Primary goal preview does not exist")
        return _preview_from_row(row)

    def replay_confirm_primary_goal(
        self,
        session: Session,
        *,
        command_id: str,
        user_id: str,
        preview_id: str,
        change_reason: str,
    ) -> tuple[PositionGoal, PrimaryGoalPlanImpact] | None:
        existing = self._goal_for_command(session, command_id)
        if existing is None:
            return None
        payload = existing["command_payload"]
        if (
            payload["userId"] != user_id
            or payload["previewId"] != preview_id
            or payload["changeReason"] != change_reason
        ):
            raise PositionGoalContractError(
                "An existing command ID refers to a different goal selection"
            )
        return (
            _goal_from_row(existing),
            PrimaryGoalPlanImpact.model_validate(payload["planImpact"]),
        )

    def confirm_primary_goal(
        self,
        session: Session,
        *,
        command: ConfirmPrimaryGoalCommand,
        now: datetime | None = None,
    ) -> PositionGoal:
        if not command.command_id.strip() or not command.change_reason.strip():
            raise PositionGoalContractError("Command ID and change reason are required")
        if command.target_score.evidence_level == "unavailable":
            raise PositionGoalContractError(
                "A confirmed primary goal requires an evidence-backed target score"
            )
        command_payload = _command_payload(command)
        existing = self._goal_for_command(session, command.command_id)
        if existing is not None:
            if existing["command_payload"] != command_payload:
                raise PositionGoalContractError(
                    "An existing command ID refers to a different goal selection"
                )
            return _goal_from_row(existing)

        changed_at = now or datetime.now(UTC)
        if command.target_score.valid_until <= changed_at:
            raise TargetScoreExpiredError("Target-score evidence has expired")
        preview = session.execute(
            text(
                """
                select *
                from learner.position_goal_previews
                where preview_id = cast(:preview_id as uuid)
                  and user_id = cast(:user_id as uuid)
                for update
                """
            ),
            {"preview_id": command.preview_id, "user_id": command.user_id},
        ).mappings().one_or_none()
        if preview is None:
            raise PositionGoalContractError("Primary goal preview does not exist")
        if preview["used_at"] is not None:
            raise PositionGoalContractError("Primary goal preview has already been used")
        if preview["expires_at"] <= changed_at:
            raise PositionGoalContractError("Primary goal preview has expired")
        if (
            str(preview["position_id"]) != command.position_id
            or str(preview["eligibility_snapshot_id"])
            != command.eligibility_snapshot_id
            or (
                str(preview["from_goal_version_id"])
                if preview["from_goal_version_id"] is not None
                else None
            )
            != command.expected_previous_goal_version_id
            or int(preview["learner_profile_version"])
            != command.learner_profile_version
            or preview["position_data_version"] != command.position_data_version
            or preview["target_score"]
            != command.target_score.model_dump(by_alias=True, mode="json")
            or preview["plan_impact"]
            != command.plan_impact.model_dump(by_alias=True, mode="json")
        ):
            raise PositionGoalContractError("Primary goal preview does not match confirmation")

        profile_version = session.execute(
            text(
                """
                select profile_version
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                for update
                """
            ),
            {"user_id": command.user_id},
        ).scalar_one_or_none()
        if profile_version is None:
            raise PositionGoalContractError("Learner profile does not exist")
        if int(profile_version) != command.learner_profile_version:
            raise PositionGoalContractError("Learner profile changed; reevaluate eligibility")

        snapshot = session.execute(
            text(
                """
                select user_id::text as user_id, position_id::text as position_id,
                       learner_profile_version, position_data_version, status
                from learner.eligibility_snapshots
                where snapshot_id = cast(:snapshot_id as uuid)
                """
            ),
            {"snapshot_id": command.eligibility_snapshot_id},
        ).mappings().one_or_none()
        if snapshot is None:
            raise PositionGoalContractError("Eligibility snapshot does not exist")
        if (
            snapshot["user_id"] != command.user_id
            or snapshot["position_id"] != command.position_id
            or int(snapshot["learner_profile_version"]) != command.learner_profile_version
            or snapshot["position_data_version"] != command.position_data_version
        ):
            raise PositionGoalContractError("Eligibility snapshot does not match the goal input")
        if snapshot["status"] != "eligible":
            raise PositionGoalContractError(
                "Only a confirmed eligible snapshot can become the primary goal"
            )

        previous = session.execute(
            text(
                """
                select goal_version_id
                from learner.position_goal_versions
                where user_id = cast(:user_id as uuid)
                  and role = 'primary'
                  and status = 'confirmed'
                for update
                """
            ),
            {"user_id": command.user_id},
        ).scalar_one_or_none()
        previous_id = str(previous) if previous is not None else None
        if previous_id != command.expected_previous_goal_version_id:
            raise PositionGoalContractError(
                "Primary goal changed after preview; create a new preview"
            )
        if previous_id is not None:
            session.execute(
                text(
                    """
                    update learner.position_goal_versions
                    set status = 'retired'
                    where goal_version_id = cast(:goal_version_id as uuid)
                    """
                ),
                {"goal_version_id": previous_id},
            )

        goal_version_id = str(uuid4())
        row = session.execute(
            text(
                """
                insert into learner.position_goal_versions (
                  goal_version_id, user_id, position_id, role, status,
                  target_score_lower, target_score_recommended, target_score_upper,
                  target_score_evidence_level, target_score_sample_count,
                  target_score_valid_until, learner_profile_version,
                  eligibility_snapshot_id, position_data_version,
                  supersedes_goal_version_id, created_at
                ) values (
                  cast(:goal_version_id as uuid), cast(:user_id as uuid),
                  cast(:position_id as uuid), 'primary', 'confirmed',
                  :target_score_lower, :target_score_recommended, :target_score_upper,
                  :target_score_evidence_level, :target_score_sample_count,
                  :target_score_valid_until, :learner_profile_version,
                  cast(:eligibility_snapshot_id as uuid), :position_data_version,
                  cast(:supersedes_goal_version_id as uuid), :created_at
                )
                returning *
                """
            ),
            {
                "goal_version_id": goal_version_id,
                "user_id": command.user_id,
                "position_id": command.position_id,
                "target_score_lower": command.target_score.lower_bound,
                "target_score_recommended": command.target_score.recommended,
                "target_score_upper": command.target_score.upper_bound,
                "target_score_evidence_level": command.target_score.evidence_level,
                "target_score_sample_count": command.target_score.sample_count,
                "target_score_valid_until": command.target_score.valid_until,
                "learner_profile_version": command.learner_profile_version,
                "eligibility_snapshot_id": command.eligibility_snapshot_id,
                "position_data_version": command.position_data_version,
                "supersedes_goal_version_id": previous_id,
                "created_at": changed_at,
            },
        ).mappings().one()

        event_type = "goal_changed" if previous_id else "goal_selected"
        event_id = str(uuid4())
        session.execute(
            text(
                """
                insert into learner.position_goal_events (
                  event_id, command_id, user_id, event_type, from_goal_version_id,
                  to_goal_version_id, change_reason, command_payload, plan_impact, created_at
                ) values (
                  cast(:event_id as uuid), :command_id, cast(:user_id as uuid), :event_type,
                  cast(:from_goal_version_id as uuid), cast(:to_goal_version_id as uuid),
                  :change_reason, cast(:command_payload as jsonb), cast(:plan_impact as jsonb),
                  :created_at
                )
                """
            ),
            {
                "event_id": event_id,
                "command_id": command.command_id,
                "user_id": command.user_id,
                "event_type": event_type,
                "from_goal_version_id": previous_id,
                "to_goal_version_id": goal_version_id,
                "change_reason": command.change_reason,
                "command_payload": json.dumps(command_payload, ensure_ascii=False, sort_keys=True),
                "plan_impact": json.dumps(
                    command.plan_impact.model_dump(by_alias=True, mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "created_at": changed_at,
            },
        )

        # This is an explicit compatibility projection for v1 readers; goal history is canonical.
        session.execute(
            text(
                """
                update learner.profiles
                set target_position_id = cast(:position_id as uuid),
                    target_score = :target_score,
                    updated_at = :updated_at
                where user_id = cast(:user_id as uuid)
                """
            ),
            {
                "user_id": command.user_id,
                "position_id": command.position_id,
                "target_score": command.target_score.recommended,
                "updated_at": changed_at,
            },
        )
        self._outbox.append(
            session,
            deduplication_key=f"position-goal:{command.command_id}",
            aggregate_type="position_goal",
            aggregate_id=goal_version_id,
            event_type="position_goal.changed",
            payload={
                "eventId": event_id,
                "userId": command.user_id,
                "fromGoalVersionId": previous_id,
                "toGoalVersionId": goal_version_id,
                "learnerProfileVersion": command.learner_profile_version,
                "positionDataVersion": command.position_data_version,
            },
        )
        session.execute(
            text(
                """
                update learner.position_goal_previews
                set used_at = :used_at, used_by_command_id = :command_id
                where preview_id = cast(:preview_id as uuid)
                """
            ),
            {
                "used_at": changed_at,
                "command_id": command.command_id,
                "preview_id": command.preview_id,
            },
        )
        return _goal_from_row(row, target_score=command.target_score)

    def _goal_for_command(self, session: Session, command_id: str) -> RowMapping | None:
        return session.execute(
            text(
                """
                select goal.*, event.command_payload
                from learner.position_goal_events event
                join learner.position_goal_versions goal
                  on goal.goal_version_id = event.to_goal_version_id
                where event.command_id = :command_id
                """
            ),
            {"command_id": command_id},
        ).mappings().one_or_none()


def _command_payload(command: ConfirmPrimaryGoalCommand) -> dict[str, Any]:
    return {
        "previewId": command.preview_id,
        "userId": command.user_id,
        "positionId": command.position_id,
        "eligibilitySnapshotId": command.eligibility_snapshot_id,
        "expectedPreviousGoalVersionId": command.expected_previous_goal_version_id,
        "learnerProfileVersion": command.learner_profile_version,
        "positionDataVersion": command.position_data_version,
        "targetScore": command.target_score.model_dump(by_alias=True, mode="json"),
        "changeReason": command.change_reason,
        "planImpact": command.plan_impact.model_dump(by_alias=True, mode="json"),
    }


def _preview_from_row(row: RowMapping) -> PrimaryGoalPreview:
    return PrimaryGoalPreview(
        preview_id=str(row["preview_id"]),
        position_id=str(row["position_id"]),
        eligibility_snapshot_id=str(row["eligibility_snapshot_id"]),
        current_goal_version_id=(
            str(row["from_goal_version_id"])
            if row["from_goal_version_id"] is not None
            else None
        ),
        learner_profile_version=int(row["learner_profile_version"]),
        position_data_version=row["position_data_version"],
        target_score=_stored_target_score(row["target_score"]),
        plan_impact=PrimaryGoalPlanImpact.model_validate(row["plan_impact"]),
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


def _goal_from_row(
    row: RowMapping,
    *,
    target_score: TargetScoreRange | None = None,
) -> PositionGoal:
    if target_score is None:
        command_payload = row.get("command_payload")
        if not command_payload or "targetScore" not in command_payload:
            raise TargetScoreExpiredError(
                "The stored goal predates the canonical target-score contract"
            )
        target_score = _stored_target_score(command_payload["targetScore"])
    return PositionGoal(
        goal_version_id=str(row["goal_version_id"]),
        position_id=str(row["position_id"]),
        role=row["role"],
        status=row["status"],
        target_score=target_score,
        learner_profile_version=row["learner_profile_version"],
        eligibility_snapshot_id=str(row["eligibility_snapshot_id"]),
        position_data_version=row["position_data_version"],
        created_at=row["created_at"],
    )


def _stored_target_score(payload: object) -> TargetScoreRange:
    required = {
        "lowerBound",
        "recommended",
        "upperBound",
        "sampleCount",
        "basisLevel",
        "evidenceLevel",
        "generatedAt",
        "validUntil",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise TargetScoreExpiredError(
            "The stored preview predates the canonical target-score contract"
        )
    try:
        return TargetScoreRange.model_validate(payload)
    except ValidationError as exc:
        raise TargetScoreExpiredError(
            "The stored target-score contract is no longer valid"
        ) from exc
