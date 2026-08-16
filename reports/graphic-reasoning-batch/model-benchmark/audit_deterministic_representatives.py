#!/usr/bin/env python3
"""Apply panel-level pixel/topology gates to deterministic representative redraws."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from candidate_ingest import CANDIDATE_SCHEMA
from verify_candidate import verify_candidate
from verify_topology import verify_topology


HERE = Path(__file__).resolve().parent
BATCH_ROOT = HERE.parent


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def render_semantic_families(primitives: list[dict[str, Any]]) -> set[str]:
    kinds = {str(item.get("type")) for item in primitives}
    families: set[str] = set()
    if kinds & {"line", "polyline"}:
        families.add("straight_stroke")
    if kinds & {"quadraticBezier", "cubicBezier", "arc", "ellipse"}:
        families.add("curved_stroke")
    if "circle" in kinds:
        families.add("circle")
    if kinds & {"polygon", "face"}:
        families.add("closed_polygon")
    horizontal = sum(
        item.get("type") == "line" and abs(float(item["from"]["y"]) - float(item["to"]["y"])) <= 4
        for item in primitives
    )
    vertical = sum(
        item.get("type") == "line" and abs(float(item["from"]["x"]) - float(item["to"]["x"])) <= 4
        for item in primitives
    )
    if horizontal >= 2 and vertical >= 2:
        families.add("grid")
    return families


def semantic_required_families(candidate: dict[str, Any]) -> set[str]:
    families: set[str] = set()
    for element in candidate.get("elements", []):
        kind = element.get("kind")
        if kind in {"line", "polyline", "rect"}:
            families.add("straight_stroke")
        elif kind in {"curve", "arc", "ellipse"}:
            families.add("curved_stroke")
        elif kind == "circle":
            families.add("circle")
        elif kind in {"polygon", "face", "textureSector"}:
            families.add("closed_polygon")
        elif kind == "grid":
            families.add("grid")
    return families


def aggregate_panel_gates(panel_rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in panel_rows if row.get("status") != "skipped_missing_marker"]
    def gate(name: str) -> dict[str, Any]:
        failures = [row["panelId"] for row in evaluated if row["gates"].get(name) != "pass"]
        return {"status": "pass" if not failures else "fail", "failedPanels": failures}
    return {
        "pixelFit": gate("pixelFit"),
        "duplicateSuppression": gate("duplicateSuppression"),
        "solidFillOnly": gate("solidFillOnly"),
        "topology": gate("topology"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=HERE / "inputs/index.jsonl")
    parser.add_argument("--manifest", type=Path, default=BATCH_ROOT / "microbatches/gpu_ready_representatives_v1/manifest.jsonl")
    parser.add_argument("--semantic-root", type=Path, default=HERE / "semantic-candidates")
    parser.add_argument("--output-root", type=Path, default=HERE / "deterministic-audit")
    args = parser.parse_args()
    inputs = read_jsonl(args.inputs)
    manifest = {row["assetId"]: row for row in read_jsonl(args.manifest)}
    summaries: list[dict[str, Any]] = []
    for asset in inputs:
        asset_id = asset["assetId"]
        generated = manifest.get(asset_id)
        if generated is None:
            summaries.append({"assetId": asset_id, "status": "repair_pending", "error": "microbatch_manifest_missing"})
            continue
        source_path = Path(asset["sourcePath"])
        structured = json.loads(Path(generated["structured"]).read_text(encoding="utf-8"))
        acceptance = json.loads(Path(generated["acceptance"]).read_text(encoding="utf-8"))
        panels = structured.get("panels", [])
        panel_rows: list[dict[str, Any]] = []
        for index, panel_input in enumerate(asset.get("panels", [])):
            panel_id = panel_input["id"]
            if panel_input.get("modelPolicy") == "skip_missing_marker":
                panel_rows.append({"panelId": panel_id, "status": "skipped_missing_marker"})
                continue
            if index >= len(panels):
                panel_rows.append({"panelId": panel_id, "status": "repair_pending", "gates": {"pixelFit": "fail", "duplicateSuppression": "fail", "solidFillOnly": "fail", "topology": "fail"}, "reasons": ["structured_panel_missing"]})
                continue
            primitives = panels[index].get("primitives", [])
            candidate = {
                "schemaVersion": CANDIDATE_SCHEMA,
                "assetId": asset_id,
                "panelId": panel_id,
                "primitives": primitives,
                "evidence": {"candidateSvgSha256": None},
            }
            verification = verify_candidate(candidate, Path(panel_input["modelInput"]))
            refinement = {
                "schemaVersion": "graphic-vector-deterministic-refinement-v1",
                "assetId": asset_id,
                "panelId": panel_id,
                "primitives": primitives,
            }
            topology = verify_topology(refinement, Path(panel_input["modelInput"]))
            semantic_path = args.semantic_root / asset_id / f"{panel_id}.json"
            semantic_gate = {"status": "pending", "reason": "semantic_candidate_not_run_for_this_panel"}
            if semantic_path.is_file():
                semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
                if semantic.get("status") == "valid_semantic_candidate":
                    required = semantic_required_families(semantic.get("candidate", {}))
                    rendered = render_semantic_families(primitives)
                    missing = sorted(required - rendered)
                    semantic_gate = {"status": "pass" if not missing else "fail", "requiredFamilies": sorted(required), "renderedFamilies": sorted(rendered), "missingFamilies": missing}
                else:
                    semantic_gate = {"status": "fail", "reason": "semantic_candidate_invalid"}
            panel_row = {
                "panelId": panel_id,
                "status": "repair_pending",
                "primitiveCount": len(primitives),
                "gates": {
                    "pixelFit": verification["gates"]["pixelSupport"]["status"],
                    "duplicateSuppression": verification["gates"]["duplicateSuppression"]["status"],
                    "solidFillOnly": verification["gates"]["solidFillOnly"]["status"],
                    "topology": topology["gates"]["componentEndpointJunctionLoop"]["status"],
                    "semanticFamilies": semantic_gate["status"],
                },
                "pixelEvidence": verification["aggregate"],
                "duplicateGeometry": verification["duplicateGeometry"],
                "sourceTopology": topology["sourceTopology"],
                "redrawnTopology": topology["redrawnTopology"],
                "semanticEvidence": semantic_gate,
            }
            write_json(args.output_root / asset_id / f"{panel_id}.json", panel_row)
            panel_rows.append(panel_row)
        aggregate = aggregate_panel_gates(panel_rows)
        first_semantic = next((row for row in panel_rows if row.get("status") != "skipped_missing_marker"), None)
        first_semantic_status = first_semantic.get("gates", {}).get("semanticFamilies", "pending") if first_semantic else "pending"
        gates = {
            "sourceHash": {"status": "pass" if sha256(source_path) == asset["sourceSha256"] else "fail"},
            "panelCount": {"status": "pass" if acceptance.get("detectedPanelCount") == asset["deterministicPanelEvidence"]["panelCount"] == len(panels) else "fail"},
            "optionCount": {"status": "pass" if acceptance.get("detectedOptionCount") == asset["deterministicPanelEvidence"]["optionCount"] else "fail"},
            **aggregate,
            "firstPanelSemanticFamilies": {"status": first_semantic_status},
            "remainingPanelSemantics": {"status": "pending"},
            "text": {"status": "pending"},
            "direction": {"status": "pending"},
            "exactAngles": {"status": "pending"},
            "answerPreservation": {"status": "pending"},
            "frontendContract": {"status": "pending"},
        }
        failures = sorted(key for key, gate in gates.items() if gate["status"] == "fail")
        summary = {
            "schemaVersion": "graphic-deterministic-representative-audit-v1",
            "assetId": asset_id,
            "sourceSha256": asset["sourceSha256"],
            "regularSubtype": asset["regularSubtype"],
            "panelCount": len(asset.get("panels", [])),
            "evaluatedPanelCount": sum(row.get("status") != "skipped_missing_marker" for row in panel_rows),
            "skippedMissingMarkerCount": sum(row.get("status") == "skipped_missing_marker" for row in panel_rows),
            "gates": gates,
            "failedGates": failures,
            "status": "repair_pending",
            "finalAccepted": False,
            "reason": "failed gates require repair; pending semantic/answer/frontend gates prohibit final acceptance even when geometry passes",
        }
        write_json(args.output_root / asset_id / "asset-audit.json", summary)
        summaries.append(summary)
    state = {
        "schemaVersion": "graphic-deterministic-representative-run-v1",
        "assets": len(summaries),
        "finalAcceptedAssets": 0,
        "repairPendingAssets": len(summaries),
        "assetsWithGeometryFailures": sum(bool(row.get("failedGates")) for row in summaries),
        "rows": summaries,
    }
    write_json(args.output_root / "audit-state.json", state)
    print(json.dumps({key: state[key] for key in ("assets", "finalAcceptedAssets", "repairPendingAssets", "assetsWithGeometryFailures")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
