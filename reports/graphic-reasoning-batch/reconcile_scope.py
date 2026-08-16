#!/usr/bin/env python3
"""Reconcile photo exclusions with the latest life-silhouette scope decision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def reconcile_row(row: dict[str, Any], explicit_exclusions: set[str]) -> tuple[dict[str, Any], str | None]:
    output = dict(row)
    asset_id = str(output.get("assetId") or "")
    subtype = str(output.get("regularSubtype") or "")
    if asset_id in explicit_exclusions:
        output["regularSubtypeStatus"] = "excluded_by_user"
        output["scopeDecision"] = "explicit_asset_exclusion_retained"
        output["exclusionEvidence"] = {
            "policy": "asset_sha256",
            "status": "excluded_by_user",
            "source": "exclusions.jsonl",
        }
        output.pop("scopeReviewEvidence", None)
        return output, "explicit_exclusion"
    if subtype in {"complex_silhouette_candidate", "semantic_life_object"}:
        output["regularSubtypeStatus"] = "semantic_or_renderer_pending"
        output["scopeDecision"] = "life_silhouette_in_scope_requires_part_semantics_and_topology"
        output.pop("exclusionEvidence", None)
        output.pop("scopeReviewEvidence", None)
        return output, "life_restored"
    if subtype == "tonal_life_or_raster_candidate":
        output["regularSubtypeStatus"] = "scope_review_required"
        output["scopeDecision"] = "asset_level_photo_or_natural_image_evidence_required"
        output.pop("exclusionEvidence", None)
        output["scopeReviewEvidence"] = {
            "status": "review_required",
            "candidateSignal": "cv_continuous_tone",
            "decisionBoundary": "natural_photo_out_only_with_asset_level_evidence; life_object_remains_in_scope",
        }
        return output, "photo_review"
    return output, None


def main() -> int:
    classification_path = ROOT / "classification.jsonl"
    rows = read_jsonl(classification_path)
    explicit_rows = read_jsonl(ROOT / "exclusions.jsonl") if (ROOT / "exclusions.jsonl").is_file() else []
    explicit = {str(row.get("assetId")) for row in explicit_rows if row.get("assetId")}
    counts = {"lifeRestored": 0, "photoReviewRequired": 0, "explicitAssetExclusions": 0}
    updated = []
    for row in rows:
        item, decision = reconcile_row(row, explicit)
        updated.append(item)
        if decision == "life_restored":
            counts["lifeRestored"] += 1
        elif decision == "photo_review":
            counts["photoReviewRequired"] += 1
        elif decision == "explicit_exclusion":
            counts["explicitAssetExclusions"] += 1
    write_jsonl(classification_path, updated)
    policy = {
        "schemaVersion": "graphic-reasoning-exclusion-policy-v2",
        "status": "latest_scope_reconciled",
        "regularSubtypes": [],
        "assetLevelExclusionsFile": "exclusions.jsonl",
        "naturalPhotoPolicy": "out_of_batch_only_after_asset_level_photo_or_natural_image_evidence",
        "lifeSilhouettePolicy": "in_scope_requires_object_parts_pose_accessories_and_topology_not_outer_contour_only",
        "supersedes": "graphic-reasoning-exclusion-policy-v1 blanket subtype exclusions",
        "recordedAt": "2026-08-11"
    }
    write_json(ROOT / "exclusion-policies.json", policy)
    for filename in ("summary.json", "regular-subtype-summary.json"):
        path = ROOT / filename
        if not path.is_file():
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        value["excludedAssets"] = len(explicit)
        value["excludedBySubtypePolicy"] = {}
        value["lifeSilhouetteRestoredAssets"] = counts["lifeRestored"]
        value["naturalPhotoScopeReviewAssets"] = counts["photoReviewRequired"]
        value["scopePolicyVersion"] = "graphic-reasoning-exclusion-policy-v2"
        write_json(path, value)
    audit = {
        "schemaVersion": "graphic-reasoning-scope-reconciliation-v1",
        "status": "reconciled",
        **counts,
        "blanketExcludedSubtypes": [],
        "finalAcceptedAssets": 0,
        "decision": "natural photos require asset evidence; life silhouettes remain in the batch and require semantic part topology"
    }
    write_json(ROOT / "cpu-audit/scope-reconciliation.json", audit)
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
