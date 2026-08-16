#!/usr/bin/env python3
"""Attach the three already human-verified formula results without API calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
from PIL import Image, ImageDraw, ImageFont

import convert_visual_bricks as converter


ROOT = Path(__file__).resolve().parent
FORMULA_ROOT = ROOT.parent / "question-formula-batch"
SCHEMA = json.loads((ROOT / "structured-schema.json").read_text(encoding="utf-8"))
FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FALLBACKS = {
    "da_86833a053957b4cc90a002b3": "贡献率＝（报告期分类指数值－上年分类指数值）÷（报告期总指数值－上年总指数值）×100%",
    "da_880c89b11b550c716325080f": "Vₛ",
    "da_a5703206f0218f920b3d4f45": "Vɢ",
}


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def result_for(asset_id: str) -> dict[str, Any]:
    formula_id = "qi_" + asset_id.removeprefix("da_")
    return json.loads((FORMULA_ROOT / "results" / f"{formula_id}.json").read_text(encoding="utf-8"))


def render_fallback(text: str, width: int, height: int, output: Path) -> None:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    size = max(10, min(max(12, height // 3), max(12, width // max(4, len(text)) * 2)))
    try:
        font = ImageFont.truetype(FONT_PATH, size)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (box[2] - box[0])) / 2, (height - (box[3] - box[1])) / 2), text, fill="#111111", font=font)
    image.save(output)


def main() -> None:
    manifest = {entry["asset_id"]: entry for entry in converter.read_jsonl(ROOT / "manifest.jsonl")}
    for asset_id, fallback in FALLBACKS.items():
        formula = result_for(asset_id)
        entry = manifest[asset_id]
        source_path = Path(entry["source"]["local_path"])
        source_hash = converter.sha256_file(source_path)
        if formula["sha256"] != source_hash or formula["status"] != "verified_override":
            raise ValueError(f"{asset_id}: verified formula evidence mismatch")
        block = formula["blocks"][0]
        with Image.open(source_path) as source_image:
            width, height = source_image.size
        visual = {
            "schemaVersion": "semantic-formula-v1",
            "kind": "semanticFormula",
            "visualGrammar": "formula_expression",
            "fidelityTier": "semantic_vector",
            "layoutType": "single",
            "coordinateSpace": {"width": width, "height": height},
            "expression": block["expression"],
            "format": "latex",
            "display": block["display"],
            "fallbackText": fallback,
            "evidence": {
                "sourceAsset": asset_id,
                "sourceSha256": source_hash,
                "provider": "manual_verified",
                "converterVersion": "semantic-formula-v1",
                "renderSha256": "0" * 64,
            },
            "complexity": {
                "expressionLength": len(block["expression"]),
                "estimatedDrawCommandCount": max(1, len(block["expression"])),
            },
        }
        rendered_path = ROOT / "previews/structured_visual" / f"{asset_id}.png"
        render_fallback(fallback, width, height, rendered_path)
        visual["evidence"]["renderSha256"] = hashlib.sha256(rendered_path.read_bytes()).hexdigest()
        source_rgb = converter.source_rgb(source_path)
        rendered_rgb = converter.source_rgb(rendered_path)
        quality = converter.metrics(source_rgb, rendered_rgb)
        comparison_path = ROOT / "previews/structured_visual" / f"{asset_id}-comparison.png"
        converter.save_comparison(source_rgb, rendered_rgb, comparison_path, quality, right_title="VERIFIED SEMANTIC FORMULA", details=f"latex={block['expression']}")

        candidate_path = ROOT / "structured" / f"{asset_id}.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate.update({
            "chart_type": "structured_visual", "categories": [], "series": [], "table": None,
            "pie_geometry": None, "structured_visual": visual, "legend": [],
            "confidence": 1.0, "review_status": "verified_sample",
        })
        verification = candidate["verification"]
        verification["schema_valid"] = True
        verification["ocr_engine"] = "manual_verified_formula"
        verification["ocr_mean_confidence"] = 1.0
        verification["notes"] = [
            note for note in verification.get("notes", [])
            if not str(note).startswith(("converter:", "semantic_reconstruction_pending", "comparison_artifact:"))
        ] + ["converter:semantic-formula-v1", "manual_formula_result_reused:no_xfyun_api_call", f"comparison_artifact:{comparison_path.relative_to(ROOT)}"]
        jsonschema.validate(candidate, SCHEMA)
        write_json(candidate_path, candidate)

        result_path = ROOT / "work/results" / f"{asset_id}.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result.update({"chart_type":"structured_visual","status":"success","confidence":1.0,"errors":[],"comparison":{"changed":0,"change_ratio":0.0,"initial_values":1,"verified_values":1,"missing_to_zero":0}})
        write_json(result_path, result)
        print(f"{asset_id} {block['expression']} ok")


if __name__ == "__main__":
    main()
