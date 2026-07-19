from __future__ import annotations

from dataclasses import dataclass
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.multi_model_grading import (
    ADJUDICATION_CONTRACT_VERSION,
    COMPARISON_POLICY_VERSION,
    GRADING_PLAN_VERSION,
    EssayBlindAdjudicationDecision,
    EssayBlindAdjudicationRequest,
    EssayCandidateComparison,
    EssayGradingCandidate,
    ResolvedEssayGrading,
)


@dataclass(frozen=True)
class EssayAdjudicationAudit:
    adjudication_id: UUID
    provider_release_id: str
    model: str
    prompt_version: str
    schema_version: str
    model_call_id: UUID
    blind_request: EssayBlindAdjudicationRequest
    decision: EssayBlindAdjudicationDecision
    output_fingerprint: str


class EssayMultiModelGradingRepository:
    def insert_bundle(
        self,
        session: Session,
        *,
        grading_run_id: UUID,
        intended_review_version_id: UUID,
        job_id: UUID,
        user_id: str,
        input_fingerprint: str,
        candidates: list[EssayGradingCandidate],
        resolved: ResolvedEssayGrading,
        adjudication_audit: EssayAdjudicationAudit | None,
    ) -> None:
        comparison = resolved.comparison
        if adjudication_audit is None and resolved.adjudication is not None:
            raise ValueError("adjudication decision requires complete model-call audit")
        if adjudication_audit is not None:
            if resolved.adjudication is None:
                raise ValueError("adjudication audit is forbidden without a decision")
            if adjudication_audit.decision != resolved.adjudication:
                raise ValueError("adjudication audit decision does not match resolution")
            if (
                adjudication_audit.blind_request.comparison_fingerprint
                != comparison.comparison_fingerprint
            ):
                raise ValueError("adjudication request belongs to another comparison")

        final = resolved.payload.score if resolved.payload is not None else None
        self._insert_run_and_candidates(
            session,
            grading_run_id=grading_run_id,
            intended_review_version_id=intended_review_version_id,
            job_id=job_id,
            user_id=user_id,
            input_fingerprint=input_fingerprint,
            candidates=candidates,
            comparison=comparison,
            selected_candidate_id=resolved.selected_candidate_id,
            final=final,
            failure_code=None,
            failure_payload=None,
        )
        if adjudication_audit is not None:
            selected = resolved.selected_candidate_id
            if selected is None:
                raise ValueError("adjudication must select a candidate")
            session.execute(
                text(
                    """
                    insert into ai_audit.essay_grading_adjudications (
                      adjudication_id, grading_run_id, contract_version,
                      provider_release_id, model, prompt_version, schema_version,
                      model_call_id, blind_request, decision,
                      selected_candidate_id, output_fingerprint
                    ) values (
                      cast(:adjudication_id as uuid), cast(:grading_run_id as uuid),
                      :contract_version, :provider_release_id, :model,
                      :prompt_version, :schema_version, cast(:model_call_id as uuid),
                      cast(:blind_request as jsonb), cast(:decision as jsonb),
                      cast(:selected_candidate_id as uuid), :output_fingerprint
                    )
                    """
                ),
                {
                    "adjudication_id": str(adjudication_audit.adjudication_id),
                    "grading_run_id": str(grading_run_id),
                    "contract_version": ADJUDICATION_CONTRACT_VERSION,
                    "provider_release_id": adjudication_audit.provider_release_id,
                    "model": adjudication_audit.model,
                    "prompt_version": adjudication_audit.prompt_version,
                    "schema_version": adjudication_audit.schema_version,
                    "model_call_id": str(adjudication_audit.model_call_id),
                    "blind_request": _json(adjudication_audit.blind_request),
                    "decision": _json(adjudication_audit.decision),
                    "selected_candidate_id": str(selected),
                    "output_fingerprint": adjudication_audit.output_fingerprint,
                },
            )

    def insert_failure_bundle(
        self,
        session: Session,
        *,
        grading_run_id: UUID,
        intended_review_version_id: UUID,
        job_id: UUID,
        user_id: str,
        input_fingerprint: str,
        candidates: list[EssayGradingCandidate],
        comparison: EssayCandidateComparison,
        failure_code: str,
        adjudication_audit: EssayAdjudicationAudit | None,
    ) -> None:
        if comparison.state not in {"minor_disagreement", "major_disagreement"}:
            raise ValueError("Only a disputed comparison can fail adjudication")
        failure_payload: dict[str, object] = {
            "errorCode": failure_code,
            "comparisonFingerprint": comparison.comparison_fingerprint,
        }
        if adjudication_audit is not None:
            if (
                adjudication_audit.blind_request.comparison_fingerprint
                != comparison.comparison_fingerprint
            ):
                raise ValueError("Failed adjudication belongs to another comparison")
            failure_payload["adjudicationAudit"] = {
                "adjudicationId": str(adjudication_audit.adjudication_id),
                "providerReleaseId": adjudication_audit.provider_release_id,
                "model": adjudication_audit.model,
                "promptVersion": adjudication_audit.prompt_version,
                "schemaVersion": adjudication_audit.schema_version,
                "modelCallId": str(adjudication_audit.model_call_id),
                "blindRequest": adjudication_audit.blind_request.model_dump(
                    by_alias=True,
                    mode="json",
                ),
                "decision": adjudication_audit.decision.model_dump(
                    by_alias=True,
                    mode="json",
                ),
                "outputFingerprint": adjudication_audit.output_fingerprint,
            }
        self._insert_run_and_candidates(
            session,
            grading_run_id=grading_run_id,
            intended_review_version_id=intended_review_version_id,
            job_id=job_id,
            user_id=user_id,
            input_fingerprint=input_fingerprint,
            candidates=candidates,
            comparison=comparison,
            selected_candidate_id=None,
            final=None,
            failure_code=failure_code,
            failure_payload=failure_payload,
        )

    def _insert_run_and_candidates(
        self,
        session: Session,
        *,
        grading_run_id: UUID,
        intended_review_version_id: UUID,
        job_id: UUID,
        user_id: str,
        input_fingerprint: str,
        candidates: list[EssayGradingCandidate],
        comparison: EssayCandidateComparison,
        selected_candidate_id: UUID | None,
        final,
        failure_code: str | None,
        failure_payload: dict[str, object] | None,
    ) -> None:
        session.execute(
            text(
                """
                insert into ai_audit.essay_grading_runs (
                  grading_run_id, intended_review_version_id, job_id, user_id,
                  input_fingerprint, plan_version, comparison_policy_version,
                  state, candidate_count, valid_candidate_count,
                  comparison_fingerprint, comparison_payload,
                  selected_candidate_id, final_score_lower,
                  final_score_recommended, final_score_upper, final_score_maximum,
                  failure_code, failure_payload
                ) values (
                  cast(:grading_run_id as uuid), cast(:review_version_id as uuid),
                  cast(:job_id as uuid), cast(:user_id as uuid), :input_fingerprint,
                  :plan_version, :comparison_policy_version, :state,
                  :candidate_count, :valid_candidate_count,
                  :comparison_fingerprint, cast(:comparison_payload as jsonb),
                  cast(:selected_candidate_id as uuid), :score_lower,
                  :score_recommended, :score_upper, :score_maximum,
                  :failure_code, cast(:failure_payload as jsonb)
                )
                """
            ),
            {
                "grading_run_id": str(grading_run_id),
                "review_version_id": str(intended_review_version_id),
                "job_id": str(job_id),
                "user_id": user_id,
                "input_fingerprint": input_fingerprint,
                "plan_version": GRADING_PLAN_VERSION,
                "comparison_policy_version": COMPARISON_POLICY_VERSION,
                "state": comparison.state,
                "candidate_count": len(candidates),
                "valid_candidate_count": len(comparison.valid_candidate_ids),
                "comparison_fingerprint": comparison.comparison_fingerprint,
                "comparison_payload": _json(comparison),
                "selected_candidate_id": (
                    str(selected_candidate_id) if selected_candidate_id else None
                ),
                "score_lower": final.score_range.lower if final else None,
                "score_recommended": final.score_range.recommended if final else None,
                "score_upper": final.score_range.upper if final else None,
                "score_maximum": final.maximum if final else None,
                "failure_code": failure_code,
                "failure_payload": (
                    json.dumps(
                        failure_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if failure_payload is not None
                    else None
                ),
            },
        )
        for candidate in candidates:
            session.execute(
                text(
                    """
                    insert into ai_audit.essay_grading_candidates (
                      candidate_id, grading_run_id, slot_id, provider_release_id,
                      model, prompt_version, schema_version, input_fingerprint,
                      output_fingerprint, status, payload, model_call_id, error_code
                    ) values (
                      cast(:candidate_id as uuid), cast(:grading_run_id as uuid),
                      :slot_id, :provider_release_id, :model, :prompt_version,
                      :schema_version, :input_fingerprint, :output_fingerprint,
                      :status, cast(:payload as jsonb), cast(:model_call_id as uuid),
                      :error_code
                    )
                    """
                ),
                {
                    "candidate_id": str(candidate.candidate_id),
                    "grading_run_id": str(grading_run_id),
                    "slot_id": candidate.slot_id,
                    "provider_release_id": candidate.provider_release_id,
                    "model": candidate.model,
                    "prompt_version": candidate.prompt_version,
                    "schema_version": candidate.schema_version,
                    "input_fingerprint": candidate.input_fingerprint,
                    "output_fingerprint": candidate.output_fingerprint,
                    "status": candidate.status,
                    "payload": _json(candidate.payload) if candidate.payload else None,
                    "model_call_id": (
                        str(candidate.model_call_id)
                        if candidate.model_call_id is not None
                        else None
                    ),
                    "error_code": candidate.error_code,
                },
            )

    def bind_review(
        self,
        session: Session,
        *,
        review_version_id: UUID,
        grading_run_id: UUID,
    ) -> None:
        session.execute(
            text(
                """
                insert into ai_audit.essay_review_grading_bindings (
                  review_version_id, grading_run_id
                ) values (
                  cast(:review_version_id as uuid), cast(:grading_run_id as uuid)
                )
                """
            ),
            {
                "review_version_id": str(review_version_id),
                "grading_run_id": str(grading_run_id),
            },
        )

    def by_review(
        self,
        session: Session,
        *,
        review_version_id: UUID,
    ):
        return session.execute(
            text(
                """
                select run.*
                  from ai_audit.essay_review_grading_bindings binding
                  join ai_audit.essay_grading_runs run
                    on run.grading_run_id = binding.grading_run_id
                 where binding.review_version_id = cast(:review_version_id as uuid)
                """
            ),
            {"review_version_id": str(review_version_id)},
        ).mappings().one_or_none()


def _json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
