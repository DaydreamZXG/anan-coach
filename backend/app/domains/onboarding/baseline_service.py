from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy.orm import Session

from app.domains.ability_estimation.schemas import ReportedScoreEvidenceRequest
from app.domains.ability_estimation.service import (
    AbilityEstimateContractError,
    AbilityEstimateService,
)
from app.domains.onboarding.baseline_repository import (
    OnboardingBaselineRepository,
    ability_baseline_from_row,
)
from app.domains.onboarding.progress_repository import (
    OnboardingProgressError,
    OnboardingProgressRepository,
)
from app.domains.onboarding.v2_schemas import (
    AbilityBaselineProfile,
    AbilityBaselineWrite,
)
from app.domains.qualification.repository import PositionRuleReleaseUnavailable
from app.platform.outbox import OutboxRepository


class OnboardingBaselineService:
    def __init__(
        self,
        *,
        repository: OnboardingBaselineRepository | None = None,
        progress: OnboardingProgressRepository | None = None,
        ability: AbilityEstimateService | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or OnboardingBaselineRepository()
        self._progress = progress or OnboardingProgressRepository()
        self._ability = ability or AbilityEstimateService()
        self._outbox = outbox or OutboxRepository()

    def get_current(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> AbilityBaselineProfile:
        row = self._repository.current_row(session, user_id=user_id)
        if row is None:
            raise OnboardingProgressError(
                "ability_baseline_missing",
                "尚未确认能力起点。",
            )
        return ability_baseline_from_row(row)

    def record(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: AbilityBaselineWrite,
        now: datetime | None = None,
    ) -> AbilityBaselineProfile:
        recorded_at = now or datetime.now(UTC)
        payload = {
            "mode": command.mode,
            "xingceScore": command.xingce_score,
            "shenlunScore": command.shenlun_score,
        }
        fingerprint = sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        replay = self._repository.by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            if str(replay["input_fingerprint"]) != fingerprint:
                raise OnboardingProgressError(
                    "idempotency_key_reused",
                    "同一确认标识不能用于不同的能力起点。",
                )
            return ability_baseline_from_row(replay)

        self._repository.lock_owner(session, user_id=user_id)
        progress = self._progress.get_progress(
            session,
            user_id=user_id,
            for_update=True,
        )
        if not progress.result_ready:
            raise OnboardingProgressError(
                "ability_baseline_not_ready",
                "请先完成学习时间设置，再确认能力起点。",
            )
        if progress.completed:
            raise OnboardingProgressError(
                "onboarding_already_completed",
                "备考档案已完成，后续成绩请从成绩导入入口更新。",
            )
        current = self._repository.current_row(session, user_id=user_id)
        current_version = int(current["version_no"]) if current is not None else 0
        if current_version != command.expected_baseline_version:
            raise OnboardingProgressError(
                "ability_baseline_version_conflict",
                "能力起点已更新，请刷新后再保存。",
                details={
                    "expectedVersion": command.expected_baseline_version,
                    "currentVersion": current_version,
                },
            )

        score_evidence_id: str | None = None
        if command.mode == "reported_score":
            try:
                evidence = self._ability.record_reported_score(
                    session,
                    user_id=user_id,
                    command_id=f"{command_id}:score",
                    report=ReportedScoreEvidenceRequest(
                        reportedContext="historical_estimate",
                        xingceScore=command.xingce_score,
                        shenlunScore=command.shenlun_score,
                        timedStatus="unknown",
                        occurredAt=recorded_at,
                    ),
                    now=recorded_at,
                )
            except (AbilityEstimateContractError, PositionRuleReleaseUnavailable) as exc:
                raise OnboardingProgressError(
                    "ability_baseline_evidence_rejected",
                    "这次能力起点没有保存成功，请核对成绩后重试。",
                    details={"reason": str(exc)},
                ) from exc
            score_evidence_id = evidence.evidence.evidence_id

        profile = self._repository.append(
            session,
            user_id=user_id,
            command_id=command_id,
            expected_baseline_version=command.expected_baseline_version,
            mode=command.mode,
            score_evidence_id=score_evidence_id,
            input_fingerprint=fingerprint,
            now=recorded_at,
        )
        self._outbox.append(
            session,
            deduplication_key=f"onboarding-baseline:{command_id}",
            aggregate_type="ability_baseline",
            aggregate_id=user_id,
            event_type="onboarding.ability_baseline_recorded",
            payload={
                "userId": user_id,
                "baselineVersionId": profile.baseline_version_id,
                "baselineVersion": profile.baseline_version,
                "mode": profile.mode,
                "scoreEvidenceId": profile.score_evidence_id,
            },
        )
        return profile
