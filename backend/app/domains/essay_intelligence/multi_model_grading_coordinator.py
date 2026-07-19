from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domains.essay_intelligence.grounding import GroundedReviewContext
from app.domains.essay_intelligence.multi_model_grading import (
    GRADING_PLAN_VERSION,
    EssayCandidateComparison,
    EssayBlindAdjudicationRequest,
    EssayGradingCandidate,
    ResolvedEssayGrading,
    build_blind_adjudication_request,
    compare_essay_grading_candidates,
    resolve_essay_grading,
)
from app.domains.essay_intelligence.multi_model_grading_repository import (
    EssayAdjudicationAudit,
)
from app.domains.essay_intelligence.schemas import EssayReviewRequest


class AuditedEssayCandidateRunner(Protocol):
    def run_candidate(
        self,
        *,
        grading_run_id: UUID,
        slot_id: str,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        input_fingerprint: str,
    ) -> EssayGradingCandidate: ...


class AuditedEssayAdjudicator(Protocol):
    def adjudicate(
        self,
        *,
        grading_run_id: UUID,
        request: EssayBlindAdjudicationRequest,
    ) -> EssayAdjudicationAudit: ...


@dataclass(frozen=True)
class EssayMultiModelGradingExecution:
    grading_run_id: UUID
    candidates: list[EssayGradingCandidate]
    resolved: ResolvedEssayGrading
    adjudication_audit: EssayAdjudicationAudit | None


class EssayMultiModelGradingExecutionError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        retryable: bool,
        grading_run_id: UUID,
        candidates: list[EssayGradingCandidate],
        comparison: EssayCandidateComparison,
        adjudication_audit: EssayAdjudicationAudit | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.grading_run_id = grading_run_id
        self.candidates = candidates
        self.comparison = comparison
        self.adjudication_audit = adjudication_audit


class EssayMultiModelGradingCoordinator:
    """Run every configured candidate independently, then compare and adjudicate."""

    def __init__(
        self,
        *,
        slot_ids: tuple[str, ...],
        candidate_runner: AuditedEssayCandidateRunner,
        adjudicator: AuditedEssayAdjudicator,
    ) -> None:
        if len(slot_ids) not in {2, 3} or len(set(slot_ids)) != len(slot_ids):
            raise ValueError("formal grading plan requires two or three unique slots")
        self._slot_ids = slot_ids
        self._candidate_runner = candidate_runner
        self._adjudicator = adjudicator

    def execute(
        self,
        *,
        intended_review_version_id: UUID,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        input_fingerprint: str,
    ) -> EssayMultiModelGradingExecution:
        grading_run_id = uuid5(
            NAMESPACE_URL,
            f"{GRADING_PLAN_VERSION}:{intended_review_version_id}:{input_fingerprint}",
        )
        candidates = [
            self._candidate_runner.run_candidate(
                grading_run_id=grading_run_id,
                slot_id=slot_id,
                context=context,
                request=request,
                input_fingerprint=input_fingerprint,
            )
            for slot_id in self._slot_ids
        ]
        comparison = compare_essay_grading_candidates(candidates)
        if comparison.state in {"consistent", "unable_to_score"}:
            resolved = resolve_essay_grading(
                candidates=candidates,
                comparison=comparison,
            )
            return EssayMultiModelGradingExecution(
                grading_run_id=grading_run_id,
                candidates=candidates,
                resolved=resolved,
                adjudication_audit=None,
            )

        blind_request, mapping = build_blind_adjudication_request(
            candidates=candidates,
            comparison=comparison,
        )
        try:
            adjudication_audit = self._adjudicator.adjudicate(
                grading_run_id=grading_run_id,
                request=blind_request,
            )
        except Exception as exc:
            raise EssayMultiModelGradingExecutionError(
                code=str(getattr(exc, "code", "adjudication_failed")),
                retryable=bool(getattr(exc, "retryable", False)),
                grading_run_id=grading_run_id,
                candidates=candidates,
                comparison=comparison,
            ) from exc
        selected_id = mapping.get(
            adjudication_audit.decision.selected_candidate_ref
        )
        if selected_id is None:
            raise EssayMultiModelGradingExecutionError(
                code="adjudication_decision_invalid",
                retryable=False,
                grading_run_id=grading_run_id,
                candidates=candidates,
                comparison=comparison,
                adjudication_audit=adjudication_audit,
            )
        try:
            resolved = resolve_essay_grading(
                candidates=candidates,
                comparison=comparison,
                adjudication=adjudication_audit.decision,
            )
        except ValueError as exc:
            raise EssayMultiModelGradingExecutionError(
                code="adjudication_decision_invalid",
                retryable=False,
                grading_run_id=grading_run_id,
                candidates=candidates,
                comparison=comparison,
                adjudication_audit=adjudication_audit,
            ) from exc
        return EssayMultiModelGradingExecution(
            grading_run_id=grading_run_id,
            candidates=candidates,
            resolved=resolved,
            adjudication_audit=adjudication_audit,
        )
