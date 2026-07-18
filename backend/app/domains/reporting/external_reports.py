from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.ability_estimation.service import (
    AbilityEstimateService,
    MAX_EVIDENCE_AGE_DAYS,
)
from app.domains.reporting.repository import ReportingRepository
from app.domains.reporting.schemas import (
    ExternalMockReportConfirmCommand,
    ExternalMockReportDraftCommand,
    ExternalMockReportProjection,
)


class ExternalMockReportError(RuntimeError):
    pass


class ExternalMockReportNotFound(LookupError):
    pass


class ExternalMockReportService:
    def __init__(
        self,
        *,
        repository: ReportingRepository | None = None,
        ability: AbilityEstimateService | None = None,
    ) -> None:
        self._repository = repository or ReportingRepository()
        self._ability = ability or AbilityEstimateService()

    def create_draft(
        self,
        session: Session,
        *,
        user_id: str,
        idempotency_key: str,
        command: ExternalMockReportDraftCommand,
        now: datetime | None = None,
    ) -> ExternalMockReportProjection:
        timestamp = _as_utc(now or datetime.now(UTC))
        occurred_at = _as_utc(command.occurred_at)
        if occurred_at > timestamp:
            raise ExternalMockReportError("External report cannot occur in the future")
        request_fingerprint = _fingerprint(
            {
                "commandType": "draft",
                "userId": user_id,
                "report": command.model_dump(by_alias=True, mode="json"),
            }
        )
        self._repository.lock_user(session, user_id=user_id)
        replay = self._repository.report_for_command(
            session, user_id=user_id, idempotency_key=idempotency_key
        )
        if replay is not None:
            stored_command, report = replay
            self._require_replay(
                stored_command,
                command_type="draft",
                request_fingerprint=request_fingerprint,
            )
            return _projection(report)
        recognition = self._repository.confirmed_printed_recognition(
            session,
            user_id=user_id,
            recognition_revision_id=str(command.recognition_revision_id),
            artifact_version_id=str(command.artifact_version_id),
        )
        if recognition is None:
            raise ExternalMockReportError(
                "Only the confirmed head of printed screenshot OCR can create a report"
            )
        current = self._repository.current_for_recognition(
            session,
            user_id=user_id,
            recognition_revision_id=str(command.recognition_revision_id),
        )
        if current is not None and current["state"] == "confirmed":
            raise ExternalMockReportError(
                "Confirmed external report facts cannot be edited; upload a new screenshot"
            )
        if current is not None and current["input_fingerprint"] == request_fingerprint:
            result = _projection(current)
            self._record_command(
                session,
                user_id=user_id,
                idempotency_key=idempotency_key,
                command_type="draft",
                request_fingerprint=request_fingerprint,
                result=result,
                created_at=timestamp,
            )
            return result
        report_id = str(current["report_id"]) if current is not None else str(uuid4())
        report_version_id = str(uuid4())
        row = self._repository.insert_report(
            session,
            values={
                "report_version_id": report_version_id,
                "report_id": report_id,
                "command_id": f"external-report-draft:{user_id}:{idempotency_key}",
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1 if current else 1,
                "state": "candidate",
                "artifact_version_id": str(command.artifact_version_id),
                "recognition_revision_id": str(command.recognition_revision_id),
                "platform_name": command.platform_name,
                "exam_name": command.exam_name,
                "occurred_at": occurred_at,
                "timed_status": command.timed_status,
                "duration_minutes": command.duration_minutes,
                "total_score": command.total_score,
                "xingce_score": command.xingce_score,
                "shenlun_score": command.shenlun_score,
                "module_scores": command.module_scores,
                "evidence_grade": "candidate_unconfirmed",
                "score_evidence_id": None,
                "estimate_version_id": None,
                "input_fingerprint": request_fingerprint,
                "supersedes_report_version_id": (
                    str(current["report_version_id"]) if current is not None else None
                ),
                "confirmed_at": None,
                "created_at": timestamp,
            },
        )
        result = _projection(row)
        self._record_command(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            command_type="draft",
            request_fingerprint=request_fingerprint,
            result=result,
            created_at=timestamp,
        )
        return result

    def confirm(
        self,
        session: Session,
        *,
        user_id: str,
        report_version_id: str,
        idempotency_key: str,
        command: ExternalMockReportConfirmCommand,
        now: datetime | None = None,
    ) -> ExternalMockReportProjection:
        timestamp = _as_utc(now or datetime.now(UTC))
        if str(command.expected_report_version_id) != report_version_id:
            raise ExternalMockReportError(
                "expectedReportVersionId must match the report being confirmed"
            )
        request_fingerprint = _fingerprint(
            {
                "commandType": "confirm",
                "userId": user_id,
                "reportVersionId": report_version_id,
            }
        )
        self._repository.lock_user(session, user_id=user_id)
        replay = self._repository.report_for_command(
            session, user_id=user_id, idempotency_key=idempotency_key
        )
        if replay is not None:
            stored_command, report = replay
            self._require_replay(
                stored_command,
                command_type="confirm",
                request_fingerprint=request_fingerprint,
            )
            return _projection(report)
        candidate = self._repository.report(
            session, user_id=user_id, report_version_id=report_version_id
        )
        if candidate is None:
            raise ExternalMockReportNotFound(report_version_id)
        if candidate["state"] != "candidate":
            raise ExternalMockReportError("Only candidate reports can be confirmed")
        current = self._repository.current_for_recognition(
            session,
            user_id=user_id,
            recognition_revision_id=str(candidate["recognition_revision_id"]),
        )
        if current is None or str(current["report_version_id"]) != report_version_id:
            raise ExternalMockReportError(
                "A newer field revision exists; confirm the current report version"
            )
        duplicate = self._repository.confirmed_for_artifact(
            session,
            user_id=user_id,
            artifact_version_id=str(candidate["artifact_version_id"]),
        )
        if duplicate is not None:
            result = _projection(duplicate, duplicate=True)
            self._record_command(
                session,
                user_id=user_id,
                idempotency_key=idempotency_key,
                command_type="confirm",
                request_fingerprint=request_fingerprint,
                result=result,
                created_at=timestamp,
            )
            return result
        grade = _evidence_grade(candidate, now=timestamp)
        confirmed_version_id = str(uuid4())
        evidence_id: str | None = None
        estimate_id: str | None = None
        if grade == "ability_eligible":
            ability_result = self._ability.record_external_mock_score(
                session,
                user_id=user_id,
                command_id=f"external-report-evidence:{confirmed_version_id}",
                source_report_version_id=confirmed_version_id,
                occurred_at=_as_utc(candidate["occurred_at"]),
                xingce_score=_optional_float(candidate["xingce_score"]),
                shenlun_score=_optional_float(candidate["shenlun_score"]),
                now=timestamp,
            )
            evidence_id = str(ability_result.evidence.evidence_id)
            estimate_id = str(ability_result.estimate.estimate_version_id)
        confirmed_fingerprint = _fingerprint(
            {
                "candidateFingerprint": candidate["input_fingerprint"],
                "confirmationCommand": request_fingerprint,
                "evidenceGrade": grade,
                "scoreEvidenceId": evidence_id,
                "estimateVersionId": estimate_id,
            }
        )
        row = self._repository.insert_report(
            session,
            values={
                "report_version_id": confirmed_version_id,
                "report_id": str(candidate["report_id"]),
                "command_id": f"external-report-confirm:{user_id}:{idempotency_key}",
                "user_id": user_id,
                "version_no": int(candidate["version_no"]) + 1,
                "state": "confirmed",
                "artifact_version_id": str(candidate["artifact_version_id"]),
                "recognition_revision_id": str(candidate["recognition_revision_id"]),
                "platform_name": candidate["platform_name"],
                "exam_name": candidate["exam_name"],
                "occurred_at": candidate["occurred_at"],
                "timed_status": candidate["timed_status"],
                "duration_minutes": candidate["duration_minutes"],
                "total_score": candidate["total_score"],
                "xingce_score": candidate["xingce_score"],
                "shenlun_score": candidate["shenlun_score"],
                "module_scores": dict(candidate["module_scores"]),
                "evidence_grade": grade,
                "score_evidence_id": evidence_id,
                "estimate_version_id": estimate_id,
                "input_fingerprint": confirmed_fingerprint,
                "supersedes_report_version_id": report_version_id,
                "confirmed_at": timestamp,
                "created_at": timestamp,
            },
        )
        result = _projection(row)
        self._record_command(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            command_type="confirm",
            request_fingerprint=request_fingerprint,
            result=result,
            created_at=timestamp,
        )
        return result

    def get(
        self,
        session: Session,
        *,
        user_id: str,
        report_version_id: str,
    ) -> ExternalMockReportProjection:
        row = self._repository.report(
            session, user_id=user_id, report_version_id=report_version_id
        )
        if row is None:
            raise ExternalMockReportNotFound(report_version_id)
        return _projection(row)

    @staticmethod
    def _require_replay(
        command: RowMapping, *, command_type: str, request_fingerprint: str
    ) -> None:
        if (
            command["command_type"] != command_type
            or command["request_fingerprint"] != request_fingerprint
        ):
            raise ExternalMockReportError("Idempotency key was reused")

    def _record_command(
        self,
        session: Session,
        *,
        user_id: str,
        idempotency_key: str,
        command_type: str,
        request_fingerprint: str,
        result: ExternalMockReportProjection,
        created_at: datetime,
    ) -> None:
        self._repository.insert_command(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            request_fingerprint=request_fingerprint,
            report_version_id=str(result.report_version_id),
            response_payload=result.model_dump(by_alias=True, mode="json"),
            created_at=created_at,
        )


def _evidence_grade(row: RowMapping, *, now: datetime) -> str:
    occurred_at = _as_utc(row["occurred_at"])
    if occurred_at + timedelta(days=MAX_EVIDENCE_AGE_DAYS) <= now:
        return "trend_only_stale"
    if row["timed_status"] != "timed" or row["duration_minutes"] is None:
        return "trend_only_untimed"
    if row["xingce_score"] is None and row["shenlun_score"] is None:
        return "trend_only_incomplete"
    return "ability_eligible"


def _projection(
    row: RowMapping, *, duplicate: bool = False
) -> ExternalMockReportProjection:
    state = str(row["state"])
    grade = str(row["evidence_grade"])
    if state == "candidate":
        impact = "blocked_until_confirmed"
        message = "OCR 和结构化字段仍是候选；确认前不进入能力估计。"
    elif grade == "ability_eligible":
        impact = "entered_estimate"
        message = "已以用户确认的计时模考证据进入新能力版本。"
    else:
        impact = "trend_only"
        message = "该记录只进入趋势报告，不影响能力估计。"
    if duplicate:
        message = "该截图已确认过，已返回原记录，未重复写入能力证据。"
    return ExternalMockReportProjection(
        reportVersionId=row["report_version_id"],
        reportId=row["report_id"],
        versionNo=int(row["version_no"]),
        state=state,
        artifactVersionId=row["artifact_version_id"],
        recognitionRevisionId=row["recognition_revision_id"],
        platformName=row["platform_name"],
        examName=row["exam_name"],
        occurredAt=row["occurred_at"],
        timedStatus=row["timed_status"],
        durationMinutes=row["duration_minutes"],
        totalScore=_optional_float(row["total_score"]),
        xingceScore=_optional_float(row["xingce_score"]),
        shenlunScore=_optional_float(row["shenlun_score"]),
        moduleScores={
            str(key): float(value) for key, value in dict(row["module_scores"]).items()
        },
        evidenceGrade=grade,
        scoreEvidenceId=row["score_evidence_id"],
        estimateVersionId=row["estimate_version_id"],
        abilityImpact=impact,
        confirmationMessage=message,
        confirmedAt=row["confirmed_at"],
        createdAt=row["created_at"],
    )


def _fingerprint(value: Any) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExternalMockReportError("External report timestamps must include timezone")
    return value.astimezone(UTC)


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
