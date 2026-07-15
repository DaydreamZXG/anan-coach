from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domains.qualification.repository import (
    FactEvidenceInput,
    FactMutation,
    QualificationRepository,
)
from app.domains.qualification.schemas import (
    PositionEligibilitySnapshot,
    QualificationFactInput,
    QualificationProfile,
    RequirementConfirmation,
    RequirementConfirmationResponse,
)
from app.domains.qualification.service import evaluate_eligibility
from app.domains.onboarding.progress_service import OnboardingProgressService
from app.platform.outbox import OutboxRepository


class QualificationApplicationService:
    def __init__(
        self,
        *,
        repository: QualificationRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or QualificationRepository()
        self._outbox = outbox or OutboxRepository()

    def get_profile(self, session: Session, *, user_id: str) -> QualificationProfile:
        return QualificationProfile(
            profile_version=self._repository.get_profile_version(session, user_id=user_id),
            facts=self._repository.list_current_facts(session, user_id=user_id),
        )

    def record_reported_facts(
        self,
        session: Session,
        *,
        user_id: str,
        facts: list[QualificationFactInput],
        command_id: str,
        now: datetime | None = None,
    ) -> QualificationProfile:
        recorded_at = now or datetime.now(UTC)
        version, created = self._repository.replace_facts(
            session,
            user_id=user_id,
            command_id=command_id,
            mutations=[
                FactMutation(
                    key=fact.key,
                    value=fact.value,
                    verification_status="reported",
                    evidence=(
                        FactEvidenceInput(
                            source_type="learner_report",
                            source_reference="wechat:qualification-profile",
                            recorded_at=recorded_at,
                        ),
                    ),
                    effective_at=recorded_at,
                )
                for fact in facts
            ],
            now=recorded_at,
        )
        # Qualification facts can invalidate an open onboarding milestone, but
        # saving facts never completes onboarding. The progress aggregate remains
        # the single owner of A05/A06 resume state.
        OnboardingProgressService().invalidate_qualification_milestone(
            session,
            user_id=user_id,
            source_command_id=command_id,
            current_profile_version=version,
            now=recorded_at,
        )
        self._outbox.append(
            session,
            deduplication_key=f"qualification-profile:{command_id}",
            aggregate_type="qualification_profile",
            aggregate_id=user_id,
            event_type="qualification_profile.changed",
            payload={
                "userId": user_id,
                "profileVersion": version,
                "factIds": sorted(fact.fact_id for fact in created),
            },
        )
        return QualificationProfile(
            profile_version=version,
            facts=self._repository.list_current_facts(session, user_id=user_id),
        )

    def evaluate_position(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
        now: datetime | None = None,
    ) -> PositionEligibilitySnapshot:
        release = self._repository.active_release_for_position(
            session,
            position_id=position_id,
        )
        profile_version = self._repository.get_profile_version(session, user_id=user_id)
        facts = self._repository.list_current_facts(session, user_id=user_id)
        requirements = self._repository.list_requirements(
            session,
            position_id=position_id,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
        )
        requirements_authoritative = self._repository.compilation_is_authoritative(
            session,
            position_id=position_id,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
        )
        confirmations = self._repository.list_current_requirement_confirmations(
            session,
            user_id=user_id,
            position_id=position_id,
        )
        evaluation = evaluate_eligibility(
            facts=facts,
            requirements=requirements,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
            evaluated_at=now,
            requirements_authoritative=requirements_authoritative,
            confirmations=confirmations,
        )
        snapshot_id = self._repository.save_snapshot(
            session,
            user_id=user_id,
            position_id=position_id,
            learner_profile_version=profile_version,
            evaluation=evaluation,
        )
        return PositionEligibilitySnapshot(
            snapshot_id=snapshot_id,
            position_id=position_id,
            learner_profile_version=profile_version,
            facts=facts,
            evaluation=evaluation,
        )

    def confirm_requirement(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
        requirement_id: str,
        response: RequirementConfirmationResponse,
        statement: str | None,
        command_id: str,
        now: datetime | None = None,
    ) -> tuple[RequirementConfirmation, PositionEligibilitySnapshot]:
        confirmation = self._repository.record_requirement_confirmation(
            session,
            user_id=user_id,
            position_id=position_id,
            requirement_id=requirement_id,
            response=response,
            statement=statement,
            command_id=command_id,
            now=now,
        )
        self._outbox.append(
            session,
            deduplication_key=f"qualification-requirement:{command_id}",
            aggregate_type="qualification_profile",
            aggregate_id=user_id,
            event_type="qualification_requirement.confirmed",
            payload={
                "confirmationId": confirmation.confirmation_id,
                "userId": user_id,
                "positionId": position_id,
                "requirementId": requirement_id,
                "profileVersion": confirmation.profile_version,
                "response": response,
            },
        )
        eligibility = self.evaluate_position(
            session,
            user_id=user_id,
            position_id=position_id,
            now=now,
        )
        return confirmation, eligibility
