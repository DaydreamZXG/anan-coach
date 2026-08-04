from __future__ import annotations

from difflib import SequenceMatcher
from collections.abc import Callable
from datetime import UTC, datetime
import json
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.closure.repository import EssayClosureRepository
from app.domains.essay_intelligence.closure.schemas import (
    EssayRewriteCommand,
    EssayAnswerDiffSegment,
    EssayRewriteComparison,
    EssayRewritePlanTaskCompletionCommand,
    EssayRewritePlanTaskCompletionResult,
    EssayRewriteRecord,
    EssayRewriteResult,
    EssayTransferCommand,
    EssayTransferContentGap,
    EssayTransferPlanTaskCompletionCommand,
    EssayTransferPlanTaskCompletionResult,
    EssayTransferRecord,
    EssayTransferResult,
)
from app.domains.essay_intelligence.learning_schemas import (
    ReviewAcknowledgementPayload,
)
from app.domains.essay_intelligence.mastery.service import (
    EssayMasteryContractError,
    EssayMasteryService,
)
from app.domains.essay_intelligence.review_contracts import _fingerprint
from app.domains.essay_intelligence.workflow_repository import answer_from_row
from app.domains.essay_intelligence.workflow_schemas import EssayAnswerCreateCommand
from app.domains.essay_intelligence.workflow_service import EssayReviewWorkflowService
from app.domains.planning.schemas import (
    PlanningEvidenceRef,
    TaskExecutionCommand,
)
from app.domains.planning.service import PlanningService
from app.platform.outbox import OutboxRepository


REWRITE_CONTRACT_VERSION = "essay-rewrite-writer@v2"
TRANSFER_CONTRACT_VERSION = "essay-transfer-writer@v2"
MASTERY_POLICY_VERSION = "essay-mastery@v1"


class EssayClosureContractError(RuntimeError):
    pass


class EssayClosureNotFound(RuntimeError):
    pass


class EssayTransferContentGapError(EssayClosureContractError):
    def __init__(self, detail: EssayTransferContentGap) -> None:
        super().__init__(detail.message)
        self.detail = detail


class EssayClosureService:
    def __init__(
        self,
        *,
        repository: EssayClosureRepository | None = None,
        mastery: EssayMasteryService | None = None,
        workflow: EssayReviewWorkflowService | None = None,
        planning: PlanningService | None = None,
        outbox: OutboxRepository | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._repository = repository or EssayClosureRepository()
        self._mastery = mastery or EssayMasteryService()
        self._workflow = workflow or EssayReviewWorkflowService()
        self._planning = planning or PlanningService(now=self._now)
        self._outbox = outbox or OutboxRepository()

    def record_rewrite(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayRewriteCommand,
        now: datetime | None = None,
    ) -> EssayRewriteResult:
        recorded_at = now or self._now()
        fingerprint = _fingerprint(
            {
                "contractVersion": REWRITE_CONTRACT_VERSION,
                "userId": user_id,
                "command": command.model_dump(by_alias=True, mode="json"),
            }
        )
        replay = self._repository.rewrite_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            return self._rewrite_replay(
                session,
                user_id=user_id,
                command_id=command_id,
                row=replay,
                expected_fingerprint=fingerprint,
            )

        self._repository.lock_user(session, user_id=user_id)
        replay = self._repository.rewrite_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            return self._rewrite_replay(
                session,
                user_id=user_id,
                command_id=command_id,
                row=replay,
                expected_fingerprint=fingerprint,
            )

        source = self._repository.rewrite_source(
            session,
            user_id=user_id,
            attempt_id=str(command.attempt_id),
            review_version_id=str(command.base_review_version_id),
            answer_version_id=str(command.base_answer_version_id),
            decision_artifact_version_id=str(
                command.finding_decision_artifact_version_id
            ),
        )
        if source is None:
            raise EssayClosureNotFound(
                "Rewrite source must be the current owned grounded review and finding decision"
            )
        decisions = ReviewAcknowledgementPayload.model_validate(
            source["decision_payload"]
        )
        accepted_ids = set(decisions.accepted_finding_ids)
        resolution_by_finding = {
            item.finding_id: item.status for item in command.resolutions
        }
        if not accepted_ids or set(resolution_by_finding) != accepted_ids:
            raise EssayClosureContractError(
                "Rewrite resolutions must exactly cover accepted learner findings"
            )

        current = self._repository.current_rewrite(
            session,
            user_id=user_id,
            review_version_id=str(command.base_review_version_id),
            for_update=True,
        )
        based_on_rewrite = command.based_on_rewrite_version_id
        if current is None and based_on_rewrite is not None:
            raise EssayClosureContractError("Rewrite scope has no version to supersede")
        if current is not None and (
            based_on_rewrite is None
            or str(based_on_rewrite) != str(current["rewrite_version_id"])
        ):
            raise EssayClosureContractError(
                "Rewrite changed; reload the current version before submitting"
            )

        answer = self._workflow.create_answer(
            session,
            user_id=user_id,
            command_id=f"{command_id}:answer",
            command=EssayAnswerCreateCommand(
                attempt_id=command.attempt_id,
                source_type=command.source_type,
                source_artifact_version_id=command.source_artifact_version_id,
                recognition_revision_id=command.recognition_revision_id,
                answer_text=command.answer_text,
                thinking=command.thinking,
                based_on_answer_version_id=command.based_on_answer_version_id,
            ),
            now=recorded_at,
        ).answer
        addressed = sorted(
            finding_id
            for finding_id, state in resolution_by_finding.items()
            if state == "resolved"
        )
        unresolved = sorted(accepted_ids - set(addressed))
        status: Literal["passed", "needs_revision"] = (
            "needs_revision" if unresolved else "passed"
        )
        analysis_payload: dict[str, Any] = {
            "contractVersion": REWRITE_CONTRACT_VERSION,
            "policyVersion": MASTERY_POLICY_VERSION,
            "authority": "learner_confirmed_finding_resolution",
            "status": status,
            "addressedFindingIds": addressed,
            "unresolvedFindingIds": unresolved,
            "resolutionEvidence": {
                item.finding_id: item.learner_evidence for item in command.resolutions
            },
            "scoreIsolation": {
                "writesScoreEvidence": False,
                "writesFormalAbility": False,
            },
        }
        output_fingerprint = _fingerprint(
            {
                "rewrite": analysis_payload,
                "originalAnswerVersionId": str(command.base_answer_version_id),
                "rewrittenAnswerVersionId": str(answer.answer_version_id),
                "findingDecisionArtifactVersionId": str(
                    command.finding_decision_artifact_version_id
                ),
            }
        )
        rewrite_id = str(uuid4())
        row = self._repository.insert_rewrite(
            session,
            values={
                "rewrite_version_id": rewrite_id,
                "command_id": command_id,
                "attempt_id": str(command.attempt_id),
                "user_id": user_id,
                "task_version_id": str(source["task_version_id"]),
                "base_review_version_id": str(command.base_review_version_id),
                "base_answer_version_id": str(command.base_answer_version_id),
                "rewritten_answer_version_id": str(answer.answer_version_id),
                "analysis_payload": json.dumps(
                    analysis_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "input_fingerprint": fingerprint,
                "output_fingerprint": output_fingerprint,
                "supersedes_rewrite_version_id": (
                    str(current["rewrite_version_id"]) if current else None
                ),
                "finding_decision_artifact_version_id": str(
                    command.finding_decision_artifact_version_id
                ),
                "writer_contract_version": REWRITE_CONTRACT_VERSION,
                "writer_policy_version": MASTERY_POLICY_VERSION,
                "created_at": recorded_at,
            },
        )
        try:
            mastery = self._mastery.advance_rewrite(
                session,
                user_id=user_id,
                command_id=f"{command_id}:mastery",
                decision_artifact_version_id=str(
                    command.finding_decision_artifact_version_id
                ),
                resolution_by_finding=resolution_by_finding,
                rewrite_version_id=rewrite_id,
                recorded_at=recorded_at,
            )
        except EssayMasteryContractError as exc:
            raise EssayClosureContractError(str(exc)) from exc
        self._append_rewrite_event(
            session,
            user_id=user_id,
            rewrite_version_id=rewrite_id,
            source_review_version_id=str(command.base_review_version_id),
            status=status,
            input_fingerprint=fingerprint,
            output_fingerprint=output_fingerprint,
        )
        return EssayRewriteResult(
            answer=answer,
            rewrite=_rewrite_from_row(row),
            mastery_versions=mastery,
        )

    def rewrite_comparison(
        self,
        session: Session,
        *,
        user_id: str,
        rewrite_version_id: str,
    ) -> EssayRewriteComparison:
        rewrite = self._repository.rewrite_by_id(
            session,
            user_id=user_id,
            rewrite_version_id=rewrite_version_id,
        )
        if rewrite is None:
            raise EssayClosureNotFound("Owned essay rewrite version does not exist")
        original = self._repository.answer_by_id(
            session,
            user_id=user_id,
            answer_version_id=str(rewrite["base_answer_version_id"]),
        )
        rewritten = self._repository.answer_by_id(
            session,
            user_id=user_id,
            answer_version_id=str(rewrite["rewritten_answer_version_id"]),
        )
        if original is None or rewritten is None:
            raise EssayClosureContractError(
                "Rewrite comparison lost an immutable answer version"
            )
        original_answer = answer_from_row(original)
        rewritten_answer = answer_from_row(rewritten)
        matcher = SequenceMatcher(
            None,
            original_answer.answer_text,
            rewritten_answer.answer_text,
            autojunk=False,
        )
        segments: list[EssayAnswerDiffSegment] = []
        for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
            if operation in {"equal", "delete", "replace"} and left_end > left_start:
                segments.append(
                    EssayAnswerDiffSegment(
                        operation="equal" if operation == "equal" else "delete",
                        text=original_answer.answer_text[left_start:left_end],
                    )
                )
            if operation in {"insert", "replace"} and right_end > right_start:
                segments.append(
                    EssayAnswerDiffSegment(
                        operation="insert",
                        text=rewritten_answer.answer_text[right_start:right_end],
                    )
                )
        return EssayRewriteComparison(
            rewrite=_rewrite_from_row(rewrite),
            original_answer=original_answer,
            rewritten_answer=rewritten_answer,
            diff_segments=segments,
        )

    def record_rewrite_and_complete_plan_task(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayRewritePlanTaskCompletionCommand,
        now: datetime | None = None,
    ) -> EssayRewritePlanTaskCompletionResult:
        recorded_at = now or self._now()
        writer = self.record_rewrite(
            session,
            user_id=user_id,
            command_id=f"{command_id}:writer",
            command=command.rewrite,
            now=recorded_at,
        )
        rewrite_version_id = str(writer.rewrite.rewrite_version_id)
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
                        entity_type="essay_rewrite_version",
                        entity_id=rewrite_version_id,
                        version_id=rewrite_version_id,
                    )
                ],
                based_on_execution_version_id=(
                    str(command.based_on_execution_version_id)
                    if command.based_on_execution_version_id is not None
                    else None
                ),
            ),
            owner="essay_intelligence",
            now=recorded_at,
        )
        return EssayRewritePlanTaskCompletionResult(
            answer=writer.answer,
            rewrite=writer.rewrite,
            mastery_versions=writer.mastery_versions,
            completion=completion,
        )

    def record_transfer(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayTransferCommand,
        now: datetime | None = None,
    ) -> EssayTransferResult:
        recorded_at = now or self._now()
        fingerprint = _fingerprint(
            {
                "contractVersion": TRANSFER_CONTRACT_VERSION,
                "userId": user_id,
                "command": command.model_dump(by_alias=True, mode="json"),
            }
        )
        replay = self._repository.transfer_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            return self._transfer_replay(
                session,
                user_id=user_id,
                command_id=command_id,
                row=replay,
                expected_fingerprint=fingerprint,
            )

        self._repository.lock_user(session, user_id=user_id)
        replay = self._repository.transfer_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            return self._transfer_replay(
                session,
                user_id=user_id,
                command_id=command_id,
                row=replay,
                expected_fingerprint=fingerprint,
            )
        source = self._repository.rewrite_for_transfer(
            session,
            user_id=user_id,
            rewrite_version_id=str(command.source_rewrite_version_id),
            decision_artifact_version_id=str(
                command.source_finding_decision_artifact_version_id
            ),
        )
        if source is None or source["analysis_payload"].get("status") != "passed":
            raise EssayClosureNotFound(
                "Transfer requires the current owned passed rewrite and source decision"
            )
        transfer = self._repository.transfer_context(
            session,
            user_id=user_id,
            attempt_id=str(command.transfer_attempt_id),
            answer_version_id=str(command.transfer_answer_version_id),
            review_version_id=str(command.transfer_review_version_id),
            decision_artifact_version_id=str(
                command.transfer_finding_decision_artifact_version_id
            ),
        )
        if transfer is None:
            raise EssayClosureNotFound(
                "Transfer evidence must be the current owned grounded review and finding decision"
            )
        if transfer["valid_until"] <= recorded_at:
            raise EssayClosureContractError(
                "Transfer grounded review expired; generate a current review before verifying"
            )
        if transfer["transfer_answer_created_at"] < source["created_at"]:
            raise EssayClosureContractError(
                "Transfer answer must be created after the source rewrite"
            )
        if (
            str(source["attempt_id"]) == str(transfer["attempt_id"])
            or str(source["task_version_id"]) == str(transfer["task_version_id"])
            or str(source["material_bundle_version_id"])
            == str(transfer["material_bundle_version_id"])
        ):
            raise EssayTransferContentGapError(
                EssayTransferContentGap(
                    code="essay_transfer_content_gap",
                    message=(
                        "Stable mastery requires a different published task, material, "
                        "and attempt"
                    ),
                    source_task_version_id=source["task_version_id"],
                    requires="different_published_task_and_material",
                )
            )

        source_dimensions = _dimensions_by_code(source["source_dimensions"])
        transfer_dimensions = _dimensions_by_code(transfer["dimensions"])
        tested = set(command.tested_criterion_codes)
        if not tested.issubset(source_dimensions) or not tested.issubset(
            transfer_dimensions
        ):
            raise EssayClosureContractError(
                "Tested criteria must exist in both grounded review versions"
            )
        transfer_decisions = ReviewAcknowledgementPayload.model_validate(
            transfer["decision_payload"]
        )
        status = _transfer_status(transfer_decisions)
        evidence_payload: dict[str, Any] = {
            "contractVersion": TRANSFER_CONTRACT_VERSION,
            "policyVersion": MASTERY_POLICY_VERSION,
            "authority": "learner_confirmed_transfer_findings",
            "status": status,
            "sourceFindingDecisionArtifactVersionId": str(
                command.source_finding_decision_artifact_version_id
            ),
            "transferFindingDecisionArtifactVersionId": str(
                command.transfer_finding_decision_artifact_version_id
            ),
            "testedCriteria": [
                {
                    "criterionCode": code,
                    "sourceEvidence": _dimension_evidence(source_dimensions[code]),
                    "transferEvidence": _dimension_evidence(
                        transfer_dimensions[code]
                    ),
                }
                for code in command.tested_criterion_codes
            ],
            "transferFindingDecisions": [
                item.model_dump(by_alias=True, mode="json")
                for item in transfer_decisions.decisions
            ],
            "scoreIsolation": {
                "writesScoreEvidence": False,
                "writesFormalAbility": False,
            },
        }
        output_fingerprint = _fingerprint(
            {
                "sourceRewriteVersionId": str(command.source_rewrite_version_id),
                "transferReviewVersionId": str(command.transfer_review_version_id),
                "evidence": evidence_payload,
            }
        )
        current = self._repository.current_transfer(
            session,
            user_id=user_id,
            source_review_version_id=str(source["base_review_version_id"]),
            transfer_attempt_id=str(command.transfer_attempt_id),
            for_update=True,
        )
        based_on = command.based_on_transfer_verification_version_id
        if current is None and based_on is not None:
            raise EssayClosureContractError("Transfer scope has no version to supersede")
        if current is not None and (
            based_on is None
            or str(based_on) != str(current["verification_version_id"])
        ):
            raise EssayClosureContractError(
                "Transfer verification changed; reload before submitting"
            )
        verification_id = str(uuid4())
        row = self._repository.insert_transfer(
            session,
            values={
                "verification_version_id": verification_id,
                "command_id": command_id,
                "user_id": user_id,
                "source_review_version_id": str(source["base_review_version_id"]),
                "source_attempt_id": str(source["attempt_id"]),
                "source_task_version_id": str(source["task_version_id"]),
                "transfer_attempt_id": str(command.transfer_attempt_id),
                "transfer_task_version_id": str(transfer["task_version_id"]),
                "transfer_answer_version_id": str(
                    command.transfer_answer_version_id
                ),
                "fresh_material_bundle_version_id": str(
                    transfer["material_bundle_version_id"]
                ),
                "status": status,
                "tested_criterion_codes": json.dumps(
                    command.tested_criterion_codes,
                    ensure_ascii=False,
                ),
                "evidence_payload": json.dumps(
                    evidence_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "input_fingerprint": fingerprint,
                "output_fingerprint": output_fingerprint,
                "supersedes_verification_version_id": (
                    str(current["verification_version_id"]) if current else None
                ),
                "generated_at": recorded_at,
                "valid_until": transfer["valid_until"],
                "source_rewrite_version_id": str(command.source_rewrite_version_id),
                "transfer_review_version_id": str(command.transfer_review_version_id),
                "finding_decision_artifact_version_id": str(
                    command.source_finding_decision_artifact_version_id
                ),
                "transfer_finding_decision_artifact_version_id": str(
                    command.transfer_finding_decision_artifact_version_id
                ),
                "writer_contract_version": TRANSFER_CONTRACT_VERSION,
                "writer_policy_version": MASTERY_POLICY_VERSION,
            },
        )
        try:
            mastery = self._mastery.advance_transfer(
                session,
                user_id=user_id,
                command_id=f"{command_id}:mastery",
                decision_artifact_version_id=str(
                    command.source_finding_decision_artifact_version_id
                ),
                status=status,
                transfer_verification_version_id=verification_id,
                recorded_at=recorded_at,
            )
        except EssayMasteryContractError as exc:
            raise EssayClosureContractError(str(exc)) from exc
        self._append_transfer_event(
            session,
            user_id=user_id,
            verification_version_id=verification_id,
            source_rewrite_version_id=str(command.source_rewrite_version_id),
            status=status,
            input_fingerprint=fingerprint,
            output_fingerprint=output_fingerprint,
        )
        return EssayTransferResult(
            transfer=_transfer_from_row(row),
            mastery_versions=mastery,
        )

    def record_transfer_and_complete_plan_task(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayTransferPlanTaskCompletionCommand,
        now: datetime | None = None,
    ) -> EssayTransferPlanTaskCompletionResult:
        recorded_at = now or self._now()
        writer = self.record_transfer(
            session,
            user_id=user_id,
            command_id=f"{command_id}:writer",
            command=command.transfer,
            now=recorded_at,
        )
        verification_version_id = str(
            writer.transfer.verification_version_id
        )
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
                        entity_type="essay_transfer_verification",
                        entity_id=verification_version_id,
                        version_id=verification_version_id,
                    )
                ],
                based_on_execution_version_id=(
                    str(command.based_on_execution_version_id)
                    if command.based_on_execution_version_id is not None
                    else None
                ),
            ),
            owner="essay_intelligence",
            now=recorded_at,
        )
        return EssayTransferPlanTaskCompletionResult(
            transfer=writer.transfer,
            mastery_versions=writer.mastery_versions,
            completion=completion,
        )

    def _rewrite_replay(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        row: RowMapping,
        expected_fingerprint: str,
    ) -> EssayRewriteResult:
        _require_fingerprint(row["input_fingerprint"], expected_fingerprint)
        answer = self._repository.answer_by_id(
            session,
            user_id=user_id,
            answer_version_id=str(row["rewritten_answer_version_id"]),
        )
        if answer is None:
            raise EssayClosureContractError("Rewrite replay lost its answer version")
        return EssayRewriteResult(
            answer=answer_from_row(answer),
            rewrite=_rewrite_from_row(row),
            mastery_versions=self._mastery.versions_for_command(
                session,
                user_id=user_id,
                command_prefix=f"{command_id}:mastery",
            ),
        )

    def _transfer_replay(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        row: RowMapping,
        expected_fingerprint: str,
    ) -> EssayTransferResult:
        _require_fingerprint(row["input_fingerprint"], expected_fingerprint)
        return EssayTransferResult(
            transfer=_transfer_from_row(row),
            mastery_versions=self._mastery.versions_for_command(
                session,
                user_id=user_id,
                command_prefix=f"{command_id}:mastery",
            ),
        )

    def _append_rewrite_event(
        self,
        session: Session,
        *,
        user_id: str,
        rewrite_version_id: str,
        source_review_version_id: str,
        status: str,
        input_fingerprint: str,
        output_fingerprint: str,
    ) -> None:
        self._outbox.append(
            session,
            deduplication_key=f"essay-rewrite:{rewrite_version_id}",
            aggregate_type="essay_rewrite",
            aggregate_id=rewrite_version_id,
            event_type="essay_rewrite.recorded",
            payload={
                "contractVersion": REWRITE_CONTRACT_VERSION,
                "rewriteVersionId": rewrite_version_id,
                "sourceReviewVersionId": source_review_version_id,
                "userId": user_id,
                "status": status,
                "inputFingerprint": input_fingerprint,
                "outputFingerprint": output_fingerprint,
            },
        )

    def _append_transfer_event(
        self,
        session: Session,
        *,
        user_id: str,
        verification_version_id: str,
        source_rewrite_version_id: str,
        status: str,
        input_fingerprint: str,
        output_fingerprint: str,
    ) -> None:
        self._outbox.append(
            session,
            deduplication_key=f"essay-transfer:{verification_version_id}",
            aggregate_type="essay_transfer_verification",
            aggregate_id=verification_version_id,
            event_type="essay_transfer.verified",
            payload={
                "contractVersion": TRANSFER_CONTRACT_VERSION,
                "verificationVersionId": verification_version_id,
                "sourceRewriteVersionId": source_rewrite_version_id,
                "userId": user_id,
                "status": status,
                "inputFingerprint": input_fingerprint,
                "outputFingerprint": output_fingerprint,
            },
        )


def _transfer_status(
    payload: ReviewAcknowledgementPayload,
) -> Literal["verified", "not_verified", "inconclusive"]:
    decisions = {item.decision for item in payload.decisions}
    if "accepted" in decisions:
        return "not_verified"
    if "uncertain" in decisions:
        return "inconclusive"
    return "verified"


def _dimensions_by_code(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise EssayClosureContractError("Grounded review dimensions are invalid")
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(
            item.get("criterionCode"), str
        ):
            raise EssayClosureContractError("Grounded review dimension is invalid")
        code = item["criterionCode"]
        if code in result:
            raise EssayClosureContractError("Grounded review criteria are duplicated")
        result[code] = item
    return result


def _dimension_evidence(dimension: dict[str, Any]) -> dict[str, Any]:
    return {
        "criterionCode": dimension["criterionCode"],
        "findingIds": dimension.get("findingIds", []),
        "answerEvidenceRefs": dimension.get("answerEvidenceRefs", []),
        "materialEvidenceRefs": dimension.get("materialEvidenceRefs", []),
    }


def _rewrite_from_row(row: RowMapping) -> EssayRewriteRecord:
    payload = row["analysis_payload"]
    return EssayRewriteRecord(
        contract_version=row["writer_contract_version"],
        policy_version=row["writer_policy_version"],
        rewrite_version_id=row["rewrite_version_id"],
        attempt_id=row["attempt_id"],
        task_version_id=row["task_version_id"],
        base_review_version_id=row["base_review_version_id"],
        base_answer_version_id=row["base_answer_version_id"],
        rewritten_answer_version_id=row["rewritten_answer_version_id"],
        finding_decision_artifact_version_id=row[
            "finding_decision_artifact_version_id"
        ],
        status=payload["status"],
        addressed_finding_ids=payload["addressedFindingIds"],
        unresolved_finding_ids=payload["unresolvedFindingIds"],
        resolution_evidence=payload["resolutionEvidence"],
        input_fingerprint=row["input_fingerprint"],
        output_fingerprint=row["output_fingerprint"],
        supersedes_rewrite_version_id=row["supersedes_rewrite_version_id"],
        created_at=row["created_at"],
    )


def _transfer_from_row(row: RowMapping) -> EssayTransferRecord:
    return EssayTransferRecord(
        contract_version=row["writer_contract_version"],
        policy_version=row["writer_policy_version"],
        verification_version_id=row["verification_version_id"],
        source_rewrite_version_id=row["source_rewrite_version_id"],
        source_review_version_id=row["source_review_version_id"],
        source_attempt_id=row["source_attempt_id"],
        source_task_version_id=row["source_task_version_id"],
        transfer_attempt_id=row["transfer_attempt_id"],
        transfer_task_version_id=row["transfer_task_version_id"],
        transfer_answer_version_id=row["transfer_answer_version_id"],
        transfer_review_version_id=row["transfer_review_version_id"],
        fresh_material_bundle_version_id=row["fresh_material_bundle_version_id"],
        finding_decision_artifact_version_id=row[
            "finding_decision_artifact_version_id"
        ],
        transfer_finding_decision_artifact_version_id=row[
            "transfer_finding_decision_artifact_version_id"
        ],
        status=row["status"],
        tested_criterion_codes=row["tested_criterion_codes"],
        evidence_payload=row["evidence_payload"],
        input_fingerprint=row["input_fingerprint"],
        output_fingerprint=row["output_fingerprint"],
        supersedes_verification_version_id=row[
            "supersedes_verification_version_id"
        ],
        generated_at=row["generated_at"],
    )


def _require_fingerprint(actual: str, expected: str) -> None:
    if actual != expected:
        raise EssayClosureContractError(
            "Idempotency key already belongs to a different closure command"
        )
