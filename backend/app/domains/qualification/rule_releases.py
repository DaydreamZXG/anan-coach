from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.qualification.compiler import (
    COMPILER_VERSION,
    RawPositionConditions,
    compile_position_rules,
)
from app.platform.outbox import OutboxRepository


class RuleReleaseContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuleCompilationSummary:
    position_count: int
    deterministic_count: int
    needs_confirmation_count: int
    no_conditions_count: int
    requirements_count: int
    manual_review_count: int


class PositionRuleReleaseService:
    def __init__(self, outbox: OutboxRepository | None = None) -> None:
        self._outbox = outbox or OutboxRepository()

    def ensure_draft(
        self,
        session: Session,
        *,
        exam_code: str,
        exam_year: int,
        rule_set_version: str,
        position_data_version: str,
    ) -> str:
        row = session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status
                ) values (
                  cast(:release_id as uuid), :exam_code, :exam_year,
                  :rule_set_version, :position_data_version, 'draft'
                )
                on conflict (
                  exam_code, exam_year, rule_set_version, position_data_version
                ) do nothing
                returning release_id
                """
            ),
            {
                "release_id": str(uuid4()),
                "exam_code": exam_code,
                "exam_year": exam_year,
                "rule_set_version": rule_set_version,
                "position_data_version": position_data_version,
            },
        ).scalar_one_or_none()
        if row is not None:
            return str(row)
        existing = session.execute(
            text(
                """
                select release_id
                from product.position_rule_releases
                where exam_code = :exam_code and exam_year = :exam_year
                  and rule_set_version = :rule_set_version
                  and position_data_version = :position_data_version
                """
            ),
            {
                "exam_code": exam_code,
                "exam_year": exam_year,
                "rule_set_version": rule_set_version,
                "position_data_version": position_data_version,
            },
        ).scalar_one()
        return str(existing)

    def compile_draft(
        self,
        session: Session,
        *,
        release_id: str,
        compiled_at: datetime | None = None,
    ) -> RuleCompilationSummary:
        release = self._locked_release(session, release_id)
        if release["status"] != "draft":
            raise RuleReleaseContractError("Only a draft rule release can be compiled")
        rows = session.execute(
            text(
                """
                select position_id::text as position_id, education_min, major_required,
                       political_req, service_min_yrs, hukou_req, remark, raw_json
                from product.positions
                where year = :exam_year
                order by position_id
                """
            ),
            {"exam_year": release["exam_year"]},
        ).mappings().all()
        if not rows:
            raise RuleReleaseContractError("The release year has no positions")
        source_conditions_complete = all(
            row["raw_json"] is not None for row in rows
        )

        timestamp = compiled_at or datetime.now(UTC)
        status_counts = {
            "deterministic": 0,
            "needs_confirmation": 0,
            "no_conditions": 0,
        }
        requirements_count = 0
        manual_review_count = 0
        for row in rows:
            source = RawPositionConditions(
                position_id=row["position_id"],
                education_min=row["education_min"],
                major_required=row["major_required"],
                political_req=row["political_req"],
                service_min_yrs=row["service_min_yrs"],
                hukou_req=row["hukou_req"],
                remark=row["remark"],
                raw_payload=row["raw_json"],
            )
            compiled = compile_position_rules(
                source,
                rule_set_version=release["rule_set_version"],
                position_data_version=release["position_data_version"],
                source_conditions_complete=source_conditions_complete,
            )
            existing = session.execute(
                text(
                    """
                    select source_fingerprint, compiler_version
                    from product.position_rule_compilations
                    where position_id = cast(:position_id as uuid)
                      and rule_set_version = :rule_set_version
                      and position_data_version = :position_data_version
                    """
                ),
                {
                    "position_id": source.position_id,
                    "rule_set_version": release["rule_set_version"],
                    "position_data_version": release["position_data_version"],
                },
            ).mappings().one_or_none()
            if existing is not None:
                if (
                    existing["source_fingerprint"] != compiled.source_fingerprint
                    or existing["compiler_version"] != COMPILER_VERSION
                ):
                    raise RuleReleaseContractError(
                        "A compiled release is immutable; create a new version after source changes"
                    )
            else:
                for requirement in compiled.requirements:
                    session.execute(
                        text(
                            """
                            insert into product.position_requirements (
                              requirement_id, position_id, rule_set_version,
                              position_data_version, fact_key, operator, expected,
                              hard_condition, source_text, source_reference
                            ) values (
                              cast(:requirement_id as uuid), cast(:position_id as uuid),
                              :rule_set_version, :position_data_version, :fact_key,
                              :operator, cast(:expected as jsonb), :hard_condition,
                              :source_text, :source_reference
                            )
                            """
                        ),
                        {
                            "requirement_id": requirement.requirement_id,
                            "position_id": source.position_id,
                            "rule_set_version": release["rule_set_version"],
                            "position_data_version": release["position_data_version"],
                            "fact_key": requirement.field,
                            "operator": requirement.operator,
                            "expected": (
                                json.dumps(requirement.expected, ensure_ascii=False)
                                if requirement.expected is not None
                                else None
                            ),
                            "hard_condition": requirement.hard_condition,
                            "source_text": requirement.source_text,
                            "source_reference": requirement.source_reference,
                        },
                    )
                session.execute(
                    text(
                        """
                        insert into product.position_rule_compilations (
                          compilation_id, position_id, rule_set_version,
                          position_data_version, compiler_version, source_fingerprint,
                          status, requirements_count, manual_review_count, warnings,
                          compiled_at
                        ) values (
                          cast(:compilation_id as uuid), cast(:position_id as uuid),
                          :rule_set_version, :position_data_version, :compiler_version,
                          :source_fingerprint, :status, :requirements_count,
                          :manual_review_count, cast(:warnings as jsonb), :compiled_at
                        )
                        """
                    ),
                    {
                        "compilation_id": str(uuid4()),
                        "position_id": source.position_id,
                        "rule_set_version": release["rule_set_version"],
                        "position_data_version": release["position_data_version"],
                        "compiler_version": COMPILER_VERSION,
                        "source_fingerprint": compiled.source_fingerprint,
                        "status": compiled.status,
                        "requirements_count": len(compiled.requirements),
                        "manual_review_count": compiled.manual_review_count,
                        "warnings": json.dumps(compiled.warnings, ensure_ascii=False),
                        "compiled_at": timestamp,
                    },
                )
            status_counts[compiled.status] += 1
            requirements_count += len(compiled.requirements)
            manual_review_count += compiled.manual_review_count

        return RuleCompilationSummary(
            position_count=len(rows),
            deterministic_count=status_counts["deterministic"],
            needs_confirmation_count=status_counts["needs_confirmation"],
            no_conditions_count=status_counts["no_conditions"],
            requirements_count=requirements_count,
            manual_review_count=manual_review_count,
        )

    def activate(
        self,
        session: Session,
        *,
        release_id: str,
        activated_at: datetime | None = None,
    ) -> None:
        release = self._locked_release(session, release_id)
        if release["status"] == "active":
            return
        if release["status"] != "draft":
            raise RuleReleaseContractError("Only a draft release can be activated")
        counts = session.execute(
            text(
                """
                select
                  (select count(*) from product.positions where year = :exam_year) as positions,
                  (select count(*)
                   from product.position_rule_compilations compilation
                   join product.positions position
                     on position.position_id = compilation.position_id
                   where position.year = :exam_year
                     and compilation.rule_set_version = :rule_set_version
                     and compilation.position_data_version = :position_data_version) as compilations,
                  (select coalesce(sum(compilation.requirements_count), 0)
                   from product.position_rule_compilations compilation
                   join product.positions position
                     on position.position_id = compilation.position_id
                   where position.year = :exam_year
                     and compilation.rule_set_version = :rule_set_version
                     and compilation.position_data_version = :position_data_version) as audited_requirements,
                  (select count(*)
                   from product.position_requirements requirement
                   join product.positions position
                     on position.position_id = requirement.position_id
                   where position.year = :exam_year
                     and requirement.rule_set_version = :rule_set_version
                     and requirement.position_data_version = :position_data_version) as stored_requirements
                """
            ),
            {
                "exam_year": release["exam_year"],
                "rule_set_version": release["rule_set_version"],
                "position_data_version": release["position_data_version"],
            },
        ).mappings().one()
        if (
            counts["positions"] == 0
            or counts["positions"] != counts["compilations"]
            or counts["audited_requirements"] != counts["stored_requirements"]
        ):
            raise RuleReleaseContractError(
                "Every position must have a compilation audit before activation"
            )
        timestamp = activated_at or datetime.now(UTC)
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'retired'
                where exam_code = :exam_code and exam_year = :exam_year
                  and status = 'active'
                """
            ),
            {"exam_code": release["exam_code"], "exam_year": release["exam_year"]},
        )
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'active', activated_at = :activated_at
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": release_id, "activated_at": timestamp},
        )
        self._outbox.append(
            session,
            deduplication_key=f"position-rule-release:{release_id}:activated",
            aggregate_type="position_rule_release",
            aggregate_id=release_id,
            event_type="position_rule_release.activated",
            payload={
                "releaseId": release_id,
                "examCode": release["exam_code"],
                "examYear": release["exam_year"],
                "ruleSetVersion": release["rule_set_version"],
                "positionDataVersion": release["position_data_version"],
            },
        )

    def _locked_release(self, session: Session, release_id: str):
        row = session.execute(
            text(
                """
                select *
                from product.position_rule_releases
                where release_id = cast(:release_id as uuid)
                for update
                """
            ),
            {"release_id": release_id},
        ).mappings().one_or_none()
        if row is None:
            raise RuleReleaseContractError("Rule release does not exist")
        return row
