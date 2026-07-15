from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.drafting_schemas import (
    EssayDraftingAnswerCreate,
    EssayDraftingCompletionCommand,
    EssayDraftingCompletionResult,
)
from app.domains.essay_intelligence.learning_schemas import (
    EssayLearningArtifactCommand,
)
from app.domains.essay_intelligence.learning_service import EssayLearningArtifactService
from app.domains.essay_intelligence.schemas import EssayAnswerVersion, EssayAttempt
from app.domains.essay_intelligence.workflow_repository import (
    EssayReviewWorkflowRepository,
    EssayWorkflowContractError,
    EssayWorkflowNotFound,
    answer_from_row,
    attempt_from_row,
)
from app.domains.essay_intelligence.workflow_schemas import (
    EssayAnswerCreateCommand,
    EssayAttemptCreateCommand,
)
from app.domains.essay_intelligence.workflow_service import EssayReviewWorkflowService
from app.domains.planning.lifecycle import require_executable_plan_task
from app.domains.planning.repository import PlanningContractError, PlanningRepository
from app.domains.planning.schemas import (
    CompletionContract,
    PlanningEvidenceRef,
    TaskExecutionCommand,
)
from app.domains.planning.service import PlanningService


class EssayDraftingCompletionService:
    """Atomically persist drafting evidence and complete its planning work item."""

    def __init__(
        self,
        *,
        workflow: EssayReviewWorkflowService | None = None,
        workflow_repository: EssayReviewWorkflowRepository | None = None,
        artifacts: EssayLearningArtifactService | None = None,
        planning: PlanningService | None = None,
        planning_repository: PlanningRepository | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._workflow_repository = (
            workflow_repository or EssayReviewWorkflowRepository()
        )
        self._workflow = workflow or EssayReviewWorkflowService(
            repository=self._workflow_repository
        )
        self._artifacts = artifacts or EssayLearningArtifactService()
        self._planning_repository = planning_repository or PlanningRepository()
        self._planning = planning or PlanningService(
            repository=self._planning_repository,
            now=self._now,
        )

    def complete(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayDraftingCompletionCommand,
    ) -> EssayDraftingCompletionResult:
        # All participating services share this transaction. The route is the sole
        # commit boundary; any validation error below rolls every write back.
        recorded_at = _as_utc(self._now())
        self._planning_repository.lock_user(session, user_id=user_id)
        self._load_and_validate_plan_task(
            session,
            user_id=user_id,
            command_id=command_id,
            command=command,
            recorded_at=recorded_at,
        )
        command_fingerprint = _fingerprint(
            {
                "contractVersion": "essay-drafting-completion-v1",
                "userId": user_id,
                "command": command.model_dump(by_alias=True, mode="json"),
            }
        )

        attempt = self._resolve_attempt(
            session,
            user_id=user_id,
            command_id=command_id,
            command=command,
        )
        answer = self._resolve_answer(
            session,
            user_id=user_id,
            command_id=command_id,
            command=command,
            attempt_id=attempt.attempt_id,
        )
        self._require_exact_bindings(
            command=command,
            attempt_task_id=attempt.task_version_id,
            attempt_material_id=attempt.material_bundle_version_id,
            attempt_rubric_id=attempt.rubric_version_id,
            answer_task_id=answer.task_version_id,
            answer_attempt_id=answer.attempt_id,
        )

        reflection = self._artifacts.record(
            session,
            user_id=user_id,
            command_id=f"{command_id}:reflection",
            command=EssayLearningArtifactCommand(
                artifact_type="reflection",
                task_version_id=str(command.task_version_id),
                material_bundle_version_id=str(command.material_bundle_version_id),
                rubric_version_id=str(command.rubric_version_id),
                review_version_id=None,
                answer_version_id=str(answer.answer_version_id),
                based_on_artifact_version_id=(
                    str(command.based_on_reflection_artifact_version_id)
                    if command.based_on_reflection_artifact_version_id
                    else None
                ),
                payload=command.reflection,
            ),
        )
        if (
            reflection.artifact_type != "reflection"
            or str(reflection.task_version_id) != command.task_version_id
            or str(reflection.material_bundle_version_id)
            != command.material_bundle_version_id
            or str(reflection.rubric_version_id) != command.rubric_version_id
            or reflection.answer_version_id != answer.answer_version_id
        ):
            raise EssayWorkflowContractError(
                "Structured reflection does not match the submitted drafting context"
            )

        answer_id = str(answer.answer_version_id)
        reflection_id = str(reflection.artifact_version_id)
        completion = self._planning.complete_task_from_owner(
            session,
            user_id=user_id,
            task_id=str(command.plan_task_id),
            command_id=f"{command_id}:execution",
            command=TaskExecutionCommand(
                status="complete",
                elapsed_minutes=command.elapsed_minutes,
                evidence_refs=[
                    PlanningEvidenceRef(
                        owner="essay_intelligence",
                        entity_type="essay_answer_version",
                        entity_id=answer_id,
                        version_id=answer_id,
                        # Persist the complete command fingerprint inside the
                        # execution input so a reused root key with any changed
                        # field is rejected even when it references the same rows.
                        role=f"drafting_command:{command_fingerprint}",
                    ),
                    PlanningEvidenceRef(
                        owner="essay_intelligence",
                        entity_type="reflection_artifact",
                        entity_id=reflection_id,
                        version_id=reflection_id,
                        role="structured_reflection",
                    ),
                ],
                based_on_execution_version_id=(
                    str(command.based_on_execution_version_id)
                    if command.based_on_execution_version_id
                    else None
                ),
            ),
            owner="essay_intelligence",
            now=recorded_at,
        )
        review_intent = self._workflow.ensure_review_intent(
            session,
            user_id=user_id,
            attempt_id=attempt.attempt_id,
            answer_version_id=answer.answer_version_id,
            review_scope="grounded_full",
        )
        return EssayDraftingCompletionResult(
            attempt=attempt,
            answer=answer,
            reflection=reflection,
            completion=completion,
            review_intent=review_intent,
        )

    def _load_and_validate_plan_task(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayDraftingCompletionCommand,
        recorded_at: datetime,
    ) -> RowMapping:
        execution = self._planning_repository.task_execution_by_command(
            session,
            user_id=user_id,
            command_id=f"{command_id}:execution",
        )
        if execution is not None:
            if str(execution["task_id"]) != str(command.plan_task_id):
                raise PlanningContractError(
                    "Idempotency key already belongs to a different drafting task"
                )
            task = self._planning_repository.owned_task_for_execution(
                session,
                user_id=user_id,
                task_id=str(command.plan_task_id),
            )
        else:
            task = self._planning_repository.current_task_for_execution(
                session,
                user_id=user_id,
                task_id=str(command.plan_task_id),
            )
        if task is None:
            raise PlanningContractError("Owned essay drafting plan task does not exist")
        if execution is None:
            require_executable_plan_task(task, at=recorded_at)
        if (
            task["track"] != "essay"
            or task["stage"] != "essay_drafting"
            or task["kind"] != "essay"
        ):
            raise PlanningContractError(
                "Drafting completion accepts only an essay_drafting plan task"
            )
        completion = CompletionContract.model_validate(task["completion_contract"])
        if (
            not completion.requires_reflection
            or set(completion.completion_evidence_types)
            != {"essay_answer_version", "task_execution"}
        ):
            raise PlanningContractError(
                "Essay drafting task has an incompatible completion contract"
            )
        expected = {
            "essay_task_version": str(command.task_version_id),
            "essay_material_bundle_version": str(command.material_bundle_version_id),
            "essay_rubric_version": str(command.rubric_version_id),
        }
        for entity_type, entity_id in expected.items():
            values = {
                str(item.get("entityId"))
                for item in task["evidence_refs"]
                if item.get("entityType") == entity_type
            }
            if values != {entity_id}:
                raise PlanningContractError(
                    f"Drafting task {entity_type} does not match the command"
                )
        return task

    def _resolve_attempt(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayDraftingCompletionCommand,
    ) -> EssayAttempt:
        if command.attempt.mode == "create":
            return self._workflow.create_attempt(
                session,
                user_id=user_id,
                command_id=f"{command_id}:attempt",
                command=EssayAttemptCreateCommand(
                    task_version_id=UUID(command.task_version_id),
                    material_bundle_version_id=UUID(command.material_bundle_version_id),
                    rubric_version_id=UUID(command.rubric_version_id),
                    source_context=command.attempt.source_context,
                    timed_status=command.attempt.timed_status,
                    elapsed_seconds=command.attempt.elapsed_seconds,
                ),
            ).attempt
        row = self._workflow_repository.owned_attempt(
            session,
            user_id=user_id,
            attempt_id=UUID(command.attempt.attempt_id),
        )
        if row is None:
            raise EssayWorkflowNotFound("Owned essay attempt does not exist")
        return attempt_from_row(row)

    def _resolve_answer(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayDraftingCompletionCommand,
        attempt_id: UUID,
    ) -> EssayAnswerVersion:
        answer_input = command.answer
        if isinstance(answer_input, EssayDraftingAnswerCreate):
            return self._workflow.create_answer(
                session,
                user_id=user_id,
                command_id=f"{command_id}:answer",
                command=EssayAnswerCreateCommand(
                    attempt_id=attempt_id,
                    source_type=answer_input.source_type,
                    answer_text=answer_input.answer_text,
                    thinking=answer_input.thinking,
                    based_on_answer_version_id=(
                        UUID(answer_input.based_on_answer_version_id)
                        if answer_input.based_on_answer_version_id
                        else None
                    ),
                ),
            ).answer
        row = self._workflow_repository.current_answer(
            session,
            user_id=user_id,
            attempt_id=attempt_id,
            for_update=True,
        )
        if row is None or str(row["answer_version_id"]) != str(
            answer_input.answer_version_id
        ):
            raise EssayWorkflowNotFound(
                "Owned current essay answer version does not exist for this attempt"
            )
        return answer_from_row(row)

    @staticmethod
    def _require_exact_bindings(
        *,
        command: EssayDraftingCompletionCommand,
        attempt_task_id: UUID,
        attempt_material_id: UUID,
        attempt_rubric_id: UUID,
        answer_task_id: UUID,
        answer_attempt_id: UUID,
    ) -> None:
        if (
            str(attempt_task_id) != command.task_version_id
            or str(attempt_material_id) != command.material_bundle_version_id
            or str(attempt_rubric_id) != command.rubric_version_id
            or str(answer_task_id) != command.task_version_id
            or (
                command.attempt.mode == "existing"
                and str(answer_attempt_id) != command.attempt.attempt_id
            )
        ):
            raise EssayWorkflowContractError(
                "Attempt, answer, task, material, and rubric versions must match exactly"
            )


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
