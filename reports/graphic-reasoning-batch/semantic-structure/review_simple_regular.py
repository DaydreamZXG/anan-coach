#!/usr/bin/env python3
"""Promote only manually established representative visible-fact fixtures.

This is deliberately separate from extraction: schema-valid model output cannot
promote itself.  The fixtures below encode only facts visible in the source
images and never use the official answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from run_batch import write_json  # noqa: E402


TRIANGLE_ASSET = "gr_09a239f1f5c89bec86ff315d"
GRID_ASSET = "gr_814f04ca63ec75e0fd69a9fb"
REPRESENTATIVE_ASSETS = [
    "gr_7b00c758d0ab44e2c464e04f",
    "gr_06e94661142e35ee6757e42d",
    "gr_0751a8530a3a10bda058e654",
    TRIANGLE_ASSET,
    GRID_ASSET,
]
QUADRANTS = {"top_left", "top_right", "bottom_left", "bottom_right"}
RIGHT_ANGLE_CORNERS = QUADRANTS

# Manually checked against the source image.  These are input-independent
# visible-fact fixtures, not deductions from the answer key.
GRID_EXPECTED: dict[int, set[tuple[str, int, int, str]]] = {
    1: {("star", 1, 1, "none"), ("triangle", 3, 2, "up")},
    2: {("triangle", 2, 3, "down"), ("star", 4, 4, "none")},
    3: {("triangle", 2, 2, "down"), ("star", 4, 1, "none")},
    4: {("pentagon", 1, 3, "none"), ("arrow", 3, 1, "left")},
    5: {("arrow", 2, 4, "right"), ("pentagon", 4, 2, "none")},
    7: {("arrow", 1, 2, "right"), ("pentagon", 4, 3, "none")},
    8: {("arrow", 2, 1, "left"), ("pentagon", 4, 3, "none")},
    9: {("pentagon", 1, 2, "none"), ("arrow", 3, 4, "left")},
    10: {("pentagon", 1, 2, "none"), ("arrow", 3, 4, "right")},
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_hash_ok(solver: dict[str, Any]) -> bool:
    source = solver.get("source", {})
    paths = source.get("imagePaths") or []
    if not paths or not Path(paths[0]).is_file():
        return False
    return hashlib.sha256(Path(paths[0]).read_bytes()).hexdigest() == source.get("sha256")


def check(name: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "evidence": evidence}


def review_triangle(solver: dict[str, Any], geometry: dict[str, Any]) -> list[dict[str, Any]]:
    layout = solver.get("layout", {})
    panels = solver.get("panels", [])
    visible = [panel for panel in panels if panel.get("role") != "missing"]
    rows: list[dict[str, Any]] = [
        check("layout", layout.get("panelCount") == 9 and layout.get("optionCount") == 4, layout),
        check("visible_panel_count", len(visible) == 8, len(visible)),
    ]
    panel_facts: list[dict[str, Any]] = []
    all_pass = True
    for panel in visible:
        shapes = panel.get("shapes") or []
        group = shapes[0] if len(shapes) == 1 else {}
        instances = group.get("instances") or []
        regions = {item.get("region") for item in instances}
        valid = (
            group.get("kind") == "rightTriangle"
            and group.get("count") == 4
            and len(instances) == 4
            and regions == QUADRANTS
            and all(item.get("fill") == "hollow" for item in instances)
            and all(item.get("rightAngleAt") in RIGHT_ANGLE_CORNERS for item in instances)
            and panel.get("topology") == {"components": 4, "endpoints": 0, "junctions": 0, "loops": 4}
        )
        all_pass = all_pass and valid
        panel_facts.append({"p": panel.get("p"), "regions": sorted(str(item) for item in regions), "instances": len(instances), "pass": valid})
    rows.append(check("four_hollow_right_triangles_per_panel", all_pass, panel_facts))
    errors = [
        float(instance.get("rightAngleErrorDegrees", 999))
        for panel in geometry.get("panels", [])
        for instance in panel.get("instances") or []
    ]
    rows.append(check("right_angle_fit", len(errors) == 32 and max(errors, default=999) <= 2.0, {"count": len(errors), "maxErrorDegrees": max(errors, default=None)}))
    return rows


def review_grid(solver: dict[str, Any], geometry: dict[str, Any]) -> list[dict[str, Any]]:
    layout = solver.get("layout", {})
    panels = solver.get("panels", [])
    by_position = {int(panel["p"]): panel for panel in panels if isinstance(panel.get("p"), int)}
    rows: list[dict[str, Any]] = [
        check("layout", layout.get("layoutType") == "matrix" and layout.get("panelCount") == 10 and layout.get("optionCount") == 4, layout),
        check("missing_slot", by_position.get(6, {}).get("role") == "missing" and by_position.get(6, {}).get("symbols") == [], by_position.get(6)),
    ]
    observed: dict[int, set[tuple[str, int, int, str]]] = {}
    frames_pass = True
    hollow_pass = True
    for position in GRID_EXPECTED:
        panel = by_position.get(position, {})
        frame = panel.get("frame") or {}
        frames_pass = frames_pass and frame == {"kind": "grid", "rows": 4, "columns": 4}
        symbols = panel.get("symbols") or []
        hollow_pass = hollow_pass and len(symbols) == 2 and all(symbol.get("fill") == "hollow" for symbol in symbols)
        observed[position] = {
            (str(symbol.get("kind")), int(symbol.get("cell", [0, 0])[0]), int(symbol.get("cell", [0, 0])[1]), str(symbol.get("orientation")))
            for symbol in symbols
            if isinstance(symbol.get("cell"), list) and len(symbol["cell"]) == 2
        }
    rows.append(check("exact_4x4_frames", frames_pass, {"visiblePanels": len(GRID_EXPECTED)}))
    rows.append(check("two_hollow_symbols", hollow_pass, {"visiblePanels": len(GRID_EXPECTED)}))
    mismatches = {
        str(position): {"expected": sorted(GRID_EXPECTED[position]), "observed": sorted(observed.get(position, set()))}
        for position in GRID_EXPECTED
        if observed.get(position) != GRID_EXPECTED[position]
    }
    rows.append(check("symbol_kind_cell_orientation", not mismatches, {"mismatches": mismatches, "checkedSymbols": 18}))
    geometry_rows = geometry.get("panels") or []
    cv_pass = (
        len(geometry_rows) == 9
        and all(len(panel.get("horizontalLinePixels") or []) == 5 for panel in geometry_rows)
        and all(len(panel.get("verticalLinePixels") or []) == 5 for panel in geometry_rows)
        and all(len(panel.get("symbols") or []) == 2 for panel in geometry_rows)
    )
    rows.append(check("deterministic_grid_and_components", cv_pass, {"visiblePanels": len(geometry_rows), "components": sum(len(panel.get("symbols") or []) for panel in geometry_rows)}))
    return rows


def review_asset(asset_root: Path, asset_id: str) -> dict[str, Any]:
    solver_path = asset_root / "solver.json"
    geometry_path = asset_root / "geometry-evidence.json"
    acceptance_path = asset_root / "acceptance.json"
    missing = [path.name for path in (solver_path, geometry_path, acceptance_path) if not path.is_file()]
    if missing:
        return {"assetId": asset_id, "status": "repair_pending", "errors": [f"missing:{name}" for name in missing]}
    solver = read_json(solver_path)
    geometry = read_json(geometry_path)
    acceptance = read_json(acceptance_path)
    checks = [
        check("asset_identity", solver.get("assetId") == asset_id, solver.get("assetId")),
        check("source_sha256", source_hash_ok(solver), solver.get("source", {}).get("sha256")),
        check("original_image_presentation", solver.get("presentation", {}).get("mode") == "original_image", solver.get("presentation")),
        check("answer_isolation", acceptance.get("solverInputExcludesAnswer") is True, acceptance.get("solverInputExcludesAnswer")),
    ]
    if asset_id == TRIANGLE_ASSET:
        checks.extend(review_triangle(solver, geometry))
    elif asset_id == GRID_ASSET:
        checks.extend(review_grid(solver, geometry))
    else:
        checks.append(check("fixture_available", False, "no representative fixture"))
    visible_facts_passed = all(item["status"] == "pass" for item in checks)
    evaluation_path = asset_root / "solver-evaluation.json"
    evaluation = read_json(evaluation_path) if evaluation_path.is_file() else {}
    checks.append(check(
        "semantic_answer_preservation",
        evaluation.get("semanticJsonCorrect") is True,
        {
            "evaluationPresent": evaluation_path.is_file(),
            "semanticJsonCorrect": evaluation.get("semanticJsonCorrect"),
            "originalImageCorrect": evaluation.get("originalImageCorrect"),
        },
    ))
    passed = all(item["status"] == "pass" for item in checks)
    review = {
        "schemaVersion": "graphic-reasoning-representative-visible-fact-review-v1",
        "assetId": asset_id,
        "reviewedAt": datetime.now(timezone.utc).isoformat(),
        "method": "manual_source_fixture_plus_deterministic_assertions",
        "answerUsed": False,
        "visibleFactsStatus": "pass" if visible_facts_passed else "fail",
        "status": "semantic_solver_validated" if passed else "repair_pending",
        "checks": checks,
    }
    write_json(asset_root / "fact-review.json", review)
    if passed:
        acceptance.update({
            "status": "semantic_solver_validated",
            "qualityStatus": "representative_visible_facts_and_answer_preservation_pass",
            "repairReasons": [],
            "factReview": "pass",
            "scopeLimit": "representative_only_original_image_presentation",
        })
    else:
        acceptance.update({
            "status": "repair_pending",
            "qualityStatus": "visible_fact_review_failed",
            "repairReasons": [item["name"] for item in checks if item["status"] == "fail"],
            "factReview": "fail",
        })
    write_json(acceptance_path, acceptance)
    return {"assetId": asset_id, "status": review["status"], "failedChecks": [item["name"] for item in checks if item["status"] == "fail"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=HERE / "model-benchmarks" / "gemma-4-31b-it")
    parser.add_argument("--asset", action="append", default=[])
    args = parser.parse_args()
    asset_ids = args.asset or [TRIANGLE_ASSET, GRID_ASSET]
    rows = [review_asset(args.output_root.resolve() / "assets" / asset_id, asset_id) for asset_id in asset_ids]
    state = {
        "schemaVersion": "graphic-reasoning-representative-review-state-v1",
        "reviewedAt": datetime.now(timezone.utc).isoformat(),
        "reviewed": len(rows),
        "semanticSolverValidated": sum(row["status"] == "semantic_solver_validated" for row in rows),
        "repairPending": sum(row["status"] != "semantic_solver_validated" for row in rows),
        "rows": rows,
    }
    write_json(args.output_root.resolve() / "fact-review-state.json", state)
    representative_rows: list[dict[str, Any]] = []
    for asset_id in REPRESENTATIVE_ASSETS:
        acceptance_path = args.output_root.resolve() / "assets" / asset_id / "acceptance.json"
        acceptance = read_json(acceptance_path) if acceptance_path.is_file() else {}
        representative_rows.append({
            "assetId": asset_id,
            "status": acceptance.get("status", "repair_pending"),
            "qualityStatus": acceptance.get("qualityStatus", "missing"),
        })
    representative_state = {
        "schemaVersion": "graphic-reasoning-representative-semantic-state-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "assets": len(representative_rows),
        "semanticSolverValidated": sum(row["status"] == "semantic_solver_validated" for row in representative_rows),
        "repairPending": sum(row["status"] != "semantic_solver_validated" for row in representative_rows),
        "rows": representative_rows,
    }
    write_json(args.output_root.resolve() / "representative-state.json", representative_state)
    print(json.dumps(state, ensure_ascii=False))
    return 0 if state["repairPending"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
