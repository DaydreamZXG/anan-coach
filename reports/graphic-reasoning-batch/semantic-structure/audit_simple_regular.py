#!/usr/bin/env python3
"""CPU-only preflight for narrowly defined simple regular families."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from build_simple_regular import grid_symbol_geometry_array, triangle_panels  # noqa: E402
from run_batch import read_jsonl, write_json  # noqa: E402


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def audit_closed_polygon(job: dict[str, Any]) -> dict[str, Any]:
    panels, evidence, errors = triangle_panels(job)
    visible = [panel for panel in panels if panel.get("role") != "missing"]
    evidence_instances = [
        instance
        for panel in evidence.get("panels", [])
        for instance in panel.get("instances") or []
    ]
    return {
        "assetId": job["assetId"],
        "sourceSha256": job["sourceSha256"],
        "questionReferenceCount": len(job.get("questionReferences") or []),
        "panelCount": job.get("layoutCandidate", {}).get("panelCount"),
        "optionCount": job.get("layoutCandidate", {}).get("optionCount"),
        "family": "four_hollow_right_triangles" if not errors else "closed_polygon_other",
        "preflight": "pass" if not errors else "repair_pending",
        "visiblePanels": len(visible),
        "triangleInstances": sum(len((panel.get("shapes") or [{}])[0].get("instances") or []) for panel in visible),
        "maxRightAngleErrorDegrees": max((float(item["rightAngleErrorDegrees"]) for item in evidence_instances), default=None),
        "errors": errors,
    }


def audit_grid(job: dict[str, Any]) -> dict[str, Any]:
    source = cv2.imread(str(job["sourcePaths"][0]), cv2.IMREAD_GRAYSCALE)
    layout = job.get("layoutCandidate", {})
    boxes = layout.get("panelBoxes") or []
    roles = layout.get("panelRoles") or []
    errors: list[str] = []
    panel_evidence: list[dict[str, Any]] = []
    if source is None:
        errors.append("source_unreadable")
    elif len(boxes) != layout.get("panelCount") or len(roles) != layout.get("panelCount"):
        errors.append("panel_layout")
    else:
        for index, (box, role) in enumerate(zip(boxes, roles)):
            position = index + 1
            if role == "missing":
                continue
            if not isinstance(box, dict):
                errors.append(f"p{position}:box")
                continue
            x, y = int(box["x"]), int(box["y"])
            width, height = int(box["width"]), int(box["height"])
            crop = source[y : y + height, x : x + width]
            symbols, grid_evidence, panel_errors = grid_symbol_geometry_array(crop, None, position)
            errors.extend(f"p{position}:{error}" for error in panel_errors)
            panel_evidence.append({
                "p": position,
                "horizontalLines": len(grid_evidence.get("horizontalLinePixels") or []),
                "verticalLines": len(grid_evidence.get("verticalLinePixels") or []),
                "symbolComponents": len(symbols),
                "cells": [symbol.get("cell") for symbol in symbols],
            })
    exact = not errors and bool(panel_evidence)
    return {
        "assetId": job["assetId"],
        "sourceSha256": job["sourceSha256"],
        "questionReferenceCount": len(job.get("questionReferences") or []),
        "panelCount": layout.get("panelCount"),
        "optionCount": layout.get("optionCount"),
        "family": "grid_4x4_two_symbol_components" if exact else "grid_partition_other",
        "preflight": "pass" if exact else "repair_pending",
        "visiblePanels": len(panel_evidence),
        "panelEvidence": panel_evidence,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=HERE / "queue.jsonl")
    parser.add_argument("--output-root", type=Path, default=HERE / "simple-family-preflight")
    parser.add_argument("--subtype", default="closed_polygon_composition")
    args = parser.parse_args()
    if args.subtype not in {"closed_polygon_composition", "grid_partition"}:
        raise SystemExit("v1 preflight supports closed_polygon_composition or grid_partition")
    jobs = [job for job in read_jsonl(args.queue) if job.get("regularSubtypeCandidate") == args.subtype]
    rows = [audit_closed_polygon(job) if args.subtype == "closed_polygon_composition" else audit_grid(job) for job in jobs]
    family_counts = Counter(row["family"] for row in rows)
    pass_family = "four_hollow_right_triangles" if args.subtype == "closed_polygon_composition" else "grid_4x4_two_symbol_components"
    state = {
        "schemaVersion": "graphic-reasoning-simple-family-preflight-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "cpuOnly": True,
        "subtype": args.subtype,
        "candidates": len(rows),
        "exactFamilyPass": family_counts[pass_family],
        "repairPending": len(rows) - family_counts[pass_family],
        "familyCounts": dict(sorted(family_counts.items())),
    }
    output_root = args.output_root.resolve()
    write_jsonl(output_root / f"{args.subtype}.jsonl", rows)
    write_json(output_root / f"{args.subtype}-summary.json", state)
    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
