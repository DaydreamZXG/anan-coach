from __future__ import annotations

from datetime import datetime

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.repository import PlanningContractError, PlanningRepository
from app.domains.planning.xingce_completion import (
    XingcePlanCompletion,
    complete_xingce_plan_task,
    current_xingce_plan_completion,
)
from app.domains.task_runtime.xingce_attempt.contracts import (
    is_current_xingce_session,
)
from app.domains.task_runtime.xingce_attempt.repository import (
    XingceTaskRuntimeRepository,
)
from app.domains.xingce_learning.real_attempt.evidence import (
    settle_xingce_evidence,
)
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.domains.xingce_learning.real_attempt.schemas import XingceCompletionReceipt


def completion_receipt(result: XingcePlanCompletion) -> XingceCompletionReceipt:
    return XingceCompletionReceipt(
        receiptId=result.receipt_id,
        settlementStatus=result.settlement_status,
        plan=result.plan,
    )


def settle_completion_after_cause(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    runtime_repository: XingceTaskRuntimeRepository,
    user_id: str,
    task_session: RowMapping,
    command_id: str,
    recorded_at: datetime,
) -> XingceCompletionReceipt:
    """Settle Xingce evidence without letting replay payloads classify sessions."""
    if not is_current_xingce_session(task_session):
        return _settle_historical_completion(
            session,
            repository=repository,
            runtime_repository=runtime_repository,
            user_id=user_id,
            task_session=task_session,
            command_id=command_id,
            recorded_at=recorded_at,
        )

    # A marked session can never enter the historical completion writer.  Its
    # submit transaction must already have produced the canonical terminal
    # planning fact (and late-evidence fact for an abandoned plan execution).
    plan_completion = current_xingce_plan_completion(
        session,
        user_id=user_id,
        task_session=task_session,
        observed_at=recorded_at,
    )
    _settle_required_pending_evidence(
        session,
        repository=repository,
        user_id=user_id,
        task_session=task_session,
        command_id=command_id,
        execution_version_id=plan_completion.execution_version_id,
        recorded_at=recorded_at,
    )
    return completion_receipt(plan_completion)


def _settle_historical_completion(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    runtime_repository: XingceTaskRuntimeRepository,
    user_id: str,
    task_session: RowMapping,
    command_id: str,
    recorded_at: datetime,
) -> XingceCompletionReceipt:
    """Compatibility adapter for sessions whose private bundle has no marker."""
    current = PlanningRepository().current_task_execution(
        session,
        user_id=user_id,
        work_item_key=str(task_session["work_item_key"]),
    )
    if current is None or current["status"] == "in_progress":
        plan_completion = complete_xingce_plan_task(
            session,
            user_id=user_id,
            task_session=task_session,
            command_id=command_id,
            completed_at=recorded_at,
        )
    elif current["status"] == "complete":
        plan_completion = current_xingce_plan_completion(
            session,
            user_id=user_id,
            task_session=task_session,
            observed_at=recorded_at,
        )
    elif current["status"] == "abandoned":
        try:
            plan_completion = current_xingce_plan_completion(
                session,
                user_id=user_id,
                task_session=task_session,
                observed_at=recorded_at,
            )
        except PlanningContractError:
            # Historical abandoned sessions may predate late-evidence linkage.
            plan_completion = complete_xingce_plan_task(
                session,
                user_id=user_id,
                task_session=task_session,
                command_id=command_id,
                completed_at=recorded_at,
            )
    else:
        raise PlanningContractError(
            "Historical Xingce session has an incompatible planning execution"
        )

    settled = _settle_required_pending_evidence(
        session,
        repository=repository,
        user_id=user_id,
        task_session=task_session,
        command_id=command_id,
        execution_version_id=plan_completion.execution_version_id,
        recorded_at=recorded_at,
    )

    # Historical response snapshots remain valid replay material, but the
    # canonical execution above—not snapshot presence—selected this path.
    replay_payload = runtime_repository.completion_payload(
        session,
        user_id=user_id,
        session_id=str(task_session["session_id"]),
    )
    if replay_payload is not None:
        replay = XingceCompletionReceipt.model_validate(replay_payload, strict=False)
        return replay.model_copy(update={"plan": plan_completion.plan})
    return XingceCompletionReceipt(
        receiptId=(
            plan_completion.receipt_id
            if plan_completion.settlement_status == "late_evidence_linked"
            else str(settled["evidence_version_id"])
        ),
        settlementStatus=plan_completion.settlement_status,
        plan=plan_completion.plan,
    )


def _settle_required_pending_evidence(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    user_id: str,
    task_session: RowMapping,
    command_id: str,
    execution_version_id: str,
    recorded_at: datetime,
) -> RowMapping:
    current = repository.current_plan_evidence(
        session,
        user_id=user_id,
        session_id=str(task_session["session_id"]),
    )
    if current is None or current["state"] != "pending_reflection":
        raise PlanningContractError(
            "Submitted Xingce session is missing its pending evidence fact"
        )
    return settle_xingce_evidence(
        session,
        repository=repository,
        command_id=command_id,
        user_id=user_id,
        task_session=task_session,
        execution_version_id=execution_version_id,
        created_at=recorded_at,
    )
