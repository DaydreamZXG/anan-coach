from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from app.domains.teaching_packages.publication.models import CandidateProjection
from app.domains.teaching_packages.publication.projector import (
    CandidateProjectionError,
    canonical_json,
    canonical_sha256,
    project_candidate,
)


_ASSET_ID = re.compile(r"^qi_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_ROUTES = frozenset(
    {"annotated_raster", "data_visual", "perceptual_grid", "reasoning_diagram"}
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _literal(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return canonical_json(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).strip()


def _fact(
    asset_id: str,
    position: int,
    kind: str,
    dimensions: Sequence[Any],
    value: Any,
    unit: Any = "",
) -> dict[str, Any]:
    rendered = _literal(value)
    if not rendered:
        raise CandidateProjectionError("empty media fact")
    return {
        "factRef": f"visual:{asset_id}:fact:{position:04d}",
        "kind": kind,
        "dimensions": [_text(item) for item in dimensions],
        "value": rendered,
        "unit": _text(unit),
    }


def _source_identity(candidate: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    asset_id = _text(candidate.get("assetId"))
    source = candidate.get("source")
    if _ASSET_ID.fullmatch(asset_id) is None or not isinstance(source, Mapping):
        raise CandidateProjectionError("invalid non-graphic asset identity")
    sha = _text(source.get("sha256"))
    verification = candidate.get("verification")
    if (
        _SHA256.fullmatch(sha) is None
        or not isinstance(verification, Mapping)
        or verification.get("sourceHashVerified") is not True
    ):
        raise CandidateProjectionError("non-graphic source hash is unverified")
    question_ids = tuple(
        sorted({_text(item) for item in source.get("questionIds") or () if _text(item)})
    )
    if not question_ids:
        raise CandidateProjectionError("non-graphic source questions are missing")
    return asset_id, sha, question_ids


def _walk_semantics(
    asset_id: str,
    value: Any,
    *,
    path: tuple[str, ...] = (),
    facts: list[dict[str, Any]],
) -> None:
    if isinstance(value, Mapping):
        for key in sorted(value):
            if key in {"confidence", "uncertainties", "topology_conflicts"}:
                continue
            _walk_semantics(
                asset_id, value[key], path=(*path, str(key)), facts=facts
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_semantics(
                asset_id, item, path=(*path, str(index)), facts=facts
            )
        return
    if value is None or not path or (isinstance(value, str) and not value.strip()):
        return
    kind = (
        "label"
        if any(key in path[-1].lower() for key in ("label", "text"))
        else "relationship"
    )
    facts.append(_fact(asset_id, len(facts) + 1, kind, path, value))


def project_non_graphic_candidate(candidate: Mapping[str, Any]) -> CandidateProjection:
    if candidate.get("schemaVersion") != "non-graphic-question-visual-v1":
        raise CandidateProjectionError("unsupported non-graphic schema")
    if candidate.get("status") != "success":
        raise CandidateProjectionError("non-graphic candidate is not verified")
    route = _text(candidate.get("route"))
    if route not in _ALLOWED_ROUTES:
        raise CandidateProjectionError("unsupported non-graphic route")
    asset_id, source_sha, question_ids = _source_identity(candidate)
    reasoning = candidate.get("reasoningLayer")
    if not isinstance(reasoning, Mapping):
        raise CandidateProjectionError("non-graphic reasoning layer is missing")

    if route == "data_visual":
        adapted = dict(reasoning)
        adapted.update(
            {
                "asset_id": asset_id.replace("qi_", "da_"),
                "source": {
                    "sha256": source_sha,
                    "occurrences": [
                        {"question_id": question_id} for question_id in question_ids
                    ],
                },
                "review_status": "verified_sample",
                "verification": {
                    "schema_valid": True,
                    "missing_values_preserved": True,
                },
            }
        )
        try:
            projected = project_candidate(adapted)
        except CandidateProjectionError:
            facts = []
            _walk_semantics(asset_id, reasoning, facts=facts)
            if not facts:
                raise CandidateProjectionError(
                    "data visual candidate produced no semantic facts"
                )
            authority = "verified_non_graphic_reasoning_layer"
        else:
            facts = projected.payload["facts"]
            authority = projected.authority
    elif route == "annotated_raster":
        facts = [
            _fact(
                asset_id,
                1,
                "source_only",
                ("source-image",),
                "原图是当前题面权威；结构化结果只含可见对象，不补齐不可辨认文字。",
            )
        ]
        observable = reasoning.get("observable_entities")
        if isinstance(observable, list):
            for item in observable:
                if isinstance(item, Mapping):
                    value = item.get("description") or item.get("label") or item
                    facts.append(
                        _fact(
                            asset_id,
                            len(facts) + 1,
                            "entity",
                            ("observable",),
                            value,
                        )
                    )
        authority = "source_image_with_observations"
    else:
        facts = []
        _walk_semantics(asset_id, reasoning, facts=facts)
        if not facts:
            raise CandidateProjectionError(
                "non-graphic candidate produced no semantic facts"
            )
        authority = "semantic_reasoning_layer"

    payload = {
        "chartType": route,
        "evidenceKind": route,
        "reviewState": "verified",
        "sourceRoles": sorted(
            {
                _text(item)
                for item in candidate["source"].get("roles") or ()
                if _text(item)
            }
        ),
        "audiences": [],
        "facts": facts,
    }
    return CandidateProjection(
        evidence_ref=f"question-media:{asset_id}",
        source_asset_sha256=source_sha,
        source_payload_sha256=canonical_sha256(candidate),
        chart_type=route,
        payload=payload,
        authority=authority,
    )


def project_formula_candidate(
    candidate: Mapping[str, Any],
    *,
    source_occurrences: Sequence[Mapping[str, Any]],
) -> CandidateProjection:
    if candidate.get("schema_version") != "formula-asset-v1":
        raise CandidateProjectionError("unsupported formula schema")
    asset_id = _text(candidate.get("asset_id"))
    source_sha = _text(candidate.get("sha256"))
    if _ASSET_ID.fullmatch(asset_id) is None or _SHA256.fullmatch(source_sha) is None:
        raise CandidateProjectionError("invalid formula source identity")
    verification = candidate.get("verification")
    if (
        not isinstance(verification, Mapping)
        or verification.get("source_hash_matches") is not True
    ):
        raise CandidateProjectionError("formula source hash is unverified")
    if candidate.get("status") not in {"candidate_auto", "verified_override"}:
        raise CandidateProjectionError("formula did not pass final review policy")
    if (
        verification.get("braces_balanced") is not True
        or verification.get("latex_nonempty") is not True
    ):
        raise CandidateProjectionError("formula quality gates failed")
    blocks = candidate.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise CandidateProjectionError("formula blocks are missing")
    facts: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") != "formula":
            raise CandidateProjectionError("unsupported formula block")
        expression = _text(block.get("expression"))
        if not expression or expression.startswith("["):
            raise CandidateProjectionError("formula expression is unresolved")
        facts.append(
            _fact(asset_id, len(facts) + 1, "formula", ("latex",), expression)
        )
    source_roles = sorted(
        {
            _text(item.get("role"))
            for item in source_occurrences
            if isinstance(item, Mapping) and _text(item.get("role"))
        }
    )
    payload = {
        "chartType": "formula",
        "evidenceKind": "formula",
        "reviewState": (
            "verified"
            if candidate.get("status") == "verified_override"
            else "machine_verified"
        ),
        "sourceRoles": source_roles,
        "audiences": [],
        "facts": facts,
    }
    return CandidateProjection(
        evidence_ref=f"question-media:{asset_id}",
        source_asset_sha256=source_sha,
        source_payload_sha256=canonical_sha256(
            {
                "candidate": candidate,
                "sourceOccurrences": [
                    {
                        "questionId": _text(item.get("question_id")),
                        "role": _text(item.get("role")),
                        "field": _text(item.get("field")),
                        "imageRef": _text(item.get("image_ref")),
                    }
                    for item in source_occurrences
                    if isinstance(item, Mapping)
                ],
            }
        ),
        chart_type="formula",
        payload=payload,
        authority=(
            "verified_override"
            if candidate.get("status") == "verified_override"
            else "quality_gated_machine_transcription"
        ),
    )


def project_analysis_source_image(
    candidate: Mapping[str, Any],
) -> CandidateProjection:
    """Represent an unstructured analysis image without inventing semantics."""

    asset_id = _text(candidate.get("asset_id"))
    source_sha = _text(candidate.get("sha256"))
    if _ASSET_ID.fullmatch(asset_id) is None or _SHA256.fullmatch(source_sha) is None:
        raise CandidateProjectionError("invalid analysis source image identity")
    payload = {
        "chartType": "source_image",
        "evidenceKind": "source_image",
        "reviewState": "verified",
        "sourceRoles": [],
        "audiences": [],
        "facts": [
            _fact(
                asset_id,
                1,
                "source_only",
                ("image_sha256",),
                source_sha,
            )
        ],
    }
    return CandidateProjection(
        evidence_ref=f"question-media:{asset_id}",
        source_asset_sha256=source_sha,
        source_payload_sha256=canonical_sha256(candidate),
        chart_type="source_image",
        payload=payload,
        authority="source_image_identity_only",
    )


def source_question_ids(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    if candidate.get("schemaVersion") == "non-graphic-question-visual-v1":
        source = candidate.get("source") or {}
        return tuple(
            sorted({_text(item) for item in source.get("questionIds") or () if _text(item)})
        )
    raise CandidateProjectionError(
        "source question IDs require a recognized media schema"
    )


def audiences_for_part_kind(part_kind: str) -> tuple[str, ...]:
    value = _text(part_kind).casefold().removesuffix("_html")
    if value in {"stem", "material", "option", "question", "prompt"}:
        return ("student", "verifier")
    if value in {"analysis", "solution", "explanation", "answer"}:
        return ("verifier",)
    return ()
