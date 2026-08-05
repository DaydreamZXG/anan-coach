from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.learning_repository import (
    EssayArtifactContractError,
    EssayArtifactNotFound,
    EssayLearningArtifactRepository,
)
from app.domains.essay_intelligence.learning_schemas import (
    EssayArtifactPlanTaskCompletionCommand,
    EssayArtifactPlanTaskCompletionResult,
    EssayLearningArtifactCommand,
    EssayLearningArtifactVersion,
    EssayTaskArtifactState,
    EssayTaskWorkspace,
    EssayWorkspaceMaterial,
    EssayWorkspaceMaterialScope,
    EssayWorkspaceMaterialSegment,
    EssayWorkspaceProgression,
    EssayWorkspaceRubric,
    EssayWorkspaceRubricAvailability,
    EssayWorkspaceReview,
    EssayWorkspaceTask,
    MaterialAnnotationPayload,
    OutlinePayload,
    PointExtractionPayload,
    ReflectionPayload,
    ReviewAcknowledgementPayload,
)
from app.domains.essay_intelligence.material_scope import resolve_material_scope
from app.domains.essay_intelligence.mastery.service import EssayMasteryService
from app.domains.essay_intelligence.review_contracts import _fingerprint
from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayFinding,
    EssayThinkingSnapshot,
)
from app.domains.essay_intelligence.workflow_schemas import (
    EssayReviewIntentView,
    EssayReviewQuota,
)
from app.domains.planning.schemas import PlanningEvidenceRef, TaskExecutionCommand
from app.domains.planning.service import PlanningService
from app.domains.learning_media.repository import LearningMediaRepository
from app.platform.outbox import OutboxRepository


class EssayLearningArtifactService:
    def __init__(
        self,
        *,
        repository: EssayLearningArtifactRepository | None = None,
        outbox: OutboxRepository | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository or EssayLearningArtifactRepository()
        self._outbox = outbox or OutboxRepository()
        self._now = now or (lambda: datetime.now(UTC))

    def record(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayLearningArtifactCommand,
        now: datetime | None = None,
    ) -> EssayLearningArtifactVersion:
        created_at = now or self._now()
        payload = command.model_dump(by_alias=True, mode="json")
        input_fingerprint = _fingerprint(
            {"userId": user_id, "command": payload}
        )
        replay = self._repository.artifact_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            if replay["input_fingerprint"] != input_fingerprint:
                raise EssayArtifactContractError(
                    "Idempotency key already belongs to a different artifact command"
                )
            return _artifact_from_row(replay)

        self._repository.lock_user(session, user_id=user_id)
        task_id = str(command.task_version_id)
        material_id = str(command.material_bundle_version_id)
        rubric_id = (
            str(command.rubric_version_id) if command.rubric_version_id else None
        )
        review_id = (
            str(command.review_version_id) if command.review_version_id else None
        )
        answer_id = (
            str(command.answer_version_id) if command.answer_version_id else None
        )
        content = self._repository.consumable_content(
            session,
            task_version_id=task_id,
            material_bundle_version_id=material_id,
            rubric_version_id=rubric_id,
        )
        if content is None:
            raise EssayArtifactContractError(
                "Artifact must bind an exact published task, complete material, "
                "and published rubric when required"
            )

        segment_rows = self._repository.workspace_segments(
            session,
            material_bundle_version_id=material_id,
        )
        segments = _scoped_material_segments(
            prompt=str(content["prompt"]),
            segment_rows=segment_rows,
        )
        self._validate_payload_dependencies(
            session,
            user_id=user_id,
            command=command,
            task_id=task_id,
            material_id=material_id,
            rubric_id=rubric_id,
            review_id=review_id,
            answer_id=answer_id,
            segments=segments,
            word_limit=int(content["word_limit"]),
        )

        scope_key = _fingerprint(
            {
                "userId": user_id,
                "artifactType": command.artifact_type,
                "taskVersionId": task_id,
                "materialBundleVersionId": material_id,
                "rubricVersionId": rubric_id,
                "reviewVersionId": review_id,
                "answerVersionId": answer_id,
            }
        )
        current = self._repository.current_in_scope(
            session,
            user_id=user_id,
            scope_key=scope_key,
            for_update=True,
        )
        based_on = command.based_on_artifact_version_id
        if current is None and based_on is not None:
            raise EssayArtifactContractError(
                "Artifact scope has no current version to supersede"
            )
        if current is not None and (
            based_on is None
            or str(based_on) != str(current["artifact_version_id"])
        ):
            raise EssayArtifactContractError(
                "Artifact changed; reload the current version before submitting"
            )

        artifact_payload = command.payload.model_dump(by_alias=True, mode="json")
        payload_sha256 = _fingerprint(artifact_payload)
        row = self._repository.insert_artifact(
            session,
            values={
                "artifact_version_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "artifact_type": command.artifact_type,
                "scope_key": scope_key,
                "task_version_id": task_id,
                "material_bundle_version_id": material_id,
                "rubric_version_id": rubric_id,
                "review_version_id": review_id,
                "answer_version_id": answer_id,
                "version_no": int(current["version_no"]) + 1 if current else 1,
                "payload": artifact_payload,
                "payload_sha256": payload_sha256,
                "input_fingerprint": input_fingerprint,
                "supersedes_artifact_version_id": (
                    str(current["artifact_version_id"]) if current else None
                ),
                "created_at": created_at,
            },
        )
        artifact = _artifact_from_row(row)
        if isinstance(command.payload, ReviewAcknowledgementPayload):
            assert review_id is not None and rubric_id is not None
            review = self._repository.completed_review(
                session,
                user_id=user_id,
                review_version_id=review_id,
                task_version_id=task_id,
                material_bundle_version_id=material_id,
                rubric_version_id=rubric_id,
            )
            if review is None:
                raise EssayArtifactContractError(
                    "Review decision lost its current grounded review dependency"
                )
            EssayMasteryService().reconcile_finding_decisions(
                session,
                user_id=user_id,
                command_id=f"{command_id}:mastery",
                artifact=artifact,
                review=review,
                payload=command.payload,
                recorded_at=created_at,
            )
        if isinstance(command.payload, ReflectionPayload) and (
            command.payload.voice_artifact_version_ids
        ):
            try:
                LearningMediaRepository().bind_available_artifacts(
                    session,
                    user_id=user_id,
                    artifact_version_ids=[
                        str(item)
                        for item in command.payload.voice_artifact_version_ids
                    ],
                    entity_type="essay_reflection",
                    entity_version_id=str(artifact.artifact_version_id),
                    created_at=created_at,
                )
            except (ValueError, IntegrityError) as exc:
                raise EssayArtifactContractError(
                    "Reflection voice artifact is unavailable, duplicated, or already bound"
                ) from exc
        self._outbox.append(
            session,
            deduplication_key=f"essay-learning-artifact:{artifact.artifact_version_id}",
            aggregate_type="essay_learning_artifact",
            aggregate_id=str(artifact.artifact_version_id),
            event_type="essay_learning_artifact.recorded",
            payload={
                "artifactVersionId": str(artifact.artifact_version_id),
                "artifactType": artifact.artifact_type,
                "userId": user_id,
                "taskVersionId": task_id,
                "materialBundleVersionId": material_id,
                "rubricVersionId": rubric_id,
                "reviewVersionId": review_id,
                "answerVersionId": answer_id,
                "versionNo": artifact.version_no,
                "inputFingerprint": input_fingerprint,
            },
        )
        return artifact

    def record_and_complete_plan_task(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayArtifactPlanTaskCompletionCommand,
        now: datetime | None = None,
    ) -> EssayArtifactPlanTaskCompletionResult:
        recorded_at = now or self._now()
        artifact = self.record(
            session,
            user_id=user_id,
            command_id=f"{command_id}:artifact",
            command=command.artifact,
            now=recorded_at,
        )
        evidence_type_by_artifact = {
            "material_annotation": "essay_material_annotation",
            "point_extraction": "essay_point_extraction",
            "outline": "essay_outline",
            "review_acknowledgement": "essay_review_acknowledgement",
            "reflection": "reflection_artifact",
        }
        artifact_id = str(artifact.artifact_version_id)
        completion = PlanningService().complete_task_from_owner(
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
                        entity_type=evidence_type_by_artifact[artifact.artifact_type],
                        entity_id=artifact_id,
                        version_id=artifact_id,
                    )
                ],
                based_on_execution_version_id=(
                    command.based_on_execution_version_id
                    if command.based_on_execution_version_id
                    else None
                ),
            ),
            owner="essay_intelligence",
            now=recorded_at,
        )
        return EssayArtifactPlanTaskCompletionResult(
            artifact=artifact,
            completion=completion,
        )

    def get_owned(
        self,
        session: Session,
        *,
        user_id: str,
        artifact_version_id: str,
    ) -> EssayLearningArtifactVersion:
        row = self._repository.artifact_by_id(
            session,
            user_id=user_id,
            artifact_version_id=artifact_version_id,
        )
        if row is None:
            raise EssayArtifactNotFound("Essay learning artifact does not exist")
        return _artifact_from_row(row)

    def current_for_task(
        self,
        session: Session,
        *,
        user_id: str,
        task_version_id: str,
    ) -> EssayTaskArtifactState:
        content = self._repository.workspace_content(
            session,
            task_version_id=task_version_id,
        )
        if content is None:
            raise EssayArtifactNotFound("Consumable essay task does not exist")
        progression = self._repository.workspace_progression(
            session,
            user_id=user_id,
            task_version_id=task_version_id,
            material_bundle_version_id=str(content["material_bundle_version_id"]),
            rubric_version_id=(
                str(content["rubric_version_id"])
                if content["rubric_version_id"] is not None
                else None
            ),
        )
        artifacts = {
            str(row["artifact_type"]): _artifact_from_row(row)
            for row in self._repository.current_for_task(
                session,
                user_id=user_id,
                task_version_id=task_version_id,
                material_bundle_version_id=str(
                    content["material_bundle_version_id"]
                ),
                rubric_version_id=(
                    str(content["rubric_version_id"])
                    if content["rubric_version_id"] is not None
                    else None
                ),
                review_version_id=(
                    str(progression["review_version_id"])
                    if progression is not None
                    and progression["review_version_id"] is not None
                    else None
                ),
                answer_version_id=(
                    str(progression["answer_version_id"])
                    if progression is not None
                    and progression["answer_version_id"] is not None
                    else None
                ),
            )
        }
        return EssayTaskArtifactState(
            task_version_id=UUID(task_version_id),
            current_by_type=artifacts,  # type: ignore[arg-type]
        )

    def workspace(
        self,
        session: Session,
        *,
        user_id: str,
        task_version_id: str,
    ) -> EssayTaskWorkspace:
        content = self._repository.workspace_content(
            session,
            task_version_id=task_version_id,
        )
        if content is None:
            raise EssayArtifactNotFound(
                "Latest consumable published essay task and complete material do not exist"
            )
        material_id = str(content["material_bundle_version_id"])
        segments = self._repository.workspace_segments(
            session,
            material_bundle_version_id=material_id,
        )
        material_scope = resolve_material_scope(
            str(content["prompt"]),
            [int(item["material_order"]) for item in segments],
        )
        selected_orders = set(material_scope.material_orders)
        scoped_segments = [
            item
            for item in segments
            if int(item["material_order"]) in selected_orders
        ]
        artifact_state = self.current_for_task(
            session,
            user_id=user_id,
            task_version_id=task_version_id,
        )
        progression = self._repository.workspace_progression(
            session,
            user_id=user_id,
            task_version_id=task_version_id,
            material_bundle_version_id=material_id,
            rubric_version_id=(
                str(content["rubric_version_id"])
                if content["rubric_version_id"] is not None
                else None
            ),
        )
        rubric = None
        if content["rubric_version_id"] is not None:
            rubric = EssayWorkspaceRubric.model_validate_json(
                json.dumps(
                    {
                        "rubricVersionId": str(content["rubric_version_id"]),
                        "sourceKind": content["rubric_source_kind"],
                        "verificationStatus": content[
                            "rubric_verification_status"
                        ],
                        "totalMaximum": float(content["total_maximum"]),
                        "criteria": content["criteria"],
                        "points": content["points"],
                    },
                    ensure_ascii=False,
                )
            )
        return EssayTaskWorkspace(
            task=EssayWorkspaceTask(
                task_version_id=content["task_version_id"],
                task_key=content["task_key"],
                prompt=content["prompt"],
                requirements=None,
                question_type=content["question_type"],
                word_limit=int(content["word_limit"]),
                maximum_score=float(content["maximum_score"]),
                material_scope=EssayWorkspaceMaterialScope(
                    mode=material_scope.mode,
                    material_orders=list(material_scope.material_orders),
                ),
            ),
            material=EssayWorkspaceMaterial(
                material_bundle_version_id=content["material_bundle_version_id"],
                source_kind=content["material_source_kind"],
                segments=[
                    EssayWorkspaceMaterialSegment(
                        material_id=item["material_id"],
                        segment_id=item["segment_id"],
                        material_order=int(item["material_order"]),
                        segment_order=int(item["segment_order"]),
                        text=item["text"],
                    )
                    for item in scoped_segments
                ],
            ),
            rubric_availability=EssayWorkspaceRubricAvailability(
                status="published" if rubric else "pending",
                rubric=rubric,
            ),
            current_artifacts=artifact_state.current_by_type,
            progression=_progression_from_row(progression),
        )

    def _validate_payload_dependencies(
        self,
        session: Session,
        *,
        user_id: str,
        command: EssayLearningArtifactCommand,
        task_id: str,
        material_id: str,
        rubric_id: str | None,
        review_id: str | None,
        answer_id: str | None,
        segments: dict[str, str],
        word_limit: int,
    ) -> None:
        payload = command.payload
        if isinstance(payload, MaterialAnnotationPayload):
            annotation_ids = {item.segment_id for item in payload.annotations}
            if annotation_ids != set(segments):
                raise EssayArtifactContractError(
                    "Material annotation must cover every segment in the pinned bundle"
                )
            for annotation in payload.annotations:
                if (
                    annotation.evidence_quote is not None
                    and annotation.evidence_quote not in segments[annotation.segment_id]
                ):
                    raise EssayArtifactContractError(
                        "Material annotation evidenceQuote must be exact source text"
                    )
            return
        if isinstance(payload, PointExtractionPayload):
            dependency = self._repository.current_dependency(
                session,
                user_id=user_id,
                artifact_version_id=str(payload.source_material_annotation_version_id),
                artifact_type="material_annotation",
                task_version_id=task_id,
                material_bundle_version_id=material_id,
                rubric_version_id=None,
            )
            if dependency is None:
                raise EssayArtifactContractError(
                    "Point extraction requires the current owned material annotation"
                )
            if any(
                not set(point.source_segment_ids).issubset(segments)
                for point in payload.points
            ):
                raise EssayArtifactContractError(
                    "Extracted points may cite only pinned material segments"
                )
            return
        if isinstance(payload, OutlinePayload):
            dependency = self._repository.current_dependency(
                session,
                user_id=user_id,
                artifact_version_id=str(payload.source_point_extraction_version_id),
                artifact_type="point_extraction",
                task_version_id=task_id,
                material_bundle_version_id=material_id,
                rubric_version_id=rubric_id,
            )
            if dependency is None:
                raise EssayArtifactContractError(
                    "Outline requires the current owned point extraction"
                )
            source_payload = PointExtractionPayload.model_validate(dependency["payload"])
            source_point_ids = {item.point_id for item in source_payload.points}
            if any(
                not set(section.source_point_ids).issubset(source_point_ids)
                for section in payload.sections
            ):
                raise EssayArtifactContractError(
                    "Outline may cite only points in the pinned extraction"
                )
            if sum(section.planned_words for section in payload.sections) > word_limit:
                raise EssayArtifactContractError(
                    "Outline planned words cannot exceed the task word limit"
                )
            return
        if isinstance(payload, ReviewAcknowledgementPayload):
            assert review_id is not None and rubric_id is not None
            review = self._repository.completed_review(
                session,
                user_id=user_id,
                review_version_id=review_id,
                task_version_id=task_id,
                material_bundle_version_id=material_id,
                rubric_version_id=rubric_id,
            )
            if review is None:
                raise EssayArtifactContractError(
                    "Review acknowledgement requires the current completed owned review"
                )
            finding_ids = {
                str(item["findingId"])
                for item in review["findings"]
                if isinstance(item, dict) and item.get("findingId")
            }
            if not finding_ids or {item.finding_id for item in payload.decisions} != finding_ids:
                raise EssayArtifactContractError(
                    "Review acknowledgement must cover every finding in the review"
                )
            return
        assert answer_id is not None and rubric_id is not None
        if self._repository.owned_answer(
            session,
            user_id=user_id,
            answer_version_id=answer_id,
            task_version_id=task_id,
            material_bundle_version_id=material_id,
            rubric_version_id=rubric_id,
        ) is None:
            raise EssayArtifactContractError(
                "Reflection requires the current owned answer version"
            )
        if review_id is not None:
            assert rubric_id is not None
            if self._repository.completed_review(
                session,
                user_id=user_id,
                review_version_id=review_id,
                task_version_id=task_id,
                material_bundle_version_id=material_id,
                rubric_version_id=rubric_id,
                answer_version_id=answer_id,
            ) is None:
                raise EssayArtifactContractError(
                    "Reflection reviewVersionId must be the current completed owned review"
                )


def _artifact_from_row(row: RowMapping) -> EssayLearningArtifactVersion:
    return EssayLearningArtifactVersion(
        artifact_version_id=row["artifact_version_id"],
        artifact_type=row["artifact_type"],
        scope_key=row["scope_key"],
        version_no=int(row["version_no"]),
        task_version_id=row["task_version_id"],
        material_bundle_version_id=row["material_bundle_version_id"],
        rubric_version_id=row["rubric_version_id"],
        review_version_id=row["review_version_id"],
        answer_version_id=row["answer_version_id"],
        payload=row["payload"],
        input_fingerprint=row["input_fingerprint"],
        supersedes_artifact_version_id=row["supersedes_artifact_version_id"],
        created_at=row["created_at"],
    )


def _progression_from_row(row: RowMapping | None) -> EssayWorkspaceProgression:
    if row is None:
        return EssayWorkspaceProgression()
    return EssayWorkspaceProgression(
        attempt_id=row["attempt_id"],
        answer_version_id=row["answer_version_id"],
        review_version_id=row["review_version_id"],
        review_job_id=row["review_job_id"],
        review_intent=_review_intent_from_progression(row),
        review_status=row["review_status"],
        rewrite_version_id=row["rewrite_version_id"],
        transfer_verification_version_id=row["verification_version_id"],
        transfer_status=row["transfer_status"],
        current_review=(
            EssayWorkspaceReview(
                review_version_id=row["review_version_id"],
                status=row["review_status"],
                findings=[
                    EssayFinding.model_validate_json(
                        json.dumps(item, ensure_ascii=False)
                    )
                    for item in row["review_findings"]
                ],
            )
            if row["review_version_id"] is not None
            else None
        ),
        current_answer=(
            EssayAnswerVersion(
                answer_version_id=row["answer_version_id"],
                attempt_id=row["attempt_id"],
                user_id=row["answer_user_id"],
                task_version_id=row["answer_task_version_id"],
                version_no=row["answer_version_no"],
                source_type=row["answer_source_type"],
                source_artifact_version_id=row["answer_source_artifact_version_id"],
                recognition_revision_id=row["answer_recognition_revision_id"],
                answer_text=row["answer_text"],
                answer_sha256=row["answer_sha256"],
                thinking=(
                    EssayThinkingSnapshot.model_validate(row["thinking_payload"])
                    if row["thinking_payload"] is not None
                    else None
                ),
                thinking_sha256=row["thinking_sha256"],
                input_fingerprint=row["answer_input_fingerprint"],
                supersedes_answer_version_id=row["supersedes_answer_version_id"],
                created_at=row["answer_created_at"],
            )
            if row["answer_version_id"] is not None
            else None
        ),
    )


def _review_intent_from_progression(
    row: RowMapping,
) -> EssayReviewIntentView | None:
    if row["review_intent_id"] is None:
        return None
    quota = None
    if row["review_intent_quota_feature"] is not None:
        quota = EssayReviewQuota(
            feature=row["review_intent_quota_feature"],
            used=int(row["review_intent_quota_used"]),
            access_mode=row["review_intent_quota_access_mode"],
            limit=(
                int(row["review_intent_quota_limit"])
                if row["review_intent_quota_limit"] is not None
                else None
            ),
            usage_date=row["review_intent_quota_usage_date"],
        )
    return EssayReviewIntentView(
        intent_id=row["review_intent_id"],
        intent_version_id=row["review_intent_version_id"],
        version_no=int(row["review_intent_version_no"]),
        status=row["review_intent_status"],
        attempt_id=row["attempt_id"],
        answer_version_id=row["answer_version_id"],
        task_version_id=row["review_intent_task_version_id"],
        material_bundle_version_id=row["review_intent_material_version_id"],
        rubric_version_id=row["review_intent_rubric_version_id"],
        review_scope=row["review_intent_scope"],
        input_fingerprint=row["review_intent_input_fingerprint"],
        intent_fingerprint=row["review_intent_fingerprint"],
        state_fingerprint=row["review_intent_state_fingerprint"],
        job_id=row["review_intent_job_id"],
        job_status=row["review_intent_job_status"],
        quota=quota,
        retry_after=row["review_intent_retry_after"],
        supersedes_intent_version_id=row["review_intent_supersedes_id"],
        created_at=row["review_intent_created_at"],
    )


def _scoped_material_segments(
    *,
    prompt: str,
    segment_rows: list[RowMapping],
) -> dict[str, str]:
    """Return the exact material slice exposed by the task workspace."""
    material_scope = resolve_material_scope(
        prompt,
        [int(item["material_order"]) for item in segment_rows],
    )
    selected_orders = set(material_scope.material_orders)
    return {
        str(item["segment_id"]): str(item["text"])
        for item in segment_rows
        if int(item["material_order"]) in selected_orders
    }



