#!/usr/bin/env python3
"""Prepare a resumable full-asset semantic queue without model calls."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUESTION_BANK = Path("/Users/zxg/Documents/新项目/data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613/paper_questions.jsonl")
REPRESENTATIVE_ORDER = [
    "gr_7b00c758d0ab44e2c464e04f",
    "gr_06e94661142e35ee6757e42d",
    "gr_0751a8530a3a10bda058e654",
    "gr_09a239f1f5c89bec86ff315d",
    "gr_814f04ca63ec75e0fd69a9fb",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def cluster_box_rows(boxes: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cluster CV boxes into visual rows while tolerating small y jitter."""
    valid = [dict(box) for box in boxes if all(isinstance(box.get(key), (int, float)) for key in ("x", "y", "width", "height"))]
    if len(valid) != len(boxes) or not valid:
        return [valid] if valid else []
    tolerance = max(4.0, statistics.median(float(box["height"]) for box in valid) * 0.35)
    rows: list[list[dict[str, Any]]] = []
    row_centers: list[float] = []
    for box in sorted(valid, key=lambda item: (float(item["y"]) + float(item["height"]) / 2, float(item["x"]))):
        center_y = float(box["y"]) + float(box["height"]) / 2
        if not rows or abs(center_y - row_centers[-1]) > tolerance:
            rows.append([box])
            row_centers.append(center_y)
        else:
            rows[-1].append(box)
            row_centers[-1] = sum(float(item["y"]) + float(item["height"]) / 2 for item in rows[-1]) / len(rows[-1])
    return [sorted(row, key=lambda item: float(item["x"])) for row in rows]


def row_major_boxes(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order CV boxes from left to right, then top to bottom."""
    return [box for row in cluster_box_rows(boxes) for box in row]


def main() -> int:
    inventory = read_jsonl(ROOT / "inventory.jsonl")
    classifications = {row["assetId"]: row for row in read_jsonl(ROOT / "classification.jsonl")}
    benchmark = {row["assetId"]: row for row in read_jsonl(ROOT / "model-benchmark/inputs/index.jsonl")}
    needed_questions = {
        str(occurrence.get("questionId"))
        for row in inventory for occurrence in row.get("occurrences", []) if occurrence.get("questionId")
    }
    questions: dict[str, dict[str, Any]] = {}
    with QUESTION_BANK.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            question = json.loads(line)
            question_id = str(question.get("id") or "")
            if question_id not in needed_questions:
                continue
            questions[question_id] = {
                "questionId": question_id,
                "answerLabel": question.get("answer"),
                "optionLabels": [str(item.get("label") or item.get("text") or "") for item in question.get("options", []) if isinstance(item, dict)],
                "sourceJsonlLine": line_number,
            }
    rows: list[dict[str, Any]] = []
    for inventory_row in inventory:
        asset_id = inventory_row["assetId"]
        classification = classifications.get(asset_id, {})
        structured_path = ROOT / "assets" / asset_id / "structured.json"
        panel_boxes: list[dict[str, Any]] = []
        if structured_path.is_file():
            structured = json.loads(structured_path.read_text(encoding="utf-8"))
            candidate_boxes = structured.get("evidence", {}).get("panelBoxes", [])
            if isinstance(candidate_boxes, list):
                panel_boxes = row_major_boxes([box for box in candidate_boxes if isinstance(box, dict)])
        benchmark_row = benchmark.get(asset_id)
        if benchmark_row:
            panel_count = int(benchmark_row["deterministicPanelEvidence"]["panelCount"])
            option_count = int(benchmark_row["deterministicPanelEvidence"]["optionCount"])
            panel_roles = [str(panel["role"]) for panel in benchmark_row.get("panels", [])]
            layout_type = str(benchmark_row["deterministicPanelEvidence"]["layoutType"])
            layout_source = "phase1_benchmark_cv"
        else:
            panel_count = int(classification.get("expectedPanelCount") or 1)
            option_count = int(classification.get("expectedOptionCount") or 0)
            panel_roles = [str(role) for role in classification.get("panelRoles", [])]
            layout_type = str(classification.get("layoutType") or "other")
            layout_source = "classification_cv_candidate"
        qwen_layout = str((classification.get("qwenCandidate") or {}).get("layout") or "")
        if panel_boxes and len(panel_boxes) == panel_count and option_count > 0 and qwen_layout.startswith("matrix"):
            stem_boxes = panel_boxes[:-option_count]
            option_boxes = panel_boxes[-option_count:]
            stem_rows = cluster_box_rows(stem_boxes)
            widest_row = max((len(row) for row in stem_rows), default=0)
            missing_at_row_end = (
                len(stem_rows) >= 2
                and widest_row >= 2
                and all(len(row) == widest_row for row in stem_rows[:-1])
                and len(stem_rows[-1]) == widest_row - 1
            )
            if missing_at_row_end:
                panel_boxes = stem_boxes + [None] + option_boxes
                panel_count += 1
                panel_roles = ["stem"] * len(stem_boxes) + ["missing"] + ["option"] * option_count
                layout_type = "matrix"
                layout_source += "+matrix_missing_slot_inferred"
        references = []
        for occurrence in inventory_row.get("occurrences", []):
            question_id = str(occurrence.get("questionId") or "")
            context = questions.get(question_id, {"questionId": question_id})
            references.append({
                "questionId": question_id,
                "role": occurrence.get("role"),
                "field": occurrence.get("field"),
                "knowledgePoint": occurrence.get("knowledgePoint"),
                "sourceJsonlLine": occurrence.get("sourceJsonlLine"),
                "answerLabel": context.get("answerLabel"),
                "optionLabels": context.get("optionLabels", []),
            })
        checkpoint_material = "\0".join([
            str(inventory_row["sha256"]), str(panel_count), str(option_count), layout_type,
            json.dumps(panel_boxes, sort_keys=True, separators=(",", ":")),
            "graphic-reasoning-ai-semantic-prompt-v3-panel-crops",
        ])
        rows.append({
            "schemaVersion": "graphic-reasoning-semantic-job-v1",
            "assetId": asset_id,
            "sourceSha256": inventory_row["sha256"],
            "sourcePaths": inventory_row["localPaths"],
            "questionReferences": references,
            "layoutCandidate": {
                "layoutType": layout_type,
                "panelCount": panel_count,
                "optionCount": option_count,
                "panelRoles": panel_roles,
                "panelBoxes": panel_boxes if len(panel_boxes) == panel_count else [],
                "source": layout_source,
            },
            "visualGrammarCandidate": classification.get("visualGrammar"),
            "regularSubtypeCandidate": classification.get("regularSubtype"),
            "checkpointKey": hashlib.sha256(checkpoint_material.encode()).hexdigest(),
            "priority": REPRESENTATIVE_ORDER.index(asset_id) + 1 if asset_id in REPRESENTATIVE_ORDER else 100,
            "status": "pending",
        })
    rows.sort(key=lambda row: (int(row["priority"]), row["assetId"]))
    write_jsonl(HERE / "queue.jsonl", rows)
    state = {
        "schemaVersion": "graphic-reasoning-semantic-queue-state-v1",
        "totalAssets": len(rows),
        "pending": len(rows),
        "representativeAssets": REPRESENTATIVE_ORDER,
        "textOnlyGlyphQuestions": 32,
        "modelCalls": 0,
    }
    write_json(HERE / "queue-state.json", state)
    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
