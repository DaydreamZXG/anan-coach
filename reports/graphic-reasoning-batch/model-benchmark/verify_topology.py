#!/usr/bin/env python3
"""Compare source and refined-candidate topology without using a raster fallback."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from verify_candidate import sample_primitive


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pipeline  # noqa: E402


def _clean_mask(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    output = np.zeros_like(mask, dtype=np.uint8)
    minimum = max(2, round(mask.size * 0.00012))
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= minimum:
            output[labels == label] = 255
    return output


def _render(primitives: list[dict[str, Any]], width: int, height: int) -> np.ndarray:
    canvas = np.zeros((height, width), np.uint8)
    for primitive in primitives:
        if primitive.get("type") == "textGlyph":
            continue
        points, closed = sample_primitive(primitive, width, height)
        if not len(points):
            continue
        stroke_width = max(1, round(float(primitive.get("style", {}).get("lineWidth") or 10) / 1000.0 * (width + height) / 2.0))
        cv2.polylines(canvas, [np.rint(points).astype(np.int32)], closed, 255, stroke_width, cv2.LINE_AA)
    return canvas


def _summary(mask: np.ndarray) -> dict[str, int]:
    cleaned = _clean_mask(mask)
    component_count, _, stats, _ = cv2.connectedComponentsWithStats((cleaned > 0).astype(np.uint8), 8)
    components = sum(int(row[cv2.CC_STAT_AREA]) >= 2 for row in stats[1:component_count])
    skeleton = pipeline._skeletonize_mask(cleaned)
    nodes, edges = pipeline._trace_skeleton_graph(skeleton)
    incidence: Counter[int] = Counter()
    for edge in edges:
        if edge.get("from") is not None:
            incidence[int(edge["from"])] += 1
        if edge.get("to") is not None and edge.get("to") != edge.get("from"):
            incidence[int(edge["to"])] += 1
    loop_nodes = {int(node["id"]) for node in nodes if node.get("loopAnchor")}
    return {
        "componentCount": components,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "endpointCount": sum(degree <= 1 and node_id not in loop_nodes for node_id, degree in incidence.items()),
        "junctionCount": sum(degree >= 3 for degree in incidence.values()),
        "loopCount": sum(bool(edge.get("closed")) for edge in edges),
    }


def _right_angle_evidence(primitives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines = [primitive for primitive in primitives if primitive.get("type") == "line"]
    evidence: list[dict[str, Any]] = []
    for left_index, left in enumerate(lines):
        left_points = [np.array([left[key]["x"], left[key]["y"]], dtype=float) for key in ("from", "to")]
        for right_index in range(left_index + 1, len(lines)):
            right = lines[right_index]
            right_points = [np.array([right[key]["x"], right[key]["y"]], dtype=float) for key in ("from", "to")]
            distance = min(np.linalg.norm(a - b) for a in left_points for b in right_points)
            if distance > 15:
                continue
            lv, rv = left_points[1] - left_points[0], right_points[1] - right_points[0]
            denominator = max(1e-9, np.linalg.norm(lv) * np.linalg.norm(rv))
            angle = math.degrees(math.acos(max(-1.0, min(1.0, abs(float(np.dot(lv, rv))) / denominator))))
            if abs(angle - 90) <= 5:
                evidence.append({"lineA": left_index, "lineB": right_index, "angleDegrees": round(angle, 4), "exact": abs(angle - 90) <= 0.05})
    return evidence


def verify_topology(refinement: dict[str, Any], source_panel: Path) -> dict[str, Any]:
    if refinement.get("schemaVersion") != "graphic-vector-deterministic-refinement-v1":
        raise ValueError("refinement_schema_mismatch")
    gray = cv2.imread(str(source_panel), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("source_panel_decode_failed")
    height, width = gray.shape
    source = np.where(gray < 210, 255, 0).astype(np.uint8)
    redrawn = _render(refinement.get("primitives", []), width, height)
    source_summary, redraw_summary = _summary(source), _summary(redrawn)
    compared_fields = ("componentCount", "endpointCount", "junctionCount", "loopCount")
    mismatches = [field for field in compared_fields if source_summary[field] != redraw_summary[field]]
    right_angles = _right_angle_evidence(refinement.get("primitives", []))
    non_exact_right_angles = [item for item in right_angles if not item["exact"]]
    status = "pass" if not mismatches and not non_exact_right_angles else "fail"
    return {
        "schemaVersion": "graphic-vector-topology-verification-v1",
        "assetId": refinement["assetId"],
        "panelId": refinement["panelId"],
        "sourcePanel": str(source_panel.resolve()),
        "sourceTopology": source_summary,
        "redrawnTopology": redraw_summary,
        "rightAngleEvidence": right_angles,
        "gates": {
            "componentEndpointJunctionLoop": {"status": "pass" if not mismatches else "fail", "mismatches": mismatches},
            "exactDetectedRightAngles": {"status": "pass" if not non_exact_right_angles else "fail"},
            "textContentAndDirection": {"status": "pending", "reason": "semantic_or_ocr_evidence_required"},
            "frontendContract": {"status": "pending"},
            "finalAcceptance": {"status": "blocked", "reason": "render_comparison_and_contract_validation_required"},
        },
        "status": "topology_verified" if status == "pass" else "repair_pending",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refinement", type=Path, required=True)
    parser.add_argument("--source-panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_topology(json.loads(args.refinement.read_text(encoding="utf-8")), args.source_panel)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result["status"] == "topology_verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
