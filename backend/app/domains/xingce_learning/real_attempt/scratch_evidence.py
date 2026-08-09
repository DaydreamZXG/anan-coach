from __future__ import annotations

import base64
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping

from app.domains.math_ink.rendering import (
    InvalidInkError,
    render_annotation_strokes,
)
from app.domains.math_ink.provider import (
    FORMULA_CANDIDATE_MEDIUM_THRESHOLD,
    FORMULA_CONFIDENCE_POLICY_VERSION,
    FORMULA_CONFIDENCE_SEMANTICS,
    formula_evidence_strength,
)


SCRATCH_EVIDENCE_CONTRACT_VERSION = "scratch-evidence-projection@v1"


def build_scratch_evidence_projection(
    *,
    projection_id: str,
    session_id: str,
    question_id: str,
    content_version_id: str,
    answer_version_id: str,
    answer_payload_sha256: str,
    answer: Mapping[str, Any],
    question: Mapping[str, Any],
    material: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    formula_versions: Iterable[Mapping[str, Any]],
    created_at: datetime,
) -> tuple[dict[str, Any], str]:
    strokes = [
        dict(item)
        for item in answer.get("annotations", [])
        if isinstance(item, Mapping)
    ]
    formulas = [_formula_version(item) for item in formula_versions]
    anchored = [
        {
            "strokeId": str(stroke.get("strokeId", "")),
            "surface": str(stroke.get("surface", "question")),
            "tool": str(stroke.get("tool", "pen")),
            "anchor": dict(stroke["anchor"]),
        }
        for stroke in strokes
        if isinstance(stroke.get("anchor"), Mapping)
    ]
    render_snapshots: list[dict[str, Any]] = []
    render_limitations: list[str] = []
    for surface in ("question", "material"):
        surface_strokes = [
            stroke
            for stroke in strokes
            if str(stroke.get("surface", "question")) == surface
        ]
        try:
            rendered = render_annotation_strokes(surface_strokes)
        except (InvalidInkError, TypeError, ValueError):
            rendered = None
            render_limitations.append(f"{surface}_render_invalid")
        if rendered is not None:
            render_snapshots.append(
                {
                    "surface": surface,
                    "rendererVersion": rendered.renderer_version,
                    "width": rendered.width,
                    "height": rendered.height,
                    "vectorFingerprint": rendered.vector_fingerprint,
                    "renderFingerprint": rendered.render_fingerprint,
                    "mimeType": "image/png",
                    "pngBase64": base64.b64encode(rendered.png).decode("ascii"),
                }
            )

    for item in formulas:
        item["strength"] = formula_evidence_strength(
            state=item["state"],
            source=item["source"],
            recognition_metadata=item["recognitionMetadata"],
        )
        item["sourceVersionRef"] = f"formula-version:{item['formulaVersionId']}"
    confirmed = [item for item in formulas if item["state"] == "confirmed"]
    candidates = [item for item in formulas if item["state"] == "candidate"]
    if confirmed:
        recognition_state = "confirmed"
        recognition_strength = "strong"
    elif any(item["strength"] == "medium" for item in candidates):
        recognition_state = "candidate_unconfirmed"
        recognition_strength = "medium"
    elif candidates:
        recognition_state = "candidate_unconfirmed"
        recognition_strength = "weak"
    else:
        recognition_state = "not_requested"
        recognition_strength = "weak"
    typed_draft = str(answer.get("draftText", "")).strip()
    strong_refs = [
        f"formula-version:{item['formulaVersionId']}" for item in confirmed
    ]
    # Projection records what was anchored but never promotes an anchor into a
    # cause claim.  Cause may only strengthen it after relating the exact text
    # to independent answer/scoring evidence.
    medium_refs = [
        f"formula-version:{item['formulaVersionId']}"
        for item in candidates
        if item["strength"] == "medium"
    ]
    weak_refs = [
        f"annotation-anchor:{item['strokeId']}" for item in anchored if item["strokeId"]
    ] + [
        f"annotation-stroke:{stroke.get('strokeId')}"
        for stroke in strokes
        if stroke.get("strokeId") and not isinstance(stroke.get("anchor"), Mapping)
    ] + [
        f"formula-version:{item['formulaVersionId']}" for item in candidates
        if item["strength"] == "weak"
    ]
    if typed_draft:
        strong_refs.append(f"answer-version:{answer_version_id}:typed-draft")
    evidence_refs = [
        f"answer-version:{answer_version_id}",
        f"content-version:{content_version_id}",
        *[f"formula-version:{item['formulaVersionId']}" for item in formulas],
    ]
    limitations = [
        "未锚定自由笔迹只证明笔迹存在，具体文字、公式与意图未知"
    ]
    if answer.get("actionEventsTruncated") is True:
        limitations.append("动作序列由端侧标记为截断，仅可视为真实前缀")
    limitations.extend(render_limitations)
    projection: dict[str, Any] = {
        "contractVersion": SCRATCH_EVIDENCE_CONTRACT_VERSION,
        "projectionId": projection_id,
        "sessionId": session_id,
        "questionId": question_id,
        "contentVersionId": content_version_id,
        "answerVersionId": answer_version_id,
        "answerPayloadSha256": answer_payload_sha256,
        "question": dict(question),
        "material": dict(material) if material is not None else None,
        "userAnswer": {
            key: answer.get(key)
            for key in (
                "selectedOptionIds",
                "eliminatedOptionIds",
                "confidence",
                "marked",
                "skipped",
                "elapsedSeconds",
                "materialPosition",
            )
        },
        "scoring": dict(result),
        "typedDraft": typed_draft,
        "formulaVersions": formulas,
        "recognition": {
            "state": recognition_state,
            "confidence": _recognition_confidence(formulas),
            "strength": recognition_strength,
            "confidencePolicyVersion": FORMULA_CONFIDENCE_POLICY_VERSION,
            "candidateMediumThreshold": FORMULA_CANDIDATE_MEDIUM_THRESHOLD,
            "confidenceSemantics": FORMULA_CONFIDENCE_SEMANTICS,
            "sourceVersions": [
                {
                    "source": item["source"],
                    "formulaVersionId": item["formulaVersionId"],
                    "sourceVersionRef": item["sourceVersionRef"],
                    "state": item["state"],
                    "confidence": _metadata_confidence(item["recognitionMetadata"]),
                    "strength": item["strength"],
                    "recognitionMetadata": item["recognitionMetadata"],
                }
                for item in formulas
            ],
        },
        "annotations": {
            "vectorStrokes": strokes,
            "anchoredAnnotations": anchored,
            "unanchoredStrokeCount": len(strokes) - len(anchored),
            "renderSnapshots": render_snapshots,
            "semanticState": (
                "anchored_and_unknown_freehand"
                if anchored and len(anchored) < len(strokes)
                else "anchored"
                if anchored
                else "freehand_semantic_unknown"
                if strokes
                else "absent"
            ),
        },
        "actionSequence": list(answer.get("actionEvents", [])),
        "actionSequenceTruncated": bool(answer.get("actionEventsTruncated", False)),
        "evidenceStrength": {
            "strong": strong_refs,
            "medium": medium_refs,
            "weak": weak_refs,
        },
        "evidenceRefs": list(dict.fromkeys(evidence_refs)),
        "limitations": limitations,
        "createdAt": created_at.isoformat(),
    }
    fingerprint = sha256(
        json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return projection, fingerprint


def _formula_version(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = row.get("recognition_metadata")
    return {
        "formulaVersionId": str(row["formula_version_id"]),
        "versionNo": int(row["version_no"]),
        "state": str(row["state"]),
        "latex": str(row["latex"]),
        "source": str(row["source"]),
        "recognitionMetadata": dict(metadata) if isinstance(metadata, Mapping) else None,
        "createdAt": row["created_at"].isoformat(),
        "confirmedAt": (
            row["confirmed_at"].isoformat()
            if row.get("confirmed_at") is not None
            else None
        ),
    }


def _recognition_confidence(formulas: list[dict[str, Any]]) -> float | None:
    values = [value for item in formulas if (value := _metadata_confidence(item.get("recognitionMetadata"))) is not None]
    return max(values) if values else None


def _metadata_confidence(metadata: object) -> float | None:
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if 0 <= numeric <= 1 else None
