from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.teaching_packages.publication.models import (
    PublicationBinding,
    PublicationPlan,
    PublicationPlanItem,
    PublicationReasonCode,
    RuntimeAsset,
    RuntimeQuestion,
)
from app.domains.teaching_packages.publication.projector import (
    CandidateProjectionError,
    canonical_sha256,
    project_candidate,
)
from app.domains.teaching_packages.publication.media_projector import (
    audiences_for_part_kind,
)


POLICY_VERSION = "data-analysis-structured-visual-publication@v1"
MEDIA_POLICY_VERSION = "question-media-evidence-publication@v2"


def _ordered_reasons(
    reasons: set[PublicationReasonCode],
) -> tuple[PublicationReasonCode, ...]:
    return tuple(reason for reason in PublicationReasonCode if reason in reasons)


def _source_keys(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    source = candidate.get("source")
    if not isinstance(source, Mapping):
        return ()
    occurrences = source.get("occurrences")
    if not isinstance(occurrences, list):
        return ()
    return tuple(
        sorted(
            {
                str(item.get("question_id") or "").strip()
                for item in occurrences
                if isinstance(item, Mapping) and str(item.get("question_id") or "").strip()
            }
        )
    )


def _quarantined_item(
    *,
    batch_id: str,
    candidate: Mapping[str, Any],
    reason: PublicationReasonCode,
) -> PublicationPlanItem:
    source = candidate.get("source")
    source_sha = str(source.get("sha256") or "") if isinstance(source, Mapping) else ""
    asset_id = str(candidate.get("asset_id") or "unknown")
    return PublicationPlanItem(
        batch_id=batch_id,
        evidence_ref=f"structured-visual:{asset_id}",
        source_asset_sha256=source_sha,
        source_payload_sha256=canonical_sha256(candidate),
        verification_policy_version=POLICY_VERSION,
        payload={"chartType": str(candidate.get("chart_type") or "invalid"), "facts": []},
        bindings=(),
        quarantine_reasons=(reason,),
    )


def build_publication_plan(
    *,
    batch_id: str,
    candidates: Sequence[Mapping[str, Any]],
    occurrence_question_ids: Mapping[str, str],
    runtime_questions: Mapping[str, RuntimeQuestion],
    runtime_assets: Mapping[str, Sequence[RuntimeAsset]],
) -> PublicationPlan:
    """Resolve candidates against exact current question/content/image identity."""

    items: list[PublicationPlanItem] = []
    for candidate in sorted(candidates, key=lambda item: str(item.get("asset_id") or "")):
        try:
            projection = project_candidate(candidate)
        except CandidateProjectionError:
            items.append(
                _quarantined_item(
                    batch_id=batch_id,
                    candidate=candidate,
                    reason=PublicationReasonCode.SOURCE_SCHEMA_INVALID,
                )
            )
            continue

        unresolved_keys: list[str] = []
        rejected_questions: set[str] = set()
        candidate_reasons: set[PublicationReasonCode] = set()
        question_ids: set[str] = set()
        for source_key in _source_keys(candidate):
            question_id = occurrence_question_ids.get(source_key)
            if not question_id:
                unresolved_keys.append(source_key)
                candidate_reasons.add(PublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED)
                continue
            question_ids.add(question_id)

        bindings: set[PublicationBinding] = set()
        for question_id in sorted(question_ids):
            question = runtime_questions.get(question_id)
            if question is None or question.placeholder:
                rejected_questions.add(question_id)
                candidate_reasons.add(PublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED)
                continue
            if question.status not in {"active", "canonical"}:
                rejected_questions.add(question_id)
                candidate_reasons.add(PublicationReasonCode.QUESTION_NOT_ACTIVE)
                continue
            if not question.content_version_id:
                rejected_questions.add(question_id)
                candidate_reasons.add(PublicationReasonCode.CONTENT_HEAD_MISSING)
                continue
            matching_assets = sorted(
                (
                    asset
                    for asset in runtime_assets.get(question_id, ())
                    if asset.content_sha256 == projection.source_asset_sha256
                ),
                key=lambda asset: asset.asset_id,
            )
            if not matching_assets:
                rejected_questions.add(question_id)
                candidate_reasons.add(PublicationReasonCode.SOURCE_ASSET_HASH_MISMATCH)
                continue
            # Duplicate DB rows with identical object bytes are one content identity.
            chosen = matching_assets[0]
            bindings.add(
                PublicationBinding(
                    question_id=question_id,
                    content_version_id=question.content_version_id,
                    runtime_asset_id=chosen.asset_id,
                )
            )

        ordered_bindings = tuple(
            sorted(
                bindings,
                key=lambda item: (
                    item.question_id,
                    item.content_version_id,
                    item.runtime_asset_id,
                ),
            )
        )
        # A stale occurrence does not poison valid bindings for the same immutable
        # image. It remains explicit in diagnostics. If none survive, the asset is
        # quarantined with every observed stable reason.
        quarantine_reasons = () if ordered_bindings else _ordered_reasons(candidate_reasons)
        if not ordered_bindings and not quarantine_reasons:
            quarantine_reasons = (
                PublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED,
            )
        items.append(
            PublicationPlanItem(
                batch_id=batch_id,
                evidence_ref=projection.evidence_ref,
                source_asset_sha256=projection.source_asset_sha256,
                source_payload_sha256=projection.source_payload_sha256,
                verification_policy_version=POLICY_VERSION,
                payload=projection.payload,
                bindings=ordered_bindings,
                quarantine_reasons=quarantine_reasons,
                unresolved_occurrence_keys=tuple(sorted(unresolved_keys)),
                rejected_question_ids=tuple(sorted(rejected_questions)),
                authority=projection.authority,
            )
        )
    return PublicationPlan(
        batch_id=batch_id,
        policy_version=POLICY_VERSION,
        items=tuple(items),
    )


def _media_source_sha(candidate: Mapping[str, Any]) -> str:
    if candidate.get("schemaVersion") == "non-graphic-question-visual-v1":
        source = candidate.get("source")
        return str(source.get("sha256") or "") if isinstance(source, Mapping) else ""
    return str(candidate.get("sha256") or "")


def _media_asset_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("assetId") or candidate.get("asset_id") or "unknown")


def _media_quarantined_item(
    *,
    batch_id: str,
    candidate: Mapping[str, Any],
    reason: PublicationReasonCode,
) -> PublicationPlanItem:
    asset_id = _media_asset_id(candidate)
    return PublicationPlanItem(
        batch_id=batch_id,
        evidence_ref=f"question-media:{asset_id}",
        source_asset_sha256=_media_source_sha(candidate),
        source_payload_sha256=canonical_sha256(candidate),
        verification_policy_version=MEDIA_POLICY_VERSION,
        payload={
            "chartType": "invalid",
            "evidenceKind": "invalid",
            "reviewState": "needs_review",
            "sourceRoles": [],
            "audiences": [],
            "facts": [],
        },
        bindings=(),
        quarantine_reasons=(reason,),
    )


def build_media_publication_plan(
    *,
    batch_id: str,
    candidates: Sequence[Mapping[str, Any]],
    projector: Any,
    source_keys_for_candidate: Any,
    source_roles_for_candidate: Any | None = None,
    occurrence_question_ids: Mapping[str, str],
    runtime_questions: Mapping[str, RuntimeQuestion],
    runtime_assets: Mapping[str, Sequence[RuntimeAsset]],
) -> PublicationPlan:
    """Publish non-graphic media with runtime-asset audience authority.

    Offline roles remain diagnostics. The exact current runtime asset ``part_kind``
    decides whether the binding is safe for the student, verifier, or neither.
    """

    items: list[PublicationPlanItem] = []
    for candidate in sorted(candidates, key=_media_asset_id):
        if candidate.get("status") == "needs_review":
            items.append(
                _media_quarantined_item(
                    batch_id=batch_id,
                    candidate=candidate,
                    reason=PublicationReasonCode.SEMANTIC_CONFLICT,
                )
            )
            continue
        try:
            projection = projector(candidate)
            source_keys = tuple(source_keys_for_candidate(candidate))
        except CandidateProjectionError:
            items.append(
                _media_quarantined_item(
                    batch_id=batch_id,
                    candidate=candidate,
                    reason=PublicationReasonCode.SOURCE_SCHEMA_INVALID,
                )
            )
            continue

        unresolved_keys: list[str] = []
        rejected_questions: set[str] = set()
        candidate_reasons: set[PublicationReasonCode] = set()
        question_ids: set[str] = set()
        for source_key in sorted(set(source_keys)):
            question_id = occurrence_question_ids.get(source_key)
            if not question_id:
                unresolved_keys.append(source_key)
                candidate_reasons.add(
                    PublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED
                )
                continue
            question_ids.add(question_id)

        bindings: set[PublicationBinding] = set()
        observed_audiences: set[str] = set()
        for question_id in sorted(question_ids):
            question = runtime_questions.get(question_id)
            if question is None or question.placeholder:
                rejected_questions.add(question_id)
                candidate_reasons.add(
                    PublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED
                )
                continue
            if question.status not in {"active", "canonical"}:
                rejected_questions.add(question_id)
                candidate_reasons.add(PublicationReasonCode.QUESTION_NOT_ACTIVE)
                continue
            if not question.content_version_id:
                rejected_questions.add(question_id)
                candidate_reasons.add(PublicationReasonCode.CONTENT_HEAD_MISSING)
                continue
            matching_assets = tuple(
                asset
                for asset in runtime_assets.get(question_id, ())
                if asset.content_sha256 == projection.source_asset_sha256
            )
            if not matching_assets:
                rejected_questions.add(question_id)
                candidate_reasons.add(
                    PublicationReasonCode.SOURCE_ASSET_HASH_MISMATCH
                )
                continue
            eligible_assets = tuple(
                asset
                for asset in matching_assets
                if audiences_for_part_kind(asset.part_kind)
            )
            if not eligible_assets:
                rejected_questions.add(question_id)
                candidate_reasons.add(
                    PublicationReasonCode.SOURCE_AUDIENCE_UNRESOLVED
                )
                continue
            chosen = sorted(
                eligible_assets,
                key=lambda asset: (
                    "student" not in audiences_for_part_kind(asset.part_kind),
                    asset.asset_id,
                ),
            )[0]
            observed_audiences.update(audiences_for_part_kind(chosen.part_kind))
            bindings.add(
                PublicationBinding(
                    question_id=question_id,
                    content_version_id=question.content_version_id,
                    runtime_asset_id=chosen.asset_id,
                )
            )

        ordered_bindings = tuple(
            sorted(
                bindings,
                key=lambda item: (
                    item.question_id,
                    item.content_version_id,
                    item.runtime_asset_id,
                ),
            )
        )
        quarantine_reasons = () if ordered_bindings else _ordered_reasons(candidate_reasons)
        if not ordered_bindings and not quarantine_reasons:
            quarantine_reasons = (
                PublicationReasonCode.SOURCE_OCCURRENCE_UNRESOLVED,
            )
        payload = dict(projection.payload)
        payload["audiences"] = sorted(observed_audiences)
        if source_roles_for_candidate is not None:
            payload["sourceRoles"] = sorted(
                {
                    str(role).strip()
                    for role in source_roles_for_candidate(candidate)
                    if str(role).strip()
                }
            )
        items.append(
            PublicationPlanItem(
                batch_id=batch_id,
                evidence_ref=projection.evidence_ref,
                source_asset_sha256=projection.source_asset_sha256,
                source_payload_sha256=projection.source_payload_sha256,
                verification_policy_version=MEDIA_POLICY_VERSION,
                payload=payload,
                bindings=ordered_bindings,
                quarantine_reasons=quarantine_reasons,
                unresolved_occurrence_keys=tuple(sorted(unresolved_keys)),
                rejected_question_ids=tuple(sorted(rejected_questions)),
                authority=projection.authority,
            )
        )
    return PublicationPlan(
        batch_id=batch_id,
        policy_version=MEDIA_POLICY_VERSION,
        items=tuple(items),
    )
