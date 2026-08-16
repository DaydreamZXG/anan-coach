from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.teaching_packages.publication.models import (
    AnalysisPublicationBinding,
    AnalysisPublicationPlan,
    AnalysisPublicationPlanItem,
    AnalysisPublicationReasonCode,
    AnalysisRuntimeQuestion,
)
from app.domains.teaching_packages.publication.projector import (
    CandidateProjectionError,
    canonical_sha256,
)


ANALYSIS_MEDIA_POLICY_VERSION = "analysis-media-verifier-evidence-publication@v1"
_ANALYSIS_ROLES = frozenset({"analysis", "solution", "explanation", "answer"})


def _ordered_reasons(
    reasons: set[AnalysisPublicationReasonCode],
) -> tuple[AnalysisPublicationReasonCode, ...]:
    return tuple(reason for reason in AnalysisPublicationReasonCode if reason in reasons)


def _runtime_object_key(
    question: AnalysisRuntimeQuestion,
    *,
    source_asset_sha256: str,
    source_image_ref: str,
) -> str:
    references = question.final_analysis_asset_refs
    if not isinstance(references, Mapping):
        return ""
    if str(references.get("source") or "") != question.analysis_source:
        return ""
    assets = references.get("assets")
    if not isinstance(assets, list):
        return ""
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        content_sha = str(
            asset.get("contentSha256") or asset.get("content_sha256") or ""
        )
        image_ref = str(asset.get("imageSrc") or asset.get("image_src") or "")
        object_key = str(asset.get("objectKey") or asset.get("object_key") or "")
        if (
            content_sha == source_asset_sha256
            and image_ref == source_image_ref
            and object_key
        ):
            return object_key
    return ""


def _quarantined_item(
    *,
    batch_id: str,
    candidate: Mapping[str, Any],
    source_sha: str,
    reason: AnalysisPublicationReasonCode,
) -> AnalysisPublicationPlanItem:
    asset_id = str(candidate.get("assetId") or candidate.get("asset_id") or "unknown")
    return AnalysisPublicationPlanItem(
        batch_id=batch_id,
        evidence_ref=f"analysis-media:{asset_id}",
        source_asset_sha256=source_sha,
        source_payload_sha256=canonical_sha256(candidate),
        verification_policy_version=ANALYSIS_MEDIA_POLICY_VERSION,
        payload={
            "chartType": "invalid",
            "evidenceKind": "invalid",
            "reviewState": "needs_review",
            "sourceRoles": [],
            "audiences": ["verifier"],
            "facts": [],
        },
        analysis_bindings=(),
        quarantine_reasons=(reason,),
    )


def build_analysis_publication_plan(
    *,
    batch_id: str,
    entries: Sequence[
        tuple[Mapping[str, Any], Sequence[Mapping[str, Any]], Any]
    ],
    occurrence_question_ids: Mapping[str, str],
    runtime_questions: Mapping[str, AnalysisRuntimeQuestion],
) -> AnalysisPublicationPlan:
    """Bind explanation media to exact current analysis identity.

    The projector is supplied per entry because formulas and non-graphic media
    have different closed source schemas. Offline roles select analysis
    occurrences only; they never decide the runtime audience.
    """

    items: list[AnalysisPublicationPlanItem] = []
    for candidate, occurrences, projector in sorted(
        entries,
        key=lambda item: str(
            item[0].get("assetId") or item[0].get("asset_id") or ""
        ),
    ):
        source = candidate.get("source")
        source_sha = str(
            source.get("sha256")
            if isinstance(source, Mapping)
            else candidate.get("sha256") or ""
        )
        if candidate.get("status") == "needs_review":
            items.append(
                _quarantined_item(
                    batch_id=batch_id,
                    candidate=candidate,
                    source_sha=source_sha,
                    reason=AnalysisPublicationReasonCode.SEMANTIC_CONFLICT,
                )
            )
            continue
        try:
            projection = projector(candidate)
        except CandidateProjectionError:
            items.append(
                _quarantined_item(
                    batch_id=batch_id,
                    candidate=candidate,
                    source_sha=source_sha,
                    reason=AnalysisPublicationReasonCode.SOURCE_SCHEMA_INVALID,
                )
            )
            continue

        reasons: set[AnalysisPublicationReasonCode] = set()
        unresolved: set[str] = set()
        rejected: set[str] = set()
        bindings: set[AnalysisPublicationBinding] = set()
        observed_roles: set[str] = set()
        for occurrence in occurrences:
            role = str(occurrence.get("role") or "").strip().casefold()
            if role not in _ANALYSIS_ROLES:
                continue
            source_key = str(occurrence.get("question_id") or "").strip()
            source_image_ref = str(occurrence.get("image_ref") or "").strip()
            if not source_key or not source_image_ref:
                reasons.add(AnalysisPublicationReasonCode.ANALYSIS_ASSET_UNRESOLVED)
                continue
            observed_roles.add(role)
            question_id = occurrence_question_ids.get(source_key)
            if not question_id:
                unresolved.add(source_key)
                reasons.add(
                    AnalysisPublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED
                )
                continue
            question = runtime_questions.get(question_id)
            if question is None or question.placeholder:
                rejected.add(question_id)
                reasons.add(
                    AnalysisPublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED
                )
                continue
            if question.status not in {"active", "canonical"}:
                rejected.add(question_id)
                reasons.add(AnalysisPublicationReasonCode.QUESTION_NOT_ACTIVE)
                continue
            if not question.content_version_id:
                rejected.add(question_id)
                reasons.add(AnalysisPublicationReasonCode.CONTENT_HEAD_MISSING)
                continue
            if question.analysis_source != "original_analysis":
                rejected.add(question_id)
                reasons.add(AnalysisPublicationReasonCode.ANALYSIS_SOURCE_MISMATCH)
                continue
            bindings.add(
                AnalysisPublicationBinding(
                    question_id=question_id,
                    content_version_id=question.content_version_id,
                    analysis_source=question.analysis_source,
                    source_occurrence_question_key=source_key,
                    source_image_ref=source_image_ref,
                    runtime_object_key=_runtime_object_key(
                        question,
                        source_asset_sha256=projection.source_asset_sha256,
                        source_image_ref=source_image_ref,
                    ),
                )
            )

        ordered_bindings = tuple(
            sorted(
                bindings,
                key=lambda value: (
                    value.question_id,
                    value.content_version_id,
                    value.source_occurrence_question_key,
                    value.source_image_ref,
                ),
            )
        )
        quarantine_reasons = () if ordered_bindings else _ordered_reasons(reasons)
        if not ordered_bindings and not quarantine_reasons:
            quarantine_reasons = (
                AnalysisPublicationReasonCode.ANALYSIS_ASSET_UNRESOLVED,
            )
        payload = dict(projection.payload)
        payload["audiences"] = ["verifier"]
        payload["sourceRoles"] = sorted(observed_roles)
        items.append(
            AnalysisPublicationPlanItem(
                batch_id=batch_id,
                evidence_ref=f"analysis-media:{projection.evidence_ref.rsplit(':', 1)[-1]}",
                source_asset_sha256=projection.source_asset_sha256,
                source_payload_sha256=canonical_sha256(
                    {
                        "projection": projection.source_payload_sha256,
                        "analysisOccurrences": [
                            {
                                "questionId": str(item.get("question_id") or ""),
                                "role": str(item.get("role") or ""),
                                "field": str(item.get("field") or ""),
                                "imageRef": str(item.get("image_ref") or ""),
                            }
                            for item in occurrences
                            if str(item.get("role") or "").casefold()
                            in _ANALYSIS_ROLES
                        ],
                    }
                ),
                verification_policy_version=ANALYSIS_MEDIA_POLICY_VERSION,
                payload=payload,
                analysis_bindings=ordered_bindings,
                quarantine_reasons=quarantine_reasons,
                unresolved_occurrence_keys=tuple(sorted(unresolved)),
                rejected_question_ids=tuple(sorted(rejected)),
                authority=projection.authority,
            )
        )
    return AnalysisPublicationPlan(
        batch_id=batch_id,
        policy_version=ANALYSIS_MEDIA_POLICY_VERSION,
        items=tuple(items),
    )
