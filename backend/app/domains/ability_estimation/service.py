from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import sqrt
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.ability_estimation.schemas import (
    AbilityEstimateCurrent,
    AbilityEstimateVersion,
    AbilityEvidenceSummary,
    ReportedScoreEvidenceRequest,
    ReportedScoreEvidenceResult,
    ScoreEvidence,
    ScoreEvidenceHistory,
    ScoreEvidenceHistoryItem,
    ScoreRange,
)
from app.domains.qualification.repository import QualificationRepository
from app.platform.outbox import OutboxRepository


ALGORITHM_VERSION = "ability-estimate-v1"
MAX_EVIDENCE_AGE_DAYS = 365
CONFLICT_SPREAD = 15.0
SOURCE_WEIGHT = {
    "self_report": 0.35,
    "placement_test": 0.55,
    "external_mock": 0.75,
    "controlled_mock": 0.9,
    "official_exam": 1.0,
}
SOURCE_UNCERTAINTY = {
    "self_report": 8.0,
    "placement_test": 7.0,
    "external_mock": 4.5,
    "controlled_mock": 3.0,
    "official_exam": 1.5,
}
TIMED_FACTOR = {"timed": 1.0, "unknown": 0.82, "untimed": 0.6}
CONTEXT_FACTOR = {
    "official_exam": 1.0,
    "full_mock": 0.9,
    "historical_estimate": 0.72,
    None: 1.0,
}
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class AbilityEstimateContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Evidence:
    evidence_id: str
    source_type: str
    reported_context: str | None
    xingce_score: float | None
    shenlun_score: float | None
    timed_status: str
    verification_status: str
    occurred_at: datetime


@dataclass(frozen=True)
class _ComponentEstimate:
    score_range: ScoreRange
    confidence: str
    conflict: bool


class AbilityEstimateService:
    def __init__(
        self,
        *,
        qualification: QualificationRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._qualification = qualification or QualificationRepository()
        self._outbox = outbox or OutboxRepository()

    def get_current(
        self,
        session: Session,
        *,
        user_id: str,
        exam_year: int | None = None,
        now: datetime | None = None,
    ) -> AbilityEstimateCurrent:
        release = self._qualification.active_release(
            session,
            exam_code="guokao",
            exam_year=exam_year,
            now=now,
        )
        row = (
            session.execute(
                text(
                    """
                select *
                from learning.ability_estimate_versions
                where user_id = cast(:user_id as uuid)
                  and exam_code = :exam_code
                  and exam_year = :exam_year
                  and not exists (
                    select 1
                    from learning.ability_estimate_versions successor
                    where successor.supersedes_estimate_version_id =
                          ability_estimate_versions.estimate_version_id
                  )
                limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "exam_code": release.exam_code,
                    "exam_year": release.exam_year,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return AbilityEstimateCurrent(
                estimate=None,
                readiness="unavailable",
                missing_components=["xingce", "shenlun"],
            )
        estimate = _estimate_from_row(row)
        readiness = (
            "stale"
            if _as_utc(now or datetime.now(UTC)) >= _as_utc(estimate.valid_until)
            else estimate.status
        )
        return AbilityEstimateCurrent(
            estimate=estimate,
            readiness=readiness,
            missing_components=estimate.evidence_summary.missing_components,
        )

    def list_score_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> ScoreEvidenceHistory:
        release = self._qualification.active_release(
            session,
            exam_code="guokao",
            now=now,
        )
        rows = (
            session.execute(
                text(
                    """
                select evidence.*, successor.evidence_id as superseded_by_evidence_id
                from learning.score_evidence evidence
                left join learning.score_evidence successor
                  on successor.supersedes_evidence_id = evidence.evidence_id
                where evidence.user_id = cast(:user_id as uuid)
                  and evidence.exam_code = :exam_code
                  and evidence.exam_year = :exam_year
                order by evidence.occurred_at desc, evidence.created_at desc,
                         evidence.evidence_id desc
                """
                ),
                {
                    "user_id": user_id,
                    "exam_code": release.exam_code,
                    "exam_year": release.exam_year,
                },
            )
            .mappings()
            .all()
        )
        return ScoreEvidenceHistory(items=[_history_schema(row) for row in rows])

    def record_reported_score(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        report: ReportedScoreEvidenceRequest,
        now: datetime | None = None,
    ) -> ReportedScoreEvidenceResult:
        timestamp = _as_utc(now or datetime.now(UTC))
        occurred_at = _as_utc(report.occurred_at)
        if occurred_at > timestamp:
            raise AbilityEstimateContractError("Score evidence cannot occur in the future")
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
            raise AbilityEstimateContractError("Learner does not exist")
        existing = self._evidence_for_command(session, command_id)
        if existing is not None:
            payload = _command_payload(
                user_id=user_id,
                exam_code=existing["exam_code"],
                exam_year=int(existing["exam_year"]),
                report=report,
            )
            if existing["command_payload"] != payload:
                raise AbilityEstimateContractError(
                    "An existing command ID refers to different score evidence"
                )
            estimate = self._estimate_created_for_evidence(
                session,
                user_id=user_id,
                exam_code=existing["exam_code"],
                exam_year=int(existing["exam_year"]),
                evidence_id=str(existing["evidence_id"]),
            )
            return ReportedScoreEvidenceResult(
                evidence=_evidence_schema(existing),
                estimate=estimate,
            )
        if occurred_at + timedelta(days=MAX_EVIDENCE_AGE_DAYS) <= timestamp:
            raise AbilityEstimateContractError(
                f"Score evidence must be newer than {MAX_EVIDENCE_AGE_DAYS} days"
            )
        release = self._qualification.active_release(
            session,
            exam_code="guokao",
            now=timestamp,
        )
        payload = _command_payload(
            user_id=user_id,
            exam_code=release.exam_code,
            exam_year=release.exam_year,
            report=report,
        )

        supersedes_id = (
            str(report.supersedes_evidence_id)
            if report.supersedes_evidence_id is not None
            else None
        )
        if supersedes_id is not None:
            predecessor = session.execute(
                text(
                    """
                    select evidence_id
                    from learning.score_evidence
                    where evidence_id = cast(:evidence_id as uuid)
                      and user_id = cast(:user_id as uuid)
                      and exam_code = :exam_code
                      and exam_year = :exam_year
                    for update
                    """
                ),
                {
                    "evidence_id": supersedes_id,
                    "user_id": user_id,
                    "exam_code": release.exam_code,
                    "exam_year": release.exam_year,
                },
            ).scalar_one_or_none()
            if predecessor is None:
                raise AbilityEstimateContractError(
                    "Superseded score evidence does not belong to this learner and exam"
                )
            successor = session.execute(
                text(
                    """
                    select evidence_id
                    from learning.score_evidence
                    where supersedes_evidence_id = cast(:evidence_id as uuid)
                    """
                ),
                {"evidence_id": supersedes_id},
            ).scalar_one_or_none()
            if successor is not None:
                raise AbilityEstimateContractError("Score evidence has already been superseded")

        evidence_id = str(uuid4())
        total_score = (
            round(report.xingce_score + report.shenlun_score, 2)
            if report.xingce_score is not None and report.shenlun_score is not None
            else None
        )
        row = (
            session.execute(
                text(
                    """
                insert into learning.score_evidence (
                  evidence_id, command_id, user_id, exam_code, exam_year,
                  source_type, reported_context, xingce_score, shenlun_score,
                  total_score, timed_status, verification_status, occurred_at,
                  supersedes_evidence_id, command_payload, created_at
                ) values (
                  cast(:evidence_id as uuid), :command_id, cast(:user_id as uuid),
                  :exam_code, :exam_year, 'self_report', :reported_context,
                  :xingce_score, :shenlun_score, :total_score, :timed_status,
                  'user_confirmed', :occurred_at, cast(:supersedes_evidence_id as uuid),
                  cast(:command_payload as jsonb), :created_at
                )
                on conflict (command_id) do nothing
                returning *
                """
                ),
                {
                    "evidence_id": evidence_id,
                    "command_id": command_id,
                    "user_id": user_id,
                    "exam_code": release.exam_code,
                    "exam_year": release.exam_year,
                    "reported_context": report.reported_context,
                    "xingce_score": report.xingce_score,
                    "shenlun_score": report.shenlun_score,
                    "total_score": total_score,
                    "timed_status": report.timed_status,
                    "occurred_at": occurred_at,
                    "supersedes_evidence_id": supersedes_id,
                    "command_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    "created_at": timestamp,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            row = self._evidence_for_command(session, command_id)
            if row is None or row["command_payload"] != payload:
                raise AbilityEstimateContractError(
                    "Concurrent score evidence command has different inputs"
                )
        estimate = self._get_or_create_estimate(
            session,
            user_id=user_id,
            exam_code=release.exam_code,
            exam_year=release.exam_year,
            generated_at=timestamp,
        )
        return ReportedScoreEvidenceResult(
            evidence=_evidence_schema(row),
            estimate=estimate,
        )

    def record_external_mock_score(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        source_report_version_id: str,
        occurred_at: datetime,
        xingce_score: float | None,
        shenlun_score: float | None,
        now: datetime | None = None,
    ) -> ReportedScoreEvidenceResult:
        """Publish an explicitly confirmed, timed external mock as score evidence.

        The reporting owner decides whether a report is eligible. This owner only
        accepts the canonical component scores and keeps all ability writes here.
        """

        timestamp = _as_utc(now or datetime.now(UTC))
        evidence_at = _as_utc(occurred_at)
        if evidence_at > timestamp:
            raise AbilityEstimateContractError("Score evidence cannot occur in the future")
        if evidence_at + timedelta(days=MAX_EVIDENCE_AGE_DAYS) <= timestamp:
            raise AbilityEstimateContractError(
                f"Score evidence must be newer than {MAX_EVIDENCE_AGE_DAYS} days"
            )
        if xingce_score is None and shenlun_score is None:
            raise AbilityEstimateContractError("External mock evidence requires a component score")
        owner = session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise AbilityEstimateContractError("Learner does not exist")
        release = self._qualification.active_release(
            session,
            exam_code="guokao",
            now=timestamp,
        )
        payload = {
            "userId": user_id,
            "examCode": release.exam_code,
            "examYear": release.exam_year,
            "sourceType": "external_mock",
            "sourceReportVersionId": source_report_version_id,
            "occurredAt": evidence_at.isoformat(),
            "xingceScore": xingce_score,
            "shenlunScore": shenlun_score,
            "timedStatus": "timed",
            "verificationStatus": "user_confirmed",
        }
        existing = self._evidence_for_command(session, command_id)
        if existing is not None:
            if existing["command_payload"] != payload:
                raise AbilityEstimateContractError(
                    "An existing command ID refers to different score evidence"
                )
            return ReportedScoreEvidenceResult(
                evidence=_evidence_schema(existing),
                estimate=self._estimate_created_for_evidence(
                    session,
                    user_id=user_id,
                    exam_code=release.exam_code,
                    exam_year=release.exam_year,
                    evidence_id=str(existing["evidence_id"]),
                ),
            )
        evidence_id = str(uuid4())
        total_score = (
            round(xingce_score + shenlun_score, 2)
            if xingce_score is not None and shenlun_score is not None
            else None
        )
        row = (
            session.execute(
                text(
                    """
                insert into learning.score_evidence (
                  evidence_id, command_id, user_id, exam_code, exam_year,
                  source_type, reported_context, xingce_score, shenlun_score,
                  total_score, timed_status, verification_status, occurred_at,
                  supersedes_evidence_id, command_payload, created_at
                ) values (
                  cast(:evidence_id as uuid), :command_id, cast(:user_id as uuid),
                  :exam_code, :exam_year, 'external_mock', null,
                  :xingce_score, :shenlun_score, :total_score, 'timed',
                  'user_confirmed', :occurred_at, null,
                  cast(:command_payload as jsonb), :created_at
                )
                returning *
                """
                ),
                {
                    "evidence_id": evidence_id,
                    "command_id": command_id,
                    "user_id": user_id,
                    "exam_code": release.exam_code,
                    "exam_year": release.exam_year,
                    "xingce_score": xingce_score,
                    "shenlun_score": shenlun_score,
                    "total_score": total_score,
                    "occurred_at": evidence_at,
                    "command_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    "created_at": timestamp,
                },
            )
            .mappings()
            .one()
        )
        estimate = self._get_or_create_estimate(
            session,
            user_id=user_id,
            exam_code=release.exam_code,
            exam_year=release.exam_year,
            generated_at=timestamp,
        )
        return ReportedScoreEvidenceResult(
            evidence=_evidence_schema(row),
            estimate=estimate,
        )

    def _evidence_for_command(self, session: Session, command_id: str) -> RowMapping | None:
        return (
            session.execute(
                text("select * from learning.score_evidence where command_id = :command_id"),
                {"command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def _estimate_created_for_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        exam_code: str,
        exam_year: int,
        evidence_id: str,
    ) -> AbilityEstimateVersion:
        row = (
            session.execute(
                text(
                    """
                select *
                from learning.ability_estimate_versions
                where user_id = cast(:user_id as uuid)
                  and exam_code = :exam_code
                  and exam_year = :exam_year
                  and source_evidence_ids @> cast(:evidence_ids as jsonb)
                  and not exists (
                    select 1
                    from learning.ability_estimate_versions predecessor
                    where predecessor.estimate_version_id =
                          ability_estimate_versions.supersedes_estimate_version_id
                      and predecessor.source_evidence_ids
                          @> cast(:evidence_ids as jsonb)
                  )
                limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "exam_code": exam_code,
                    "exam_year": exam_year,
                    "evidence_ids": json.dumps([evidence_id]),
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AbilityEstimateContractError(
                "Score evidence command has no persisted estimate result"
            )
        return _estimate_from_row(row)

    def _get_or_create_estimate(
        self,
        session: Session,
        *,
        user_id: str,
        exam_code: str,
        exam_year: int,
        generated_at: datetime,
    ) -> AbilityEstimateVersion:
        rows = (
            session.execute(
                text(
                    """
                select evidence.*
                from learning.score_evidence evidence
                where evidence.user_id = cast(:user_id as uuid)
                  and evidence.exam_code = :exam_code
                  and evidence.exam_year = :exam_year
                  and evidence.occurred_at
                        + make_interval(days => :max_evidence_age_days)
                      > :generated_at
                  and not exists (
                    select 1
                    from learning.score_evidence successor
                    where successor.supersedes_evidence_id = evidence.evidence_id
                  )
                order by evidence.occurred_at, evidence.evidence_id
                """
                ),
                {
                    "user_id": user_id,
                    "exam_code": exam_code,
                    "exam_year": exam_year,
                    "generated_at": generated_at,
                    "max_evidence_age_days": MAX_EVIDENCE_AGE_DAYS,
                },
            )
            .mappings()
            .all()
        )
        evidence = [_internal_evidence(row) for row in rows]
        if not evidence:
            raise AbilityEstimateContractError("Cannot estimate ability without score evidence")
        fingerprint = _fingerprint(evidence)
        existing = (
            session.execute(
                text(
                    """
                select *
                from learning.ability_estimate_versions
                where user_id = cast(:user_id as uuid)
                  and exam_code = :exam_code
                  and exam_year = :exam_year
                  and input_fingerprint = :input_fingerprint
                """
                ),
                {
                    "user_id": user_id,
                    "exam_code": exam_code,
                    "exam_year": exam_year,
                    "input_fingerprint": fingerprint,
                },
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            return _estimate_from_row(existing)

        xingce = _estimate_component(evidence, "xingce_score")
        shenlun = _estimate_component(evidence, "shenlun_score")
        if xingce is None and shenlun is None:
            raise AbilityEstimateContractError("Score evidence has no usable components")
        total = (
            ScoreRange(
                lower=round(xingce.score_range.lower + shenlun.score_range.lower, 2),
                recommended=round(
                    xingce.score_range.recommended + shenlun.score_range.recommended,
                    2,
                ),
                upper=round(xingce.score_range.upper + shenlun.score_range.upper, 2),
            )
            if xingce is not None and shenlun is not None
            else None
        )
        confidence = _combined_confidence(xingce, shenlun)
        source_counts = Counter(item.source_type for item in evidence)
        missing_components = [
            component
            for component, estimate in (("xingce", xingce), ("shenlun", shenlun))
            if estimate is None
        ]
        conflict_components = [
            component
            for component, estimate in (("xingce", xingce), ("shenlun", shenlun))
            if estimate is not None and estimate.conflict
        ]
        summary = AbilityEvidenceSummary(
            evidence_count=len(evidence),
            xingce_evidence_count=sum(item.xingce_score is not None for item in evidence),
            shenlun_evidence_count=sum(item.shenlun_score is not None for item in evidence),
            source_counts=dict(sorted(source_counts.items())),
            latest_evidence_at=max(item.occurred_at for item in evidence),
            missing_components=missing_components,
            conflict_components=conflict_components,
        )
        component_valid_until = [
            max(item.occurred_at for item in evidence if getattr(item, field) is not None)
            + timedelta(days=MAX_EVIDENCE_AGE_DAYS)
            for field, estimate in (
                ("xingce_score", xingce),
                ("shenlun_score", shenlun),
            )
            if estimate is not None
        ]
        valid_until = min(component_valid_until)
        previous_id = session.execute(
            text(
                """
                select estimate_version_id::text
                from learning.ability_estimate_versions
                where user_id = cast(:user_id as uuid)
                  and exam_code = :exam_code
                  and exam_year = :exam_year
                  and not exists (
                    select 1
                    from learning.ability_estimate_versions successor
                    where successor.supersedes_estimate_version_id =
                          ability_estimate_versions.estimate_version_id
                  )
                limit 1
                """
            ),
            {"user_id": user_id, "exam_code": exam_code, "exam_year": exam_year},
        ).scalar_one_or_none()
        estimate_id = str(uuid4())
        values = {
            "estimate_version_id": estimate_id,
            "user_id": user_id,
            "exam_code": exam_code,
            "exam_year": exam_year,
            "status": "complete" if total is not None else "partial",
            **_range_columns("xingce", xingce.score_range if xingce else None),
            **_range_columns("shenlun", shenlun.score_range if shenlun else None),
            **_range_columns("total", total),
            "confidence": confidence,
            "source_evidence_ids": json.dumps(
                [item.evidence_id for item in evidence],
                ensure_ascii=False,
            ),
            "evidence_summary": json.dumps(
                summary.model_dump(by_alias=True, mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "algorithm_version": ALGORITHM_VERSION,
            "input_fingerprint": fingerprint,
            "supersedes_estimate_version_id": previous_id,
            "generated_at": generated_at,
            "valid_until": valid_until,
        }
        row = (
            session.execute(
                text(_INSERT_ESTIMATE_SQL),
                values,
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            row = (
                session.execute(
                    text(
                        """
                    select *
                    from learning.ability_estimate_versions
                    where user_id = cast(:user_id as uuid)
                      and exam_code = :exam_code
                      and exam_year = :exam_year
                      and input_fingerprint = :input_fingerprint
                    """
                    ),
                    values,
                )
                .mappings()
                .one()
            )
        else:
            self._outbox.append(
                session,
                deduplication_key=f"ability-estimate:{estimate_id}",
                aggregate_type="ability_estimate",
                aggregate_id=user_id,
                event_type="ability_estimate.changed",
                payload={
                    "estimateVersionId": estimate_id,
                    "userId": user_id,
                    "examCode": exam_code,
                    "examYear": exam_year,
                    "status": values["status"],
                    "total": total.model_dump() if total is not None else None,
                    "validUntil": valid_until.isoformat(),
                    "inputFingerprint": fingerprint,
                },
            )
        return _estimate_from_row(row)


def _estimate_component(
    evidence: list[_Evidence],
    field: str,
) -> _ComponentEstimate | None:
    observations = [
        (item, getattr(item, field)) for item in evidence if getattr(item, field) is not None
    ]
    if not observations:
        return None
    weighted: list[tuple[float, float, float]] = []
    for item, value in observations:
        verification = 1.0 if item.verification_status == "system_verified" else 0.85
        weight = (
            SOURCE_WEIGHT[item.source_type]
            * TIMED_FACTOR[item.timed_status]
            * CONTEXT_FACTOR[item.reported_context]
            * verification
        )
        uncertainty = SOURCE_UNCERTAINTY[item.source_type]
        if field == "shenlun_score":
            uncertainty *= 1.25
        weighted.append((float(value), weight, uncertainty))
    weight_sum = sum(weight for _, weight, _ in weighted)
    mean = sum(value * weight for value, weight, _ in weighted) / weight_sum
    variance = sum(weight * (value - mean) ** 2 for value, weight, _ in weighted) / weight_sum
    noise = sum(weight * uncertainty**2 for _, weight, uncertainty in weighted) / weight_sum
    width = min(20.0, sqrt(variance + noise))
    spread = max(value for value, _, _ in weighted) - min(value for value, _, _ in weighted)
    conflict = spread >= CONFLICT_SPREAD
    if conflict:
        width = min(20.0, max(width, spread / 2))
    score_range = ScoreRange(
        lower=round(max(0.0, mean - width), 2),
        recommended=round(mean, 2),
        upper=round(min(100.0, mean + width), 2),
    )
    system_count = sum(item.verification_status == "system_verified" for item, _ in observations)
    if not conflict and weight_sum >= 1.6 and system_count >= 2:
        confidence = "high"
    elif not conflict and weight_sum >= 0.7:
        confidence = "medium"
    else:
        confidence = "low"
    return _ComponentEstimate(score_range, confidence, conflict)


def _combined_confidence(
    xingce: _ComponentEstimate | None,
    shenlun: _ComponentEstimate | None,
) -> str:
    available = [item for item in (xingce, shenlun) if item is not None]
    return min(available, key=lambda item: CONFIDENCE_ORDER[item.confidence]).confidence


def _fingerprint(evidence: list[_Evidence]) -> str:
    payload = {
        "algorithmVersion": ALGORITHM_VERSION,
        "evidence": [
            {
                "evidenceId": item.evidence_id,
                "sourceType": item.source_type,
                "reportedContext": item.reported_context,
                "xingceScore": item.xingce_score,
                "shenlunScore": item.shenlun_score,
                "timedStatus": item.timed_status,
                "verificationStatus": item.verification_status,
                "occurredAt": item.occurred_at.isoformat(),
            }
            for item in evidence
        ],
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _range_columns(prefix: str, score_range: ScoreRange | None) -> dict[str, float | None]:
    return {
        f"{prefix}_lower": score_range.lower if score_range else None,
        f"{prefix}_recommended": score_range.recommended if score_range else None,
        f"{prefix}_upper": score_range.upper if score_range else None,
    }


def _internal_evidence(row: RowMapping) -> _Evidence:
    return _Evidence(
        evidence_id=str(row["evidence_id"]),
        source_type=row["source_type"],
        reported_context=row["reported_context"],
        xingce_score=_optional_float(row["xingce_score"]),
        shenlun_score=_optional_float(row["shenlun_score"]),
        timed_status=row["timed_status"],
        verification_status=row["verification_status"],
        occurred_at=_as_utc(row["occurred_at"]),
    )


def _evidence_schema(row: RowMapping) -> ScoreEvidence:
    return ScoreEvidence(
        evidence_id=str(row["evidence_id"]),
        exam_code=row["exam_code"],
        exam_year=int(row["exam_year"]),
        source_type=row["source_type"],
        reported_context=row["reported_context"],
        xingce_score=_optional_float(row["xingce_score"]),
        shenlun_score=_optional_float(row["shenlun_score"]),
        total_score=_optional_float(row["total_score"]),
        timed_status=row["timed_status"],
        verification_status=row["verification_status"],
        occurred_at=row["occurred_at"],
        supersedes_evidence_id=(
            str(row["supersedes_evidence_id"])
            if row["supersedes_evidence_id"] is not None
            else None
        ),
        created_at=row["created_at"],
    )


def _history_schema(row: RowMapping) -> ScoreEvidenceHistoryItem:
    evidence = _evidence_schema(row)
    successor = row["superseded_by_evidence_id"]
    return ScoreEvidenceHistoryItem(
        **evidence.model_dump(),
        superseded_by_evidence_id=str(successor) if successor is not None else None,
        active=successor is None,
    )


def _estimate_from_row(row: RowMapping) -> AbilityEstimateVersion:
    return AbilityEstimateVersion(
        estimate_version_id=str(row["estimate_version_id"]),
        exam_code=row["exam_code"],
        exam_year=int(row["exam_year"]),
        status=row["status"],
        xingce=_range_from_row(row, "xingce"),
        shenlun=_range_from_row(row, "shenlun"),
        total=_range_from_row(row, "total"),
        confidence=row["confidence"],
        source_evidence_ids=list(row["source_evidence_ids"]),
        evidence_summary=AbilityEvidenceSummary.model_validate(row["evidence_summary"]),
        algorithm_version=row["algorithm_version"],
        input_fingerprint=row["input_fingerprint"],
        supersedes_estimate_version_id=(
            str(row["supersedes_estimate_version_id"])
            if row["supersedes_estimate_version_id"] is not None
            else None
        ),
        generated_at=row["generated_at"],
        valid_until=row["valid_until"],
    )


def _command_payload(
    *,
    user_id: str,
    exam_code: str,
    exam_year: int,
    report: ReportedScoreEvidenceRequest,
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "examCode": exam_code,
        "examYear": exam_year,
        **report.model_dump(by_alias=True, mode="json"),
    }


def _range_from_row(row: RowMapping, prefix: str) -> ScoreRange | None:
    recommended = row[f"{prefix}_recommended"]
    if recommended is None:
        return None
    return ScoreRange(
        lower=float(row[f"{prefix}_lower"]),
        recommended=float(recommended),
        upper=float(row[f"{prefix}_upper"]),
    )


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


_INSERT_ESTIMATE_SQL = """
insert into learning.ability_estimate_versions (
  estimate_version_id, user_id, exam_code, exam_year, status,
  xingce_lower, xingce_recommended, xingce_upper,
  shenlun_lower, shenlun_recommended, shenlun_upper,
  total_lower, total_recommended, total_upper,
  confidence, source_evidence_ids, evidence_summary, algorithm_version,
  input_fingerprint, supersedes_estimate_version_id, generated_at, valid_until
) values (
  cast(:estimate_version_id as uuid), cast(:user_id as uuid), :exam_code,
  :exam_year, :status, :xingce_lower, :xingce_recommended, :xingce_upper,
  :shenlun_lower, :shenlun_recommended, :shenlun_upper,
  :total_lower, :total_recommended, :total_upper, :confidence,
  cast(:source_evidence_ids as jsonb), cast(:evidence_summary as jsonb),
  :algorithm_version, :input_fingerprint,
  cast(:supersedes_estimate_version_id as uuid), :generated_at, :valid_until
)
on conflict (user_id, exam_code, exam_year, input_fingerprint) do nothing
returning *
"""
