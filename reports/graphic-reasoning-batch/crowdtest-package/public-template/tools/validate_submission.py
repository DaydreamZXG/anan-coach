#!/usr/bin/env python3
"""Validate coverage, identity, semantic structure, and answer isolation.

The validator intentionally does not award visual correctness. That requires
blind downstream solving and human comparison against the source image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


FORBIDDEN_KEYS = {
    "answer",
    "answerlabel",
    "correctanswer",
    "correctoption",
    "finalanswer",
    "officialanswer",
    "selectedoption",
    "solution",
    "analysisanswer",
    "bbox",
    "base64",
    "coordinates",
    "pixels",
    "points",
    "svg",
    "svgpath",
    "vectorpath",
    "path",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "width",
    "height",
}
ANSWER_PATTERNS = [
    re.compile(r"正确答案\s*(?:是|为|[:：])?\s*[A-P]", re.I),
    re.compile(r"答案\s*(?:是|为|[:：])\s*[A-P]", re.I),
    re.compile(r"(?:correct|final)\s+answer\s*(?:is|:)?\s*[A-P]", re.I),
    re.compile(r"(?:choose|select)\s+(?:option\s+)?[A-P]\b", re.I),
    re.compile(r"最终(?:选择|选项)\s*[A-P]", re.I),
]
ALLOWED_KINDS = {
    "line", "polyline", "polygon", "rect", "circle", "ellipse", "arc",
    "curve", "dot", "grid", "arrow", "textGlyph", "lifeObject",
    "objectPart", "wireframe", "face", "textureSector", "semanticSymbol",
}
ALLOWED_ROLES = {"stem", "sequence", "missing", "option", "sample"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk(value: Any, location: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in FORBIDDEN_KEYS:
                errors.append(f"forbidden key at {location}.{key}")
            walk(child, f"{location}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk(child, f"{location}[{index}]", errors)
    elif isinstance(value, str):
        for pattern in ANSWER_PATTERNS:
            if pattern.search(value):
                errors.append(f"answer-like statement at {location}")
                break


def require_keys(value: dict[str, Any], keys: set[str], location: str, errors: list[str]) -> None:
    missing = sorted(keys - set(value))
    if missing:
        errors.append(f"{location}: missing keys {missing}")


def validate_asset(doc: dict[str, Any], expected: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "schemaVersion", "assetId", "source", "presentation", "layout",
        "visualGrammar", "panels", "reasoningCandidates",
        "globalUncertainties", "extraction",
    }
    require_keys(doc, required, "$", errors)
    if doc.get("schemaVersion") != "graphic-reasoning-semantic-scene-v2":
        errors.append("wrong schemaVersion")
    if doc.get("assetId") != expected["assetId"]:
        errors.append("assetId does not match inventory")
    source = doc.get("source", {})
    if not isinstance(source, dict):
        errors.append("source must be an object")
        source = {}
    if source.get("sha256") != expected["sourceSha256"]:
        errors.append("source.sha256 does not match inventory")
    if source.get("imagePath") != expected["imagePath"]:
        errors.append("source.imagePath does not match inventory")
    image_path = root / expected["imagePath"]
    if not image_path.is_file():
        errors.append("referenced source image is missing")
    elif sha256_file(image_path) != expected["sourceSha256"]:
        errors.append("referenced source image hash mismatch")

    presentation = doc.get("presentation", {})
    if presentation != {"mode": "original_image", "vectorRequired": False}:
        errors.append("presentation must keep the original image and disable required vector rendering")

    layout = doc.get("layout", {})
    panels = doc.get("panels", [])
    if not isinstance(layout, dict) or not isinstance(panels, list) or not panels:
        errors.append("layout and non-empty panels are required")
        panels = []
        layout = layout if isinstance(layout, dict) else {}
    if layout.get("panelCount") != len(panels):
        errors.append("layout.panelCount does not equal panels length")

    option_labels: list[str] = []
    panel_ids: set[str] = set()
    for index, panel in enumerate(panels, 1):
        location = f"$.panels[{index - 1}]"
        if not isinstance(panel, dict):
            errors.append(f"{location}: must be an object")
            continue
        require_keys(panel, {"id", "position", "role", "optionLabel", "frame", "visualFacts", "uncertainties"}, location, errors)
        if panel.get("position") != index:
            errors.append(f"{location}: position must be {index}")
        panel_id = panel.get("id")
        if panel_id in panel_ids or panel_id != f"p{index}":
            errors.append(f"{location}: panel id must be unique and sequential")
        panel_ids.add(panel_id)
        role = panel.get("role")
        if role not in ALLOWED_ROLES:
            errors.append(f"{location}: invalid role")
        label = panel.get("optionLabel")
        if role == "option":
            if not isinstance(label, str) or not re.fullmatch(r"[A-P]", label):
                errors.append(f"{location}: option panel needs A-P optionLabel")
            else:
                option_labels.append(label)
        elif label is not None:
            errors.append(f"{location}: non-option panel must have null optionLabel")

        facts = panel.get("visualFacts", {})
        if not isinstance(facts, dict):
            errors.append(f"{location}.visualFacts: must be an object")
            continue
        require_keys(facts, {"elements", "relations", "topology", "counts", "observations"}, f"{location}.visualFacts", errors)
        for element_index, element in enumerate(facts.get("elements", [])):
            element_location = f"{location}.visualFacts.elements[{element_index}]"
            if not isinstance(element, dict):
                errors.append(f"{element_location}: must be an object")
                continue
            require_keys(element, {"id", "kind", "label", "textContent", "count", "instances", "parts", "attributes", "confidence"}, element_location, errors)
            if element.get("kind") not in ALLOWED_KINDS:
                errors.append(f"{element_location}: invalid kind")
            instances = element.get("instances", [])
            if not isinstance(instances, list) or element.get("count") != len(instances):
                errors.append(f"{element_location}: count must equal instances length")
            kind = element.get("kind")
            for instance_index, instance in enumerate(instances if isinstance(instances, list) else []):
                if not isinstance(instance, dict):
                    errors.append(f"{element_location}.instances[{instance_index}]: must be an object")
                    continue
                line_character = instance.get("lineCharacter")
                if kind == "line" and line_character != "straight":
                    errors.append(f"{element_location}: line must use straight lineCharacter")
                if kind in {"arc", "curve", "circle", "ellipse"} and line_character != "curved":
                    errors.append(f"{element_location}: curved primitive must use curved lineCharacter")

    if layout.get("optionCount") != len(option_labels):
        errors.append("layout.optionCount does not equal option panel count")
    if option_labels and option_labels != [chr(65 + i) for i in range(len(option_labels))]:
        errors.append("option labels must be consecutive from A in panel order")

    extraction = doc.get("extraction", {})
    if not isinstance(extraction, dict) or extraction.get("sourceInspected") is not True:
        errors.append("extraction.sourceInspected must be true")
    else:
        quality = extraction.get("qualityStatus")
        repairs = extraction.get("repairReasons")
        if quality not in {"structured_candidate", "repair_pending"}:
            errors.append("invalid extraction.qualityStatus")
        if quality == "repair_pending" and not repairs:
            errors.append("repair_pending requires repairReasons")

    walk(doc, "$", errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    report_path = args.report or output / "validation-report.json"

    try:
        inventory_rows = load_jsonl(root / "input" / "inventory-public.jsonl")
        manifest_rows = load_jsonl(output / "manifest.jsonl")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    inventory = {row.get("assetId"): row for row in inventory_rows}
    manifest = {row.get("assetId"): row for row in manifest_rows}
    global_errors: list[str] = []
    if len(inventory) != len(inventory_rows):
        global_errors.append("inventory has duplicate assetId")
    if len(manifest) != len(manifest_rows):
        global_errors.append("manifest has duplicate assetId")
    missing_manifest = sorted(set(inventory) - set(manifest))
    extra_manifest = sorted(set(manifest) - set(inventory))
    if missing_manifest:
        global_errors.append(f"manifest missing {len(missing_manifest)} assets")
    if extra_manifest:
        global_errors.append(f"manifest has {len(extra_manifest)} unknown assets")

    asset_results: list[dict[str, Any]] = []
    error_counter: Counter[str] = Counter()
    for asset_id, expected in inventory.items():
        asset_path = output / "assets" / f"{asset_id}.json"
        errors: list[str] = []
        quality_status = None
        if not asset_path.is_file():
            errors.append("output asset file missing")
        elif asset_path.stat().st_size > 2 * 1024 * 1024:
            errors.append("output asset exceeds 2 MiB; likely contains non-semantic data")
        else:
            try:
                doc = json.loads(asset_path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    errors.append("output asset must be a JSON object")
                else:
                    errors.extend(validate_asset(doc, expected, root))
                    extraction = doc.get("extraction")
                    if isinstance(extraction, dict):
                        quality_status = extraction.get("qualityStatus")
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"cannot parse output asset: {exc}")
        manifest_row = manifest.get(asset_id)
        if not isinstance(manifest_row, dict):
            errors.append("manifest row missing")
        else:
            manifest_required = {"assetId", "sourceSha256", "outputPath", "schemaStatus", "qualityStatus", "errorCodes"}
            require_keys(manifest_row, manifest_required, "manifest", errors)
            if set(manifest_row) - manifest_required:
                errors.append("manifest row has unexpected keys")
            if manifest_row.get("sourceSha256") != expected["sourceSha256"]:
                errors.append("manifest sourceSha256 mismatch")
            if manifest_row.get("outputPath") != f"output/assets/{asset_id}.json":
                errors.append("manifest outputPath mismatch")
            if manifest_row.get("schemaStatus") != "valid":
                errors.append("manifest schemaStatus must be valid")
            if quality_status is not None and manifest_row.get("qualityStatus") != quality_status:
                errors.append("manifest qualityStatus mismatch")
            if not isinstance(manifest_row.get("errorCodes"), list):
                errors.append("manifest errorCodes must be an array")
            walk(manifest_row, "manifest", errors)

        for error in errors:
            error_counter[error.split(":", 1)[0]] += 1
        asset_results.append({"assetId": asset_id, "valid": not errors, "errors": errors})

    required_files = [output / "errors.jsonl", output / "run-state.json", output / "README.md"]
    for required_file in required_files:
        if not required_file.is_file():
            global_errors.append(f"missing required output file: {required_file.name}")

    try:
        error_rows = load_jsonl(output / "errors.jsonl") if (output / "errors.jsonl").is_file() else []
        for row in error_rows:
            if row.get("assetId") not in inventory:
                global_errors.append("errors.jsonl contains unknown assetId")
            require_keys(row, {"assetId", "code", "message", "recoverable"}, "errors.jsonl row", global_errors)
            if set(row) - {"assetId", "code", "message", "recoverable"}:
                global_errors.append("errors.jsonl row has unexpected keys")
            walk(row, "errors.jsonl", global_errors)
    except ValueError as exc:
        global_errors.append(str(exc))

    run_state_path = output / "run-state.json"
    if run_state_path.is_file():
        try:
            run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
            required_state = {"schemaVersion", "totalAssets", "processedAssets", "structuredCandidateCount", "repairPendingCount", "completed", "updatedAt", "notes"}
            if not isinstance(run_state, dict):
                global_errors.append("run-state.json must contain an object")
            else:
                require_keys(run_state, required_state, "run-state", global_errors)
                if set(run_state) - required_state:
                    global_errors.append("run-state has unexpected keys")
                if run_state.get("schemaVersion") != "graphic-reasoning-run-state-v1":
                    global_errors.append("run-state has wrong schemaVersion")
                if run_state.get("totalAssets") != len(inventory) or run_state.get("processedAssets") != len(inventory):
                    global_errors.append("run-state counts do not match full inventory")
                if run_state.get("structuredCandidateCount", 0) + run_state.get("repairPendingCount", 0) != len(inventory):
                    global_errors.append("run-state quality counts do not sum to inventory")
                if run_state.get("completed") is not True:
                    global_errors.append("run-state.completed must be true")
                walk(run_state, "run-state", global_errors)
        except (OSError, json.JSONDecodeError) as exc:
            global_errors.append(f"cannot parse run-state.json: {exc}")

    valid_count = sum(1 for row in asset_results if row["valid"])
    report = {
        "schemaVersion": "graphic-reasoning-validation-report-v1",
        "expectedAssetCount": len(inventory),
        "manifestAssetCount": len(manifest),
        "validAssetCount": valid_count,
        "invalidAssetCount": len(inventory) - valid_count,
        "coverageRate": valid_count / len(inventory) if inventory else 0,
        "globalErrors": global_errors,
        "errorSummary": dict(error_counter.most_common()),
        "assetResults": asset_results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["expectedAssetCount", "manifestAssetCount", "validAssetCount", "invalidAssetCount", "coverageRate", "globalErrors"]}, ensure_ascii=False, indent=2))
    return 0 if not global_errors and valid_count == len(inventory) else 1


if __name__ == "__main__":
    raise SystemExit(main())
