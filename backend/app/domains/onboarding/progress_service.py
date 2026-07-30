from __future__ import annotations

from datetime import UTC, datetime
from sqlalchemy.orm import Session

from sqlalchemy import text

from app.domains.ability_estimation.service import AbilityEstimateService

from app.domains.onboarding.progress_repository import (
    REQUIRED_QUALIFICATION_FACT_KEYS,
    OnboardingProgressError,
    OnboardingProgressRepository,
    payload_fingerprint,
    position_preference_from_row,
    position_preference_input_payload,
    preference_input_payload,
)
from app.domains.onboarding.v2_schemas import (
    OnboardingProgress,
    OnboardingResultAcknowledgement,
    OnboardingPrimaryGoal,
    OnboardingResultProjection,
    OnboardingResultNextAction,
    OnboardingScoreGap,
    PositionPreferenceProfile,
    PositionPreferenceWrite,
    PreparationExperienceProfile,
    PreparationExperienceResult,
    PreparationExperienceWrite,
    QualificationMilestoneCommand,
    StudyPreferenceResult,
    StudyPreferenceWrite,
    WeeklyStudyPreference,
)
from app.domains.positions.target_scores import (
    PositionTargetScoreContractError,
    PositionTargetScoreOwner,
)
from app.domains.qualification.repository import PositionRuleReleaseUnavailable
from app.platform.outbox import OutboxRepository


class OnboardingProgressService:
    def __init__(
        self,
        *,
        repository: OnboardingProgressRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or OnboardingProgressRepository()
        self._outbox = outbox or OutboxRepository()

    def get_progress(self, session: Session, *, user_id: str) -> OnboardingProgress:
        return self._repository.get_progress(session, user_id=user_id)

    def get_preparation_experience(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> PreparationExperienceProfile:
        experience = self._repository.current_experience(session, user_id=user_id)
        if experience is None:
            raise OnboardingProgressError(
                "preparation_experience_missing",
                "尚未确认备考经历。",
            )
        return experience

    def record_preparation_experience(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: PreparationExperienceWrite,
        now: datetime | None = None,
    ) -> PreparationExperienceResult:
        recorded_at = now or datetime.now(UTC)
        request_payload = command.model_dump(by_alias=True, mode="json")
        progress = self._repository.get_progress(
            session,
            user_id=user_id,
            for_update=True,
        )
        replay = self._repository.get_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="preparation_experience_recorded",
            request_payload=request_payload,
        )
        if replay is not None:
            return PreparationExperienceResult.model_validate(replay)
        self._require_progress_version(progress, command.expected_progress_version)

        current_row = self._repository.current_experience_row(session, user_id=user_id)
        current_version = int(current_row["version_no"]) if current_row is not None else 0
        if current_version != command.expected_experience_version:
            raise OnboardingProgressError(
                "preparation_experience_version_conflict",
                "备考经历已更新，请刷新后再确认。",
                details={
                    "expectedVersion": command.expected_experience_version,
                    "currentVersion": current_version,
                },
            )
        if progress.completed:
            raise OnboardingProgressError(
                "onboarding_already_completed",
                "建档已经完成；后续经历变更必须进入学习档案版本链。",
            )

        current_is_same = bool(
            current_row is not None
            and str(current_row["declaration_status"]) == "declared"
            and bool(current_row["first_systematic"]) == command.first_systematic
            and bool(current_row["external_learning"]) == command.external_learning
            and bool(current_row["prior_guokao_exam"]) == command.prior_guokao_exam
            and bool(current_row["retake"]) == command.retake
            and [int(value) for value in current_row["prior_exam_years"]]
            == command.prior_exam_years
            and str(current_row.get("current_status") or "unspecified") == command.current_status
        )
        if current_is_same:
            experience = self._repository.current_experience(session, user_id=user_id)
            assert experience is not None
            experience_changed = False
        else:
            experience = self._repository.append_experience(
                session,
                user_id=user_id,
                command_id=command_id,
                first_systematic=command.first_systematic,
                external_learning=command.external_learning,
                prior_guokao_exam=command.prior_guokao_exam,
                retake=command.retake,
                prior_exam_years=command.prior_exam_years,
                current_status=command.current_status,
                now=recorded_at,
            )
            experience_changed = True

        progress_changed = not progress.completed and (
            not progress.experience_ready
            or progress.experience_profile_version != experience.experience_version
        )
        before_version = progress.progress_version
        if progress_changed:
            progress = self._repository.advance_progress(
                session,
                user_id=user_id,
                experience_ready=True,
                experience_profile_version=experience.experience_version,
                qualification_ready=progress.qualification_ready,
                qualification_profile_version=progress.qualification_profile_version,
                study_preference_ready=progress.study_preference_ready,
                study_preference_version=progress.study_preference_version,
                result_acknowledged=False,
                now=recorded_at,
            )

        result = PreparationExperienceResult(
            experience=experience,
            progress=progress,
        )
        response_payload = result.model_dump(by_alias=True, mode="json")
        self._repository.record_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="preparation_experience_recorded",
            request_payload=request_payload,
            response_payload=response_payload,
            now=recorded_at,
        )
        if progress_changed:
            self._repository.record_progress_event(
                session,
                command_id=command_id,
                user_id=user_id,
                event_type="preparation_experience_recorded",
                before_version=before_version,
                progress=progress,
                now=recorded_at,
            )
        if experience_changed:
            self._outbox.append(
                session,
                deduplication_key=f"preparation-experience:{command_id}",
                aggregate_type="preparation_experience",
                aggregate_id=user_id,
                event_type="onboarding.preparation_experience_recorded",
                payload={
                    "userId": user_id,
                    "experienceVersionId": experience.experience_version_id,
                    "experienceVersion": experience.experience_version,
                    "recommendedEntry": experience.recommended_entry,
                    "currentStatus": experience.current_status,
                },
            )
        return result

    def complete_qualification_milestone(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: QualificationMilestoneCommand,
        now: datetime | None = None,
    ) -> OnboardingProgress:
        recorded_at = now or datetime.now(UTC)
        request_payload = command.model_dump(by_alias=True, mode="json")
        progress = self._repository.get_progress(
            session,
            user_id=user_id,
            for_update=True,
        )
        replay = self._repository.get_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="qualification_completed",
            request_payload=request_payload,
        )
        if replay is not None:
            return OnboardingProgress.model_validate(replay)
        self._require_progress_version(progress, command.expected_progress_version)
        if not progress.experience_ready:
            raise OnboardingProgressError(
                "preparation_experience_required",
                "请先确认备考经历，再继续资格建档。",
            )

        profile_version, fact_keys = self._repository.qualification_state(
            session,
            user_id=user_id,
        )
        if profile_version != command.qualification_profile_version:
            raise OnboardingProgressError(
                "qualification_profile_version_conflict",
                "资格档案已更新，请使用当前版本重新确认。",
                details={
                    "expectedVersion": command.qualification_profile_version,
                    "currentVersion": profile_version,
                },
            )
        missing = sorted(REQUIRED_QUALIFICATION_FACT_KEYS - fact_keys)
        if missing:
            raise OnboardingProgressError(
                "onboarding_qualification_incomplete",
                "资格建档信息尚未完整。",
                details={"missingFactKeys": missing},
            )

        changed = not (
            progress.qualification_ready
            and progress.qualification_profile_version == profile_version
        )
        before_version = progress.progress_version
        if changed:
            if progress.completed:
                raise OnboardingProgressError(
                    "onboarding_already_completed",
                    "建档已经完成。",
                )
            progress = self._repository.advance_progress(
                session,
                user_id=user_id,
                experience_ready=progress.experience_ready,
                experience_profile_version=progress.experience_profile_version,
                qualification_ready=True,
                qualification_profile_version=profile_version,
                study_preference_ready=progress.study_preference_ready,
                study_preference_version=progress.study_preference_version,
                result_acknowledged=False,
                now=recorded_at,
            )

        response_payload = progress.model_dump(by_alias=True, mode="json")
        self._repository.record_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="qualification_completed",
            request_payload=request_payload,
            response_payload=response_payload,
            now=recorded_at,
        )
        if changed:
            self._repository.record_progress_event(
                session,
                command_id=command_id,
                user_id=user_id,
                event_type="qualification_completed",
                before_version=before_version,
                progress=progress,
                now=recorded_at,
            )
            self._outbox.append(
                session,
                deduplication_key=f"onboarding-qualification:{command_id}",
                aggregate_type="onboarding_progress",
                aggregate_id=user_id,
                event_type="onboarding.qualification_completed",
                payload={
                    "userId": user_id,
                    "progressVersion": progress.progress_version,
                    "qualificationProfileVersion": profile_version,
                },
            )
        return progress

    def get_position_preference(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> PositionPreferenceProfile:
        preference = self._repository.current_position_preference(
            session,
            user_id=user_id,
        )
        if preference is None:
            raise OnboardingProgressError(
                "position_preference_missing",
                "尚未确认岗位偏好。",
            )
        return preference

    def record_position_preference(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: PositionPreferenceWrite,
        now: datetime | None = None,
    ) -> PositionPreferenceProfile:
        recorded_at = now or datetime.now(UTC)
        payload = position_preference_input_payload(
            preferred_regions=command.preferred_regions,
            accepts_relocation=command.accepts_relocation,
            accepted_position_levels=command.accepted_position_levels,
        )
        fingerprint = payload_fingerprint(payload)
        replay = self._repository.position_preference_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            if str(replay["input_fingerprint"]) != fingerprint:
                raise OnboardingProgressError(
                    "idempotency_key_reused",
                    "同一确认标识不能用于不同的岗位偏好。",
                )
            return position_preference_from_row(replay)

        preference = self._repository.append_position_preference(
            session,
            user_id=user_id,
            command_id=command_id,
            expected_preference_version=command.expected_preference_version,
            preferred_regions=command.preferred_regions,
            accepts_relocation=command.accepts_relocation,
            accepted_position_levels=command.accepted_position_levels,
            input_fingerprint=fingerprint,
            now=recorded_at,
        )
        self._outbox.append(
            session,
            deduplication_key=f"position-preference:{command_id}",
            aggregate_type="position_preference",
            aggregate_id=user_id,
            event_type="onboarding.position_preference_recorded",
            payload={
                "userId": user_id,
                "preferenceVersionId": preference.preference_version_id,
                "preferenceVersion": preference.preference_version,
                "preferredRegions": preference.preferred_regions,
                "acceptsRelocation": preference.accepts_relocation,
                "acceptedPositionLevels": preference.accepted_position_levels,
            },
        )
        return preference

    def get_study_preference(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> WeeklyStudyPreference:
        preference = self._repository.current_preference(session, user_id=user_id)
        if preference is None:
            raise OnboardingProgressError(
                "study_preference_missing",
                "尚未确认正式学习时间偏好。",
            )
        return preference

    def get_result_projection(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> OnboardingResultProjection:
        generated_at = now or datetime.now(UTC)
        progress = self._repository.get_progress(session, user_id=user_id)
        if (
            not progress.result_ready
            or progress.experience_profile_version is None
            or progress.qualification_profile_version is None
        ):
            raise OnboardingProgressError(
                "onboarding_result_not_ready",
                "建档结果尚未生成。",
                details={"nextStep": progress.next_step},
            )
        preference = self.get_study_preference(session, user_id=user_id)
        experience = self._repository.experience_by_version(
            session,
            user_id=user_id,
            version_no=progress.experience_profile_version,
        )
        target_owner = PositionTargetScoreOwner()
        try:
            context = target_owner.active_context(
                session,
                exam_year=None,
                now=generated_at,
            )
        except PositionTargetScoreContractError as exc:
            raise OnboardingProgressError(
                exc.code,
                "当前国考岗位发布或目标分证据不可用。",
            ) from exc

        goal = (
            session.execute(
                text(
                    """
                select goal.goal_version_id::text as goal_version_id,
                       goal.position_id::text as position_id,
                       goal.position_data_version,
                       case when position_snapshot.snapshot_id is not null
                         then position_snapshot.name else legacy_position.name
                       end as position_name,
                       case when position_snapshot.snapshot_id is not null
                         then position_snapshot.department_full
                         else legacy_position.department_full
                       end as department_name
                from learner.current_primary_position_goals goal
                join learner.profiles profile on profile.user_id = goal.user_id
                left join product.position_import_batches import_batch
                  on import_batch.position_data_version
                       = goal.position_data_version
                left join product.position_snapshots position_snapshot
                  on position_snapshot.position_id = goal.position_id
                 and position_snapshot.position_data_version
                       = goal.position_data_version
                left join product.positions legacy_position
                  on legacy_position.position_id = goal.position_id
                 and import_batch.position_data_version is null
                where goal.user_id = cast(:user_id as uuid)
                  and goal.learner_profile_version = profile.profile_version
                  and goal.position_data_version = :position_data_version
                  and goal.target_score_valid_until =
                        :current_window_closes_at
                  and coalesce(position_snapshot.year, legacy_position.year)
                        = :exam_year
                limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "exam_year": context.release.exam_year,
                    "position_data_version": context.release.position_data_version,
                    "current_window_closes_at": context.valid_until,
                },
            )
            .mappings()
            .one_or_none()
        )
        try:
            ability = AbilityEstimateService().get_current(
                session,
                user_id=user_id,
                now=generated_at,
            )
        except PositionRuleReleaseUnavailable as exc:
            raise OnboardingProgressError(
                "position_release_unavailable",
                "当前没有可用的国考岗位发布。",
            ) from exc
        if goal is None:
            eligible_count = int(
                session.execute(
                    text(
                        """
                        select count(distinct snapshot.position_id)
                        from learner.eligibility_snapshots snapshot
                        join learner.profiles profile on profile.user_id = snapshot.user_id
                        left join product.position_import_batches import_batch
                          on import_batch.position_data_version
                               = snapshot.position_data_version
                        left join product.position_snapshots position_snapshot
                          on position_snapshot.position_id = snapshot.position_id
                         and position_snapshot.position_data_version
                               = snapshot.position_data_version
                        left join product.positions legacy_position
                          on legacy_position.position_id = snapshot.position_id
                         and import_batch.position_data_version is null
                        where snapshot.user_id = cast(:user_id as uuid)
                          and snapshot.status = 'eligible'
                          and snapshot.learner_profile_version = profile.profile_version
                          and snapshot.rule_set_version = :rule_set_version
                          and snapshot.position_data_version = :position_data_version
                          and coalesce(
                            position_snapshot.year,
                            legacy_position.year
                          ) = :exam_year
                        """
                    ),
                    {
                        "user_id": user_id,
                        "rule_set_version": context.release.rule_set_version,
                        "position_data_version": context.release.position_data_version,
                        "exam_year": context.release.exam_year,
                    },
                ).scalar_one()
            )
            return OnboardingResultProjection(
                status=(
                    "position_selection_required" if eligible_count > 0 else "no_eligible_position"
                ),
                reason_code=(
                    "primary_goal_missing" if eligible_count > 0 else "no_current_eligible_position"
                ),
                progress_version=progress.progress_version,
                qualification_profile_version=progress.qualification_profile_version,
                preparation_experience=experience,
                ability_estimate=ability.estimate,
                primary_goal=None,
                score_gap=None,
                weekly_preference=preference,
                next_action="enter_workspace",
                generated_at=generated_at,
                valid_until=context.valid_until,
            )

        try:
            target_score = target_owner.range_for_position(
                session,
                position_id=str(goal["position_id"]),
                context=context,
                expected_position_data_version=str(goal["position_data_version"]),
                require_evidence=True,
            )
        except PositionTargetScoreContractError as exc:
            raise OnboardingProgressError(
                exc.code,
                "当前主目标的目标分证据不可用。",
            ) from exc
        assert target_score is not None
        primary_goal = OnboardingPrimaryGoal(
            goal_version_id=str(goal["goal_version_id"]),
            position_id=str(goal["position_id"]),
            position_name=str(goal["position_name"]),
            department_name=str(goal["department_name"]),
            target_score=target_score,
        )
        if ability.readiness != "complete" or ability.estimate is None:
            reason = {
                "unavailable": "ability_unavailable",
                "partial": "ability_partial",
                "stale": "ability_stale",
            }[ability.readiness]
            return OnboardingResultProjection(
                status="baseline_required",
                reason_code=reason,
                progress_version=progress.progress_version,
                qualification_profile_version=progress.qualification_profile_version,
                preparation_experience=experience,
                ability_estimate=ability.estimate,
                primary_goal=primary_goal,
                score_gap=None,
                weekly_preference=preference,
                next_action=_baseline_next_action(experience),
                generated_at=generated_at,
                valid_until=(
                    min(target_score.valid_until, ability.estimate.valid_until)
                    if ability.estimate is not None and ability.readiness != "stale"
                    else target_score.valid_until
                ),
            )

        estimate = ability.estimate
        assert estimate.total is not None
        gap = OnboardingScoreGap(
            unit="points",
            direction="positive_means_target_above_current",
            lower=round(target_score.lower_bound - estimate.total.upper, 2),
            recommended=round(
                target_score.recommended - estimate.total.recommended,
                2,
            ),
            upper=round(target_score.upper_bound - estimate.total.lower, 2),
        )
        return OnboardingResultProjection(
            status="evidence_sufficient",
            reason_code="ready",
            progress_version=progress.progress_version,
            qualification_profile_version=progress.qualification_profile_version,
            preparation_experience=experience,
            ability_estimate=estimate,
            primary_goal=primary_goal,
            score_gap=gap,
            weekly_preference=preference,
            next_action="enter_workspace",
            generated_at=generated_at,
            valid_until=min(target_score.valid_until, estimate.valid_until),
        )

    def record_study_preference(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: StudyPreferenceWrite,
        now: datetime | None = None,
    ) -> StudyPreferenceResult:
        recorded_at = now or datetime.now(UTC)
        request_payload = command.model_dump(by_alias=True, mode="json")
        progress = self._repository.get_progress(
            session,
            user_id=user_id,
            for_update=True,
        )
        replay = self._repository.get_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="study_preference_recorded",
            request_payload=request_payload,
        )
        if replay is not None:
            return StudyPreferenceResult.model_validate(replay)
        self._require_progress_version(progress, command.expected_progress_version)
        # Completing onboarding freezes the onboarding milestone, not the
        # independently versioned weekly-study preference aggregate. Returning
        # learners revise that aggregate through this same optimistic command;
        # their immutable onboarding progress snapshot intentionally stays put.
        if not progress.experience_ready:
            raise OnboardingProgressError(
                "preparation_experience_required",
                "请先确认备考经历，再设置正式学习节奏。",
            )
        if not progress.qualification_ready:
            raise OnboardingProgressError(
                "onboarding_qualification_required",
                "请先完成资格建档，再设置正式学习节奏。",
            )

        current_row = self._repository.current_preference_row(session, user_id=user_id)
        current_confirmed_version = (
            int(current_row["version_no"])
            if current_row is not None and bool(current_row["confirmed"])
            else 0
        )
        if current_confirmed_version != command.expected_preference_version:
            raise OnboardingProgressError(
                "study_preference_version_conflict",
                "学习时间偏好已更新，请刷新后再保存。",
                details={
                    "expectedVersion": command.expected_preference_version,
                    "currentVersion": current_confirmed_version,
                },
            )

        preference_payload = preference_input_payload(
            timezone=command.timezone,
            effective_from=command.effective_from,
            weekday_minutes=command.weekday_minutes,
            weekend_minutes=command.weekend_minutes,
            long_block_minutes=command.long_block_minutes,
            fragment_minutes=command.fragment_minutes,
            fixed_rest_day=command.fixed_rest_day,
            mock_exam_day=command.mock_exam_day,
        )
        current_is_same = bool(
            current_row is not None
            and bool(current_row["confirmed"])
            and str(current_row["input_fingerprint"]) == payload_fingerprint(preference_payload)
        )
        if current_is_same:
            preference = self._repository.current_preference(session, user_id=user_id)
            assert preference is not None
            preference_changed = False
        else:
            preference = self._repository.append_preference(
                session,
                user_id=user_id,
                command_id=command_id,
                timezone=command.timezone,
                effective_from=command.effective_from,
                weekday_minutes=command.weekday_minutes,
                weekend_minutes=command.weekend_minutes,
                long_block_minutes=command.long_block_minutes,
                fragment_minutes=command.fragment_minutes,
                fixed_rest_day=command.fixed_rest_day,
                mock_exam_day=command.mock_exam_day,
                now=recorded_at,
            )
            preference_changed = True

        progress_changed = not progress.completed and (
            not progress.study_preference_ready
            or progress.study_preference_version != preference.preference_version
        )
        before_version = progress.progress_version
        if progress_changed:
            progress = self._repository.advance_progress(
                session,
                user_id=user_id,
                experience_ready=progress.experience_ready,
                experience_profile_version=progress.experience_profile_version,
                qualification_ready=progress.qualification_ready,
                qualification_profile_version=progress.qualification_profile_version,
                study_preference_ready=True,
                study_preference_version=preference.preference_version,
                result_acknowledged=False,
                now=recorded_at,
            )

        result = StudyPreferenceResult(preference=preference, progress=progress)
        response_payload = result.model_dump(by_alias=True, mode="json")
        self._repository.record_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="study_preference_recorded",
            request_payload=request_payload,
            response_payload=response_payload,
            now=recorded_at,
        )
        if progress_changed:
            self._repository.record_progress_event(
                session,
                command_id=command_id,
                user_id=user_id,
                event_type="study_preference_recorded",
                before_version=before_version,
                progress=progress,
                now=recorded_at,
            )
        if preference_changed:
            self._outbox.append(
                session,
                deduplication_key=f"weekly-study-preference:{command_id}",
                aggregate_type="weekly_study_preference",
                aggregate_id=user_id,
                event_type="weekly_study_preference.changed",
                payload={
                    "userId": user_id,
                    "preferenceVersionId": preference.preference_version_id,
                    "preferenceVersion": preference.preference_version,
                    "effectiveFrom": preference.effective_from.isoformat(),
                },
            )
        return result

    def acknowledge_result(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: OnboardingResultAcknowledgement,
        now: datetime | None = None,
    ) -> OnboardingProgress:
        recorded_at = now or datetime.now(UTC)
        request_payload = command.model_dump(by_alias=True, mode="json")
        progress = self._repository.get_progress(
            session,
            user_id=user_id,
            for_update=True,
        )
        replay = self._repository.get_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="result_acknowledged",
            request_payload=request_payload,
        )
        if replay is not None:
            return OnboardingProgress.model_validate(replay)
        self._require_progress_version(progress, command.expected_progress_version)
        if not progress.result_ready:
            raise OnboardingProgressError(
                "onboarding_result_not_ready",
                "建档结果尚未生成，不能进入工作台。",
                details={"nextStep": progress.next_step},
            )

        current_profile_version, _ = self._repository.qualification_state(
            session,
            user_id=user_id,
        )
        if progress.qualification_profile_version != current_profile_version:
            raise OnboardingProgressError(
                "qualification_profile_version_conflict",
                "资格档案已更新，请重新确认建档结果。",
                details={
                    "confirmedVersion": progress.qualification_profile_version,
                    "currentVersion": current_profile_version,
                },
            )

        # A06 acknowledgement is allowed only after the server can build one of
        # the formal result states. The client never assembles this projection.
        self.get_result_projection(
            session,
            user_id=user_id,
            now=recorded_at,
        )

        changed = not progress.completed
        before_version = progress.progress_version
        if changed:
            progress = self._repository.advance_progress(
                session,
                user_id=user_id,
                experience_ready=progress.experience_ready,
                experience_profile_version=progress.experience_profile_version,
                qualification_ready=True,
                qualification_profile_version=progress.qualification_profile_version,
                study_preference_ready=True,
                study_preference_version=progress.study_preference_version,
                result_acknowledged=True,
                now=recorded_at,
            )
        response_payload = progress.model_dump(by_alias=True, mode="json")
        self._repository.record_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="result_acknowledged",
            request_payload=request_payload,
            response_payload=response_payload,
            now=recorded_at,
        )
        if changed:
            self._repository.record_progress_event(
                session,
                command_id=command_id,
                user_id=user_id,
                event_type="result_acknowledged",
                before_version=before_version,
                progress=progress,
                now=recorded_at,
            )
            self._outbox.append(
                session,
                deduplication_key=f"onboarding-completed:{command_id}",
                aggregate_type="onboarding_progress",
                aggregate_id=user_id,
                event_type="onboarding.completed",
                payload={
                    "userId": user_id,
                    "progressVersion": progress.progress_version,
                    "experienceProfileVersion": progress.experience_profile_version,
                    "qualificationProfileVersion": progress.qualification_profile_version,
                    "studyPreferenceVersion": progress.study_preference_version,
                },
            )
        return progress

    def invalidate_qualification_milestone(
        self,
        session: Session,
        *,
        user_id: str,
        source_command_id: str,
        current_profile_version: int,
        now: datetime | None = None,
    ) -> OnboardingProgress:
        recorded_at = now or datetime.now(UTC)
        command_id = f"onboarding-invalidate:{source_command_id}"
        request_payload = {"qualificationProfileVersion": current_profile_version}
        progress = self._repository.get_progress(
            session,
            user_id=user_id,
            for_update=True,
        )
        replay = self._repository.get_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="qualification_invalidated",
            request_payload=request_payload,
        )
        if replay is not None:
            return OnboardingProgress.model_validate(replay)
        if progress.completed or not progress.qualification_ready:
            return progress
        if progress.qualification_profile_version == current_profile_version:
            return progress

        before_version = progress.progress_version
        progress = self._repository.advance_progress(
            session,
            user_id=user_id,
            experience_ready=progress.experience_ready,
            experience_profile_version=progress.experience_profile_version,
            qualification_ready=False,
            qualification_profile_version=None,
            study_preference_ready=progress.study_preference_ready,
            study_preference_version=progress.study_preference_version,
            result_acknowledged=False,
            now=recorded_at,
        )
        response_payload = progress.model_dump(by_alias=True, mode="json")
        self._repository.record_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type="qualification_invalidated",
            request_payload=request_payload,
            response_payload=response_payload,
            now=recorded_at,
        )
        self._repository.record_progress_event(
            session,
            command_id=command_id,
            user_id=user_id,
            event_type="qualification_invalidated",
            before_version=before_version,
            progress=progress,
            now=recorded_at,
        )
        self._outbox.append(
            session,
            deduplication_key=f"onboarding-qualification-invalidated:{source_command_id}",
            aggregate_type="onboarding_progress",
            aggregate_id=user_id,
            event_type="onboarding.qualification_invalidated",
            payload={
                "userId": user_id,
                "progressVersion": progress.progress_version,
                "qualificationProfileVersion": current_profile_version,
            },
        )
        return progress

    @staticmethod
    def _require_progress_version(
        progress: OnboardingProgress,
        expected_version: int,
    ) -> None:
        if progress.progress_version != expected_version:
            raise OnboardingProgressError(
                "onboarding_progress_version_conflict",
                "建档进度已更新，请刷新后继续。",
                details={
                    "expectedVersion": expected_version,
                    "currentVersion": progress.progress_version,
                    "nextStep": progress.next_step,
                },
            )


def _baseline_next_action(
    experience: PreparationExperienceProfile,
) -> OnboardingResultNextAction:
    if experience.retake:
        return "start_retake_recovery"
    if experience.external_learning or experience.prior_guokao_exam:
        return "import_prior_evidence"
    if experience.first_systematic:
        return "review_exam_briefing"
    return "record_baseline"
