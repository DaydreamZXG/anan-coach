#!/usr/bin/env python3
"""Merge context-verified graphic glyph images missed by the legacy tex filter."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import cpu_audit  # noqa: E402
import pipeline  # noqa: E402


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


def normalized_inventory_row(record: dict[str, Any]) -> dict[str, Any]:
    paths = sorted(record["localPaths"])
    return {
        "assetId": record["assetId"],
        "sha256": record["sha256"],
        "kind": record["kind"],
        "localPaths": paths,
        "sizeBytes": Path(paths[0]).stat().st_size if paths and Path(paths[0]).is_file() else None,
        "occurrences": sorted(record["occurrences"], key=lambda item: (item["questionId"], item["role"], item["field"], item["sourceJsonlLine"])),
    }


def glyph_classification(row: dict[str, Any]) -> dict[str, Any]:
    source = Path(row["localPaths"][0])
    gray = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"decode_failed:{source}")
    boxes, panel_info = pipeline.detect_panels(gray)
    classification = pipeline.classify_categories(row, gray, boxes)
    categories = ["glyph", *[category for category in classification.get("categories", []) if category != "glyph"]]
    classification.update({
        "assetId": row["assetId"],
        "sha256": row["sha256"],
        "sourcePath": str(source),
        "categories": categories[:4],
        "semanticSource": "question_context+deterministic_cv_no_model",
        "visualGrammar": "glyph_life_object",
        "grammarSource": "recovered_jiangsu_character_reasoning_context",
        "grammarRenderer": ["textGlyph", "line_circle_polygon"],
        "grammarStatus": "routed_pending_renderer",
        "regularSubtype": "glyph_geometry_composition",
        "regularSubtypeSource": "question_context",
        "regularSubtypeConfidence": 1.0,
        "regularSubtypeRenderer": ["textGlyph", "line_circle_polygon"],
        "regularSubtypeStatus": "semantic_or_renderer_pending",
        "scopeDecision": "misflagged_tex_graphic_restored",
        "panelDetection": panel_info,
    })
    return classification


def main() -> int:
    summary, rescanned, _, _ = cpu_audit.rescan_source()
    inventory_path = ROOT / "inventory.jsonl"
    classification_path = ROOT / "classification.jsonl"
    existing_inventory = {row["assetId"]: row for row in read_jsonl(inventory_path)}
    existing_classification = {row["assetId"]: row for row in read_jsonl(classification_path)}
    recovered_ids = sorted(set(rescanned) - set(existing_inventory))
    if not recovered_ids and len(existing_inventory) == summary["uniqueContentSha256Assets"]:
        print(json.dumps({"status": "already_recovered", "assets": 0}, ensure_ascii=False))
        return 0
    recovered_rows = [normalized_inventory_row(rescanned[asset_id]) for asset_id in recovered_ids]
    for row in recovered_rows:
        existing_inventory[row["assetId"]] = row
        existing_classification[row["assetId"]] = glyph_classification(row)
    write_jsonl(inventory_path, [existing_inventory[key] for key in sorted(existing_inventory)])
    write_jsonl(classification_path, [existing_classification[key] for key in sorted(existing_classification)])
    classifications = list(existing_classification.values())
    category_counts = Counter(category for row in classifications for category in row.get("categories", []))
    grammar_counts = Counter(str(row.get("visualGrammar") or "unrouted") for row in classifications)
    subtype_counts = Counter(str(row.get("regularSubtype") or "") for row in classifications if row.get("regularSubtype"))
    summary_path = ROOT / "summary.json"
    current = json.loads(summary_path.read_text(encoding="utf-8"))
    current.update({
        "uniqueAssets": len(existing_inventory),
        "resolvedImageAssets": len(existing_inventory),
        "classifiedAssets": len(existing_classification),
        "questionsWithGraphicImage": summary["questionsWithGraphicImage"],
        "referencesByRole": summary["referencesByRole"],
        "classificationCategories": dict(sorted(category_counts.items())),
        "grammarRouting": dict(sorted(grammar_counts.items())),
        "regularSubtypeRouting": dict(sorted(subtype_counts.items())),
        "recoveredMisflaggedGlyphAssets": len(recovered_rows),
        "textGlyphOnlyQuestions": summary["questionsWithoutGraphicImage"],
        "finalAcceptedAssets": 0,
    })
    write_json(summary_path, current)
    write_json(ROOT / "cpu-audit/glyph-recovery.json", {
        "schemaVersion": "graphic-reasoning-glyph-recovery-v1",
        "status": "recovered",
        "recoveredAssetCount": len(recovered_rows),
        "recoveredAssetIds": recovered_ids,
        "inventoryAssetsAfterRecovery": len(existing_inventory),
        "classifiedAssetsAfterRecovery": len(existing_classification),
        "textGlyphOnlyQuestions": summary["questionsWithoutGraphicImage"],
        "gpuCalls": 0,
        "finalAcceptedAssets": 0,
    })
    print(json.dumps({"status": "recovered", "assets": len(recovered_rows), "inventory": len(existing_inventory)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
