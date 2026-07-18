from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from app.domains.onboarding.progress_repository import OnboardingProgressRepository
from app.domains.positions.target_scores import (
    PositionTargetScoreContractError,
    PositionTargetScoreOwner,
)
from app.domains.retake.repository import RetakeProgramRecord, RetakeRepository
from app.domains.retake.schemas import (
    RetakeEnrollmentResult,
    RetakeLossStructure,
    RetakeProgramView,
    RetakeScoreEvidenceOption,
    RetakeWorkspace,
)
from app.platform.outbox import OutboxRepository


class RetakeContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RetakeService:
    def __init__(self, *, outbox: OutboxRepository | None = None) -> None:
        self._outbox = outbox or OutboxRepository()

    def workspace(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> RetakeWorkspace:
        target_exam_year = self._target_exam_year(session, now=now)
        self._require_retake_experience(session, user_id=user_id)
        repository = RetakeRepository(session)
        return RetakeWorkspace(
            target_exam_year=target_exam_year,
            eligible_score_evidence=[
                _score_option(row)
                for row in repository.eligible_score_evidence(
                    user_id=user_id,
                    target_exam_year=target_exam_year,
                )
            ],
            program=(
                _program_view(program)
                if (program := repository.current_program(user_id=user_id)) is not None
                else None
            ),
            score_use_boundary=(
                "往年成绩只用于失分结构和训练优先级；不直接计算跨年提分，"
                "不参与退款、现金奖励或上岸承诺。"
            ),
        )

    def enroll(
        self,
        session: Session,
        *,
        user_id: str,
        command_key: str,
        source_score_evidence_id: UUID,
        now: datetime | None = None,
    ) -> RetakeEnrollmentResult:
        created_at = now or datetime.now(UTC)
        target_exam_year = self._target_exam_year(session, now=created_at)
        self._require_retake_experience(session, user_id=user_id)
        repository = RetakeRepository(session)
        repository.lock_owner(user_id=user_id)
        existing = repository.program_by_command(
            user_id=user_id,
            command_key=command_key,
        )
        if existing is not None:
            if (
                UUID(str(existing["source_score_evidence_id"]))
                != source_score_evidence_id
                or int(existing["target_exam_year"]) != target_exam_year
            ):
                raise RetakeContractError(
                    "idempotency_key_reused",
                    "同一幂等键不能用于不同的二战计划。",
                )
            return RetakeEnrollmentResult(program=_program_view(_record(existing)))
        if repository.current_program(user_id=user_id) is not None:
            raise RetakeContractError(
                "retake_program_already_active",
                "当前已有生效的失分回收计划。",
            )
        evidence = repository.eligible_score_evidence_by_id(
            user_id=user_id,
            evidence_id=source_score_evidence_id,
            target_exam_year=target_exam_year,
        )
        if evidence is None:
            raise RetakeContractError(
                "retake_score_evidence_ineligible",
                "请先导入并确认往年国考实考成绩。",
            )
        loss_structure = _loss_structure(evidence)
        record = repository.insert_program(
            user_id=user_id,
            command_key=command_key,
            evidence=evidence,
            target_exam_year=target_exam_year,
            loss_structure=loss_structure,
            now=created_at,
        )
        self._outbox.append(
            session,
            deduplication_key=f"retake-program:{command_key}",
            aggregate_type="retake_program",
            aggregate_id=user_id,
            event_type="retake.program_started",
            payload={
                "userId": user_id,
                "programVersionId": str(record.program_version_id),
                "sourceScoreEvidenceId": str(source_score_evidence_id),
                "sourceExamYear": record.source_exam_year,
                "targetExamYear": record.target_exam_year,
            },
        )
        return RetakeEnrollmentResult(program=_program_view(record))

    @staticmethod
    def _require_retake_experience(session: Session, *, user_id: str) -> None:
        experience = OnboardingProgressRepository().current_experience(
            session,
            user_id=user_id,
        )
        if experience is None or experience.declaration_status != "declared" or not experience.retake:
            raise RetakeContractError(
                "retake_experience_required",
                "当前备考经历未确认为二战或多次备考。",
            )

    @staticmethod
    def _target_exam_year(session: Session, *, now: datetime | None) -> int:
        try:
            context = PositionTargetScoreOwner().active_context(
                session,
                exam_year=None,
                now=now or datetime.now(UTC),
            )
        except PositionTargetScoreContractError as exc:
            raise RetakeContractError(
                "retake_target_exam_unavailable",
                "当前没有可用的国考目标年度发布。",
            ) from exc
        return context.release.exam_year


def _score_option(row: Mapping[str, Any]) -> RetakeScoreEvidenceOption:
    return RetakeScoreEvidenceOption(
        evidence_id=UUID(str(row["evidence_id"])),
        exam_year=int(row["exam_year"]),
        source_type=str(row["source_type"]),
        xingce_score=_optional_float(row["xingce_score"]),
        essay_score=_optional_float(row["shenlun_score"]),
        total_score=_optional_float(row["total_score"]),
        occurred_at=row["occurred_at"],
    )


def _loss_structure(evidence: Mapping[str, Any]) -> dict[str, object]:
    xingce = _optional_float(evidence["xingce_score"])
    essay = _optional_float(evidence["shenlun_score"])
    ranked = sorted(
        (
            (track, score)
            for track, score in (("xingce", xingce), ("essay", essay))
            if score is not None
        ),
        key=lambda item: item[1],
    )
    return {
        "xingceScore": xingce,
        "essayScore": essay,
        "availableTracks": [track for track, _score in ranked],
        "trainingFocus": [
            (
                "先用行测六模块证据细分速度、正确率与错因"
                if track == "xingce"
                else "先用申论新材料复原要点、结构与表达失分"
            )
            for track, _score in ranked
        ],
        "limitation": (
            "上年度成绩只建立失分结构；跨年度难度不同，"
            "不直接计算提分，也不参与退款或现金奖励。"
        ),
    }


def _program_view(record: RetakeProgramRecord) -> RetakeProgramView:
    return RetakeProgramView(
        program_version_id=record.program_version_id,
        source_score_evidence_id=record.source_score_evidence_id,
        source_exam_year=record.source_exam_year,
        target_exam_year=record.target_exam_year,
        state=record.state,
        loss_structure=RetakeLossStructure.model_validate(record.loss_structure),
        refund_uses_score=False,
        reward_uses_score=False,
        training_uses_score=True,
        created_at=record.created_at,
    )


def _record(row: Mapping[str, Any]) -> RetakeProgramRecord:
    return RetakeProgramRecord(
        program_version_id=UUID(str(row["program_version_id"])),
        source_score_evidence_id=UUID(str(row["source_score_evidence_id"])),
        source_exam_year=int(row["source_exam_year"]),
        target_exam_year=int(row["target_exam_year"]),
        state=str(row["state"]),
        loss_structure=dict(row["loss_structure"]),
        created_at=row["created_at"],
    )


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
