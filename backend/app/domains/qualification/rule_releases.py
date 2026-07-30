from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4, uuid5

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


@dataclass(frozen=True)
class PositionReleaseWindowInput:
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

    def configure_windows(
        self,
        session: Session,
        *,
        release_id: str,
        windows: list[PositionReleaseWindowInput],
    ) -> None:
        release = self._locked_release(session, release_id)
        if release["status"] != "draft":
            raise RuleReleaseContractError(
                "Business windows are mutable only while a release is draft"
            )
        normalized = _validated_windows(windows)
        session.execute(
            text(
                """
                delete from product.position_release_windows
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": release_id},
        )
        release_uuid = UUID(release_id)
        for window in normalized:
            session.execute(
                text(
                    """
                    insert into product.position_release_windows (
                      window_id, release_id, window_kind, sequence_no,
                      opens_at, closes_at, official_source_url,
                      official_source_title, official_source_published_at,
                      official_source_received_at, official_source_sha256,
                      official_source_receipt
                    ) values (
                      cast(:window_id as uuid), cast(:release_id as uuid),
                      :window_kind, :sequence_no, :opens_at, :closes_at,
                      :official_source_url, :official_source_title,
                      :official_source_published_at, :official_source_received_at,
                      :official_source_sha256, :official_source_receipt
                    )
                    """
                ),
                {
                    "window_id": str(
                        uuid5(
                            release_uuid,
                            f"position-release-window:{window.kind}:{window.sequence_no}",
                        )
                    ),
                    "release_id": release_id,
                    "window_kind": window.kind,
                    "sequence_no": window.sequence_no,
                    "opens_at": window.opens_at,
                    "closes_at": window.closes_at,
                    "official_source_url": window.official_source_url,
                    "official_source_title": window.official_source_title,
                    "official_source_published_at": (window.official_source_published_at),
                    "official_source_received_at": (window.official_source_received_at),
                    "official_source_sha256": window.official_source_sha256,
                    "official_source_receipt": window.official_source_receipt,
                },
            )

    def prepare_window_activation(
        self,
        session: Session,
        *,
        release_id: str,
        expected_windows: list[PositionReleaseWindowInput],
        allow_legacy_unwindowed_active: bool = False,
    ) -> bool:
        """Return true when the release must be compiled and activated.

        Migration 78 deliberately leaves a pre-existing active release untouched.
        The deployment CLI may adopt that exact release only inside the same
        transaction that installs audited windows and re-activates it. Any failure
        rolls the status transition back.
        """
        release = self._locked_release(session, release_id)
        if release["status"] == "draft":
            return True
        if release["status"] != "active":
            raise RuleReleaseContractError(
                "Only a draft or active release can be prepared for window activation"
            )
        stored_windows = self._load_windows(session, release_id=release_id)
        if stored_windows:
            if _validated_windows(stored_windows) != _validated_windows(expected_windows):
                raise RuleReleaseContractError(
                    "The active release business windows do not match the audited manifest"
                )
            if release.get("content_digest") is None:
                session.execute(
                    text(
                        """
                        update product.position_rule_releases
                        set status = status
                        where release_id = cast(:release_id as uuid)
                        """
                    ),
                    {"release_id": release_id},
                )
            return False
        if not allow_legacy_unwindowed_active:
            raise RuleReleaseContractError(
                "The legacy active release has no audited business window"
            )
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'draft'
                where release_id = cast(:release_id as uuid)
                  and status = 'active'
                """
            ),
            {"release_id": release_id},
        )
        return True

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
        rows = (
            session.execute(
                text(
                    """
                select
                  position.position_id::text as position_id,
                  case when snapshot.snapshot_id is not null
                    then snapshot.education_min else position.education_min
                  end as education_min,
                  case when snapshot.snapshot_id is not null
                    then snapshot.major_required else position.major_required
                  end as major_required,
                  case when snapshot.snapshot_id is not null
                    then snapshot.political_req else position.political_req
                  end as political_req,
                  case when snapshot.snapshot_id is not null
                    then snapshot.service_min_yrs else position.service_min_yrs
                  end as service_min_yrs,
                  case when snapshot.snapshot_id is not null
                    then snapshot.hukou_req else position.hukou_req
                  end as hukou_req,
                  case when snapshot.snapshot_id is not null
                    then snapshot.remark else position.remark
                  end as remark,
                  case when snapshot.snapshot_id is not null
                    then snapshot.raw_json else position.raw_json
                  end as raw_json
                from product.positions position
                left join product.position_import_batches import_batch
                  on import_batch.position_data_version = :position_data_version
                left join product.position_snapshots snapshot
                  on snapshot.position_id = position.position_id
                 and snapshot.position_data_version = :position_data_version
                where coalesce(snapshot.year, position.year) = :exam_year
                  and (
                    import_batch.position_data_version is null
                    or snapshot.snapshot_id is not null
                  )
                order by position.position_id
                """
                ),
                {
                    "exam_year": release["exam_year"],
                    "position_data_version": release["position_data_version"],
                },
            )
            .mappings()
            .all()
        )
        if not rows:
            raise RuleReleaseContractError("The release year has no positions")
        source_conditions_complete = all(row["raw_json"] is not None for row in rows)

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
            existing = (
                session.execute(
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
                )
                .mappings()
                .one_or_none()
            )
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
            _validated_windows(self._load_windows(session, release_id=release_id))
            if release.get("content_digest") is None:
                session.execute(
                    text(
                        """
                        update product.position_rule_releases
                        set status = status
                        where release_id = cast(:release_id as uuid)
                        """
                    ),
                    {"release_id": release_id},
                )
            return
        if release["status"] != "draft":
            raise RuleReleaseContractError("Only a draft release can be activated")
        counts = (
            session.execute(
                text(
                    """
                with release_positions as (
                  select position.position_id
                  from product.positions position
                  left join product.position_import_batches import_batch
                    on import_batch.position_data_version = :position_data_version
                  left join product.position_snapshots snapshot
                    on snapshot.position_id = position.position_id
                   and snapshot.position_data_version = :position_data_version
                  where coalesce(snapshot.year, position.year) = :exam_year
                    and (
                      import_batch.position_data_version is null
                      or snapshot.snapshot_id is not null
                    )
                )
                select
                  (select count(*) from release_positions) as positions,
                  (select count(*)
                   from product.position_rule_compilations compilation
                   join release_positions position
                     on position.position_id = compilation.position_id
                   where compilation.rule_set_version = :rule_set_version
                     and compilation.position_data_version = :position_data_version) as compilations,
                  (select coalesce(sum(compilation.requirements_count), 0)
                   from product.position_rule_compilations compilation
                   join release_positions position
                     on position.position_id = compilation.position_id
                   where compilation.rule_set_version = :rule_set_version
                     and compilation.position_data_version = :position_data_version) as audited_requirements,
                  (select count(*)
                   from product.position_requirements requirement
                   join release_positions position
                     on position.position_id = requirement.position_id
                   where requirement.rule_set_version = :rule_set_version
                     and requirement.position_data_version = :position_data_version) as stored_requirements
                """
                ),
                {
                    "exam_year": release["exam_year"],
                    "rule_set_version": release["rule_set_version"],
                    "position_data_version": release["position_data_version"],
                },
            )
            .mappings()
            .one()
        )
        if (
            counts["positions"] == 0
            or counts["positions"] != counts["compilations"]
            or counts["audited_requirements"] != counts["stored_requirements"]
        ):
            raise RuleReleaseContractError(
                "Every position must have a compilation audit before activation"
            )
        windows = _validated_windows(self._load_windows(session, release_id=release_id))
        timestamp = _aware_utc(activated_at or datetime.now(UTC))
        if any(window.official_source_received_at > timestamp for window in windows):
            raise RuleReleaseContractError(
                "Official source evidence must be received before release activation"
            )
        if timestamp >= max(window.closes_at for window in windows):
            raise RuleReleaseContractError(
                "A release cannot be activated after all business windows close"
            )
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
        content_digest = session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'active', activated_at = :activated_at
                where release_id = cast(:release_id as uuid)
                returning content_digest
                """
            ),
            {"release_id": release_id, "activated_at": timestamp},
        ).scalar_one()
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
                "contentDigest": content_digest,
                "businessWindows": [
                    {
                        "kind": window.kind,
                        "sequenceNo": window.sequence_no,
                        "opensAt": window.opens_at.isoformat(),
                        "closesAt": window.closes_at.isoformat(),
                        "officialSourceUrl": window.official_source_url,
                        "officialSourceTitle": window.official_source_title,
                        "officialSourcePublishedAt": (
                            window.official_source_published_at.isoformat()
                        ),
                        "officialSourceReceivedAt": (
                            window.official_source_received_at.isoformat()
                        ),
                        "officialSourceSha256": window.official_source_sha256,
                        "officialSourceReceipt": window.official_source_receipt,
                    }
                    for window in windows
                ],
            },
        )

    @staticmethod
    def _load_windows(
        session: Session,
        *,
        release_id: str,
    ) -> list[PositionReleaseWindowInput]:
        rows = (
            session.execute(
                text(
                    """
                    select
                      window_kind, sequence_no, opens_at, closes_at,
                      official_source_url, official_source_title,
                      official_source_published_at,
                      official_source_received_at,
                      official_source_sha256,
                      official_source_receipt
                    from product.position_release_windows
                    where release_id = cast(:release_id as uuid)
                    order by opens_at, window_kind, sequence_no
                    """
                ),
                {"release_id": release_id},
            )
            .mappings()
            .all()
        )
        return [
            PositionReleaseWindowInput(
                kind=row["window_kind"],
                sequence_no=int(row["sequence_no"]),
                opens_at=row["opens_at"],
                closes_at=row["closes_at"],
                official_source_url=row["official_source_url"],
                official_source_title=row["official_source_title"],
                official_source_published_at=row["official_source_published_at"],
                official_source_received_at=row["official_source_received_at"],
                official_source_sha256=row["official_source_sha256"],
                official_source_receipt=row["official_source_receipt"],
            )
            for row in rows
        ]

    def _locked_release(self, session: Session, release_id: str):
        row = (
            session.execute(
                text(
                    """
                select *
                from product.position_rule_releases
                where release_id = cast(:release_id as uuid)
                for update
                """
                ),
                {"release_id": release_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RuleReleaseContractError("Rule release does not exist")
        return row


def _validated_windows(
    windows: list[PositionReleaseWindowInput],
) -> list[PositionReleaseWindowInput]:
    if not windows:
        raise RuleReleaseContractError("A release requires one audited primary business window")
    normalized: list[PositionReleaseWindowInput] = []
    for window in windows:
        if window.kind not in {"primary", "supplemental"}:
            raise RuleReleaseContractError("Business window kind must be primary or supplemental")
        if window.sequence_no < 1 or (window.kind == "primary" and window.sequence_no != 1):
            raise RuleReleaseContractError("Business window sequence numbers must start at one")
        opens_at = _aware_utc(window.opens_at)
        closes_at = _aware_utc(window.closes_at)
        published_at = _aware_utc(window.official_source_published_at)
        received_at = _aware_utc(window.official_source_received_at)
        if opens_at >= closes_at:
            raise RuleReleaseContractError("Business window opensAt must be earlier than closesAt")
        if published_at > received_at:
            raise RuleReleaseContractError("Official source publishedAt cannot follow receivedAt")
        source_url = window.official_source_url.strip()
        parsed_url = urlparse(source_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or any(character.isspace() for character in source_url)
        ):
            raise RuleReleaseContractError("Official source URL must be an absolute HTTP(S) URL")
        source_title = window.official_source_title.strip()
        receipt = window.official_source_receipt.strip()
        source_sha256 = window.official_source_sha256.strip().lower()
        if not source_title or not receipt:
            raise RuleReleaseContractError("Official source title and receipt are required")
        if re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
            raise RuleReleaseContractError(
                "Official source SHA-256 must be 64 lowercase hexadecimal characters"
            )
        normalized.append(
            PositionReleaseWindowInput(
                kind=window.kind,
                sequence_no=window.sequence_no,
                opens_at=opens_at,
                closes_at=closes_at,
                official_source_url=source_url,
                official_source_title=source_title,
                official_source_published_at=published_at,
                official_source_received_at=received_at,
                official_source_sha256=source_sha256,
                official_source_receipt=receipt,
            )
        )

    primary = [window for window in normalized if window.kind == "primary"]
    if len(primary) != 1:
        raise RuleReleaseContractError(
            "A release requires exactly one audited primary business window"
        )
    supplemental = sorted(
        (window for window in normalized if window.kind == "supplemental"),
        key=lambda window: window.sequence_no,
    )
    if [window.sequence_no for window in supplemental] != list(range(1, len(supplemental) + 1)):
        raise RuleReleaseContractError("Supplemental window sequence numbers must be contiguous")
    ordered = sorted(
        normalized,
        key=lambda window: (
            window.opens_at,
            0 if window.kind == "primary" else 1,
            window.sequence_no,
        ),
    )
    if ordered[0].kind != "primary":
        raise RuleReleaseContractError(
            "The primary business window must precede supplemental windows"
        )
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.opens_at < previous.closes_at:
            raise RuleReleaseContractError("Position release business windows cannot overlap")
    return ordered


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuleReleaseContractError("Position release timestamps must include a timezone")
    return value.astimezone(UTC)
