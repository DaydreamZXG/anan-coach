from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.xingce_learning.briefing.repository import XingceBriefingRepository
from app.domains.xingce_learning.briefing.schemas import (
    XingceBriefingAbandonment,
    XingceBriefingCompletion,
    XingceBriefingTiming,
    XingceTaskBriefing,
)
from app.domains.xingce_learning.contracts import xingce_question_count


class XingceBriefingError(RuntimeError):
    pass


class XingceBriefingService:
    def __init__(self, repository: XingceBriefingRepository | None = None) -> None:
        self._repository = repository or XingceBriefingRepository()

    def get(
        self,
        session: Session,
        *,
        user_id: str,
        plan_task_id: str,
        now: datetime | None = None,
    ) -> XingceTaskBriefing:
        current = now or datetime.now(UTC)
        task = self._repository.current_task(
            session,
            user_id=user_id,
            plan_task_id=plan_task_id,
            now=current,
        )
        if task is None:
            raise XingceBriefingError("task unavailable")
        mode, module_name = _scope(task)
        source_mastery_version_id = None
        verification_stage = None
        if mode == "verification":
            source_mastery_version_id = _mastery_version(task)
            if source_mastery_version_id is None:
                raise XingceBriefingError("verification source missing")
            mastery = self._repository.mastery(
                session,
                user_id=user_id,
                mastery_version_id=source_mastery_version_id,
            )
            if mastery is None:
                raise XingceBriefingError("verification source unavailable")
            module_name = str(mastery["module_name"]) if mastery["module_name"] else None
            verification_stage = str(mastery["verification_stage"])
        target_seconds = int(task["estimated_minutes"]) * 60
        question_count = xingce_question_count(
            mode=mode,
            estimated_minutes=int(task["estimated_minutes"]),
            module_name=module_name,
        )
        evidence_types = [
            str(value)
            for value in task["completion_contract"]["completionEvidenceTypes"]
            if value != "task_execution"
        ]
        if len(evidence_types) != 1:
            raise XingceBriefingError("completion evidence contract is ambiguous")
        return XingceTaskBriefing(
            planTaskId=str(task["task_id"]),
            planVersionId=str(task["plan_version_id"]),
            taskDefinitionVersion=str(task["input_fingerprint"]),
            mode=mode,
            title=str(task["title"]),
            reason=str(task["reason"]),
            moduleName=module_name,
            questionCount=question_count,
            timing=XingceBriefingTiming(
                targetSeconds=target_seconds,
                perQuestionTargetSeconds=max(1, target_seconds // question_count),
            ),
            completion=XingceBriefingCompletion(
                evidenceType=evidence_types[0],
                requiredMetrics=[
                    "answer_verdict",
                    "confidence_evidence",
                    "timing_evidence",
                    "reflection",
                    "cause_decision",
                ],
            ),
            abandonment=XingceBriefingAbandonment(),
            verificationStage=verification_stage,
            sourceMasteryVersionId=source_mastery_version_id,
            validUntil=task["valid_until"],
        )


def _scope(task: RowMapping) -> tuple[str, str | None]:
    if task["stage"] == "objective_baseline":
        return "baseline", None
    if task["stage"] == "objective_verification":
        return "verification", None
    candidate = str(task["candidate_key"])
    module_name = candidate.split(":", 1)[1].strip() if ":" in candidate else None
    return "targeted", None if module_name == "国考高频模块" else module_name


def _mastery_version(task: RowMapping) -> str | None:
    matches: list[dict[str, Any]] = [
        item
        for item in task["evidence_refs"]
        if item.get("entityType") == "xingce_mastery_state_version"
        and item.get("role") == "due_verification"
    ]
    if len(matches) != 1:
        return None
    value = str(matches[0].get("versionId") or "")
    return value or None
