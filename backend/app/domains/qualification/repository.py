from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.qualification.schemas import (
    EligibilityEvaluation,
    FactEvidence,
    PositionRequirement,
    QualificationFact,
    QualificationFactKey,
    QualificationFactValue,
    RequirementConfirmation,
    RequirementConfirmationResponse,
)


class QualificationContractError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "qualification_contract_conflict",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class PositionRuleReleaseUnavailable(QualificationContractError):
    def __init__(
        self,
        message: str,
        *,
        lifecycle: PositionReleaseLifecycle | None = None,
    ) -> None:
        phase = lifecycle.phase if lifecycle is not None else "conflict"
        code = {
            "upcoming": "position_release_upcoming",
            "gap": "position_release_between_windows",
            "closed": "position_release_closed",
            "no_release": "position_release_unavailable",
            "conflict": "position_release_conflict",
        }.get(phase, "position_release_conflict")
        details: dict[str, Any] = {
            "phase": phase,
            "examCode": lifecycle.exam_code if lifecycle is not None else "guokao",
        }
        if lifecycle is not None:
            if lifecycle.exam_year is not None:
                details["examYear"] = lifecycle.exam_year
            if lifecycle.release_id is not None:
                details["releaseId"] = lifecycle.release_id
            if lifecycle.window is not None:
                details["windowKind"] = lifecycle.window.kind
                details["opensAt"] = lifecycle.window.opens_at.isoformat()
                details["closesAt"] = lifecycle.window.closes_at.isoformat()
                details["officialSourceTitle"] = lifecycle.window.official_source_title
                details["officialSourceUrl"] = lifecycle.window.official_source_url
        super().__init__(message, code=code, details=details)


@dataclass(frozen=True)
class PositionReleaseWindow:
    window_id: str
    kind: Literal["primary", "supplemental"]
    sequence_no: int
    opens_at: datetime
    closes_at: datetime
    official_source_url: str
    official_source_title: str
    official_source_published_at: datetime
    official_source_received_at: datetime
    official_source_sha256: str
    official_source_receipt: str


PositionReleasePhase = Literal[
    "open",
    "upcoming",
    "gap",
    "closed",
    "no_release",
    "conflict",
]


@dataclass(frozen=True)
class PositionRuleRelease:
    release_id: str
    exam_code: str
    exam_year: int
    rule_set_version: str
    position_data_version: str
    content_digest: str
    activated_at: datetime
    current_window: PositionReleaseWindow


@dataclass(frozen=True)
class PositionReleaseLifecycle:
    phase: PositionReleasePhase
    release_id: str | None
    exam_code: str
    exam_year: int | None
    rule_set_version: str | None
    position_data_version: str | None
    content_digest: str | None
    activated_at: datetime | None
    window: PositionReleaseWindow | None


@dataclass(frozen=True)
class FactEvidenceInput:
    source_type: str
    source_reference: str | None = None
    evidence_id: str | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class FactMutation:
    key: QualificationFactKey
    value: QualificationFactValue
    verification_status: str
    evidence: tuple[FactEvidenceInput, ...]
    effective_at: datetime | None = None
    fact_id: str | None = None


class QualificationRepository:
    def active_release(
        self,
        session: Session,
        *,
        exam_code: str = "guokao",
        exam_year: int | None = None,
        now: datetime | None = None,
    ) -> PositionRuleRelease:
        lifecycle = self.release_lifecycle(
            session,
            exam_code=exam_code,
            exam_year=exam_year,
            now=now,
        )
        if (
            lifecycle.phase != "open"
            or lifecycle.release_id is None
            or lifecycle.exam_year is None
            or lifecycle.rule_set_version is None
            or lifecycle.position_data_version is None
            or lifecycle.content_digest is None
            or lifecycle.activated_at is None
            or lifecycle.window is None
        ):
            raise PositionRuleReleaseUnavailable(
                f"No open {exam_code} position release (phase={lifecycle.phase})",
                lifecycle=lifecycle,
            )
        return PositionRuleRelease(
            release_id=lifecycle.release_id,
            exam_code=lifecycle.exam_code,
            exam_year=lifecycle.exam_year,
            rule_set_version=lifecycle.rule_set_version,
            position_data_version=lifecycle.position_data_version,
            content_digest=lifecycle.content_digest,
            activated_at=lifecycle.activated_at,
            current_window=lifecycle.window,
        )

    def release_lifecycle(
        self,
        session: Session,
        *,
        exam_code: str = "guokao",
        exam_year: int | None = None,
        now: datetime | None = None,
    ) -> PositionReleaseLifecycle:
        checked_at = _aware_utc(now or datetime.now(UTC))
        rows = (
            session.execute(
                text(
                    """
                    select
                      release.release_id::text as release_id,
                      release.exam_code,
                      release.exam_year,
                      release.rule_set_version,
                      release.position_data_version,
                      release.content_digest,
                      release.activated_at,
                      business_window.window_id::text as window_id,
                      business_window.window_kind,
                      business_window.sequence_no,
                      business_window.opens_at,
                      business_window.closes_at,
                      business_window.official_source_url,
                      business_window.official_source_title,
                      business_window.official_source_published_at,
                      business_window.official_source_received_at,
                      business_window.official_source_sha256,
                      business_window.official_source_receipt
                    from product.position_rule_releases release
                    left join product.position_release_windows business_window
                      on business_window.release_id = release.release_id
                    where release.exam_code = :exam_code
                      and release.status = 'active'
                      and (
                        cast(:exam_year as integer) is null
                        or release.exam_year = cast(:exam_year as integer)
                      )
                    order by
                      release.exam_year desc,
                      release.activated_at desc,
                      business_window.opens_at,
                      business_window.window_kind,
                      business_window.sequence_no
                    """
                ),
                {"exam_code": exam_code, "exam_year": exam_year},
            )
            .mappings()
            .all()
        )
        if not rows:
            return _no_position_release(exam_code)

        grouped: dict[str, list[RowMapping]] = {}
        for row in rows:
            grouped.setdefault(str(row["release_id"]), []).append(row)
        lifecycles = [
            _lifecycle_from_release_rows(release_rows, checked_at=checked_at)
            for release_rows in grouped.values()
        ]
        open_lifecycles = [lifecycle for lifecycle in lifecycles if lifecycle.phase == "open"]
        if len(open_lifecycles) > 1:
            return PositionReleaseLifecycle(
                phase="conflict",
                release_id=None,
                exam_code=exam_code,
                exam_year=None,
                rule_set_version=None,
                position_data_version=None,
                content_digest=None,
                activated_at=None,
                window=None,
            )
        if open_lifecycles:
            return open_lifecycles[0]

        gap_lifecycles = [lifecycle for lifecycle in lifecycles if lifecycle.phase == "gap"]
        if gap_lifecycles:
            return min(
                gap_lifecycles,
                key=lambda lifecycle: (
                    lifecycle.window.opens_at
                    if lifecycle.window is not None
                    else datetime.max.replace(tzinfo=UTC),
                    -(lifecycle.exam_year or 0),
                ),
            )
        upcoming_lifecycles = [
            lifecycle for lifecycle in lifecycles if lifecycle.phase == "upcoming"
        ]
        if upcoming_lifecycles:
            return min(
                upcoming_lifecycles,
                key=lambda lifecycle: (
                    lifecycle.window.opens_at
                    if lifecycle.window is not None
                    else datetime.max.replace(tzinfo=UTC),
                    -(lifecycle.exam_year or 0),
                ),
            )
        closed_lifecycles = [lifecycle for lifecycle in lifecycles if lifecycle.phase == "closed"]
        if closed_lifecycles:
            return max(
                closed_lifecycles,
                key=lambda lifecycle: (
                    lifecycle.window.closes_at
                    if lifecycle.window is not None
                    else datetime.min.replace(tzinfo=UTC),
                    lifecycle.exam_year or 0,
                ),
            )
        return _no_position_release(exam_code)

    def record_requirement_confirmation(
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
    ) -> RequirementConfirmation:
        timestamp = _aware_utc(now or datetime.now(UTC))
        payload = {
            "userId": user_id,
            "positionId": position_id,
            "requirementId": requirement_id,
            "response": response,
            "statement": statement,
        }
        existing = (
            session.execute(
                text(
                    """
                select confirmation.*, change.command_payload
                from learner.qualification_requirement_confirmations confirmation
                join learner.qualification_profile_changes change
                  on change.change_id = confirmation.change_id
                where confirmation.command_id = :command_id
                """
                ),
                {"command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if existing["command_payload"] != payload:
                raise QualificationContractError(
                    "An existing command ID refers to a different requirement confirmation",
                    code="idempotency_key_reused",
                )
            return _confirmation_from_row(existing)

        requirement = session.execute(
            text(
                """
                select requirement.requirement_id
                from product.position_requirements requirement
                join product.position_rule_releases release
                  on release.rule_set_version = requirement.rule_set_version
                 and release.position_data_version = requirement.position_data_version
                 and release.exam_code = 'guokao'
                 and release.status = 'active'
                left join product.position_import_batches import_batch
                  on import_batch.position_data_version
                       = release.position_data_version
                left join product.position_snapshots position_snapshot
                  on position_snapshot.position_id = requirement.position_id
                 and position_snapshot.position_data_version
                       = release.position_data_version
                left join product.positions legacy_position
                  on legacy_position.position_id = requirement.position_id
                 and import_batch.position_data_version is null
                join product.position_release_windows business_window
                  on business_window.release_id = release.release_id
                 and business_window.opens_at <= cast(:confirmed_at as timestamptz)
                 and cast(:confirmed_at as timestamptz) < business_window.closes_at
                where requirement.requirement_id = cast(:requirement_id as uuid)
                  and requirement.position_id = cast(:position_id as uuid)
                  and requirement.operator = 'manual_review'
                  and coalesce(position_snapshot.year, legacy_position.year)
                        = release.exam_year
                """
            ),
            {
                "requirement_id": requirement_id,
                "position_id": position_id,
                "confirmed_at": timestamp,
            },
        ).scalar_one_or_none()
        if requirement is None:
            raise QualificationContractError(
                "Only an active manual-review requirement can be confirmed",
                code="qualification_confirmation_invalid",
            )

        current_version = session.execute(
            text(
                """
                select profile_version
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                for update
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if current_version is None:
            raise QualificationContractError(
                "Learner profile does not exist",
                code="qualification_profile_missing",
            )
        next_version = int(current_version) + 1
        previous = session.execute(
            text(
                """
                select confirmation_id
                from learner.qualification_requirement_confirmations
                where user_id = cast(:user_id as uuid)
                  and requirement_id = cast(:requirement_id as uuid)
                order by profile_version desc, confirmed_at desc
                limit 1
                """
            ),
            {"user_id": user_id, "requirement_id": requirement_id},
        ).scalar_one_or_none()
        change_id = str(uuid4())
        session.execute(
            text(
                """
                insert into learner.qualification_profile_changes (
                  change_id, command_id, user_id, from_profile_version,
                  to_profile_version, command_payload, created_at
                ) values (
                  cast(:change_id as uuid), :command_id, cast(:user_id as uuid),
                  :from_profile_version, :to_profile_version,
                  cast(:command_payload as jsonb), :created_at
                )
                """
            ),
            {
                "change_id": change_id,
                "command_id": command_id,
                "user_id": user_id,
                "from_profile_version": int(current_version),
                "to_profile_version": next_version,
                "command_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "created_at": timestamp,
            },
        )
        confirmation_id = str(uuid4())
        row = (
            session.execute(
                text(
                    """
                insert into learner.qualification_requirement_confirmations (
                  confirmation_id, change_id, command_id, user_id, position_id,
                  requirement_id, profile_version, response, statement,
                  supersedes_confirmation_id, confirmed_at
                ) values (
                  cast(:confirmation_id as uuid), cast(:change_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:position_id as uuid),
                  cast(:requirement_id as uuid), :profile_version, :response, :statement,
                  cast(:supersedes_confirmation_id as uuid), :confirmed_at
                ) returning *
                """
                ),
                {
                    "confirmation_id": confirmation_id,
                    "change_id": change_id,
                    "command_id": command_id,
                    "user_id": user_id,
                    "position_id": position_id,
                    "requirement_id": requirement_id,
                    "profile_version": next_version,
                    "response": response,
                    "statement": statement,
                    "supersedes_confirmation_id": str(previous) if previous else None,
                    "confirmed_at": timestamp,
                },
            )
            .mappings()
            .one()
        )
        session.execute(
            text(
                """
                update learner.profiles
                set profile_version = :profile_version, updated_at = :updated_at
                where user_id = cast(:user_id as uuid)
                """
            ),
            {
                "user_id": user_id,
                "profile_version": next_version,
                "updated_at": timestamp,
            },
        )
        return _confirmation_from_row(row)

    def list_current_requirement_confirmations(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
    ) -> list[RequirementConfirmation]:
        rows = session.execute(
            text(
                """
                select distinct on (requirement_id) *
                from learner.qualification_requirement_confirmations
                where user_id = cast(:user_id as uuid)
                  and position_id = cast(:position_id as uuid)
                order by requirement_id, profile_version desc, confirmed_at desc
                """
            ),
            {"user_id": user_id, "position_id": position_id},
        ).mappings()
        return [_confirmation_from_row(row) for row in rows]

    def active_release_for_position(
        self,
        session: Session,
        *,
        position_id: str,
        exam_code: str = "guokao",
        now: datetime | None = None,
    ) -> PositionRuleRelease:
        position_exists = session.execute(
            text(
                """
                select exists (
                  select 1
                  from product.positions
                  where position_id = cast(:position_id as uuid)
                )
                """
            ),
            {"position_id": position_id},
        ).scalar_one()
        if not position_exists:
            raise LookupError(position_id)
        position_year = session.execute(
            text(
                """
                select release.exam_year
                from product.position_rule_releases release
                left join product.position_import_batches import_batch
                  on import_batch.position_data_version
                       = release.position_data_version
                left join product.position_snapshots position_snapshot
                  on position_snapshot.position_id = cast(:position_id as uuid)
                 and position_snapshot.position_data_version
                       = release.position_data_version
                left join product.positions legacy_position
                  on legacy_position.position_id = cast(:position_id as uuid)
                 and import_batch.position_data_version is null
                where release.exam_code = :exam_code
                  and release.status = 'active'
                  and coalesce(position_snapshot.year, legacy_position.year)
                        = release.exam_year
                order by release.exam_year desc, release.activated_at desc
                limit 1
                """
            ),
            {"position_id": position_id, "exam_code": exam_code},
        ).scalar_one_or_none()
        if position_year is None:
            position_year = session.execute(
                text(
                    """
                    select year
                    from product.positions
                    where position_id = cast(:position_id as uuid)
                    """
                ),
                {"position_id": position_id},
            ).scalar_one()
        return self.active_release(
            session,
            exam_code=exam_code,
            exam_year=int(position_year),
            now=now,
        )

    def get_profile_version(self, session: Session, *, user_id: str) -> int:
        version = session.execute(
            text(
                """
                select profile_version
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if version is None:
            raise QualificationContractError(
                "Learner profile does not exist",
                code="qualification_profile_missing",
            )
        return int(version)

    def replace_facts(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        mutations: list[FactMutation],
        now: datetime | None = None,
    ) -> tuple[int, list[QualificationFact]]:
        if not mutations:
            raise QualificationContractError(
                "At least one qualification fact is required",
                code="qualification_fact_set_invalid",
            )
        keys = [mutation.key for mutation in mutations]
        if len(keys) != len(set(keys)):
            raise QualificationContractError(
                "A profile version cannot repeat a fact key",
                code="qualification_fact_set_invalid",
            )
        if any(not mutation.evidence for mutation in mutations):
            raise QualificationContractError(
                "Every qualification fact requires evidence",
                code="qualification_fact_set_invalid",
            )

        command_payload = _mutation_payload(mutations)
        existing_change = (
            session.execute(
                text(
                    """
                select change_id, user_id::text as user_id, to_profile_version, command_payload
                from learner.qualification_profile_changes
                where command_id = :command_id
                """
                ),
                {"command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )
        if existing_change is not None:
            if (
                existing_change["user_id"] != user_id
                or existing_change["command_payload"] != command_payload
            ):
                raise QualificationContractError(
                    "An existing command ID refers to a different profile change",
                    code="idempotency_key_reused",
                )
            version = int(existing_change["to_profile_version"])
            return version, self._list_facts_for_version(
                session, user_id=user_id, profile_version=version
            )

        changed_at = now or datetime.now(UTC)
        session.execute(
            text(
                """
                insert into learner.profiles (user_id, profile_version, created_at, updated_at)
                values (cast(:user_id as uuid), 1, :now, :now)
                on conflict (user_id) do nothing
                """
            ),
            {"user_id": user_id, "now": changed_at},
        )
        current_version = session.execute(
            text(
                """
                select profile_version
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                for update
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        next_version = int(current_version) + 1
        change_id = str(uuid4())
        session.execute(
            text(
                """
                insert into learner.qualification_profile_changes (
                  change_id, command_id, user_id, from_profile_version,
                  to_profile_version, command_payload, created_at
                ) values (
                  cast(:change_id as uuid), :command_id, cast(:user_id as uuid),
                  :from_profile_version, :to_profile_version,
                  cast(:command_payload as jsonb), :created_at
                )
                """
            ),
            {
                "change_id": change_id,
                "command_id": command_id,
                "user_id": user_id,
                "from_profile_version": int(current_version),
                "to_profile_version": next_version,
                "command_payload": json.dumps(command_payload, ensure_ascii=False, sort_keys=True),
                "created_at": changed_at,
            },
        )
        previous = {
            row["fact_key"]: str(row["fact_id"])
            for row in session.execute(
                text(
                    """
                    select distinct on (fact_key) fact_key, fact_id
                    from learner.qualification_facts
                    where user_id = cast(:user_id as uuid)
                      and fact_key = any(cast(:keys as text[]))
                    order by fact_key, profile_version desc, recorded_at desc
                    """
                ),
                {"user_id": user_id, "keys": keys},
            ).mappings()
        }

        created: list[QualificationFact] = []
        for mutation in mutations:
            fact_id = mutation.fact_id or str(uuid4())
            effective_at = mutation.effective_at or changed_at
            session.execute(
                text(
                    """
                    insert into learner.qualification_facts (
                      fact_id, change_id, user_id, profile_version, fact_key, value,
                      verification_status, effective_at, supersedes_fact_id, recorded_at
                    ) values (
                      cast(:fact_id as uuid), cast(:change_id as uuid),
                      cast(:user_id as uuid), :profile_version,
                      :fact_key, cast(:value as jsonb), :verification_status,
                      :effective_at, cast(:supersedes_fact_id as uuid), :recorded_at
                    )
                    """
                ),
                {
                    "fact_id": fact_id,
                    "change_id": change_id,
                    "user_id": user_id,
                    "profile_version": next_version,
                    "fact_key": mutation.key,
                    "value": json.dumps(mutation.value, ensure_ascii=False, sort_keys=True),
                    "verification_status": mutation.verification_status,
                    "effective_at": effective_at,
                    "supersedes_fact_id": previous.get(mutation.key),
                    "recorded_at": changed_at,
                },
            )
            evidence = [
                self._insert_evidence(
                    session,
                    fact_id=fact_id,
                    item=item,
                    default_recorded_at=changed_at,
                )
                for item in mutation.evidence
            ]
            created.append(
                QualificationFact(
                    fact_id=fact_id,
                    key=mutation.key,
                    value=mutation.value,
                    verification_status=mutation.verification_status,
                    evidence=evidence,
                    effective_at=effective_at,
                    supersedes_fact_id=previous.get(mutation.key),
                )
            )

        session.execute(
            text(
                """
                update learner.profiles
                set profile_version = :profile_version, updated_at = :now
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id, "profile_version": next_version, "now": changed_at},
        )
        return next_version, created

    def _list_facts_for_version(
        self,
        session: Session,
        *,
        user_id: str,
        profile_version: int,
    ) -> list[QualificationFact]:
        rows = session.execute(
            text(
                """
                select fact_id, fact_key, value, verification_status,
                       effective_at, supersedes_fact_id
                from learner.qualification_facts
                where user_id = cast(:user_id as uuid)
                  and profile_version = :profile_version
                order by fact_key
                """
            ),
            {"user_id": user_id, "profile_version": profile_version},
        ).mappings()
        return [self._fact_from_row(session, row) for row in rows]

    def list_current_facts(self, session: Session, *, user_id: str) -> list[QualificationFact]:
        rows = (
            session.execute(
                text(
                    """
                select distinct on (fact.fact_key)
                  fact.fact_id, fact.fact_key, fact.value, fact.verification_status,
                  fact.effective_at, fact.supersedes_fact_id
                from learner.qualification_facts fact
                where fact.user_id = cast(:user_id as uuid)
                order by fact.fact_key, fact.profile_version desc, fact.recorded_at desc
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
        return [self._fact_from_row(session, row) for row in rows]

    def list_requirements(
        self,
        session: Session,
        *,
        position_id: str,
        rule_set_version: str,
        position_data_version: str,
    ) -> list[PositionRequirement]:
        rows = session.execute(
            text(
                """
                select *
                from product.position_requirements
                where position_id = cast(:position_id as uuid)
                  and rule_set_version = :rule_set_version
                  and position_data_version = :position_data_version
                order by fact_key, requirement_id
                """
            ),
            {
                "position_id": position_id,
                "rule_set_version": rule_set_version,
                "position_data_version": position_data_version,
            },
        ).mappings()
        return [
            PositionRequirement(
                requirement_id=str(row["requirement_id"]),
                field=row["fact_key"],
                operator=row["operator"],
                expected=row["expected"],
                hard_condition=row["hard_condition"],
                source_text=row["source_text"],
                source_reference=row["source_reference"],
            )
            for row in rows
        ]

    def compilation_is_authoritative(
        self,
        session: Session,
        *,
        position_id: str,
        rule_set_version: str,
        position_data_version: str,
    ) -> bool:
        return session.execute(
            text(
                """
                select exists (
                  select 1
                  from product.position_rule_compilations
                  where position_id = cast(:position_id as uuid)
                    and rule_set_version = :rule_set_version
                    and position_data_version = :position_data_version
                )
                """
            ),
            {
                "position_id": position_id,
                "rule_set_version": rule_set_version,
                "position_data_version": position_data_version,
            },
        ).scalar_one()

    def source_conditions_are_complete(
        self,
        session: Session,
        *,
        position_id: str,
        rule_set_version: str,
        position_data_version: str,
    ) -> bool:
        return bool(
            session.execute(
                text(
                    """
                    select coalesce(
                      not (
                        warnings @> '["source_conditions_incomplete"]'::jsonb
                      ),
                      false
                    )
                    from product.position_rule_compilations
                    where position_id = cast(:position_id as uuid)
                      and rule_set_version = :rule_set_version
                      and position_data_version = :position_data_version
                    """
                ),
                {
                    "position_id": position_id,
                    "rule_set_version": rule_set_version,
                    "position_data_version": position_data_version,
                },
            ).scalar_one_or_none()
        )

    def save_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
        learner_profile_version: int,
        evaluation: EligibilityEvaluation,
    ) -> str:
        snapshot_id = str(uuid4())
        payload = {
            "snapshot_id": snapshot_id,
            "user_id": user_id,
            "position_id": position_id,
            "learner_profile_version": learner_profile_version,
            "rule_set_version": evaluation.rule_set_version,
            "position_data_version": evaluation.position_data_version,
            "status": evaluation.status,
            "checks": json.dumps(
                [check.model_dump(by_alias=True, mode="json") for check in evaluation.checks],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "missing_fact_keys": json.dumps(evaluation.missing_fact_keys, ensure_ascii=False),
            "evaluated_at": evaluation.evaluated_at,
        }
        row = session.execute(
            text(
                """
                insert into learner.eligibility_snapshots (
                  snapshot_id, user_id, position_id, learner_profile_version,
                  rule_set_version, position_data_version, status, checks,
                  missing_fact_keys, evaluated_at
                ) values (
                  cast(:snapshot_id as uuid), cast(:user_id as uuid), cast(:position_id as uuid),
                  :learner_profile_version, :rule_set_version, :position_data_version,
                  :status, cast(:checks as jsonb), cast(:missing_fact_keys as jsonb), :evaluated_at
                )
                on conflict (
                  user_id, position_id, learner_profile_version,
                  rule_set_version, position_data_version
                ) do nothing
                returning snapshot_id
                """
            ),
            payload,
        ).scalar_one_or_none()
        if row is not None:
            return str(row)

        existing = (
            session.execute(
                text(
                    """
                select snapshot_id, status, checks, missing_fact_keys, evaluated_at
                from learner.eligibility_snapshots
                where user_id = cast(:user_id as uuid)
                  and position_id = cast(:position_id as uuid)
                  and learner_profile_version = :learner_profile_version
                  and rule_set_version = :rule_set_version
                  and position_data_version = :position_data_version
                """
                ),
                payload,
            )
            .mappings()
            .one()
        )
        expected_checks = json.loads(payload["checks"])
        expected_missing = json.loads(payload["missing_fact_keys"])
        if (
            existing["status"] != evaluation.status
            or existing["checks"] != expected_checks
            or existing["missing_fact_keys"] != expected_missing
        ):
            raise QualificationContractError(
                "An eligibility input version already has a different result",
                code="qualification_evaluation_conflict",
            )
        evaluation.evaluated_at = existing["evaluated_at"]
        return str(existing["snapshot_id"])

    def _insert_evidence(
        self,
        session: Session,
        *,
        fact_id: str,
        item: FactEvidenceInput,
        default_recorded_at: datetime,
    ) -> FactEvidence:
        evidence_id = item.evidence_id or str(uuid4())
        recorded_at = item.recorded_at or default_recorded_at
        session.execute(
            text(
                """
                insert into learner.qualification_evidence (
                  evidence_id, fact_id, source_type, source_reference, recorded_at
                ) values (
                  cast(:evidence_id as uuid), cast(:fact_id as uuid), :source_type,
                  :source_reference, :recorded_at
                )
                """
            ),
            {
                "evidence_id": evidence_id,
                "fact_id": fact_id,
                "source_type": item.source_type,
                "source_reference": item.source_reference,
                "recorded_at": recorded_at,
            },
        )
        return FactEvidence(
            evidence_id=evidence_id,
            source_type=item.source_type,
            source_reference=item.source_reference,
            recorded_at=recorded_at,
        )

    def _fact_from_row(self, session: Session, row: Any) -> QualificationFact:
        evidence_rows = session.execute(
            text(
                """
                select evidence_id, source_type, source_reference, recorded_at
                from learner.qualification_evidence
                where fact_id = :fact_id
                order by recorded_at, evidence_id
                """
            ),
            {"fact_id": row["fact_id"]},
        ).mappings()
        return QualificationFact(
            fact_id=str(row["fact_id"]),
            key=row["fact_key"],
            value=row["value"],
            verification_status=row["verification_status"],
            evidence=[
                FactEvidence(
                    evidence_id=str(evidence["evidence_id"]),
                    source_type=evidence["source_type"],
                    source_reference=evidence["source_reference"],
                    recorded_at=evidence["recorded_at"],
                )
                for evidence in evidence_rows
            ],
            effective_at=row["effective_at"],
            supersedes_fact_id=(
                str(row["supersedes_fact_id"]) if row["supersedes_fact_id"] else None
            ),
        )


def _lifecycle_from_release_rows(
    rows: list[RowMapping],
    *,
    checked_at: datetime,
) -> PositionReleaseLifecycle:
    release = rows[0]
    windows = [
        _position_release_window_from_row(row) for row in rows if row["window_id"] is not None
    ]
    if not windows:
        return _no_position_release(str(release["exam_code"]))

    current = next(
        (window for window in windows if window.opens_at <= checked_at < window.closes_at),
        None,
    )
    if current is not None:
        phase: PositionReleasePhase = "open"
        selected_window = current
    else:
        future = [window for window in windows if checked_at < window.opens_at]
        past = [window for window in windows if window.closes_at <= checked_at]
        if future and past:
            phase = "gap"
            selected_window = min(future, key=lambda window: window.opens_at)
        elif future:
            phase = "upcoming"
            selected_window = min(future, key=lambda window: window.opens_at)
        else:
            phase = "closed"
            selected_window = max(past, key=lambda window: window.closes_at)
    return PositionReleaseLifecycle(
        phase=phase,
        release_id=str(release["release_id"]),
        exam_code=str(release["exam_code"]),
        exam_year=int(release["exam_year"]),
        rule_set_version=str(release["rule_set_version"]),
        position_data_version=str(release["position_data_version"]),
        content_digest=(
            str(release["content_digest"]) if release["content_digest"] is not None else None
        ),
        activated_at=_aware_utc(release["activated_at"]),
        window=selected_window,
    )


def _position_release_window_from_row(row: RowMapping) -> PositionReleaseWindow:
    return PositionReleaseWindow(
        window_id=str(row["window_id"]),
        kind=row["window_kind"],
        sequence_no=int(row["sequence_no"]),
        opens_at=_aware_utc(row["opens_at"]),
        closes_at=_aware_utc(row["closes_at"]),
        official_source_url=str(row["official_source_url"]),
        official_source_title=str(row["official_source_title"]),
        official_source_published_at=_aware_utc(row["official_source_published_at"]),
        official_source_received_at=_aware_utc(row["official_source_received_at"]),
        official_source_sha256=str(row["official_source_sha256"]),
        official_source_receipt=str(row["official_source_receipt"]),
    )


def _no_position_release(exam_code: str) -> PositionReleaseLifecycle:
    return PositionReleaseLifecycle(
        phase="no_release",
        release_id=None,
        exam_code=exam_code,
        exam_year=None,
        rule_set_version=None,
        position_data_version=None,
        content_digest=None,
        activated_at=None,
        window=None,
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PositionRuleReleaseUnavailable(
            "Position release lifecycle timestamps must include a timezone"
        )
    return value.astimezone(UTC)


def _mutation_payload(mutations: list[FactMutation]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "key": mutation.key,
                "value": mutation.value,
                "verificationStatus": mutation.verification_status,
                "evidence": [
                    {
                        "sourceType": item.source_type,
                        "sourceReference": item.source_reference,
                    }
                    for item in mutation.evidence
                ],
            }
            for mutation in mutations
        ],
        key=lambda item: item["key"],
    )


def _confirmation_from_row(row: Any) -> RequirementConfirmation:
    return RequirementConfirmation(
        confirmation_id=str(row["confirmation_id"]),
        position_id=str(row["position_id"]),
        requirement_id=str(row["requirement_id"]),
        profile_version=int(row["profile_version"]),
        response=row["response"],
        statement=row["statement"],
        confirmed_at=row["confirmed_at"],
        supersedes_confirmation_id=(
            str(row["supersedes_confirmation_id"]) if row["supersedes_confirmation_id"] else None
        ),
    )
