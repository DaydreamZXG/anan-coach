#!/usr/bin/env python3
"""Full graphic-reasoning asset inventory and deterministic vector pipeline.

The pipeline deliberately keeps the model boundary narrow:

* inventory.py-style scanning owns SHA-256 deduplication and source references;
* CV owns panel boxes, geometry and normalized coordinates;
* Qwen is an optional semantic candidate provider and is never a coordinate source;
* the emitted scene is graphic-primitives-v2 compatible and contains no source-image
  canvas/background primitive.

The default execution is single-process and sequential to keep the local workstation
load bounded.  All files written by this module stay under this directory.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCE = REPO / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613/paper_questions.jsonl"
ASSET_ROOT = REPO / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613-assets"
INVENTORY_PATH = ROOT / "inventory.jsonl"
CLASSIFICATION_PATH = ROOT / "classification.jsonl"
MANIFEST_PATH = ROOT / "manifest.jsonl"
SUMMARY_PATH = ROOT / "summary.json"
QWEN_STATUS_PATH = ROOT / "qwen-status.json"
QWEN_CANDIDATES_PATH = ROOT / "qwen-candidates.jsonl"
ERRORS_PATH = ROOT / "errors.jsonl"
REPAIR_PATH = ROOT / "repair-queue.jsonl"
GRAMMAR_SUMMARY_PATH = ROOT / "grammar-summary.json"
BATCH_STATE_PATH = ROOT / "batch-state.json"
PARTIAL_REPAIR_PATH = ROOT / "partial-output-repair-queue.jsonl"
SEMANTIC_REPAIR_PATH = ROOT / "semantic-repair-queue.jsonl"
REGULAR_SUBTYPE_SUMMARY_PATH = ROOT / "regular-subtype-summary.json"
REGULAR_SUBTYPE_REPRESENTATIVES_PATH = ROOT / "regular-subtype-representatives.jsonl"
REGULAR_REPRESENTATIVE_MANIFEST_PATH = ROOT / "regular-representative-manifest.jsonl"
REGULAR_REPRESENTATIVE_REPAIR_PATH = ROOT / "regular-representative-repair-queue.jsonl"
REGULAR_REPRESENTATIVE_ERRORS_PATH = ROOT / "regular-representative-errors.jsonl"
REGULAR_QWEN_ERRORS_PATH = ROOT / "regular-qwen-errors.jsonl"
EXCLUSIONS_PATH = ROOT / "exclusions.jsonl"
EXCLUSION_POLICIES_PATH = ROOT / "exclusion-policies.json"
SUBTYPE_BATCH_ROOT = ROOT / "subtype-batches"
MICROBATCH_ROOT = ROOT / "microbatches"
OUTPUT_ROOT = ROOT / "assets"

SCHEMA_VERSION = "graphic-reasoning-batch-v1"
PRIMITIVE_SCHEMA = "graphic-primitives-v2"
MAX_CONTOURS = 96
MAX_PRIMITIVES = 128
QWEN_URL = "http://127.0.0.1:18000"
QWEN_MODEL = "qwen3-vl-30b-a3b"


class ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            self.images.append({key.lower(): value or "" for key, value in attrs})


def parse_images(fragment: Any) -> list[dict[str, str]]:
    if not isinstance(fragment, str) or "<img" not in fragment.lower():
        return []
    parser = ImageParser()
    try:
        parser.feed(fragment)
    except Exception:
        return []
    return parser.images


def canonical_track(question: dict[str, Any]) -> str:
    section = str(question.get("section") or "")
    taxonomy = question.get("primary_taxonomy") or {}
    path = taxonomy.get("catalog_path") or []
    if question.get("is_graphic_reasoning") or section == "图形推理" or "图形推理" in path:
        return "graphic_reasoning"
    root = str(path[0]) if path else ""
    if root == "资料分析" or section in {"资料分析", "资科分析", "资料分斩"}:
        return "data_analysis"
    if root == "数量关系" or section in {
        "数量关系", "数理能力", "数学运算", "数字推理", "数量关系与资料分析",
        "数量关系（数字推理）", "数量关系（数学运算）",
    }:
        return "quantity"
    if root == "言语理解与表达" or any(t in section for t in ("言语", "选词", "填空", "阅读", "段落")):
        return "verbal"
    if root == "判断推理" or section in {
        "判断推理", "判断推理能力", "逻辑推理", "逻辑判断", "推理判断",
        "演绎推理", "定义判断", "类比推理", "思维能力测验",
    }:
        return "judgment_non_graphic"
    if root in {"科学素养", "政治理论"} or any(t in section for t in ("常识", "科学", "政治", "公共基础", "综合知识", "综合分析", "法律")):
        return "knowledge_science"
    if "知觉" in section:
        return "perceptual_speed"
    return "other"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    temporary.replace(path)
    return count


def load_exclusions() -> dict[str, dict[str, Any]]:
    if not EXCLUSIONS_PATH.is_file():
        return {}
    return {row["assetId"]: row for row in (json.loads(line) for line in EXCLUSIONS_PATH.open(encoding="utf-8")) if row.get("assetId")}


def load_excluded_regular_subtypes() -> set[str]:
    if not EXCLUSION_POLICIES_PATH.is_file():
        return set()
    value = json.loads(EXCLUSION_POLICIES_PATH.read_text(encoding="utf-8"))
    return {str(item) for item in value.get("regularSubtypes", [])}


def option_images(question: dict[str, Any]) -> Iterator[tuple[str, dict[str, str]]]:
    for index, option in enumerate(question.get("options") or []):
        if not isinstance(option, dict):
            continue
        for item in parse_images(option.get("html")):
            yield f"options[{index}].html", item
        for raw in option.get("images") or []:
            if isinstance(raw, str):
                yield f"options[{index}].images", {"src": raw}
            elif isinstance(raw, dict):
                source = raw.get("src") or raw.get("url") or raw.get("path") or raw.get("local")
                if source:
                    yield f"options[{index}].images", {"src": str(source), "fromImageArray": "true"}


def qwen_health() -> dict[str, Any]:
    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result: dict[str, Any] = {
        "endpoint": f"{QWEN_URL}/v1/models",
        "model": QWEN_MODEL,
        "observedAt": observed_at,
        "status": "unavailable",
        "source": "local_health_check",
    }
    try:
        request = urllib.request.Request(f"{QWEN_URL}/v1/models", method="GET")
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        model_ids = [str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict)]
        result.update({"status": "available", "httpStatus": 200, "modelIds": model_ids})
        if QWEN_MODEL not in model_ids:
            result.update({"status": "model_mismatch", "requestedModelPresent": False})
        else:
            result["requestedModelPresent"] = True
    except urllib.error.HTTPError as exc:
        result.update({"httpStatus": exc.code, "error": f"HTTPError: {exc.reason}"})
    except Exception as exc:  # network failure is recorded, never retried in a batch loop
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _recover_graphic_tex(question: dict[str, Any], role: str, item: dict[str, str]) -> bool:
    reasons = set((question.get("graphic_reasoning") or {}).get("reasons") or [])
    return item.get("flag", "").lower() == "tex" and role in {"stem", "option"} and "jiangsu_character_reasoning" in reasons


def scan_inventory(check_model_health: bool = True) -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"question bank source missing: {SOURCE}")
    records: dict[str, dict[str, Any]] = {}
    path_hash_cache: dict[str, str] = {}
    path_size_cache: dict[str, int] = {}
    total_questions = 0
    graphic_questions = 0
    html_fields = {
        "material_html": "material",
        "stem_html": "stem",
        "original_analysis_html": "analysis",
        "original_solution_html": "solution",
    }
    for line_number, raw in enumerate(SOURCE.open(encoding="utf-8"), 1):
        question = json.loads(raw)
        total_questions += 1
        track = canonical_track(question)
        if track != "graphic_reasoning":
            continue
        graphic_questions += 1
        source_path = Path(str(question.get("source_path") or ""))
        source_dir = ASSET_ROOT / source_path.parent
        question_id = str(question.get("id") or "")
        occurrences: list[tuple[str, str, dict[str, str]]] = []
        for field, role in html_fields.items():
            occurrences.extend((field, role, item) for item in parse_images(question.get(field)))
        occurrences.extend((field, "option", item) for field, item in option_images(question))
        seen_occurrences: set[tuple[str, str]] = set()
        for field, role, item in occurrences:
            reference = html.unescape(item.get("src", "")).strip()
            if not reference or (item.get("flag", "").lower() == "tex" and not _recover_graphic_tex(question, role, item)):
                continue
            occurrence_key = (role, reference)
            if item.get("fromImageArray") == "true" and occurrence_key in seen_occurrences:
                continue
            seen_occurrences.add(occurrence_key)
            local_path = (source_dir / reference).resolve()
            path_key = str(local_path)
            if local_path.is_file():
                digest = path_hash_cache.get(path_key)
                if digest is None:
                    digest = sha256_file(local_path)
                    path_hash_cache[path_key] = digest
                    path_size_cache[path_key] = local_path.stat().st_size
                kind = "image"
                asset_id = f"gr_{digest[:24]}"
            else:
                digest = hashlib.sha256(f"missing\\0{source_path.parent}\\0{reference}".encode()).hexdigest()
                kind = "missing"
                asset_id = f"gr_missing_{digest[:24]}"
            record = records.setdefault(asset_id, {
                "assetId": asset_id,
                "sha256": digest,
                "kind": kind,
                "localPaths": [],
                "sizeBytes": path_size_cache.get(path_key),
                "occurrences": [],
            })
            if path_key not in record["localPaths"]:
                record["localPaths"].append(path_key)
            occurrence = {
                "questionId": question_id,
                "track": track,
                "section": str(question.get("section") or ""),
                "role": role,
                "field": field,
                "sourceJsonlLine": line_number,
                "imageRef": reference,
                "knowledgePoint": str(question.get("raw_knowledge_point") or "")[:240],
            }
            record["occurrences"].append(occurrence)
    rows = sorted(records.values(), key=lambda row: row["assetId"])
    for row in rows:
        row["localPaths"].sort()
        row["occurrences"].sort(key=lambda item: (item["questionId"], item["role"], item["field"], item["sourceJsonlLine"]))
    write_jsonl(INVENTORY_PATH, rows)
    qwen = qwen_health() if check_model_health else {
        "status": "not_checked_cpu_only",
        "source": "cpu_only_scan",
        "model": QWEN_MODEL,
        "endpointAccessed": False,
    }
    if check_model_health:
        write_json(QWEN_STATUS_PATH, qwen)
    by_role = Counter()
    questions_with_asset = set()
    for row in rows:
        for occurrence in row["occurrences"]:
            by_role[occurrence["role"]] += 1
            questions_with_asset.add(occurrence["questionId"])
    write_json(SUMMARY_PATH, {
        "schemaVersion": SCHEMA_VERSION,
        "source": str(SOURCE),
        "assetRoot": str(ASSET_ROOT),
        "totalQuestionsScanned": total_questions,
        "graphicReasoningQuestions": graphic_questions,
        "uniqueAssets": len(rows),
        "resolvedImageAssets": sum(row["kind"] == "image" for row in rows),
        "missingAssets": sum(row["kind"] == "missing" for row in rows),
        "questionsWithGraphicImage": len(questions_with_asset),
        "referencesByRole": dict(sorted(by_role.items())),
        "qwenHealth": qwen,
        "dedupeKey": "content_sha256",
        "occurrencePolicy": "all graphic_reasoning references; unlike the prior audit, occurrences are not capped",
    })
    print(json.dumps({"inventory": str(INVENTORY_PATH), "assets": len(rows), "qwen": qwen["status"]}, ensure_ascii=False))


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def as_dict(self, width: int, height: int) -> dict[str, Any]:
        return {
            "x": self.x, "y": self.y, "width": self.w, "height": self.h,
            "normalized": {
                "x": round(self.x / max(1, width), 6), "y": round(self.y / max(1, height), 6),
                "width": round(self.w / max(1, width), 6), "height": round(self.h / max(1, height), 6),
            },
        }


def _iou(a: Box, b: Box) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    return intersection / max(1, a.area + b.area - intersection)


def _dedup_boxes(boxes: list[Box]) -> list[Box]:
    def nested_frame(inner: Box, outer: Box) -> bool:
        """Treat a concentric inset drawing frame as content, not a second panel.

        Graphic-reasoning scans commonly wrap every item in an outer answer/stem
        cell and then draw a smaller square inside it.  Their IoU is only about
        0.7, so ordinary NMS keeps both and doubles the panel count.  Requiring
        near-total containment, similar centres and comparable side ratios keeps
        genuine neighbouring/nested figures available while suppressing only
        the repeated frame pair.
        """
        if inner.area >= outer.area:
            return False
        left = max(inner.x, outer.x)
        top = max(inner.y, outer.y)
        right = min(inner.x + inner.w, outer.x + outer.w)
        bottom = min(inner.y + inner.h, outer.y + outer.h)
        intersection = max(0, right - left) * max(0, bottom - top)
        width_ratio = inner.w / max(1, outer.w)
        height_ratio = inner.h / max(1, outer.h)
        centre_tolerance = max(3.0, min(outer.w, outer.h) * 0.08)
        return (
            intersection / max(1, inner.area) >= 0.96
            and 0.62 <= width_ratio <= 0.94
            and 0.62 <= height_ratio <= 0.94
            and abs(inner.cx - outer.cx) <= centre_tolerance
            and abs(inner.cy - outer.cy) <= centre_tolerance
        )

    selected: list[Box] = []
    for box in sorted(boxes, key=lambda item: item.area, reverse=True):
        if any(_iou(box, existing) > 0.82 or nested_frame(box, existing) for existing in selected):
            continue
        selected.append(box)
    return selected


def _rect_candidates(mask: np.ndarray) -> list[Box]:
    height, width = mask.shape[:2]
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[Box] = []
    image_area = width * height
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        if perimeter < 40:
            continue
        approx = cv2.approxPolyDP(contour, max(1.2, perimeter * 0.018), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, w, h = cv2.boundingRect(approx)
        area = w * h
        if w < max(28, width * 0.08) or h < max(28, height * 0.08):
            continue
        if area < image_area * 0.008 or area > image_area * 0.96:
            continue
        rectangularity = abs(cv2.contourArea(contour)) / max(1, area)
        if rectangularity < 0.2:
            continue
        candidates.append(Box(x, y, w, h))
    return _dedup_boxes(candidates)


def _projection_split(mask: np.ndarray, outer: Box) -> list[Box]:
    """Split a large outer frame only when long interior grid lines support it."""
    x0, y0 = outer.x + 2, outer.y + 2
    x1, y1 = outer.x + outer.w - 2, outer.y + outer.h - 2
    if x1 <= x0 or y1 <= y0:
        return []
    roi = mask[y0:y1, x0:x1]
    if roi.size == 0:
        return []
    vertical = (roi > 0).sum(axis=0)
    horizontal = (roi > 0).sum(axis=1)

    def groups(values: np.ndarray, threshold: float) -> list[int]:
        indices = np.where(values >= threshold)[0].tolist()
        output: list[int] = []
        for value in indices:
            if not output or value - output[-1] > 4:
                output.append(value)
            elif values[value] > values[output[-1]]:
                output[-1] = value
        return output

    v = groups(vertical, max(roi.shape[0] * 0.62, 24))
    h = groups(horizontal, max(roi.shape[1] * 0.62, 24))
    # A 3x3 frame has two usable interior dividers on each axis.  Requiring
    # approximately even spacing avoids treating an arbitrary drawing as a grid.
    def usable(values: list[int], size: int) -> list[int]:
        if len(values) < 2:
            return []
        target = [size / 3, size * 2 / 3]
        candidate = [min(values, key=lambda value: abs(value - t)) for t in target]
        if len(set(candidate)) != 2 or any(abs(a - b) > size * 0.14 for a, b in zip(candidate, target)):
            return []
        return [0, *sorted(candidate), size]

    vx = usable(v, roi.shape[1])
    hy = usable(h, roi.shape[0])
    if not vx or not hy:
        return []
    cells: list[Box] = []
    for row in range(3):
        for column in range(3):
            left, right = vx[column], vx[column + 1]
            top, bottom = hy[row], hy[row + 1]
            cells.append(Box(x0 + left, y0 + top, right - left, bottom - top))
    return cells


def _component_boxes(mask: np.ndarray) -> list[Box]:
    """Find repeated, disconnected figures when a source has no panel frames."""
    height, width = mask.shape[:2]
    component_mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(component_mask, connectivity=8)
    candidates: list[Box] = []
    image_area = width * height
    for index in range(1, count):
        x, y, w, h, area = [int(value) for value in stats[index]]
        if area < image_area * 0.003 or w < width * 0.025 or h < height * 0.08:
            continue
        if area > image_area * 0.32:
            continue
        candidates.append(Box(x, y, w, h))
    if len(candidates) < 3 or len(candidates) > 16:
        return []
    median_w = float(np.median([box.w for box in candidates]))
    median_h = float(np.median([box.h for box in candidates]))
    repeated = [box for box in candidates if abs(box.w - median_w) <= median_w * 0.55 and abs(box.h - median_h) <= median_h * 0.55]
    if len(repeated) < 3:
        return []
    return _dedup_boxes(repeated)


def detect_panels(gray: np.ndarray) -> tuple[list[Box], dict[str, Any]]:
    height, width = gray.shape[:2]
    mask = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)[1]
    candidates = _rect_candidates(mask)
    repeated: list[Box] = []
    if len(candidates) >= 3:
        # Prefer a repeated box-size cluster over the single outer question frame.
        clusters: list[list[Box]] = []
        for box in sorted(candidates, key=lambda item: item.area):
            placed = False
            for cluster in clusters:
                anchor = cluster[0]
                if abs(box.w - anchor.w) <= max(8, anchor.w * 0.23) and abs(box.h - anchor.h) <= max(8, anchor.h * 0.23):
                    cluster.append(box)
                    placed = True
                    break
            if not placed:
                clusters.append([box])
        clusters.sort(key=lambda cluster: (len(cluster), sum(item.area for item in cluster)), reverse=True)
        if clusters and len(clusters[0]) >= 3:
            repeated = _dedup_boxes(clusters[0])
    selected = repeated
    split_from_grid = False
    if not selected and candidates:
        outer = max(candidates, key=lambda item: item.area)
        selected = _projection_split(mask, outer)
        split_from_grid = bool(selected)
    if not selected:
        selected = _component_boxes(mask)
    if not selected and candidates:
        selected = [max(candidates, key=lambda item: item.area)]
    if not selected:
        selected = [Box(0, 0, width, height)]
    selected = [box for box in selected if box.w >= 18 and box.h >= 18]
    selected.sort(key=lambda item: (item.cy, item.cx))
    info = {
        "candidateCount": len(candidates),
        "selectedCount": len(selected),
        "splitFromGrid": split_from_grid,
        "imageWidth": width,
        "imageHeight": height,
    }
    return selected, info


def _knowledge_points(row: dict[str, Any]) -> str:
    return " ".join(sorted({str(item.get("knowledgePoint") or "") for item in row.get("occurrences", []) if item.get("knowledgePoint")}))


def _circle_candidates(contours: Iterable[np.ndarray], width: int, height: int, limit: int = 8) -> list[tuple[float, float, float]]:
    candidates: list[tuple[float, float, float]] = []
    min_dimension = min(width, height)
    for contour in contours:
        area = abs(float(cv2.contourArea(contour)))
        perimeter = float(cv2.arcLength(contour, True))
        if area < 18 or perimeter < 12:
            continue
        circularity = 4 * math.pi * area / max(1.0, perimeter * perimeter)
        x, y, w, h = cv2.boundingRect(contour)
        if circularity < 0.68 or min(w, h) < min_dimension * 0.08 or max(w, h) > min_dimension * 0.9:
            continue
        if max(w, h) / max(1, min(w, h)) > 1.28:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        if radius < min_dimension * 0.04 or radius > min_dimension * 0.48:
            continue
        if any(math.hypot(cx - px, cy - py) < max(radius, pr) * 0.55 for px, py, pr in candidates):
            continue
        candidates.append((cx, cy, radius))
        if len(candidates) >= limit:
            break
    return candidates


def infer_layout(boxes: list[Box], width: int, height: int) -> tuple[str, list[str], int]:
    if len(boxes) >= 9:
        # A square arrangement is the canonical matrix-3x3 layout.
        rows: dict[int, list[Box]] = defaultdict(list)
        tolerance = max(12, int(np.median([box.h for box in boxes]) * 0.55))
        for box in boxes:
            key = min((existing for existing in rows), key=lambda value: abs(value - box.cy), default=None)
            if key is None or abs(key - box.cy) > tolerance:
                key = int(box.cy)
            rows[key].append(box)
        row_sizes = sorted((len(value) for value in rows.values()), reverse=True)
        if row_sizes[:3] == [3, 3, 3]:
            return "matrix_3x3", ["stem" for _ in boxes], 0
    if len(boxes) == 6:
        return "classification_2x3", ["sample" for _ in boxes], 0
    # A large gap between upper panels and lower repeated boxes is the usual
    # question/options topology.  The CV gap is evidence; it is not inferred by Qwen.
    ordered = sorted(enumerate(boxes), key=lambda pair: pair[1].cy)
    gaps = [(ordered[index + 1][1].cy - ordered[index][1].cy, index) for index in range(len(ordered) - 1)]
    if gaps:
        gap, index = max(gaps)
        median_h = max(1, float(np.median([box.h for box in boxes])))
        lower_count = len(ordered) - index - 1
        if gap > median_h * 0.75 and 3 <= lower_count <= 8 and len(boxes) - lower_count >= 2:
            roles = ["sequence" for _ in boxes]
            for position, (original_index, _) in enumerate(ordered):
                roles[original_index] = "option" if position > index else "stem"
            return "sequence", roles, lower_count
    return "sequence", ["sequence" for _ in boxes], 0


def contour_features(gray: np.ndarray, boxes: list[Box]) -> dict[str, Any]:
    mask = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    line_count = 0
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if max(w, h) >= max(10, min(gray.shape) * 0.12) and max(w, h) / max(1, min(w, h)) > 2.5:
            line_count += 1
    circle_count = len(_circle_candidates(contours, gray.shape[1], gray.shape[0]))
    ink_ratio = float((mask > 0).mean())
    return {
        "inkRatio": round(ink_ratio, 6),
        "externalContourCount": len(contours),
        "lineSegmentCount": line_count,
        "circleCandidateCount": circle_count,
        "panelCount": len(boxes),
    }


def _projection_groups(values: np.ndarray, threshold: float, gap: int = 4) -> list[int]:
    indices = np.where(values >= threshold)[0].tolist()
    groups: list[int] = []
    for value in indices:
        if not groups or value - groups[-1] > gap:
            groups.append(value)
        elif values[value] > values[groups[-1]]:
            groups[-1] = value
    return groups


def _regular_panel_features(crop: np.ndarray) -> dict[str, Any]:
    """Measure visual primitives without deciding final vector coordinates."""
    height, width = crop.shape[:2]
    if min(height, width) < 12:
        return {"circle": 0, "polygon": 0, "filled": 0, "dot": 0, "curve": 0, "lineLike": 0, "straightSegment": 0, "residualCurve": 0, "grid": False}
    mask = cv2.threshold(crop, 235, 255, cv2.THRESH_BINARY_INV)[1]
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    hierarchy_rows = hierarchy[0] if hierarchy is not None else []
    circles = _circle_candidates(contours, width, height, limit=12)
    polygon_count = 0
    filled_count = 0
    dot_count = 0
    curve_count = 0
    line_like_count = 0
    crop_area = max(1, width * height)
    for index, contour in enumerate(contours):
        # Children are normally the inner edge of a thick hollow stroke.  Count
        # the outer semantic shape once instead of treating both edges as shapes.
        if len(hierarchy_rows) and int(hierarchy_rows[index][3]) >= 0:
            continue
        area = abs(float(cv2.contourArea(contour)))
        perimeter = float(cv2.arcLength(contour, True))
        if area < 3 or perimeter < 8:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w >= width * 0.95 or h >= height * 0.95:
            continue
        ratio = area / crop_area
        circularity = 4 * math.pi * area / max(1.0, perimeter * perimeter)
        approx = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.018), True)
        has_inner_boundary = bool(len(hierarchy_rows) and int(hierarchy_rows[index][2]) >= 0)
        fill = None if has_inner_boundary else _contour_color(crop, contour)
        if fill is not None and ratio >= 0.002:
            filled_count += 1
        if 0.00015 <= ratio <= 0.025 and 0.5 <= w / max(1, h) <= 2 and circularity >= 0.48 and fill is not None:
            dot_count += 1
        if 3 <= len(approx) <= 8 and ratio >= 0.0015 and circularity < 0.88:
            polygon_count += 1
        elif len(approx) >= 9 and ratio >= 0.0015 and circularity < 0.68:
            curve_count += 1
        if max(w, h) / max(1, min(w, h)) >= 3.2 and max(w, h) >= min(width, height) * 0.16:
            line_like_count += 1
    vertical = (mask > 0).sum(axis=0)
    horizontal = (mask > 0).sum(axis=1)
    vertical_lines = _projection_groups(vertical, max(10, height * 0.52))
    horizontal_lines = _projection_groups(horizontal, max(10, width * 0.52))
    straight_segments, _ = _hough_line_segments(mask)
    residual_curve_count = 0
    if circles:
        residual = mask.copy()
        for cx, cy, radius in circles:
            cv2.circle(residual, (round(cx), round(cy)), round(radius), 0, thickness=max(4, round(min(width, height) * 0.045)))
        for cx, cy, radius in _filled_round_dots(crop, allow_large=True):
            cv2.circle(residual, (round(cx), round(cy)), max(2, round(radius * 1.15)), 0, thickness=-1)
        residual_contours, _ = cv2.findContours(residual, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in residual_contours:
            perimeter = float(cv2.arcLength(contour, True))
            area = abs(float(cv2.contourArea(contour)))
            x, y, w, h = cv2.boundingRect(contour)
            if perimeter < 12 or max(w, h) < min(width, height) * 0.14:
                continue
            circularity = 4 * math.pi * area / max(1.0, perimeter * perimeter)
            approx = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.018), True)
            if len(approx) >= 8 and circularity < 0.58:
                residual_curve_count += 1
    return {
        "circle": len(circles),
        "polygon": polygon_count,
        "filled": filled_count,
        "dot": dot_count,
        "curve": curve_count,
        "lineLike": line_like_count,
        "straightSegment": len(straight_segments),
        "residualCurve": residual_curve_count,
        # Two vertical and two horizontal responses are just the boundary of a
        # rectangle.  A grid needs at least one verified internal divider on
        # each axis, hence three separated responses per axis.
        "grid": len(vertical_lines) >= 3 and len(horizontal_lines) >= 3,
    }


def regular_geometry_features(gray: np.ndarray, boxes: list[Box]) -> dict[str, Any]:
    panel_features: list[dict[str, Any]] = []
    for box in boxes:
        crop, _, _ = _box_crop(gray, box)
        panel_features.append(_regular_panel_features(crop))
    panel_count = max(1, len(panel_features))
    panel_hits = {
        key: sum(bool(features[key]) for features in panel_features)
        for key in ("circle", "polygon", "filled", "dot", "curve", "lineLike", "straightSegment", "residualCurve", "grid")
    }
    totals = {
        key: sum(int(features[key]) for features in panel_features)
        for key in ("circle", "polygon", "filled", "dot", "curve", "lineLike", "straightSegment", "residualCurve")
    }
    return {
        "panelCount": len(panel_features),
        "panelHits": panel_hits,
        "panelRatios": {key: round(value / panel_count, 4) for key, value in panel_hits.items()},
        "primitiveCandidateTotals": totals,
        "midtoneRatio": round(float(((gray > 25) & (gray < 235)).mean()), 6),
    }


def infer_regular_subtype(
    inventory_row: dict[str, Any],
    classification: dict[str, Any],
    features: dict[str, Any],
    qwen_candidate: dict[str, Any] | None,
) -> tuple[str, str, float, list[str]]:
    roles = {str(item.get("role") or "") for item in inventory_row.get("occurrences", [])}
    knowledge = _knowledge_points(inventory_row)
    ratios = features.get("panelRatios", {})
    totals = features.get("primitiveCandidateTotals", {})
    qwen_categories = set(qwen_candidate.get("categories", [])) if isinstance(qwen_candidate, dict) else set()
    qwen_notes = str(qwen_candidate.get("semantic_notes") or "") if isinstance(qwen_candidate, dict) else ""
    if roles and roles.issubset({"analysis", "solution"}):
        return "annotated_explanation", "role_provenance", 1.0, ["semantic_annotation_plus_geometry"]
    if features.get("midtoneRatio", 0) >= 0.18:
        return "tonal_life_or_raster_candidate", "cv_continuous_tone", min(1.0, float(features["midtoneRatio"]) + 0.4), ["qwen_life_semantics_required", "no_contour_fallback"]
    negative_life = any(token in qwen_notes for token in ("非生活物体", "无生活物体", "不含生活物体"))
    positive_life = any(token in qwen_notes for token in ("生活场景", "人物", "儿童", "动物", "器物")) and not negative_life
    positive_glyph = any(token in qwen_notes for token in ("汉字", "字母", "数字", "文字"))
    if positive_life:
        return "semantic_life_object", "qwen_semantic_consistent", 0.95, ["semantic_path_group_required", "no_pixel_contour_fallback"]
    if positive_glyph and "glyph" in qwen_categories:
        return "glyph_geometry_composition", "qwen_semantic_consistent", 0.9, ["textGlyph", "line_circle_polygon"]
    if "3d_fold" in qwen_categories and any(token in qwen_notes for token in ("立方体", "立体", "空间", "框架结构")):
        return "folded_3d_candidate", "qwen_semantic_consistent", 0.94, ["wireframe_face_required"]
    if (
        "grid_dot" in qwen_categories
        and "circle_ellipse_arc" in qwen_categories
        and any(token in qwen_notes for token in ("扇形", "扇区", "纹理", "点阵", "阴影"))
    ):
        return "textured_sector_candidate", "qwen_semantic", 0.96, ["sector_angle", "texture_dot_grid_diagonal"]
    explicit_arrow = any(token in qwen_notes for token in ("箭头符号", "箭头图形", "指向箭头", "方向标记箭头"))
    if "arrow_direction" in qwen_categories and explicit_arrow:
        return "directional_symbol_composition", "qwen_semantic_consistent", 0.95, ["semantic_symbol", "line_polygon"]
    mentions_curve = "curve_bezier" in qwen_categories or any(token in qwen_notes for token in ("曲线", "波形", "弧线"))
    if mentions_curve:
        if "straight_line" in qwen_categories:
            return "curve_line_composition", "qwen_semantic_consistent", 0.94, ["line_nms", "bezier_semantic_grouping_required"]
        return "irregular_curve_composition", "qwen_semantic_consistent", 0.94, ["bezier_semantic_grouping_required"]
    if "特殊规律-功能元素" in knowledge:
        return "semantic_symbol_candidate", "knowledge_point_candidate", 0.62, ["qwen_semantics_required", "line_polygon"]
    average_straight_segments = totals.get("straightSegment", 0) / max(1, features.get("panelCount", 0))
    if ratios.get("curve", 0) >= 0.70 and average_straight_segments >= 10 and ratios.get("polygon", 0) < 0.30:
        return "complex_silhouette_candidate", "cv_curve_complexity", min(1.0, float(ratios["curve"])), ["qwen_life_semantics_required", "no_hough_renderer"]
    if ratios.get("dot", 0) >= 0.25 and totals.get("dot", 0) >= max(3, features.get("panelCount", 0) // 2):
        return "dot_marker_composition", "cv_connected_component", float(ratios["dot"]), ["dot", "line_polygon"]
    if ratios.get("grid", 0) >= 0.28:
        return "grid_partition", "cv_projection", float(ratios["grid"]), ["rect", "line_nms"]
    if ratios.get("filled", 0) >= 0.25:
        return "filled_region_composition", "cv_high_confidence_fill", float(ratios["filled"]), ["face", "polygon", "line_nms"]
    if ratios.get("circle", 0) >= 0.24 and ratios.get("polygon", 0) >= 0.20:
        return "circle_polygon_composition", "cv_circle_polygon", min(1.0, float(ratios["circle"] + ratios["polygon"]) / 2 + 0.2), ["circle", "ellipse", "polygon", "line_nms"]
    if ratios.get("circle", 0) >= 0.60 and ratios.get("residualCurve", 0) >= 0.45 and ratios.get("polygon", 0) < 0.15:
        return "circle_curve_composition", "cv_circle_residual_curve", min(1.0, float(ratios["circle"] + ratios["residualCurve"]) / 2 + 0.1), ["circle", "arc_or_bezier_required"]
    if ratios.get("circle", 0) >= 0.32:
        return "circle_composition", "cv_circle", float(ratios["circle"]), ["circle", "ellipse", "line_nms"]
    if ratios.get("curve", 0) >= 0.30 and average_straight_segments < 4:
        return "irregular_curve_composition", "cv_curve", float(ratios["curve"]), ["bezier_semantic_grouping_required"]
    if ratios.get("polygon", 0) >= 0.38:
        return "closed_polygon_composition", "cv_closed_contour", float(ratios["polygon"]), ["polygon", "line_nms"]
    if ratios.get("lineLike", 0) >= 0.38 or average_straight_segments >= 4:
        return "straight_line_network", "cv_line_prior", max(float(ratios.get("lineLike", 0)), min(1.0, average_straight_segments / 8)), ["line_nms", "polyline"]
    return "mixed_regular_composition", "cv_mixed_fallback", 0.35, ["circle", "polygon", "line_nms", "repair_review"]


def classify_categories(row: dict[str, Any], gray: np.ndarray, boxes: list[Box]) -> dict[str, Any]:
    knowledge = _knowledge_points(row)
    lower = knowledge.lower()
    features = contour_features(gray, boxes)
    scores: Counter[str] = Counter()
    reasons: defaultdict[str, list[str]] = defaultdict(list)
    terms: list[tuple[str, str, str]] = [
        ("直线", "straight_line", "knowledge_point"), ("线", "straight_line", "knowledge_point"),
        ("折线", "polyline_polygon", "knowledge_point"), ("多边形", "polyline_polygon", "knowledge_point"),
        ("圆", "circle_ellipse_arc", "knowledge_point"), ("椭圆", "circle_ellipse_arc", "knowledge_point"), ("弧", "circle_ellipse_arc", "knowledge_point"),
        ("曲线", "curve_bezier", "knowledge_point"), ("贝塞尔", "curve_bezier", "knowledge_point"),
        ("阴影", "shaded_polygon", "knowledge_point"), ("黑白块", "shaded_polygon", "knowledge_point"), ("面", "shaded_polygon", "knowledge_point"),
        ("网格", "grid_dot", "knowledge_point"), ("点阵", "grid_dot", "knowledge_point"), ("数量规律-点", "grid_dot", "knowledge_point"),
        ("箭头", "arrow_direction", "knowledge_point"), ("方向", "arrow_direction", "knowledge_point"),
        ("字", "glyph", "knowledge_point"), ("汉字", "glyph", "knowledge_point"), ("字母", "glyph", "knowledge_point"), ("数字", "glyph", "knowledge_point"),
        ("生活", "life_outline", "knowledge_point"), ("人物", "life_outline", "knowledge_point"), ("动物", "life_outline", "knowledge_point"),
        ("立体", "3d_fold", "knowledge_point"), ("折叠", "3d_fold", "knowledge_point"), ("三视图", "3d_fold", "knowledge_point"), ("空间", "3d_fold", "knowledge_point"),
    ]
    for term, category, source in terms:
        if term in knowledge or term.lower() in lower:
            scores[category] += 3
            reasons[category].append(f"{source}:{term}")
    if features["lineSegmentCount"] >= max(4, features["externalContourCount"]):
        scores["straight_line"] += 1
        reasons["straight_line"].append("cv:line_segments")
    if features["circleCandidateCount"]:
        scores["circle_ellipse_arc"] += 2
        reasons["circle_ellipse_arc"].append("cv:hough_circle")
    if features["panelCount"] >= 6:
        scores["polyline_polygon"] += 1
        reasons["polyline_polygon"].append("cv:multi_panel")
    if not scores:
        scores["rare_vector"] = 1
        reasons["rare_vector"].append("cv:no_semantic_prior")
    categories = [name for name, score in scores.most_common() if score >= max(1, scores.most_common()[0][1] - 2)]
    if len(categories) > 4:
        categories = categories[:4]
    review_reasons: list[str] = []
    if len(boxes) == 1:
        review_reasons.append("panel_detector_found_only_one_region")
    if features["externalContourCount"] > MAX_CONTOURS:
        review_reasons.append("contour_complexity_capped")
    if features["inkRatio"] > 0.36:
        review_reasons.append("dense_or_photo_like_ink_distribution")
    if row.get("kind") != "image":
        review_reasons.append("missing_source_asset")
    layout, roles, option_count = infer_layout(boxes, gray.shape[1], gray.shape[0])
    return {
        "classificationVersion": "graphic-reasoning-classification-v1",
        "categories": categories,
        "categoryScores": dict(scores),
        "categoryReasons": {key: value for key, value in reasons.items()},
        "semanticSource": "deterministic_cv+knowledge_point_prior",
        "qwenCandidate": None,
        "layoutType": layout,
        "panelRoles": roles,
        "expectedPanelCount": len(boxes),
        "expectedOptionCount": option_count,
        "panelBoxes": [box.as_dict(gray.shape[1], gray.shape[0]) for box in boxes],
        "cvFeatures": features,
        "repairReasons": review_reasons,
        "status": "classified",
    }


def _qwen_prompt() -> str:
    return (
        "请给这张图形推理题做视觉语义候选。只输出 JSON，字段限定为 "
        "categories（从 straight_line, polyline_polygon, circle_ellipse_arc, "
        "curve_bezier, shaded_polygon, grid_dot, arrow_direction, glyph, "
        "life_outline, 3d_fold, rare_vector 中选）、panel_count、option_count、"
        "layout（matrix_3x3/sequence/classification_2x3/unknown）、has_text、"
        "has_direction、semantic_notes；不要输出坐标、路径或最终排版。"
    )


def _validate_qwen_candidate(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["candidate_not_object"]
    errors: list[str] = []
    allowed_categories = {"straight_line", "polyline_polygon", "circle_ellipse_arc", "curve_bezier", "shaded_polygon", "grid_dot", "arrow_direction", "glyph", "life_outline", "3d_fold", "rare_vector"}
    categories = value.get("categories")
    if not isinstance(categories, list) or any(item not in allowed_categories for item in categories):
        errors.append("categories")
    if not isinstance(value.get("panel_count"), int) or not 0 <= value["panel_count"] <= 16:
        errors.append("panel_count")
    if not isinstance(value.get("option_count"), int) or not 0 <= value["option_count"] <= 16:
        errors.append("option_count")
    if value.get("layout") not in {"matrix_3x3", "sequence", "classification_2x3", "unknown"}:
        errors.append("layout")
    if not isinstance(value.get("has_text"), bool):
        errors.append("has_text")
    if not isinstance(value.get("has_direction"), bool):
        errors.append("has_direction")
    if not isinstance(value.get("semantic_notes"), str):
        errors.append("semantic_notes")
    return errors


def classify_inventory(limit: int | None = None, qwen_limit: int = 0) -> None:
    rows = [json.loads(line) for line in INVENTORY_PATH.open(encoding="utf-8")]
    output: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        try:
            source = Path(row["localPaths"][0])
            if not source.is_file():
                raise FileNotFoundError(source)
            image = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError("cv2 could not decode image")
            boxes, panel_info = detect_panels(image)
            classification = classify_categories(row, image, boxes)
            classification.update({"assetId": row["assetId"], "sha256": row["sha256"], "sourcePath": str(source), "panelDetection": panel_info})
            output.append(classification)
        except Exception as exc:
            errors.append({"stage": "classify", "assetId": row.get("assetId"), "error": f"{type(exc).__name__}: {exc}"})
    qwen_rows: list[dict[str, Any]] = []
    if qwen_limit > 0 and output:
        sys.path.insert(0, str(ROOT))
        from qwen_client import semantic_candidate

        def qwen_priority(row: dict[str, Any]) -> tuple[int, int, int, str]:
            categories = set(row.get("categories", []))
            hard_semantics = categories.intersection({"glyph", "life_outline", "curve_bezier", "circle_ellipse_arc", "3d_fold", "rare_vector"})
            return (
                0 if row.get("repairReasons") else 1,
                0 if hard_semantics else 1,
                0 if row.get("expectedPanelCount") <= 1 else 1,
                str(row.get("assetId")),
            )

        qwen_targets = sorted(output, key=qwen_priority)[:qwen_limit]
        for row in qwen_targets:
            try:
                result = semantic_candidate(row["sourcePath"], _qwen_prompt(), timeout=60)
                candidate = result.get("candidate")
                candidate_errors = _validate_qwen_candidate(candidate)
                row["qwenCandidate"] = candidate
                row["qwenCandidateValidation"] = {"status": "valid" if not candidate_errors else "invalid", "errors": candidate_errors}
                if not candidate_errors:
                    row["semanticCategories"] = candidate.get("categories", [])
                row["semanticSource"] = "deterministic_cv+knowledge_point_prior+qwen_candidate"
                qwen_rows.append({"assetId": row["assetId"], "sha256": row["sha256"], "status": "valid" if not candidate_errors else "invalid", "candidate": candidate, "validationErrors": candidate_errors})
            except Exception as exc:
                qwen_rows.append({"assetId": row["assetId"], "sha256": row["sha256"], "status": "error", "error": f"{type(exc).__name__}: {exc}"})
    write_jsonl(CLASSIFICATION_PATH, output)
    write_jsonl(QWEN_CANDIDATES_PATH, qwen_rows)
    write_jsonl(ERRORS_PATH, errors)
    qwen = json.loads(QWEN_STATUS_PATH.read_text(encoding="utf-8")) if QWEN_STATUS_PATH.is_file() else qwen_health()
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.is_file() else {}
    summary.update({
        "classifiedAssets": len(output),
        "classificationErrors": len(errors),
        "qwenCandidateAssets": sum(item.get("status") == "valid" for item in qwen_rows),
        "qwenCandidateErrors": sum(item.get("status") == "error" for item in qwen_rows),
        "classificationCategories": dict(Counter(category for row in output for category in row.get("categories", []))),
        "qwenHealth": qwen,
    })
    write_json(SUMMARY_PATH, summary)
    print(json.dumps({"classified": len(output), "errors": len(errors)}, ensure_ascii=False))


def infer_visual_grammar(row: dict[str, Any], qwen_candidate: dict[str, Any] | None) -> tuple[str, str, list[str]]:
    knowledge = _knowledge_points(row)
    qwen_text = ""
    qwen_categories: set[str] = set()
    if isinstance(qwen_candidate, dict):
        qwen_text = str(qwen_candidate.get("semantic_notes") or "")
        qwen_categories = {str(value) for value in qwen_candidate.get("categories", []) if isinstance(value, str)}
    combined = knowledge + " " + qwen_text
    combined_lower = combined.lower()
    # Texture sectors must be routed before generic circle/contour handling.
    pie_tokens = ("饼图", "扇形区域", "扇形", "纹理", "点阵", "斜线填充", "圆形被划分")
    if (
        any(token in combined for token in pie_tokens)
        or ("circle_ellipse_arc" in qwen_categories and any(token in qwen_text for token in ("区域", "分布", "圆周")))
    ) and ("circle_ellipse_arc" in qwen_categories or row.get("cvFeatures", {}).get("circleCandidateCount", 0) > 0):
        return "textured_pie", "qwen_semantic+cv_circle", ["sector_angle", "texture_dot_grid_diagonal"]
    if "3d_fold" in qwen_categories or any(token in combined for token in ("立体", "折叠", "三视图", "空间类")):
        return "folded_3d", "qwen_semantic+knowledge_point", ["wireframe_face", "relative_depth"]
    if "glyph" in qwen_categories or "life_outline" in qwen_categories or any(token in combined for token in ("文字", "字母", "数字", "汉字", "生活化")):
        return "glyph_life_object", "qwen_semantic+knowledge_point", ["glyph_region", "life_outline_region"]
    if "curve_bezier" in qwen_categories or "circle_ellipse_arc" in qwen_categories or any(token in combined for token in ("曲线", "圆弧", "椭圆", "圆")):
        return "curve_circle_arc", "qwen_semantic+cv_curve", ["circle_ellipse_arc", "quadratic_bezier_candidate"]
    if any(token in combined for token in ("网格", "点阵", "黑白块", "阴影")) or "grid_dot" in qwen_categories:
        return "textured_geometry", "qwen_semantic+knowledge_point", ["polygon_face", "grid_dot_or_diagonal"]
    if any(category in row.get("categories", []) for category in ("straight_line", "polyline_polygon", "shaded_polygon")):
        return "regular_geometry", "knowledge_point+cv_contour", ["line_polyline_polygon", "face_optional_fill"]
    return "rare_vector", "cv_fallback", ["path_group_repair_pending"]


def route_visual_grammar() -> None:
    inventory = {row["assetId"]: row for row in (json.loads(line) for line in INVENTORY_PATH.open(encoding="utf-8"))}
    qwen = {row["assetId"]: row for row in (json.loads(line) for line in QWEN_CANDIDATES_PATH.open(encoding="utf-8"))} if QWEN_CANDIDATES_PATH.is_file() else {}
    invalid_semantic_rows = [row for row in qwen.values() if row.get("status") == "invalid"]
    write_jsonl(SEMANTIC_REPAIR_PATH, [{"assetId": row.get("assetId"), "sha256": row.get("sha256"), "status": "repair_pending", "reason": "qwen_semantic_candidate_schema_invalid", "validationErrors": row.get("validationErrors", [])} for row in invalid_semantic_rows])
    rows = [json.loads(line) for line in CLASSIFICATION_PATH.open(encoding="utf-8")]
    counts: Counter[str] = Counter()
    samples: defaultdict[str, list[str]] = defaultdict(list)
    routed: list[dict[str, Any]] = []
    for row in rows:
        qwen_row = qwen.get(row["assetId"])
        candidate = qwen_row.get("candidate") if isinstance(qwen_row, dict) and qwen_row.get("status") == "valid" else None
        grammar, source, renderer = infer_visual_grammar({**inventory.get(row["assetId"], {}), **row}, candidate)
        row["visualGrammar"] = grammar
        row["grammarSource"] = source
        row["grammarRenderer"] = renderer
        row["grammarStatus"] = "routed_pending_renderer"
        row["grammarEvidence"] = {"qwenUsed": candidate is not None, "qwenCandidate": candidate, "knowledgePoint": _knowledge_points(inventory.get(row["assetId"], {})), "cvFeatures": row.get("cvFeatures", {})}
        counts[grammar] += 1
        if len(samples[grammar]) < 8:
            samples[grammar].append(row["assetId"])
        routed.append(row)
    write_jsonl(CLASSIFICATION_PATH, routed)
    grammar_summary = {
        "schemaVersion": "graphic-reasoning-grammar-routing-v1",
        "status": "classification_delivered_renderer_pending",
        "counts": dict(sorted(counts.items())),
        "samples": {key: value for key, value in sorted(samples.items())},
        "routes": {
            "regular_geometry": "deterministic_line_polyline_polygon",
            "curve_circle_arc": "deterministic_circle_ellipse_arc_bezier_candidate",
            "glyph_life_object": "cv_region_boxes_plus_qwen_semantics_no_model_coordinates",
            "textured_pie": "deterministic_sector_angles_plus_dot_grid_diagonal_texture",
            "textured_geometry": "deterministic_face_and_texture_primitives",
            "folded_3d": "deterministic_wireframe_and_face_topology",
            "rare_vector": "deterministic_path_group_repair_pending",
        },
        "notFinal": "No full-batch success claim; contour/Hough outputs are paused until grammar-specific renderers pass representative review.",
    }
    write_json(GRAMMAR_SUMMARY_PATH, grammar_summary)
    partial_dirs = sum(path.is_dir() for path in OUTPUT_ROOT.iterdir()) if OUTPUT_ROOT.is_dir() else 0
    full_manifest_rows = [json.loads(line) for line in MANIFEST_PATH.open(encoding="utf8")] if MANIFEST_PATH.is_file() else []
    for manifest_row in full_manifest_rows:
        manifest_row["status"] = "repair_pending"
        manifest_row["repairReasons"] = sorted(set((manifest_row.get("repairReasons") or []) + ["grammar_renderer_pending", "generic_contour_render_not_final"]))
    if full_manifest_rows:
        write_jsonl(MANIFEST_PATH, full_manifest_rows)
    accepted_ids = {row["assetId"] for row in full_manifest_rows if row.get("status") == "valid"}
    representative_ids = {json.loads(line)["assetId"] for line in (ROOT / "representative-manifest.jsonl").open(encoding="utf8")} if (ROOT / "representative-manifest.jsonl").is_file() else set()
    partial_rows = []
    if OUTPUT_ROOT.is_dir():
        for output_dir in sorted(path for path in OUTPUT_ROOT.iterdir() if path.is_dir()):
            if output_dir.name not in accepted_ids and output_dir.name not in representative_ids:
                partial_rows.append({"assetId": output_dir.name, "status": "repair_pending", "reason": "generic_contour_render_interrupted_before_full_manifest", "output": str(output_dir)})
    write_jsonl(PARTIAL_REPAIR_PATH, partial_rows)
    write_json(BATCH_STATE_PATH, {
        "schemaVersion": "graphic-reasoning-batch-state-v1",
        "status": "paused_for_grammar_renderer_review",
        "reason": "generic contour/Hough full render was stopped before manifest acceptance",
        "classifiedAssets": len(routed),
        "partialOutputDirectories": partial_dirs,
        "partialRepairQueue": len(partial_rows),
        "manifestEntries": len(full_manifest_rows),
        "acceptedFinalAssets": 0,
        "repairRequiredBeforeFinal": True,
    })
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.is_file() else {}
    summary.update({
        "grammarRouting": dict(sorted(counts.items())),
        "fullBatchSuccessGate": "blocked_grammar_specific_renderer_pending",
        "processedStatus": "paused_not_final",
        "processedAssets": 0,
        "processedErrors": 0,
        "partialOutputDirectories": partial_dirs,
        "partialRepairQueue": len(partial_rows),
        "qwenCandidateInvalid": len(invalid_semantic_rows),
        "finalAcceptedAssets": 0,
    })
    write_json(SUMMARY_PATH, summary)
    print(json.dumps({"routed": len(routed), "grammars": dict(sorted(counts.items())), "partialOutputDirectories": partial_dirs}, ensure_ascii=False))


def route_regular_subtypes(representatives_per_subtype: int = 3) -> None:
    inventory = {row["assetId"]: row for row in (json.loads(line) for line in INVENTORY_PATH.open(encoding="utf-8"))}
    exclusions = load_exclusions()
    excluded_subtypes = load_excluded_regular_subtypes()
    qwen_rows = {row["assetId"]: row for row in (json.loads(line) for line in QWEN_CANDIDATES_PATH.open(encoding="utf-8"))} if QWEN_CANDIDATES_PATH.is_file() else {}
    rows = [json.loads(line) for line in CLASSIFICATION_PATH.open(encoding="utf-8")]
    counts: Counter[str] = Counter()
    excluded_policy_counts: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    candidates: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    supported = {
        "grid_partition",
        "filled_region_composition",
        "dot_marker_composition",
        "circle_polygon_composition",
        "circle_composition",
        "closed_polygon_composition",
        "straight_line_network",
    }
    for row in rows:
        if row.get("visualGrammar") != "regular_geometry":
            continue
        inventory_row = inventory[row["assetId"]]
        if row["assetId"] in exclusions:
            exclusion = exclusions[row["assetId"]]
            row.update({
                "regularSubtypeVersion": "regular-geometry-subtypes-v1",
                "regularSubtype": "excluded_by_user",
                "regularSubtypeSource": "explicit_user_exclusion",
                "regularSubtypeConfidence": 1.0,
                "regularSubtypeFeatures": {},
                "regularSubtypeRenderer": [],
                "regularSubtypeStatus": "excluded_by_user",
                "exclusionEvidence": exclusion,
            })
            counts["excluded_by_user"] += 1
            continue
        source = Path(inventory_row["localPaths"][0])
        try:
            gray = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise ValueError("cv2 could not decode image")
            occurrence_roles = {str(item.get("role") or "") for item in inventory_row.get("occurrences", [])}
            if occurrence_roles == {"option"}:
                row.update({
                    "layoutType": "sequence",
                    "panelRoles": ["option"],
                    "expectedPanelCount": 1,
                    "expectedOptionCount": 1,
                    "panelBoxes": [Box(0, 0, int(gray.shape[1]), int(gray.shape[0])).as_dict(gray.shape[1], gray.shape[0])],
                    "panelDetection": {**row.get("panelDetection", {}), "selectedCount": 1, "roleOverride": "standalone_option_image"},
                })
            boxes = [Box(int(item["x"]), int(item["y"]), int(item["width"]), int(item["height"])) for item in row.get("panelBoxes", [])]
            if not boxes:
                boxes, _ = detect_panels(gray)
            features = regular_geometry_features(gray, boxes)
            qwen_row = qwen_rows.get(row["assetId"], {})
            candidate = qwen_row.get("candidate") if qwen_row.get("status") == "valid" else row.get("qwenCandidate")
            subtype, source_name, confidence, renderer = infer_regular_subtype(inventory_row, row, features, candidate)
            excluded_by_policy = subtype in excluded_subtypes
            subtype_status = (
                "excluded_by_user"
                if excluded_by_policy
                else "scope_review_required"
                if subtype == "tonal_life_or_raster_candidate"
                else "renderer_candidate"
                if subtype in supported
                else "semantic_or_renderer_pending"
            )
            row.update({
                "regularSubtypeVersion": "regular-geometry-subtypes-v1",
                "regularSubtype": subtype,
                "regularSubtypeSource": source_name,
                "regularSubtypeConfidence": round(float(confidence), 4),
                "regularSubtypeFeatures": features,
                "regularSubtypeRenderer": renderer,
                "regularSubtypeStatus": subtype_status,
            })
            if excluded_by_policy:
                row["exclusionEvidence"] = {
                    "status": "excluded_by_user",
                    "policy": "regular_subtype",
                    "subtype": subtype,
                    "reason": "life_scene_or_complex_silhouette_excluded_by_user",
                }
                excluded_policy_counts[subtype] += 1
            else:
                # A prior policy blanket-excluded life drawings.  Once that
                # policy is superseded, stale evidence must not survive a
                # fresh route and mislead downstream scope checks.
                row.pop("exclusionEvidence", None)
                if subtype in {"complex_silhouette_candidate", "semantic_life_object"}:
                    row["scopeDecision"] = "life_silhouette_in_scope_requires_part_semantics_and_topology"
                    row.pop("scopeReviewEvidence", None)
                elif subtype == "tonal_life_or_raster_candidate":
                    row["scopeDecision"] = "asset_level_photo_or_natural_image_evidence_required"
                    row["scopeReviewEvidence"] = {
                        "status": "review_required",
                        "candidateSignal": "cv_continuous_tone",
                        "decisionBoundary": "natural_photo_out_only_with_asset_level_evidence; life_object_remains_in_scope",
                    }
                else:
                    row.pop("scopeDecision", None)
                    row.pop("scopeReviewEvidence", None)
            counts[subtype] += 1
            if subtype_status not in {"excluded_by_user", "scope_review_required"}:
                candidates[subtype].append({
                "assetId": row["assetId"],
                "sha256": row["sha256"],
                "regularSubtype": subtype,
                "confidence": round(float(confidence), 4),
                "panelCount": row.get("expectedPanelCount"),
                "sourcePath": str(source),
                "status": "representative_pending_review",
            })
        except Exception as exc:
            failures.append({"assetId": row.get("assetId"), "stage": "regular_subtype", "error": f"{type(exc).__name__}: {exc}"})
            row.update({
                "regularSubtypeVersion": "regular-geometry-subtypes-v1",
                "regularSubtype": "subtype_failed",
                "regularSubtypeStatus": "repair_pending",
                "regularSubtypeFeatures": {},
            })
            counts["subtype_failed"] += 1
    representative_rows: list[dict[str, Any]] = []
    representative_ids: dict[str, list[str]] = {}
    for subtype, items in sorted(candidates.items()):
        items.sort(key=lambda item: (-float(item["confidence"]), abs(int(item.get("panelCount") or 0) - 9), item["assetId"]))
        selected = items[:max(1, representatives_per_subtype)]
        representative_rows.extend(selected)
        representative_ids[subtype] = [item["assetId"] for item in selected]
    write_jsonl(CLASSIFICATION_PATH, rows)
    write_jsonl(REGULAR_SUBTYPE_REPRESENTATIVES_PATH, representative_rows)
    write_json(REGULAR_SUBTYPE_SUMMARY_PATH, {
        "schemaVersion": "regular-geometry-subtypes-v1",
        "status": "subtypes_routed_representative_review_pending",
        "totalRegularGeometry": sum(counts.values()),
        "counts": dict(sorted(counts.items())),
        "representatives": representative_ids,
        "failures": failures,
        "rendererCandidateSubtypes": sorted(supported),
        "semanticOrRendererPendingSubtypes": sorted(set(counts) - supported - excluded_subtypes - {"excluded_by_user"}),
        "excludedAssets": len(exclusions) + sum(excluded_policy_counts.values()),
        "excludedBySubtypePolicy": dict(sorted(excluded_policy_counts.items())),
        "acceptanceGate": "No subtype may enter final manifest until representative topology and visual review pass.",
    })
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.is_file() else {}
    summary.update({
        "regularSubtypeRouting": dict(sorted(counts.items())),
        "regularSubtypeFailures": len(failures),
        "excludedAssets": len(exclusions) + sum(excluded_policy_counts.values()),
        "excludedBySubtypePolicy": dict(sorted(excluded_policy_counts.items())),
        "regularSubtypeRepresentativeAssets": len(representative_rows),
        "regularSubtypeStatus": "representative_review_pending",
    })
    write_json(SUMMARY_PATH, summary)
    print(json.dumps({"regular": sum(counts.values()), "subtypes": dict(sorted(counts.items())), "representatives": len(representative_rows), "failures": len(failures)}, ensure_ascii=False))


def semanticize_regular_representatives(limit: int | None = None) -> None:
    """Ask Qwen for subtype semantics only; deterministic CV keeps geometry."""
    if not REGULAR_SUBTYPE_REPRESENTATIVES_PATH.is_file():
        raise FileNotFoundError("run regular-route before regular-qwen")
    sys.path.insert(0, str(ROOT))
    from qwen_client import semantic_candidate

    exclusions = load_exclusions()
    representatives = [row for row in (json.loads(line) for line in REGULAR_SUBTYPE_REPRESENTATIVES_PATH.open(encoding="utf-8")) if row.get("assetId") not in exclusions]
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in representatives:
        grouped[str(row["regularSubtype"])].append(row)
    # Round-robin keeps a small request budget representative across every
    # subtype instead of spending it on the first alphabetical group.
    targets: list[dict[str, Any]] = []
    depth = 0
    while True:
        added = False
        for subtype in sorted(grouped):
            if depth < len(grouped[subtype]):
                targets.append(grouped[subtype][depth])
                added = True
        if not added:
            break
        depth += 1
    if limit is not None and limit > 0:
        targets = targets[:limit]
    existing_rows = [json.loads(line) for line in QWEN_CANDIDATES_PATH.open(encoding="utf-8")] if QWEN_CANDIDATES_PATH.is_file() else []
    merged = {row["assetId"]: row for row in existing_rows}
    classifications = {row["assetId"]: row for row in (json.loads(line) for line in CLASSIFICATION_PATH.open(encoding="utf-8"))}
    errors: list[dict[str, Any]] = []
    valid = 0
    invalid = 0
    prompt = _qwen_prompt() + " 另请在 semantic_notes 中简述主图元组合、是否为连续灰阶照片/生活物体、是否含扇区纹理或注释标记。"
    for row in targets:
        asset_id = row["assetId"]
        try:
            result = semantic_candidate(row["sourcePath"], prompt, timeout=60)
            candidate = result.get("candidate")
            validation_errors = _validate_qwen_candidate(candidate)
            status = "valid" if not validation_errors else "invalid"
            merged[asset_id] = {
                "assetId": asset_id,
                "sha256": row["sha256"],
                "status": status,
                "candidate": candidate,
                "validationErrors": validation_errors,
                "semanticScope": "regular_subtype_representative",
            }
            classification = classifications.get(asset_id)
            if classification is not None:
                classification["qwenCandidate"] = candidate
                classification["qwenCandidateValidation"] = {"status": status, "errors": validation_errors}
                if status == "valid":
                    classification["semanticCategories"] = candidate.get("categories", [])
                    classification["semanticSource"] = "deterministic_cv+knowledge_point_prior+qwen_candidate"
            if status == "valid":
                valid += 1
            else:
                invalid += 1
        except Exception as exc:
            errors.append({"assetId": asset_id, "stage": "regular_qwen", "error": f"{type(exc).__name__}: {exc}"})
            merged[asset_id] = {"assetId": asset_id, "sha256": row["sha256"], "status": "error", "error": f"{type(exc).__name__}: {exc}", "semanticScope": "regular_subtype_representative"}
    write_jsonl(QWEN_CANDIDATES_PATH, [merged[key] for key in sorted(merged)])
    write_jsonl(CLASSIFICATION_PATH, [classifications[key] for key in sorted(classifications)])
    write_jsonl(REGULAR_QWEN_ERRORS_PATH, errors)
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.is_file() else {}
    summary.update({"regularQwenRequested": len(targets), "regularQwenValid": valid, "regularQwenInvalid": invalid, "regularQwenErrors": len(errors)})
    write_json(SUMMARY_PATH, summary)
    print(json.dumps({"requested": len(targets), "valid": valid, "invalid": invalid, "errors": len(errors)}, ensure_ascii=False))


def _validate_stroke_semantic_candidate(candidate: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(candidate, dict):
        return ["candidate_not_object"]
    if candidate.get("visual_grammar") != "stroke_graph":
        errors.append("visual_grammar")
    if not isinstance(candidate.get("panel_count"), int) or not 1 <= candidate["panel_count"] <= 32:
        errors.append("panel_count")
    if not isinstance(candidate.get("option_count"), int) or not 0 <= candidate["option_count"] <= 16:
        errors.append("option_count")
    groups = candidate.get("groups")
    if not isinstance(groups, list) or len(groups) > 32:
        errors.append("groups")
    forbidden = {"x", "y", "width", "height", "bbox", "box", "point", "points", "control", "control1", "control2", "path", "paths", "svg", "coordinates"}

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in forbidden:
                    errors.append(f"forbidden_coordinate_key:{key}")
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)

    inspect(candidate)
    return sorted(set(errors))


def semanticize_stroke_assets(asset_ids: set[str], batch_name: str) -> None:
    """Qwen supplies semantic grouping only; graph geometry remains deterministic."""
    sys.path.insert(0, str(ROOT))
    from qwen_client import semantic_candidate

    inventory = {row["assetId"]: row for row in (json.loads(line) for line in INVENTORY_PATH.open(encoding="utf-8"))}
    classifications = {row["assetId"]: row for row in (json.loads(line) for line in CLASSIFICATION_PATH.open(encoding="utf-8"))}
    batch_root = MICROBATCH_ROOT / batch_name
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    prompt = """你正在分析公务员考试图形推理题。只做视觉语义分组，不输出坐标、边界框、SVG、路径或控制点。
输出严格 JSON：
{
  "visual_grammar": "stroke_graph",
  "panel_count": 整数,
  "option_count": 整数,
  "contains_filled_regions": 布尔值,
  "contains_text": 布尔值,
  "contains_directional_symbols": 布尔值,
  "groups": [
    {"panel_position": 从1开始的整数, "motifs": [简短图元名称], "topology": ["open"或"closed"或"junction"], "relation": "同面板图元之间的相对/嵌套关系，不写坐标"}
  ],
  "notes": "主图元及难点的简短描述"
}
不确定时如实写 notes，不猜几何位置。"""
    for asset_id in sorted(asset_ids):
        row = inventory.get(asset_id)
        if row is None:
            errors.append({"assetId": asset_id, "error": "asset_not_in_inventory"})
            continue
        try:
            result = semantic_candidate(row["localPaths"][0], prompt, timeout=60)
            candidate = result.get("candidate")
            validation_errors = _validate_stroke_semantic_candidate(candidate)
            classification = classifications.get(asset_id, {})
            count_audit = {
                "cvPanelCount": classification.get("expectedPanelCount"),
                "qwenPanelCount": candidate.get("panel_count") if isinstance(candidate, dict) else None,
                "panelCountAgreement": bool(isinstance(candidate, dict) and candidate.get("panel_count") == classification.get("expectedPanelCount")),
                "cvOptionCount": classification.get("expectedOptionCount"),
                "qwenOptionCount": candidate.get("option_count") if isinstance(candidate, dict) else None,
                "optionCountAgreement": bool(isinstance(candidate, dict) and candidate.get("option_count") == classification.get("expectedOptionCount")),
                "authority": "deterministic_cv; Qwen counts are advisory and count disagreement is retained",
            }
            rows.append({
                "assetId": asset_id,
                "sha256": row["sha256"],
                "model": result.get("model"),
                "status": "schema_valid" if not validation_errors else "schema_invalid",
                "candidate": candidate,
                "validationErrors": validation_errors,
                "countAudit": count_audit,
                "scope": "semantic_grouping_only_no_geometry",
            })
        except Exception as exc:
            errors.append({"assetId": asset_id, "error": f"{type(exc).__name__}: {exc}"})
    write_jsonl(batch_root / "qwen-semantic.jsonl", rows)
    write_jsonl(batch_root / "qwen-semantic-errors.jsonl", errors)
    write_json(batch_root / "qwen-semantic-status.json", {
        "model": QWEN_MODEL,
        "endpoint": QWEN_URL + "/v1/chat/completions",
        "requested": len(asset_ids),
        "schemaValid": sum(row["status"] == "schema_valid" for row in rows),
        "schemaInvalid": sum(row["status"] == "schema_invalid" for row in rows),
        "panelCountDisagreements": sum(not row["countAudit"]["panelCountAgreement"] for row in rows if row["status"] == "schema_valid"),
        "optionCountDisagreements": sum(not row["countAudit"]["optionCountAgreement"] for row in rows if row["status"] == "schema_valid"),
        "errors": len(errors),
        "coordinateAuthority": "deterministic_cv_only",
    })
    print(json.dumps({"requested": len(asset_ids), "schemaValid": sum(row["status"] == "schema_valid" for row in rows), "schemaInvalid": sum(row["status"] == "schema_invalid" for row in rows), "panelCountDisagreements": sum(not row["countAudit"]["panelCountAgreement"] for row in rows if row["status"] == "schema_valid"), "errors": len(errors)}, ensure_ascii=False))


def identity_style(fill: str | None = None, stroke: str | None = "#101828", line_width: float = 2.0) -> dict[str, Any]:
    return {"stroke": stroke, "fill": fill, "lineWidth": line_width, "dash": [], "opacity": 1}


def identity_transform() -> dict[str, Any]:
    return {"translateX": 0, "translateY": 0, "scaleX": 1, "scaleY": 1, "rotateDegrees": 0, "originX": 0, "originY": 0}


def _path_commands(points: np.ndarray, width: int, height: int) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if len(points) < 2:
        return commands
    for index, point in enumerate(points):
        x = round(float(np.clip(point[0][0] / max(1, width) * 1000, 0, 1000)), 3)
        y = round(float(np.clip(point[0][1] / max(1, height) * 1000, 0, 1000)), 3)
        commands.append({"command": "M" if index == 0 else "L", "x": x, "y": y})
    commands.append({"command": "Z"})
    return commands


def _box_crop(gray: np.ndarray, box: Box) -> tuple[np.ndarray, int, int]:
    x0, y0 = max(0, box.x + 3), max(0, box.y + 3)
    x1, y1 = min(gray.shape[1], box.x + box.w - 3), min(gray.shape[0], box.y + box.h - 3)
    if x1 <= x0 or y1 <= y0:
        return gray, 0, 0
    return gray[y0:y1, x0:x1], x0, y0


def _contour_color(crop: np.ndarray, contour: np.ndarray) -> str | None:
    mask = np.zeros(crop.shape, dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, thickness=-1)
    values = crop[mask > 0]
    if values.size == 0:
        return None
    mean = float(values.mean())
    dark_ratio = float((values < 220).mean())
    # A contour enclosing a thin outline contains a large bright interior and
    # has a low dark-pixel ratio.  Require dense dark pixels before assigning a
    # fill; this is the deterministic guard against hollow-line solidification.
    if dark_ratio < 0.58:
        return None
    x, y, w, h = cv2.boundingRect(contour)
    ink = np.where(crop < 220, 255, 0).astype(np.uint8)
    thickness = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    maximum_thickness = float(thickness[mask > 0].max()) if np.any(mask > 0) else 0.0
    # Open or hollow strokes can enclose a narrow dark contour whose pixel
    # density is high.  A true filled face also needs a substantial inscribed
    # radius relative to its own short dimension.
    if maximum_thickness < max(2.2, min(w, h) * 0.09):
        return None
    if mean < 105:
        return "#101828"
    if mean < 205:
        return "#6b7280"
    return None


def _segment_length(segment: tuple[float, float, float, float]) -> float:
    return math.hypot(segment[2] - segment[0], segment[3] - segment[1])


def _segment_angle(segment: tuple[float, float, float, float]) -> float:
    return math.atan2(segment[3] - segment[1], segment[2] - segment[0]) % math.pi


def _point_line_distance(px: float, py: float, segment: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = segment
    denominator = math.hypot(y1 - y0, x1 - x0)
    if denominator <= 1e-6:
        return math.hypot(px - x0, py - y0)
    return abs((y1 - y0) * px - (x1 - x0) * py + x1 * y0 - y1 * x0) / denominator


def _line_nms(raw: Iterable[tuple[float, float, float, float]], distance_tolerance: float = 4.0, angle_tolerance_degrees: float = 5.0) -> list[tuple[float, float, float, float]]:
    """Suppress the two edge responses commonly produced by one thick line."""
    kept: list[tuple[float, float, float, float]] = []
    angle_tolerance = math.radians(angle_tolerance_degrees)
    for candidate in sorted(raw, key=_segment_length, reverse=True):
        angle = _segment_angle(candidate)
        midpoint = ((candidate[0] + candidate[2]) / 2, (candidate[1] + candidate[3]) / 2)
        duplicate = False
        for existing in kept:
            difference = abs(angle - _segment_angle(existing))
            difference = min(difference, math.pi - difference)
            if difference > angle_tolerance:
                continue
            if _point_line_distance(midpoint[0], midpoint[1], existing) > distance_tolerance:
                continue
            axis_x, axis_y = math.cos(_segment_angle(existing)), math.sin(_segment_angle(existing))
            existing_projection = sorted((existing[0] * axis_x + existing[1] * axis_y, existing[2] * axis_x + existing[3] * axis_y))
            candidate_projection = sorted((candidate[0] * axis_x + candidate[1] * axis_y, candidate[2] * axis_x + candidate[3] * axis_y))
            overlap = min(existing_projection[1], candidate_projection[1]) - max(existing_projection[0], candidate_projection[0])
            shorter = min(existing_projection[1] - existing_projection[0], candidate_projection[1] - candidate_projection[0])
            if overlap >= -distance_tolerance and overlap >= shorter * 0.35:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def _hough_line_segments(mask: np.ndarray) -> tuple[list[tuple[float, float, float, float]], int]:
    height, width = mask.shape[:2]
    if min(height, width) < 18 or width * height > 180_000:
        return [], 0
    lines = cv2.HoughLinesP(
        mask,
        1,
        np.pi / 360,
        threshold=max(10, min(width, height) // 9),
        minLineLength=max(12, int(min(width, height) * 0.16)),
        maxLineGap=max(3, int(min(width, height) * 0.035)),
    )
    if lines is None:
        return [], 0
    raw = [tuple(float(value) for value in line[0]) for line in lines[:160]]
    kept = _line_nms(raw, distance_tolerance=max(3.0, min(width, height) * 0.025))
    return kept[:48], len(raw)


def _circle_edge_support(mask: np.ndarray, cx: float, cy: float, radius: float) -> float:
    height, width = mask.shape[:2]
    supported = 0
    samples = 72
    for index in range(samples):
        angle = 2 * math.pi * index / samples
        x = int(round(cx + radius * math.cos(angle)))
        y = int(round(cy + radius * math.sin(angle)))
        x0, x1 = max(0, x - 2), min(width, x + 3)
        y0, y1 = max(0, y - 2), min(height, y + 3)
        if x0 < x1 and y0 < y1 and np.any(mask[y0:y1, x0:x1] > 0):
            supported += 1
    return supported / samples


def _circle_radial_fit(mask: np.ndarray, cx: float, cy: float, radius: float) -> tuple[float, float, float]:
    height, width = mask.shape[:2]
    deviations: list[float] = []
    band = max(3.0, min(8.0, radius * 0.15))
    for index in range(144):
        angle = 2 * math.pi * index / 144
        hits: list[float] = []
        for distance in np.linspace(max(1.0, radius - band), radius + band, max(7, int(band * 4) + 1)):
            x = int(round(cx + distance * math.cos(angle)))
            y = int(round(cy + distance * math.sin(angle)))
            if 0 <= x < width and 0 <= y < height and mask[y, x] > 0:
                hits.append(abs(distance - radius))
        if hits:
            deviations.append(min(hits))
    if not deviations:
        return 0.0, 999.0, 999.0
    return len(deviations) / 144, float(np.mean(deviations)), float(np.std(deviations))


def _regular_circle_candidates(crop: np.ndarray, contours: list[np.ndarray]) -> list[tuple[float, float, float]]:
    height, width = crop.shape[:2]
    mask = cv2.threshold(crop, 235, 255, cv2.THRESH_BINARY_INV)[1]
    candidates = list(_circle_candidates(contours, width, height, limit=16))
    if min(width, height) >= 36:
        detected = cv2.HoughCircles(
            crop,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(14, min(width, height) // 5),
            param1=90,
            param2=max(24, min(width, height) // 6),
            minRadius=max(5, min(width, height) // 14),
            maxRadius=max(8, int(min(width, height) * 0.45)),
        )
        if detected is not None:
            for cx, cy, radius in detected[0, :16]:
                candidate = (float(cx), float(cy), float(radius))
                if _circle_edge_support(mask, *candidate) >= 0.60:
                    candidates.append(candidate)
    accepted: list[tuple[float, float, float]] = []
    yy, xx = np.ogrid[:height, :width]
    for candidate in sorted(candidates, key=lambda item: (_circle_edge_support(mask, *item), item[2]), reverse=True):
        cx, cy, radius = candidate
        interior = (xx - cx) ** 2 + (yy - cy) ** 2 <= max(1.0, radius * 0.72) ** 2
        interior_density = float((crop[interior] < 180).mean()) if np.any(interior) else 0.0
        if interior_density >= 0.58:
            continue
        coverage, mean_deviation, std_deviation = _circle_radial_fit(mask, cx, cy, radius)
        if coverage < 0.84 or mean_deviation > max(0.9, radius * 0.022) or std_deviation > max(0.8, radius * 0.02):
            continue
        if any(math.hypot(cx - ax, cy - ay) <= max(radius, ar) * 0.42 and abs(radius - ar) <= max(radius, ar) * 0.34 for ax, ay, ar in accepted):
            continue
        accepted.append(candidate)
    return accepted[:12]


def _regular_ellipse_candidates(contours: list[np.ndarray], width: int, height: int, circles: list[tuple[float, float, float]]) -> list[tuple[float, float, float, float, float]]:
    accepted: list[tuple[float, float, float, float, float]] = []
    minimum = min(width, height)
    for contour in contours:
        if len(contour) < 12 or cv2.contourArea(contour) < 24:
            continue
        (cx, cy), (axis_a, axis_b), angle = cv2.fitEllipse(contour)
        radius_x, radius_y = axis_a / 2, axis_b / 2
        short, long = sorted((radius_x, radius_y))
        if short < minimum * 0.045 or long > minimum * 0.48 or long / max(1.0, short) < 1.45 or long / max(1.0, short) > 3.5:
            continue
        if any(math.hypot(cx - ccx, cy - ccy) < max(long, radius) * 0.35 for ccx, ccy, radius in circles):
            continue
        radians = math.radians(angle)
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        sampled = contour[:, 0, :][::max(1, len(contour) // 80)].astype(float)
        normalized_errors: list[float] = []
        for px, py in sampled:
            dx, dy = px - cx, py - cy
            local_x = dx * cos_a + dy * sin_a
            local_y = -dx * sin_a + dy * cos_a
            normalized_errors.append(abs(math.sqrt((local_x / max(1.0, radius_x)) ** 2 + (local_y / max(1.0, radius_y)) ** 2) - 1))
        if not normalized_errors or float(np.median(normalized_errors)) > 0.08:
            continue
        candidate = (float(cx), float(cy), float(radius_x), float(radius_y), float(angle))
        if any(math.hypot(cx - ex, cy - ey) < max(long, erx, ery) * 0.3 for ex, ey, erx, ery, _ in accepted):
            continue
        accepted.append(candidate)
    return accepted[:8]


def _line_matches_polygon_edge(segment: tuple[float, float, float, float], polygon: list[tuple[float, float]], tolerance: float) -> bool:
    if len(polygon) < 2:
        return False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        edge = (start[0], start[1], end[0], end[1])
        if _point_line_distance(segment[0], segment[1], edge) <= tolerance and _point_line_distance(segment[2], segment[3], edge) <= tolerance:
            return True
    return False


def _line_matches_circle_tangent(segment: tuple[float, float, float, float], circle: tuple[float, float, float], tolerance: float) -> bool:
    cx, cy, radius = circle
    points = ((segment[0], segment[1]), ((segment[0] + segment[2]) / 2, (segment[1] + segment[3]) / 2), (segment[2], segment[3]))
    return all(abs(math.hypot(px - cx, py - cy) - radius) <= tolerance for px, py in points)


def _filled_round_dots(crop: np.ndarray, allow_large: bool) -> list[tuple[float, float, float]]:
    mask = cv2.threshold(crop, 190, 255, cv2.THRESH_BINARY_INV)[1]
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    height, width = crop.shape[:2]
    crop_area = width * height
    dots: list[tuple[float, float, float]] = []
    for index in range(1, count):
        x, y, w, h, area = [int(value) for value in stats[index]]
        maximum_area = crop_area * (0.03 if allow_large else 0.005)
        if not (2 <= area <= maximum_area and 0.65 <= w / max(1, h) <= 1.5):
            continue
        component = np.where(labels == index, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        contour_area = abs(float(cv2.contourArea(contour)))
        perimeter = float(cv2.arcLength(contour, True))
        (_, _), enclosing_radius = cv2.minEnclosingCircle(contour)
        circularity = 4 * math.pi * contour_area / max(1.0, perimeter * perimeter)
        enclosing_fill = contour_area / max(1.0, math.pi * enclosing_radius * enclosing_radius)
        if area >= 20 and (circularity < 0.74 or enclosing_fill < 0.78):
            continue
        if area < 20 and enclosing_fill < 0.58:
            continue
        cx, cy = [float(value) for value in centroids[index]]
        dots.append((cx, cy, max(1.5, math.sqrt(area / math.pi))))
    # A solid marker often touches a circle or chord, so ordinary connected
    # components merge it into the whole drawing.  The distance-transform core
    # remains isolated because thin strokes have a much smaller radius.
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    core_threshold = max(2.4, min(width, height) * 0.022)
    core = np.where(distance >= core_threshold, 255, 0).astype(np.uint8)
    core_count, core_labels, core_stats, core_centroids = cv2.connectedComponentsWithStats(core, 8)
    for index in range(1, core_count):
        x, y, w, h, area = [int(value) for value in core_stats[index]]
        if area < 3 or area > crop_area * (0.025 if allow_large else 0.006) or not (0.55 <= w / max(1, h) <= 1.8):
            continue
        component_distances = np.where(core_labels == index, distance, 0)
        flat_index = int(np.argmax(component_distances))
        cy, cx = np.unravel_index(flat_index, distance.shape)
        radius = float(component_distances[cy, cx])
        if radius < core_threshold:
            continue
        candidate = (float(cx), float(cy), radius)
        if any(math.hypot(candidate[0] - px, candidate[1] - py) < max(candidate[2], pr) * 0.8 for px, py, pr in dots):
            continue
        dots.append(candidate)
    local_maximum = cv2.dilate(distance, np.ones((5, 5), np.uint8))
    peaks = np.where((distance >= local_maximum - 1e-5) & (distance >= core_threshold * 1.35), 255, 0).astype(np.uint8)
    peak_count, _, peak_stats, peak_centroids = cv2.connectedComponentsWithStats(peaks, 8)
    for index in range(1, peak_count):
        x, y, w, h, area = [int(value) for value in peak_stats[index]]
        if area > crop_area * 0.01:
            continue
        cx, cy = [float(value) for value in peak_centroids[index]]
        radius = float(distance[min(height - 1, max(0, round(cy))), min(width - 1, max(0, round(cx)))])
        candidate = (cx, cy, radius)
        if any(math.hypot(candidate[0] - px, candidate[1] - py) < max(candidate[2], pr) * 1.1 for px, py, pr in dots):
            continue
        dots.append(candidate)
    return dots[:32]


def _is_question_mark_panel(crop: np.ndarray) -> bool:
    height, width = crop.shape[:2]
    mask = cv2.threshold(crop, 190, 255, cv2.THRESH_BINARY_INV)[1]
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    minimum_area = max(3, int(width * height * 0.0015))
    components: list[tuple[int, int, int, int, int, float, float]] = []
    for index in range(1, count):
        x, y, w, h, area = [int(value) for value in stats[index]]
        if area >= minimum_area and w < width * 0.8 and h < height * 0.8:
            cx, cy = [float(value) for value in centroids[index]]
            components.append((x, y, w, h, area, cx, cy))
    if len(components) != 2:
        return False
    upper, lower = sorted(components, key=lambda item: item[6])
    _, _, upper_w, upper_h, upper_area, upper_cx, upper_cy = upper
    _, _, lower_w, lower_h, lower_area, lower_cx, lower_cy = lower
    return (
        upper_area >= lower_area * 1.8
        and lower_area <= width * height * 0.04
        and 0.55 <= lower_w / max(1, lower_h) <= 1.6
        and lower_cy - upper_cy >= height * 0.16
        and abs(lower_cx - upper_cx) <= width * 0.18
        and upper_h >= height * 0.2
    )


def extract_grid_primitives(crop: np.ndarray) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Represent a rectilinear grid as shared grid lines plus cell faces."""
    height, width = crop.shape[:2]
    mask = cv2.threshold(crop, 225, 255, cv2.THRESH_BINARY_INV)[1]
    vertical = (mask > 0).sum(axis=0)
    horizontal = (mask > 0).sum(axis=1)
    xs = _projection_groups(vertical, max(8, height * 0.48), gap=max(3, width // 80))
    ys = _projection_groups(horizontal, max(8, width * 0.48), gap=max(3, height // 80))
    if len(xs) < 3 or len(ys) < 3:
        return [], {"grammar": "regular_geometry", "subtype": "grid_partition", "status": "repair_pending", "reason": "verified_grid_lines_not_found", "gridColumns": 0, "gridRows": 0}
    # Dense anti-aliased borders may create an extra near-duplicate response.
    xs = _projection_groups(np.array([vertical[index] if index in xs else 0 for index in range(width)]), max(8, height * 0.48), gap=max(4, width // 55))
    ys = _projection_groups(np.array([horizontal[index] if index in ys else 0 for index in range(height)]), max(8, width * 0.48), gap=max(4, height // 55))
    primitives: list[dict[str, Any]] = []
    for x in xs:
        primitives.append({"type": "line", "from": _norm_point(x, ys[0], width, height), "to": _norm_point(x, ys[-1], width, height), "style": identity_style(None, "#101828", 1.5), "transform": identity_transform()})
    for y in ys:
        primitives.append({"type": "line", "from": _norm_point(xs[0], y, width, height), "to": _norm_point(xs[-1], y, width, height), "style": identity_style(None, "#101828", 1.5), "transform": identity_transform()})
    filled_cells: list[dict[str, Any]] = []
    for row_index in range(len(ys) - 1):
        for column_index in range(len(xs) - 1):
            x0, x1 = xs[column_index], xs[column_index + 1]
            y0, y1 = ys[row_index], ys[row_index + 1]
            margin_x, margin_y = max(1, int((x1 - x0) * 0.2)), max(1, int((y1 - y0) * 0.2))
            roi = crop[y0 + margin_y:y1 - margin_y, x0 + margin_x:x1 - margin_x]
            if roi.size == 0:
                continue
            dark_ratio = float((roi < 100).mean())
            gray_ratio = float(((roi >= 100) & (roi < 230)).mean())
            fill = "#101828" if dark_ratio >= 0.58 else ("#9ca3af" if gray_ratio >= 0.62 else None)
            if fill is None:
                continue
            cell = {
                "row": row_index,
                "column": column_index,
                "fill": fill,
                "box": {"x": round(x0 / width * 1000, 3), "y": round(y0 / height * 1000, 3), "width": round((x1 - x0) / width * 1000, 3), "height": round((y1 - y0) / height * 1000, 3)},
            }
            filled_cells.append(cell)
            primitives.insert(0, {"type": "rect", "box": cell["box"], "style": identity_style(fill, None, 0), "transform": identity_transform()})
    return primitives[:MAX_PRIMITIVES], {
        "grammar": "regular_geometry",
        "subtype": "grid_partition",
        "status": "renderer_candidate",
        "gridColumns": len(xs) - 1,
        "gridRows": len(ys) - 1,
        "verticalLineCount": len(xs),
        "horizontalLineCount": len(ys),
        "filledCells": filled_cells,
    }


def extract_regular_primitives(crop: np.ndarray, source_asset: str, subtype: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Grammar-specific regular geometry extraction; never emits arbitrary paths."""
    height, width = crop.shape[:2]
    if min(height, width) <= 0:
        return [], {"grammar": "regular_geometry", "subtype": subtype, "status": "repair_pending", "reason": "empty_crop"}
    mask = cv2.threshold(crop, 235, 255, cv2.THRESH_BINARY_INV)[1]
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    hierarchy_rows = hierarchy[0] if hierarchy is not None else []
    circle_enabled = subtype in {"circle_polygon_composition", "circle_composition"}
    circles = _regular_circle_candidates(crop, contours) if circle_enabled else []
    ellipses = _regular_ellipse_candidates(contours, width, height, circles) if circle_enabled else []
    dots = _filled_round_dots(crop, allow_large=circle_enabled) if subtype in {"dot_marker_composition", "circle_polygon_composition", "circle_composition"} else []
    primitives: list[dict[str, Any]] = []
    polygon_pixels: list[list[tuple[float, float]]] = []
    filled_faces = 0
    for index, contour in enumerate(contours):
        if len(hierarchy_rows) and int(hierarchy_rows[index][3]) >= 0:
            continue
        area = abs(float(cv2.contourArea(contour)))
        perimeter = float(cv2.arcLength(contour, True))
        if area < 4 or perimeter < 10:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w >= width * 0.95 or h >= height * 0.95:
            continue
        if any(math.hypot(x + w / 2 - cx, y + h / 2 - cy) <= max(w, h) * 0.25 and abs(max(w, h) / 2 - radius) <= max(radius, max(w, h) / 2) * 0.35 for cx, cy, radius in circles):
            continue
        if any(math.hypot(x + w / 2 - cx, y + h / 2 - cy) <= max(w, h) * 0.28 for cx, cy, _, _, _ in ellipses):
            continue
        if any(math.hypot(x + w / 2 - cx, y + h / 2 - cy) <= max(w, h) * 0.35 for cx, cy, _ in dots):
            continue
        approx = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.018), True)
        if not 3 <= len(approx) <= 10:
            continue
        has_inner_boundary = bool(len(hierarchy_rows) and int(hierarchy_rows[index][2]) >= 0)
        fill = None if has_inner_boundary else _contour_color(crop, contour)
        points_px = [(float(point[0][0]), float(point[0][1])) for point in approx]
        points = [_norm_point(px, py, width, height) for px, py in points_px]
        primitive_type = "face" if fill is not None else "polygon"
        primitives.append({"type": primitive_type, "points": points, "style": identity_style(fill, "#101828", 1.6), "transform": identity_transform()})
        polygon_pixels.append(points_px)
        if fill is not None:
            filled_faces += 1
    if circle_enabled:
        for cx, cy, radius in circles:
            primitives.append({"type": "circle", "center": _norm_point(cx, cy, width, height), "radius": round(radius / max(width, height) * 1000, 3), "style": identity_style(None, "#101828", 1.6), "transform": identity_transform()})
        for cx, cy, radius_x, radius_y, angle in ellipses:
            primitives.append({"type": "ellipse", "center": _norm_point(cx, cy, width, height), "radiusX": round(radius_x / width * 1000, 3), "radiusY": round(radius_y / height * 1000, 3), "rotationDegrees": round(angle, 3), "style": identity_style(None, "#101828", 1.6), "transform": identity_transform()})
    for cx, cy, radius in dots:
        primitives.append({"type": "dot", "center": _norm_point(cx, cy, width, height), "radius": round(radius / max(width, height) * 1000, 3), "style": identity_style("#101828", "#101828", 0.8), "transform": identity_transform()})
    lines, raw_line_count = _hough_line_segments(mask)
    line_count = 0
    edge_tolerance = max(3.0, min(width, height) * 0.025)
    for segment in lines:
        if any(_line_matches_polygon_edge(segment, polygon, edge_tolerance) for polygon in polygon_pixels):
            continue
        if any(_line_matches_circle_tangent(segment, circle, edge_tolerance) for circle in circles):
            continue
        if any(_segment_length(segment) < radius * 3.2 and math.hypot((segment[0] + segment[2]) / 2 - cx, (segment[1] + segment[3]) / 2 - cy) < radius * 1.5 for cx, cy, radius in dots):
            continue
        x0, y0, x1, y1 = segment
        primitives.append({"type": "line", "from": _norm_point(x0, y0, width, height), "to": _norm_point(x1, y1, width, height), "style": identity_style(None, "#101828", 1.4), "transform": identity_transform()})
        line_count += 1
    primitives = primitives[:MAX_PRIMITIVES]
    return primitives, {
        "grammar": "regular_geometry",
        "subtype": subtype,
        "status": "renderer_candidate",
        "polygonCount": len(polygon_pixels),
        "filledFaceCount": filled_faces,
        "circleCount": len(circles),
        "ellipseCount": len(ellipses),
        "dotCount": len(dots),
        "rawHoughLineCount": raw_line_count,
        "lineCountAfterNmsAndEdgeSuppression": line_count,
        "sourceEvidence": source_asset,
    }


def _content_frame(crop: np.ndarray) -> Box | None:
    """Return the single inset square that belongs to a panel's visual grammar."""
    height, width = crop.shape[:2]
    mask = cv2.threshold(crop, 235, 255, cv2.THRESH_BINARY_INV)[1]
    candidates = [
        box for box in _rect_candidates(mask)
        if 0.45 <= box.area / max(1, width * height) <= 0.92
        and 0.78 <= box.w / max(1, box.h) <= 1.28
        and abs(box.cx - width / 2) <= width * 0.16
        and abs(box.cy - height / 2) <= height * 0.16
    ]
    return max(candidates, key=lambda item: item.area) if candidates else None


def _erase_rect_boundary(mask: np.ndarray, box: Box, radius: int) -> np.ndarray:
    output = mask.copy()
    x0, y0 = max(0, box.x - radius), max(0, box.y - radius)
    x1, y1 = min(mask.shape[1] - 1, box.x + box.w - 1 + radius), min(mask.shape[0] - 1, box.y + box.h - 1 + radius)
    cv2.line(output, (x0, box.y), (x1, box.y), 0, radius * 2 + 1)
    cv2.line(output, (x0, box.y + box.h - 1), (x1, box.y + box.h - 1), 0, radius * 2 + 1)
    cv2.line(output, (box.x, y0), (box.x, y1), 0, radius * 2 + 1)
    cv2.line(output, (box.x + box.w - 1, y0), (box.x + box.w - 1, y1), 0, radius * 2 + 1)
    return output


def _snap_line(points: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, str]:
    start, end = points[0].astype(float), points[-1].astype(float)
    delta = end - start
    angle = math.atan2(float(delta[1]), float(delta[0])) % math.pi
    snap_targets = (
        (0.0, "horizontal"),
        (math.pi / 4, "diagonal_down"),
        (math.pi / 2, "vertical"),
        (math.pi * 3 / 4, "diagonal_up"),
    )
    target, name = min(snap_targets, key=lambda item: abs(angle - item[0]))
    if abs(angle - target) > math.radians(3.0):
        return start, end, "arbitrary_straight"
    values = np.array(points, dtype=float)
    centre = values.mean(axis=0)
    direction = np.array([math.cos(target), math.sin(target)], dtype=float)
    projections = (values - centre) @ direction
    return centre + direction * float(projections.min()), centre + direction * float(projections.max()), name


def _cubic_fit(points: list[np.ndarray], width: int, height: int, line_width: float, depth: int = 0) -> tuple[list[dict[str, Any]], float]:
    values = np.array(points, dtype=float)
    if len(values) < 3:
        start, end, direction = _snap_line(points)
        primitive = {
            "type": "line", "from": _norm_point(start[0], start[1], width, height),
            "to": _norm_point(end[0], end[1], width, height),
            "geometryClass": direction,
            "style": identity_style(None, "#101828", line_width), "transform": identity_transform(),
        }
        return [primitive], 0.0
    distances = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(values, axis=0), axis=1))]
    if distances[-1] <= 1e-6:
        return [], 0.0
    t = distances / distances[-1]
    start, end = values[0], values[-1]
    a = np.column_stack((3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2))
    residual_target = values - ((1 - t) ** 3)[:, None] * start - (t ** 3)[:, None] * end
    controls, _, _, _ = np.linalg.lstsq(a, residual_target, rcond=None)
    control1, control2 = controls
    fitted = (
        ((1 - t) ** 3)[:, None] * start
        + (3 * (1 - t) ** 2 * t)[:, None] * control1
        + (3 * (1 - t) * t ** 2)[:, None] * control2
        + (t ** 3)[:, None] * end
    )
    errors = np.linalg.norm(fitted - values, axis=1)
    maximum_error = float(errors.max())
    split = int(np.argmax(errors))
    tolerance = max(1.45, math.hypot(width, height) * 0.012)
    if maximum_error > tolerance and depth < 2 and 2 <= split <= len(points) - 3:
        left, left_error = _cubic_fit(points[:split + 1], width, height, line_width, depth + 1)
        right, right_error = _cubic_fit(points[split:], width, height, line_width, depth + 1)
        return left + right, max(left_error, right_error)
    primitive = {
        "type": "cubicBezier",
        "from": _norm_point(start[0], start[1], width, height),
        "control1": _norm_point(control1[0], control1[1], width, height),
        "control2": _norm_point(control2[0], control2[1], width, height),
        "to": _norm_point(end[0], end[1], width, height),
        "geometryClass": "smooth_curve",
        "style": identity_style(None, "#101828", line_width),
        "transform": identity_transform(),
    }
    return [primitive], maximum_error


def _verified_center_lines(skeleton: np.ndarray, frame: Box | None, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[tuple[float, float, float, float]]:
    """Recover full straight strokes and reject local tangents on curves."""
    height, width = skeleton.shape[:2]
    raw, _ = _hough_line_segments((skeleton > 0).astype(np.uint8) * 255)
    pixels = np.argwhere(skeleton > 0)
    if not raw or not len(pixels):
        return []
    xy = pixels[:, ::-1].astype(float)
    node_points = np.array([[float(node["x"]), float(node["y"])] for node in nodes], dtype=float) if nodes else np.empty((0, 2))

    def near_terminal(point: np.ndarray, direction: np.ndarray) -> bool:
        if len(node_points) and float(np.linalg.norm(node_points - point, axis=1).min()) <= max(4.0, min(width, height) * 0.052):
            return True
        if frame is not None:
            tolerance = max(3.0, min(width, height) * 0.04)
            inside_x = frame.x - tolerance <= point[0] <= frame.x + frame.w - 1 + tolerance
            inside_y = frame.y - tolerance <= point[1] <= frame.y + frame.h - 1 + tolerance
            if (
                inside_y and min(abs(point[0] - frame.x), abs(point[0] - (frame.x + frame.w - 1))) <= tolerance
                or inside_x and min(abs(point[1] - frame.y), abs(point[1] - (frame.y + frame.h - 1))) <= tolerance
            ):
                return True
        # Degree-two pixels hide an L corner from the graph.  A true corner or
        # crossing has nearby ink leaving the candidate line sharply; a smooth
        # arc tangent remains inside this narrow band and is rejected.
        neighbourhood = xy[np.linalg.norm(xy - point, axis=1) <= max(5.5, min(width, height) * 0.065)]
        if len(neighbourhood):
            normal = np.array([-direction[1], direction[0]])
            if float(np.abs((neighbourhood - point) @ normal).max()) >= max(2.8, min(width, height) * 0.032):
                return True
        return False

    extended: list[tuple[float, float, float, float]] = []
    minimum_length = max(13.0, min(width, height) * 0.145)

    def edge_linearity(edge: dict[str, Any]) -> tuple[bool, float]:
        simplified = _simplify_centerline(edge["points"], False, math.hypot(width, height))
        residual, tortuosity = _line_residual(simplified)
        values = np.array(edge["points"], dtype=float)
        direction_spread = 0.0
        if len(values) >= 7:
            step = max(3, min(7, len(values) // 4))
            vectors = values[step:] - values[:-step]
            usable = np.linalg.norm(vectors, axis=1) > 1e-6
            angles = np.mod(np.arctan2(vectors[usable, 1], vectors[usable, 0]), math.pi)
            if len(angles):
                mean = math.atan2(float(np.sin(angles * 2).mean()), float(np.cos(angles * 2).mean())) / 2 % math.pi
                differences = np.abs(angles - mean)
                differences = np.minimum(differences, math.pi - differences)
                direction_spread = float(np.quantile(differences, 0.9))
        return (
            residual <= max(1.75, min(width, height) * 0.019)
            and tortuosity <= 1.055
            and direction_spread <= math.radians(7.5)
        ), _polyline_length(edge["points"])

    # A complete graph edge between semantic endpoints is stronger evidence
    # than a local Hough tangent and also recovers short segments at crossings.
    for edge in edges:
        if edge.get("closed"):
            continue
        linear, edge_length = edge_linearity(edge)
        if not linear or edge_length < minimum_length:
            continue
        values = [np.array(point, dtype=float) for point in edge["points"]]
        start, end, _ = _snap_line(values)
        extended.append((float(start[0]), float(start[1]), float(end[0]), float(end[1])))

    for segment in raw:
        start = np.array(segment[:2], dtype=float)
        end = np.array(segment[2:], dtype=float)
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length < minimum_length:
            continue
        direction = delta / length
        normal = np.array([-direction[1], direction[0]])
        relative = xy - start
        perpendicular = np.abs(relative @ normal)
        projections = relative @ direction
        selected = projections[perpendicular <= 1.55]
        if not len(selected):
            continue
        bins = sorted(set(int(round(value)) for value in selected))
        groups: list[list[int]] = []
        for value in bins:
            if not groups or value - groups[-1][-1] > 2:
                groups.append([value])
            else:
                groups[-1].append(value)
        original_low, original_high = sorted((0.0, length))
        candidates = [
            group for group in groups
            if min(group[-1], original_high) - max(group[0], original_low) >= minimum_length * 0.45
        ]
        if not candidates:
            continue
        group = max(candidates, key=lambda item: (min(item[-1], original_high) - max(item[0], original_low), item[-1] - item[0]))
        low, high = float(group[0]), float(group[-1])
        verified_length = high - low
        coverage = len(group) / max(1.0, verified_length + 1)
        if verified_length < minimum_length or coverage < 0.72:
            continue
        verified_start, verified_end = start + direction * low, start + direction * high
        if not (near_terminal(verified_start, direction) and near_terminal(verified_end, direction)):
            continue
        candidate_tuple = (float(verified_start[0]), float(verified_start[1]), float(verified_end[0]), float(verified_end[1]))
        supported_linear_length = 0.0
        supported_curve_length = 0.0
        for edge in edges:
            edge_points = edge.get("points", [])
            if not edge_points:
                continue
            supported = 0
            for px, py in edge_points:
                projection = (px - candidate_tuple[0]) * direction[0] + (py - candidate_tuple[1]) * direction[1]
                distance_to_line = abs((py - candidate_tuple[1]) * direction[0] - (px - candidate_tuple[0]) * direction[1])
                if -3 <= projection <= verified_length + 3 and distance_to_line <= 2.2:
                    supported += 1
            if supported / len(edge_points) < 0.62:
                continue
            linear, edge_length = edge_linearity(edge)
            if linear:
                supported_linear_length += edge_length
            else:
                supported_curve_length += edge_length
        if supported_linear_length < verified_length * 0.42 or supported_curve_length > supported_linear_length * 0.35:
            continue
        snapped_start, snapped_end, _ = _snap_line([verified_start, verified_end])
        extended.append((float(snapped_start[0]), float(snapped_start[1]), float(snapped_end[0]), float(snapped_end[1])))
    return _line_nms(extended, distance_tolerance=max(2.4, min(width, height) * 0.024), angle_tolerance_degrees=4.0)


def _edge_on_verified_line(edge: dict[str, Any], lines: list[tuple[float, float, float, float]]) -> bool:
    values = edge.get("points", [])
    if not values:
        return False
    for segment in lines:
        x0, y0, x1, y1 = segment
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            continue
        ux, uy = dx / length, dy / length
        supported = 0
        for px, py in values:
            projection = (px - x0) * ux + (py - y0) * uy
            distance = abs((py - y0) * ux - (px - x0) * uy)
            if -3 <= projection <= length + 3 and distance <= 2.2:
                supported += 1
        if supported / len(values) >= 0.72:
            return True
    return False


def _stitch_curve_strokes(strokes: list[dict[str, Any]], maximum_gap: float) -> list[dict[str, Any]]:
    """Reconnect curve pieces separated by a crossing line's stroke width."""
    output = [dict(stroke, points=list(stroke["points"]), throughNodes=list(stroke.get("throughNodes", []))) for stroke in strokes]

    def oriented(stroke: dict[str, Any], reverse: bool) -> dict[str, Any]:
        if not reverse:
            return dict(stroke, points=list(stroke["points"]), throughNodes=list(stroke.get("throughNodes", [])))
        return {
            **stroke,
            "from": stroke.get("to"), "to": stroke.get("from"),
            "points": list(reversed(stroke["points"])),
            "throughNodes": list(reversed(stroke.get("throughNodes", []))),
        }

    while True:
        best: tuple[float, int, int, bool, bool] | None = None
        for first_index in range(len(output)):
            if len(output[first_index]["points"]) < 3:
                continue
            for second_index in range(first_index + 1, len(output)):
                if len(output[second_index]["points"]) < 3:
                    continue
                for reverse_first in (False, True):
                    first = oriented(output[first_index], reverse_first)
                    first_end = np.array(first["points"][-1], dtype=float)
                    first_anchor = np.array(first["points"][-2], dtype=float)
                    for raw in reversed(first["points"][:-1]):
                        candidate_anchor = np.array(raw, dtype=float)
                        if float(np.linalg.norm(first_end - candidate_anchor)) >= 4.5:
                            first_anchor = candidate_anchor
                            break
                    first_tangent = first_end - first_anchor
                    first_length = float(np.linalg.norm(first_tangent))
                    if first_length <= 1e-6:
                        continue
                    first_tangent /= first_length
                    for reverse_second in (False, True):
                        second = oriented(output[second_index], reverse_second)
                        second_start = np.array(second["points"][0], dtype=float)
                        second_anchor = np.array(second["points"][1], dtype=float)
                        for raw in second["points"][1:]:
                            candidate_anchor = np.array(raw, dtype=float)
                            if float(np.linalg.norm(candidate_anchor - second_start)) >= 4.5:
                                second_anchor = candidate_anchor
                                break
                        second_tangent = second_anchor - second_start
                        second_length = float(np.linalg.norm(second_tangent))
                        gap = second_start - first_end
                        gap_length = float(np.linalg.norm(gap))
                        if second_length <= 1e-6 or gap_length > maximum_gap:
                            continue
                        second_tangent /= second_length
                        if float(np.dot(first_tangent, second_tangent)) < math.cos(math.radians(42)):
                            continue
                        if gap_length > 1.25:
                            gap_direction = gap / gap_length
                            if min(float(np.dot(first_tangent, gap_direction)), float(np.dot(second_tangent, gap_direction))) < math.cos(math.radians(58)):
                                continue
                        score = gap_length + (1 - float(np.dot(first_tangent, second_tangent))) * maximum_gap
                        candidate = (score, first_index, second_index, reverse_first, reverse_second)
                        if best is None or candidate < best:
                            best = candidate
        if best is None:
            break
        _, first_index, second_index, reverse_first, reverse_second = best
        first = oriented(output[first_index], reverse_first)
        second = oriented(output[second_index], reverse_second)
        merged = {
            "from": first.get("from"), "to": second.get("to"), "closed": False,
            "throughNodes": [*first.get("throughNodes", []), first.get("to"), second.get("from"), *second.get("throughNodes", [])],
            "points": [*first["points"], *second["points"]],
        }
        output[first_index] = merged
        output.pop(second_index)
    return output


def _dominant_cubic_curve(curve_skeleton: np.ndarray, width: int, height: int, line_width: float) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fit one robust smooth curve after the verified straight layer is removed."""
    points = np.argwhere(curve_skeleton > 0)[:, ::-1].astype(float)
    if len(points) < 10:
        return None, {"status": "insufficient_curve_pixels", "pixelCount": int(len(points))}
    rng = np.random.default_rng(20260810)
    best: tuple[float, int, np.ndarray, np.ndarray, float, float] | None = None
    for orientation in (0, 1):
        u = points[:, orientation]
        v = points[:, 1 - orientation]
        centre = float(np.mean(u))
        scale = max(1.0, float(np.ptp(u)) / 2)
        q = (u - centre) / scale
        if float(np.ptp(u)) < min(width, height) * 0.2:
            continue
        for _ in range(180):
            indices = rng.choice(len(points), 4, replace=False)
            if len(np.unique(np.round(q[indices], 3))) < 4 or float(np.ptp(q[indices])) < 1.1:
                continue
            try:
                coefficients = np.polyfit(q[indices], v[indices], 3)
            except np.linalg.LinAlgError:
                continue
            residuals = np.abs(np.polyval(coefficients, q) - v)
            inliers = residuals <= 2.15
            if int(inliers.sum()) < 9:
                continue
            coverage = float(np.ptp(u[inliers]) / max(1.0, np.ptp(u)))
            score = float(inliers.sum()) + coverage * len(points) * 0.35 - float(np.median(residuals[inliers])) * 2
            candidate = (score, orientation, inliers, coefficients, centre, scale)
            if best is None or candidate[0] > best[0]:
                best = candidate
    if best is None:
        return None, {"status": "curve_ransac_failed", "pixelCount": int(len(points))}
    _, orientation, inliers, coefficients, centre, scale = best
    u = points[:, orientation]
    v = points[:, 1 - orientation]
    q = (u - centre) / scale
    for _ in range(3):
        coefficients = np.polyfit(q[inliers], v[inliers], 3)
        residuals = np.abs(np.polyval(coefficients, q) - v)
        updated = residuals <= 2.25
        if np.array_equal(updated, inliers):
            break
        inliers = updated
    if int(inliers.sum()) < 9:
        return None, {"status": "curve_refit_failed", "pixelCount": int(len(points))}
    u0, u1 = float(u[inliers].min()), float(u[inliers].max())

    def value_and_derivative(raw_u: float) -> tuple[float, float]:
        normalized = (raw_u - centre) / scale
        value = float(np.polyval(coefficients, normalized))
        derivative_coefficients = np.polyder(coefficients)
        derivative = float(np.polyval(derivative_coefficients, normalized) / scale)
        return value, derivative

    v0, derivative0 = value_and_derivative(u0)
    v1, derivative1 = value_and_derivative(u1)
    span = u1 - u0
    if orientation == 0:
        start = np.array([u0, v0]); end = np.array([u1, v1])
        tangent0 = np.array([1.0, derivative0]); tangent1 = np.array([1.0, derivative1])
    else:
        start = np.array([v0, u0]); end = np.array([v1, u1])
        tangent0 = np.array([derivative0, 1.0]); tangent1 = np.array([derivative1, 1.0])
    control1 = start + tangent0 * span / 3
    control2 = end - tangent1 * span / 3
    primitive = {
        "type": "cubicBezier", "from": _norm_point(start[0], start[1], width, height),
        "control1": _norm_point(control1[0], control1[1], width, height),
        "control2": _norm_point(control2[0], control2[1], width, height),
        "to": _norm_point(end[0], end[1], width, height), "geometryClass": "smooth_curve",
        "style": identity_style(None, "#101828", line_width), "transform": identity_transform(),
    }
    residuals = np.abs(np.polyval(coefficients, q) - v)
    return primitive, {
        "status": "verified", "pixelCount": int(len(points)), "inlierCount": int(inliers.sum()),
        "inlierRatio": round(float(inliers.mean()), 6), "axis": "x" if orientation == 0 else "y",
        "domainSpanPixels": round(float(span), 4), "medianResidualPixels": round(float(np.median(residuals[inliers])), 4),
        "maximumResidualPixels": round(float(residuals[inliers].max()), 4),
        "endpoints": [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))],
    }


def _optimized_cubic_curve(curve_skeleton: np.ndarray, width: int, height: int, line_width: float) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Deterministically optimize one semantic cubic against residual curve ink."""
    points = np.argwhere(curve_skeleton > 0)[:, ::-1].astype(float)
    if len(points) < 12:
        return None, {"status": "insufficient_curve_pixels", "pixelCount": int(len(points))}

    def basis(t: np.ndarray) -> np.ndarray:
        return np.column_stack(((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3))

    centred = points - points.mean(axis=0)
    _, _, axes = np.linalg.svd(centred, full_matrices=False)
    projections = [points[:, 0], points[:, 1], centred @ axes[0], centred @ axes[1]]
    candidates: list[tuple[float, np.ndarray, dict[str, Any]]] = []
    sample_t = np.linspace(0, 1, 161)
    sample_basis = basis(sample_t)
    for projection_index, projection in enumerate(projections):
        span = float(np.ptp(projection))
        if span < min(width, height) * 0.18:
            continue
        for reverse in (False, True):
            t = (projection - projection.min()) / max(1e-6, span)
            if reverse:
                t = 1 - t
            inliers = np.ones(len(points), dtype=bool)
            controls = np.zeros((4, 2), dtype=float)
            residuals = np.full(len(points), 999.0)
            for _ in range(12):
                if int(inliers.sum()) < 10:
                    break
                controls, _, _, _ = np.linalg.lstsq(basis(t[inliers]), points[inliers], rcond=None)
                sampled = sample_basis @ controls
                squared = ((points[:, None, :] - sampled[None, :, :]) ** 2).sum(axis=2)
                nearest = squared.argmin(axis=1)
                residuals = np.sqrt(squared[np.arange(len(points)), nearest])
                t = sample_t[nearest]
                cutoff = min(3.2, max(1.65, float(np.quantile(residuals, 0.84))))
                updated = residuals <= cutoff
                if np.array_equal(updated, inliers):
                    break
                inliers = updated
            if int(inliers.sum()) < 10:
                continue
            controls, _, _, _ = np.linalg.lstsq(basis(t[inliers]), points[inliers], rcond=None)
            sampled = sample_basis @ controls
            if (
                controls[:, 0].min() < -width * 0.45 or controls[:, 0].max() > width * 1.45
                or controls[:, 1].min() < -height * 0.45 or controls[:, 1].max() > height * 1.45
            ):
                continue
            squared = ((points[:, None, :] - sampled[None, :, :]) ** 2).sum(axis=2)
            target_distances = np.sqrt(squared.min(axis=1))
            curve_distances = np.sqrt(squared.min(axis=0))
            target_trim = np.sort(target_distances)[:max(10, round(len(target_distances) * 0.86))]
            curve_trim = np.sort(curve_distances)[:max(20, round(len(curve_distances) * 0.9))]
            endpoint_span = float(np.linalg.norm(controls[-1] - controls[0]))
            target_span = max(float(np.ptp(points[:, 0])), float(np.ptp(points[:, 1])))
            span_penalty = max(0.0, target_span * 0.55 - endpoint_span) * 0.18
            score = float(target_trim.mean() + curve_trim.mean() * 0.75 + span_penalty)
            candidates.append((score, controls, {
                "projectionSeed": projection_index, "reverseSeed": reverse,
                "pixelCount": int(len(points)), "inlierCount": int(inliers.sum()),
                "inlierRatio": round(float(inliers.mean()), 6),
                "meanTargetDistancePixels": round(float(target_trim.mean()), 4),
                "meanCurveDistancePixels": round(float(curve_trim.mean()), 4),
            }))
    if not candidates:
        return None, {"status": "bezier_optimization_failed", "pixelCount": int(len(points))}
    score, controls, metrics = min(candidates, key=lambda item: item[0])
    primitive = {
        "type": "cubicBezier", "from": _norm_point(controls[0][0], controls[0][1], width, height),
        "control1": _norm_point(controls[1][0], controls[1][1], width, height),
        "control2": _norm_point(controls[2][0], controls[2][1], width, height),
        "to": _norm_point(controls[3][0], controls[3][1], width, height), "geometryClass": "smooth_curve",
        "style": identity_style(None, "#101828", line_width), "transform": identity_transform(),
    }
    return primitive, {
        "status": "verified_candidate", "objective": round(score, 6), **metrics,
        "endpoints": [(float(controls[0][0]), float(controls[0][1])), (float(controls[3][0]), float(controls[3][1]))],
    }


def extract_line_curve_primitives(crop: np.ndarray, source_asset: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Typed semantic geometry for clean line/curve composition questions.

    Skeleton pixels are measurement evidence only.  The output is a compact
    rect/line/cubic scene plus explicit graph topology; no contour path is
    allowed to cross this boundary.
    """
    height, width = crop.shape[:2]
    raw_mask = np.where(crop < 235, 255, 0).astype(np.uint8)
    frame = _content_frame(crop)
    content_mask = _erase_rect_boundary(raw_mask, frame, max(2, round(min(width, height) * 0.025))) if frame else raw_mask
    count, labels, stats, _ = cv2.connectedComponentsWithStats(content_mask, 8)
    clean = np.zeros_like(content_mask)
    minimum_component = max(4, round(content_mask.size * 0.00045))
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= minimum_component:
            clean[labels == label] = 255
    skeleton = _skeletonize_mask(clean)
    nodes, edges = _trace_skeleton_graph(skeleton)
    nodes, edges = _split_graph_corners(nodes, edges, math.hypot(width, height))
    verified_lines = _verified_center_lines(skeleton, frame, nodes, edges)
    curve_skeleton = skeleton.copy()
    removal_width = max(3, round(min(width, height) * 0.048))
    for x0, y0, x1, y1 in verified_lines:
        cv2.line(curve_skeleton, (round(x0), round(y0)), (round(x1), round(y1)), 0, removal_width)
    curve_nodes, curve_edges = _trace_skeleton_graph(curve_skeleton)
    node_offset = max((int(node["id"]) for node in nodes), default=-1) + 1
    for node in curve_nodes:
        node["id"] = int(node["id"]) + node_offset
    for edge in curve_edges:
        edge["from"] = int(edge["from"]) + node_offset
        if edge.get("to") is not None:
            edge["to"] = int(edge["to"]) + node_offset
    nodes = [*nodes, *curve_nodes]
    strokes = _chain_graph_strokes(curve_edges, force_kind="curve")
    strokes = _stitch_curve_strokes(strokes, max(11.0, min(width, height) * 0.14))
    distance = cv2.distanceTransform(raw_mask, cv2.DIST_L2, 5)
    samples = distance[skeleton > 0]
    line_width = round(float(np.clip(np.percentile(samples, 40) * 1.55 if samples.size else 1.6, 1.2, 3.0)), 3)
    dominant_curve, dominant_curve_metrics = _optimized_cubic_curve(curve_skeleton, width, height, line_width)
    if dominant_curve is None:
        dominant_curve, dominant_curve_metrics = _dominant_cubic_curve(curve_skeleton, width, height, line_width)
    primitives: list[dict[str, Any]] = []
    if frame is not None:
        primitives.append({
            "type": "rect",
            "box": {
                "x": round(frame.x / width * 1000, 3), "y": round(frame.y / height * 1000, 3),
                "width": round(frame.w / width * 1000, 3), "height": round(frame.h / height * 1000, 3),
            },
            "geometryClass": "content_frame",
            "style": identity_style(None, "#101828", line_width), "transform": identity_transform(),
        })
    topology_edges: list[dict[str, Any]] = []
    fit_errors: list[float] = []
    node_by_id = {node["id"]: node for node in nodes}

    def closest_node(point: tuple[float, float]) -> int | None:
        if not nodes:
            return None
        node = min(nodes, key=lambda item: math.hypot(float(item["x"]) - point[0], float(item["y"]) - point[1]))
        return int(node["id"]) if math.hypot(float(node["x"]) - point[0], float(node["y"]) - point[1]) <= max(5.0, min(width, height) * 0.06) else None

    for line_index, segment in enumerate(verified_lines):
        start, end, direction = _snap_line([np.array(segment[:2]), np.array(segment[2:])])
        primitive_start = len(primitives)
        primitives.append({
            "type": "line", "from": _norm_point(start[0], start[1], width, height),
            "to": _norm_point(end[0], end[1], width, height), "geometryClass": direction,
            "style": identity_style(None, "#101828", line_width), "transform": identity_transform(),
        })
        topology_edges.append({
            "id": f"line-{line_index + 1}", "fromNode": closest_node((float(start[0]), float(start[1]))),
            "toNode": closest_node((float(end[0]), float(end[1]))), "throughNodes": [],
            "geometry": "line", "primitiveStart": primitive_start, "primitiveCount": 1,
        })
    if dominant_curve is not None:
        primitive_start = len(primitives)
        primitives.append(dominant_curve)
        endpoints = dominant_curve_metrics.get("endpoints", [])
        topology_edges.append({
            "id": "curve-1", "fromNode": closest_node(tuple(endpoints[0])) if len(endpoints) == 2 else None,
            "toNode": closest_node(tuple(endpoints[1])) if len(endpoints) == 2 else None,
            "throughNodes": [], "geometry": "smooth_curve", "primitiveStart": primitive_start, "primitiveCount": 1,
        })
        fit_errors.append(float(dominant_curve_metrics.get("maximumResidualPixels", 0)))
    else:
        minimum_edge_length = max(8.0, min(width, height) * 0.09)
        for edge_index, edge in enumerate(strokes):
            if edge["closed"] or _polyline_length(edge["points"]) < minimum_edge_length:
                continue
            simplified = _simplify_centerline(edge["points"], False, math.hypot(width, height))
            if len(simplified) < 2:
                continue
            primitive_start = len(primitives)
            edge_primitives, fit_error = _cubic_fit(simplified, width, height, line_width)
            edge_primitives = [primitive for primitive in edge_primitives if primitive["type"] == "cubicBezier"]
            if not edge_primitives:
                continue
            primitives.extend(edge_primitives)
            fit_errors.append(float(fit_error))
            topology_edges.append({
                "id": f"edge-{edge_index + 1}", "fromNode": edge["from"], "toNode": edge.get("to"),
                "throughNodes": edge.get("throughNodes", []), "geometry": "smooth_curve",
                "primitiveStart": primitive_start, "primitiveCount": len(edge_primitives),
            })
    used_nodes = (
        {edge["fromNode"] for edge in topology_edges if edge.get("fromNode") is not None}
        | {edge["toNode"] for edge in topology_edges if edge.get("toNode") is not None}
        | {node_id for edge in topology_edges for node_id in edge.get("throughNodes", []) if node_id is not None}
    )
    topology_nodes = [
        {"id": node_id, "point": _norm_point(node_by_id[node_id]["x"], node_by_id[node_id]["y"], width, height)}
        for node_id in sorted(used_nodes) if node_id in node_by_id
    ]
    type_counts = Counter(primitive["type"] for primitive in primitives)
    reasons: list[str] = []
    if frame is None:
        reasons.append("content_frame_not_verified")
    if not topology_edges:
        reasons.append("typed_geometry_empty")
    if type_counts["cubicBezier"] > 16 or len(primitives) > 28:
        reasons.append("typed_geometry_too_dense")
    return primitives[:MAX_PRIMITIVES], {
        "grammar": "regular_geometry", "subtype": "line_curve_composition",
        "status": "renderer_candidate" if not reasons else "repair_pending", "reason": reasons,
        "sourceEvidence": source_asset, "frameDetected": frame is not None,
        "lineCount": type_counts["line"], "curveCount": type_counts["cubicBezier"],
        "rectCount": type_counts["rect"], "maximumCurveFitErrorPixels": round(max(fit_errors, default=0.0), 4),
        "curveModel": {key: value for key, value in dominant_curve_metrics.items() if key != "endpoints"},
        "topology": {"schemaVersion": "typed-geometry-topology-v1", "nodes": topology_nodes, "edges": topology_edges},
        "forbiddenPrimitiveCounts": {primitive_type: type_counts[primitive_type] for primitive_type in ("pathGroup", "polygon", "face")},
    }


def _skeletonize_mask(mask: np.ndarray) -> np.ndarray:
    """Return a deterministic one-pixel centerline for a binary ink mask."""
    try:
        from skimage.morphology import skeletonize

        return skeletonize(mask > 0).astype(np.uint8)
    except ImportError:
        # Reproducible OpenCV fallback for environments without scikit-image.
        work = np.where(mask > 0, 255, 0).astype(np.uint8)
        skeleton = np.zeros_like(work)
        element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        for _ in range(max(work.shape) * 2):
            opened = cv2.morphologyEx(work, cv2.MORPH_OPEN, element)
            skeleton = cv2.bitwise_or(skeleton, cv2.subtract(work, opened))
            work = cv2.erode(work, element)
            if not np.any(work):
                break
        return (skeleton > 0).astype(np.uint8)


def _skeleton_adjacency(skeleton: np.ndarray) -> dict[tuple[int, int], list[tuple[int, int]]]:
    height, width = skeleton.shape
    points = {tuple(int(value) for value in point) for point in np.argwhere(skeleton > 0)}
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for y, x in points:
        neighbours: list[tuple[int, int]] = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if not (dx or dy):
                    continue
                ny, nx = y + dy, x + dx
                if not (0 <= ny < height and 0 <= nx < width) or (ny, nx) not in points:
                    continue
                # A diagonal is redundant when an orthogonal bridge exists.
                # Removing it prevents one raster corner from becoming a false
                # three-way junction in the topology graph.
                if dx and dy and ((y, nx) in points or (ny, x) in points):
                    continue
                neighbours.append((ny, nx))
        adjacency[(y, x)] = sorted(neighbours)
    return adjacency


def _pixel_edge(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    return (a, b) if a <= b else (b, a)


def _polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(math.hypot(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1]) for index in range(1, len(points)))


def _trace_skeleton_graph(skeleton: np.ndarray) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Trace maximal centerline paths between endpoints/junctions and loops."""
    adjacency = _skeleton_adjacency(skeleton)
    if not adjacency:
        return [], []
    node_pixels = {point for point, neighbours in adjacency.items() if len(neighbours) != 2}
    clusters: list[set[tuple[int, int]]] = []
    remaining = set(node_pixels)
    while remaining:
        start = min(remaining)
        stack = [start]
        cluster: set[tuple[int, int]] = set()
        remaining.remove(start)
        while stack:
            point = stack.pop()
            cluster.add(point)
            for neighbour in adjacency.get(point, []):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    stack.append(neighbour)
        clusters.append(cluster)
    pixel_to_node: dict[tuple[int, int], int] = {}
    nodes: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters):
        for point in cluster:
            pixel_to_node[point] = index
        ys = [point[0] for point in cluster]
        xs = [point[1] for point in cluster]
        nodes.append({"id": index, "x": float(sum(xs) / len(xs)), "y": float(sum(ys) / len(ys)), "pixelCount": len(cluster)})
    visited: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for cluster in clusters:
        for point in cluster:
            for neighbour in adjacency.get(point, []):
                if neighbour in cluster:
                    visited.add(_pixel_edge(point, neighbour))
    edges: list[dict[str, Any]] = []

    def trace(start_node: int, start_pixel: tuple[int, int], first: tuple[int, int]) -> None:
        transition = _pixel_edge(start_pixel, first)
        if transition in visited:
            return
        visited.add(transition)
        anchor = nodes[start_node]
        points: list[tuple[float, float]] = [(anchor["x"], anchor["y"]), (float(first[1]), float(first[0]))]
        previous, current = start_pixel, first
        end_node: int | None = None
        for _ in range(len(adjacency) + 1):
            if current in pixel_to_node:
                end_node = pixel_to_node[current]
                end_anchor = nodes[end_node]
                points[-1] = (end_anchor["x"], end_anchor["y"])
                break
            candidates = [neighbour for neighbour in adjacency.get(current, []) if neighbour != previous]
            if not candidates:
                break
            unvisited = [neighbour for neighbour in candidates if _pixel_edge(current, neighbour) not in visited]
            if not unvisited:
                break
            next_point = min(unvisited)
            visited.add(_pixel_edge(current, next_point))
            points.append((float(next_point[1]), float(next_point[0])))
            previous, current = current, next_point
        if len(points) >= 2:
            edges.append({"from": start_node, "to": end_node, "closed": False, "points": points})

    for node_index, cluster in enumerate(clusters):
        for point in sorted(cluster):
            for neighbour in adjacency.get(point, []):
                if neighbour not in cluster:
                    trace(node_index, point, neighbour)

    # Every unvisited degree-two component is a closed loop and therefore has
    # no natural endpoint. Add a deterministic loop anchor at its first pixel.
    for point in sorted(adjacency):
        for neighbour in adjacency[point]:
            if _pixel_edge(point, neighbour) in visited:
                continue
            loop_node = len(nodes)
            nodes.append({"id": loop_node, "x": float(point[1]), "y": float(point[0]), "pixelCount": 1, "loopAnchor": True})
            loop_points: list[tuple[float, float]] = [(float(point[1]), float(point[0]))]
            previous, current = point, neighbour
            visited.add(_pixel_edge(previous, current))
            for _ in range(len(adjacency) + 1):
                loop_points.append((float(current[1]), float(current[0])))
                candidates = [candidate for candidate in adjacency.get(current, []) if candidate != previous]
                if not candidates:
                    break
                next_point = min(candidates, key=lambda candidate: (_pixel_edge(current, candidate) in visited, candidate))
                if next_point == point:
                    visited.add(_pixel_edge(current, next_point))
                    loop_points.append(loop_points[0])
                    break
                if _pixel_edge(current, next_point) in visited:
                    break
                visited.add(_pixel_edge(current, next_point))
                previous, current = current, next_point
            edges.append({"from": loop_node, "to": loop_node, "closed": True, "points": loop_points})

    # Remove only tiny terminal spurs introduced by antialiasing. Internal
    # short edges are kept because they can encode genuine crossings.
    incidence = Counter()
    for edge in edges:
        incidence[edge["from"]] += 1
        if edge.get("to") is not None and edge["to"] != edge["from"]:
            incidence[edge["to"]] += 1
    minimum_spur = max(2.5, min(skeleton.shape) * 0.018)
    kept: list[dict[str, Any]] = []
    for edge in edges:
        terminal = incidence[edge["from"]] == 1 or (edge.get("to") is not None and incidence[edge["to"]] == 1)
        if terminal and not edge["closed"] and _polyline_length(edge["points"]) < minimum_spur:
            continue
        kept.append(edge)
    used_nodes = {edge["from"] for edge in kept} | {edge["to"] for edge in kept if edge.get("to") is not None}
    return [node for node in nodes if node["id"] in used_nodes], kept


def _chain_graph_strokes(edges: list[dict[str, Any]], force_kind: str | None = None) -> list[dict[str, Any]]:
    """Continue strokes through crossings by pairing opposite local tangents.

    A skeleton graph correctly records intersections but splits both the line
    and the curve at every junction.  Pairing only near-opposite tangents joins
    the same visual stroke through T/X crossings while deliberately preserving
    corners and right angles as separate typed edges.
    """
    incident: defaultdict[int, list[tuple[int, int]]] = defaultdict(list)
    for edge_index, edge in enumerate(edges):
        incident[int(edge["from"])].append((edge_index, 0))
        if edge.get("to") is not None:
            incident[int(edge["to"])].append((edge_index, 1))

    def oriented_points(half: tuple[int, int]) -> list[tuple[float, float]]:
        points = list(edges[half[0]]["points"])
        return points if half[1] == 0 else list(reversed(points))

    def tangent(half: tuple[int, int]) -> np.ndarray | None:
        points = oriented_points(half)
        if len(points) < 2:
            return None
        origin = np.array(points[0], dtype=float)
        for raw in points[1:]:
            delta = np.array(raw, dtype=float) - origin
            length = float(np.linalg.norm(delta))
            if length >= 3.0:
                return delta / length
        return None

    edge_kinds: dict[int, str] = {}
    for edge_index, edge in enumerate(edges):
        values = [np.array(point, dtype=float) for point in edge["points"]]
        residual, tortuosity = _line_residual(values)
        edge_kinds[edge_index] = force_kind or ("line" if residual <= 1.8 and tortuosity <= 1.065 else "curve")

    pairs: dict[tuple[int, int], tuple[int, int]] = {}
    for halves in incident.values():
        remaining = set(halves)
        while len(remaining) >= 2:
            candidates: list[tuple[float, tuple[int, int], tuple[int, int]]] = []
            ordered = sorted(remaining)
            for index, first in enumerate(ordered):
                first_tangent = tangent(first)
                if first_tangent is None:
                    continue
                for second in ordered[index + 1:]:
                    if edge_kinds[first[0]] != edge_kinds[second[0]]:
                        continue
                    second_tangent = tangent(second)
                    if second_tangent is None:
                        continue
                    candidates.append((float(np.dot(first_tangent, second_tangent)), first, second))
            if not candidates:
                break
            dot, first, second = min(candidates, key=lambda item: item[0])
            # At most 36 degrees away from a smooth 180-degree continuation.
            if dot > -math.cos(math.radians(36)):
                break
            pairs[first] = second
            pairs[second] = first
            remaining.remove(first)
            remaining.remove(second)

    def opposite(half: tuple[int, int]) -> tuple[int, int]:
        return half[0], 1 - half[1]

    visited: set[int] = set()
    strokes: list[dict[str, Any]] = []

    def trace(seed: tuple[int, int]) -> dict[str, Any]:
        current = seed
        points: list[tuple[float, float]] = []
        node_ids: list[int] = []
        start_node = edges[current[0]]["from"] if current[1] == 0 else edges[current[0]].get("to")
        end_node = start_node
        while current[0] not in visited:
            edge = edges[current[0]]
            visited.add(current[0])
            chunk = oriented_points(current)
            points.extend(chunk if not points else chunk[1:])
            arrival = opposite(current)
            end_node = edge.get("to") if current[1] == 0 else edge["from"]
            if end_node is not None:
                node_ids.append(int(end_node))
            next_half = pairs.get(arrival)
            if next_half is None or next_half[0] in visited:
                break
            current = next_half
        return {"from": start_node, "to": end_node, "throughNodes": node_ids[:-1], "points": points, "closed": end_node == start_node and len(points) > 2}

    unpaired = [
        half for halves in incident.values() for half in halves
        if half not in pairs
    ]
    for half in sorted(unpaired):
        if half[0] not in visited:
            strokes.append(trace(half))
    for edge_index in range(len(edges)):
        if edge_index not in visited:
            strokes.append(trace((edge_index, 0)))
    return [stroke for stroke in strokes if len(stroke["points"]) >= 2]


def _split_graph_corners(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], diagonal: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Make degree-two visual corners explicit before line/curve separation."""
    output_nodes = [dict(node) for node in nodes]
    next_node = max((int(node["id"]) for node in output_nodes), default=-1) + 1
    output_edges: list[dict[str, Any]] = []
    for edge in edges:
        if edge.get("closed"):
            output_edges.append(edge)
            continue
        simplified = _simplify_centerline(edge["points"], False, diagonal)
        corners = [
            index for index in range(1, len(simplified) - 1)
            if _turn_degrees(simplified[index - 1], simplified[index], simplified[index + 1]) >= 56
        ]
        if not corners:
            output_edges.append(edge)
            continue
        boundaries = [0, *corners, len(simplified) - 1]
        boundary_nodes: list[int | None] = [edge.get("from")]
        for index in corners:
            point = simplified[index]
            output_nodes.append({"id": next_node, "x": float(point[0]), "y": float(point[1]), "pixelCount": 1, "visualCorner": True})
            boundary_nodes.append(next_node)
            next_node += 1
        boundary_nodes.append(edge.get("to"))
        for chunk_index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            points = [(float(point[0]), float(point[1])) for point in simplified[start:end + 1]]
            if len(points) >= 2:
                output_edges.append({
                    "from": boundary_nodes[chunk_index], "to": boundary_nodes[chunk_index + 1],
                    "closed": False, "points": points,
                })
    return output_nodes, output_edges


def _turn_degrees(previous: np.ndarray, point: np.ndarray, following: np.ndarray) -> float:
    incoming = point - previous
    outgoing = following - point
    denominator = float(np.linalg.norm(incoming) * np.linalg.norm(outgoing))
    if denominator <= 1e-6:
        return 0.0
    cosine = float(np.clip(np.dot(incoming, outgoing) / denominator, -1, 1))
    return math.degrees(math.acos(cosine))


def _simplify_centerline(points: list[tuple[float, float]], closed: bool, diagonal: float) -> list[np.ndarray]:
    values = np.array(points, dtype=np.float32)
    if len(values) < 2:
        return []
    if closed and np.linalg.norm(values[0] - values[-1]) <= 1.5:
        values = values[:-1]
    epsilon = max(0.85, diagonal * 0.0045)
    approximation = cv2.approxPolyDP(values.reshape((-1, 1, 2)), epsilon, closed).reshape((-1, 2))
    simplified = [point.astype(float) for point in approximation]
    if not closed:
        if np.linalg.norm(simplified[0] - values[0]) > 0.5:
            simplified.insert(0, values[0].astype(float))
        if np.linalg.norm(simplified[-1] - values[-1]) > 0.5:
            simplified.append(values[-1].astype(float))
    return simplified


def _line_residual(points: list[np.ndarray]) -> tuple[float, float]:
    if len(points) <= 2:
        return 0.0, 1.0
    start, end = points[0], points[-1]
    chord = float(np.linalg.norm(end - start))
    if chord <= 1e-6:
        return float("inf"), float("inf")
    direction = end - start
    residual = max(abs(float(direction[0] * (point - start)[1] - direction[1] * (point - start)[0])) / chord for point in points[1:-1])
    path_length = sum(float(np.linalg.norm(points[index] - points[index - 1])) for index in range(1, len(points)))
    return residual, path_length / chord


def _chunk_to_primitives(points: list[np.ndarray], width: int, height: int, line_width: float, closed_cycle: bool = False) -> list[dict[str, Any]]:
    if len(points) < 2:
        return []
    residual, tortuosity = _line_residual(points)
    style = identity_style(None, "#101828", line_width)
    if not closed_cycle and residual <= max(0.8, math.hypot(width, height) * 0.006) and tortuosity <= 1.035:
        return [{"type": "line", "from": _norm_point(points[0][0], points[0][1], width, height), "to": _norm_point(points[-1][0], points[-1][1], width, height), "style": style, "transform": identity_transform()}]
    result: list[dict[str, Any]] = []
    count = len(points)
    segment_count = count if closed_cycle else count - 1
    for index in range(segment_count):
        first = points[index]
        second = points[(index + 1) % count]
        chord = second - first
        chord_length = float(np.linalg.norm(chord))
        if chord_length <= 0.55:
            continue
        before = points[(index - 1) % count] if closed_cycle or index > 0 else first
        after = points[(index + 2) % count] if closed_cycle or index + 2 < count else second
        control1 = first + (second - before) / 6.0
        control2 = second - (after - first) / 6.0

        # Catmull-Rom conversion can create a tiny loop when one simplified
        # segment is short and its neighbouring segment is long. Keep both
        # handles forward-facing and bounded by the local chord. This retains
        # smooth curves without inventing circular hooks at corners/endpoints.
        direction = chord / chord_length
        handle1 = control1 - first
        handle2 = second - control2
        maximum_handle = chord_length * 0.45
        if float(np.dot(handle1, direction)) < 0:
            handle1 = np.zeros(2, dtype=float)
        if float(np.dot(handle2, direction)) < 0:
            handle2 = np.zeros(2, dtype=float)
        if float(np.linalg.norm(handle1)) > maximum_handle:
            handle1 *= maximum_handle / float(np.linalg.norm(handle1))
        if float(np.linalg.norm(handle2)) > maximum_handle:
            handle2 *= maximum_handle / float(np.linalg.norm(handle2))
        control1 = first + handle1
        control2 = second - handle2
        result.append({
            "type": "cubicBezier",
            "from": _norm_point(first[0], first[1], width, height),
            "control1": _norm_point(control1[0], control1[1], width, height),
            "control2": _norm_point(control2[0], control2[1], width, height),
            "to": _norm_point(second[0], second[1], width, height),
            "style": style,
            "transform": identity_transform(),
        })
    return result


def _centerline_primitives(points: list[tuple[float, float]], closed: bool, width: int, height: int, line_width: float) -> list[dict[str, Any]]:
    simplified = _simplify_centerline(points, closed, math.hypot(width, height))
    if len(simplified) < 2:
        return []
    if not closed:
        corners = [index for index in range(1, len(simplified) - 1) if _turn_degrees(simplified[index - 1], simplified[index], simplified[index + 1]) >= 68]
        boundaries = [0, *corners, len(simplified) - 1]
        primitives: list[dict[str, Any]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            primitives.extend(_chunk_to_primitives(simplified[start:end + 1], width, height, line_width))
        return primitives
    corners = [index for index in range(len(simplified)) if _turn_degrees(simplified[index - 1], simplified[index], simplified[(index + 1) % len(simplified)]) >= 68]
    if len(corners) < 2:
        return _chunk_to_primitives(simplified, width, height, line_width, closed_cycle=True)
    primitives = []
    for index, start in enumerate(corners):
        end = corners[(index + 1) % len(corners)]
        chunk = [simplified[start]]
        cursor = (start + 1) % len(simplified)
        while cursor != end:
            chunk.append(simplified[cursor])
            cursor = (cursor + 1) % len(simplified)
        chunk.append(simplified[end])
        primitives.extend(_chunk_to_primitives(chunk, width, height, line_width))
    return primitives


def _render_stroke_primitives(primitives: list[dict[str, Any]], width: int, height: int) -> np.ndarray:
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    box = Box(0, 0, width, height)
    for primitive in primitives:
        stroke_width = max(1, round(float(primitive.get("style", {}).get("lineWidth") or 1)))
        if primitive["type"] == "line":
            draw.line([_svg_point(primitive["from"], box), _svg_point(primitive["to"], box)], fill=0, width=stroke_width)
        elif primitive["type"] == "cubicBezier":
            start = np.array(_svg_point(primitive["from"], box))
            control1 = np.array(_svg_point(primitive["control1"], box))
            control2 = np.array(_svg_point(primitive["control2"], box))
            end = np.array(_svg_point(primitive["to"], box))
            curve = []
            for step in range(31):
                t = step / 30
                point = (1 - t) ** 3 * start + 3 * (1 - t) ** 2 * t * control1 + 3 * (1 - t) * t ** 2 * control2 + t ** 3 * end
                curve.append((float(point[0]), float(point[1])))
            draw.line(curve, fill=0, width=stroke_width, joint="curve")
    return np.array(canvas)


def _stroke_raster_metrics(source_mask: np.ndarray, skeleton: np.ndarray, redrawn: np.ndarray) -> dict[str, Any]:
    target = redrawn < 235
    source = source_mask > 0
    tolerance = max(1, round(min(source.shape) * 0.018))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (tolerance * 2 + 1, tolerance * 2 + 1))
    source_dilated = cv2.dilate(source.astype(np.uint8), kernel) > 0
    target_dilated = cv2.dilate(target.astype(np.uint8), kernel) > 0
    precision = float((target & source_dilated).sum() / max(1, target.sum()))
    recall = float((source & target_dilated).sum() / max(1, source.sum()))
    target_skeleton = _skeletonize_mask(target.astype(np.uint8) * 255)
    distance_to_target = cv2.distanceTransform(np.where(target_skeleton > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3)
    distance_to_source = cv2.distanceTransform(np.where(skeleton > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3)
    diagonal = max(1.0, math.hypot(source.shape[1], source.shape[0]))
    source_chamfer = float(distance_to_target[skeleton > 0].mean(dtype=np.float64) / diagonal) if np.any(skeleton) and np.any(target_skeleton) else 1.0
    target_chamfer = float(distance_to_source[target_skeleton > 0].mean(dtype=np.float64) / diagonal) if np.any(target_skeleton) and np.any(skeleton) else 1.0

    def component_count(mask: np.ndarray) -> int:
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        minimum = max(2, round(mask.size * 0.00015))
        return sum(int(row[cv2.CC_STAT_AREA]) >= minimum for row in stats[1:count])

    return {
        "edgePrecision": round(precision, 6),
        "edgeRecall": round(recall, 6),
        "sourceToRedrawChamfer": round(source_chamfer, 6),
        "redrawToSourceChamfer": round(target_chamfer, 6),
        "sourceComponentCount": component_count(source),
        "redrawnComponentCount": component_count(target),
        "tolerancePixels": tolerance,
    }


def extract_stroke_graph_primitives(crop: np.ndarray, source_asset: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Universal line-art representation: centerline topology plus line/cubic edges."""
    height, width = crop.shape[:2]
    if min(height, width) <= 0:
        return [], {"grammar": "stroke_graph", "status": "repair_pending", "reason": "empty_crop"}
    raw_mask = np.where(crop < 235, 255, 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, 8)
    minimum_component = max(3, round(raw_mask.size * 0.00012))
    mask = np.zeros_like(raw_mask)
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= minimum_component:
            mask[labels == label] = 255
    if not np.any(mask):
        return [], {"grammar": "stroke_graph", "status": "repair_pending", "reason": "no_ink_after_noise_filter"}
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    wide_threshold = max(3.2, min(width, height) * 0.035)
    wide_ink_ratio = float(((distance >= wide_threshold) & (mask > 0)).sum() / max(1, (mask > 0).sum()))
    skeleton = _skeletonize_mask(mask)
    nodes, edges = _trace_skeleton_graph(skeleton)
    positive_distances = distance[skeleton > 0]
    line_width = round(float(np.clip(np.percentile(positive_distances, 35) * 1.65 if positive_distances.size else 1.6, 1.2, 5.0)), 3)
    primitives: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    for index, edge in enumerate(edges):
        start = len(primitives)
        edge_primitives = _centerline_primitives(edge["points"], bool(edge["closed"]), width, height, line_width)
        primitives.extend(edge_primitives)
        graph_edges.append({
            "id": f"edge-{index + 1}",
            "fromNode": edge["from"],
            "toNode": edge.get("to"),
            "closed": bool(edge["closed"]),
            "sourcePixelLength": round(_polyline_length(edge["points"]), 3),
            "primitiveStart": start,
            "primitiveCount": len(edge_primitives),
        })
    primitives = primitives[:MAX_PRIMITIVES]
    incidence = Counter()
    for edge in graph_edges:
        incidence[edge["fromNode"]] += 1
        if edge.get("toNode") is not None and edge["toNode"] != edge["fromNode"]:
            incidence[edge["toNode"]] += 1
    graph_nodes = [{
        "id": node["id"],
        "point": _norm_point(node["x"], node["y"], width, height),
        "kind": "loop_anchor" if node.get("loopAnchor") else ("endpoint" if incidence[node["id"]] <= 1 else "junction"),
        "degree": incidence[node["id"]],
    } for node in nodes]
    redrawn = _render_stroke_primitives(primitives, width, height)
    raster_metrics = _stroke_raster_metrics(mask, skeleton, redrawn)
    topology_ok = raster_metrics["sourceComponentCount"] == raster_metrics["redrawnComponentCount"]
    raster_ok = (
        raster_metrics["edgePrecision"] >= 0.92
        and raster_metrics["edgeRecall"] >= 0.92
        and raster_metrics["sourceToRedrawChamfer"] <= 0.012
        and raster_metrics["redrawToSourceChamfer"] <= 0.012
    )
    reasons: list[str] = []
    if wide_ink_ratio > 0.015:
        reasons.append("filled_or_wide_region_requires_separate_face_layer")
    if not graph_edges or not primitives:
        reasons.append("stroke_graph_empty")
    if len(primitives) >= MAX_PRIMITIVES:
        reasons.append("primitive_limit_reached")
    if not topology_ok:
        reasons.append("rendered_component_topology_mismatch")
    if not raster_ok:
        reasons.append("strict_raster_geometry_gate_failed")
    return primitives, {
        "grammar": "stroke_graph",
        "subtype": "centerline_bezier_graph",
        "status": "renderer_candidate" if not reasons else "repair_pending",
        "reason": reasons,
        "sourceEvidence": source_asset,
        "lineWidth": line_width,
        "wideInkRatio": round(wide_ink_ratio, 6),
        "nodeCount": len(graph_nodes),
        "edgeCount": len(graph_edges),
        "endpointCount": sum(node["kind"] == "endpoint" for node in graph_nodes),
        "junctionCount": sum(node["kind"] == "junction" for node in graph_nodes),
        "loopCount": sum(edge["closed"] for edge in graph_edges),
        "lineCount": sum(primitive["type"] == "line" for primitive in primitives),
        "cubicBezierCount": sum(primitive["type"] == "cubicBezier" for primitive in primitives),
        "rasterVerification": raster_metrics,
        "strokeGraph": {"schemaVersion": "deterministic-stroke-graph-v1", "nodes": graph_nodes, "edges": graph_edges},
    }


def extract_primitives(crop: np.ndarray, source_asset: str, allow_circles: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    height, width = crop.shape[:2]
    if width <= 0 or height <= 0:
        return [], {"contourCount": 0, "keptContourCount": 0, "houghLineCount": 0, "circleCount": 0}
    mask = cv2.threshold(crop, 235, 255, cv2.THRESH_BINARY_INV)[1]
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    primitives: list[dict[str, Any]] = []
    contour_count = len(contours)
    kept = 0
    image_area = width * height
    for index, contour in enumerate(contours):
        x, y, w, h = cv2.boundingRect(contour)
        area = abs(cv2.contourArea(contour))
        perimeter = cv2.arcLength(contour, True)
        if area < 4 or perimeter < 10 or w >= width * 0.97 or h >= height * 0.97:
            continue
        if kept >= MAX_CONTOURS:
            break
        approx = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.012), True)
        kept += 1
        fill = _contour_color(crop, contour)
        # Extremely thin contours are better represented as line segments.
        if len(approx) == 2:
            a, b = approx[0][0], approx[1][0]
            primitives.append({"type": "line", "from": {"x": round(a[0] / width * 1000, 3), "y": round(a[1] / height * 1000, 3)}, "to": {"x": round(b[0] / width * 1000, 3), "y": round(b[1] / height * 1000, 3)}, "style": identity_style(None, "#101828", 2), "transform": identity_transform()})
        elif len(approx) <= 8 and area / image_area < 0.28:
            points = [{"x": round(float(point[0][0]) / width * 1000, 3), "y": round(float(point[0][1]) / height * 1000, 3)} for point in approx]
            primitives.append({"type": "polygon", "points": points, "style": identity_style(fill, "#101828", 1.8), "transform": identity_transform()})
        else:
            primitives.append({"type": "pathGroup", "paths": [{"commands": _path_commands(approx, width, height), "fillRule": "evenodd"}], "sourceEvidence": {"sourceAsset": source_asset, "extractionMethod": "cv_contour", "verified": True}, "style": identity_style(fill, "#101828", 1.6), "transform": identity_transform()})
    lines = None
    if len(primitives) < 12 and width * height <= 150_000:
        lines = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=max(10, min(width, height) // 10), minLineLength=max(12, min(width, height) // 9), maxLineGap=4)
    hough_count = int(len(lines)) if lines is not None else 0
    if lines is not None and len(primitives) < 12:
        for line in lines[:24]:
            x0, y0, x1, y1 = [int(value) for value in line[0]]
            length = math.hypot(x1 - x0, y1 - y0)
            if length < min(width, height) * 0.14:
                continue
            primitives.append({"type": "line", "from": {"x": round(x0 / width * 1000, 3), "y": round(y0 / height * 1000, 3)}, "to": {"x": round(x1 / width * 1000, 3), "y": round(y1 / height * 1000, 3)}, "style": identity_style(None, "#101828", 1.5), "transform": identity_transform()})
    accepted_circles = _circle_candidates(contours, width, height, limit=4) if allow_circles else []
    circle_count = len(accepted_circles)
    for cx, cy, radius in accepted_circles:
        primitives.append({"type": "circle", "center": {"x": round(cx / width * 1000, 3), "y": round(cy / height * 1000, 3)}, "radius": round(radius / max(width, height) * 1000, 3), "style": identity_style(None, "#101828", 1.5), "transform": identity_transform()})
    # Keep deterministic output within the frontend contract limits.
    primitives = primitives[:MAX_PRIMITIVES]
    return primitives, {"contourCount": contour_count, "keptContourCount": kept, "houghLineCount": hough_count, "circleCount": circle_count}


def build_scene(row: dict[str, Any], classification: dict[str, Any], gray: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
    boxes = [Box(int(item["x"]), int(item["y"]), int(item["width"]), int(item["height"])) for item in classification["panelBoxes"]]
    roles = classification.get("panelRoles", ["sequence"] * len(boxes))
    grammar = classification.get("visualGrammar", "regular_geometry")
    if grammar == "textured_pie" and (not boxes or max(box.area for box in boxes) < gray.shape[0] * gray.shape[1] * 0.6):
        boxes = [Box(0, 0, int(gray.shape[1]), int(gray.shape[0]))]
        roles = ["sequence"]
    panels: list[dict[str, Any]] = []
    primitive_total = 0
    path_commands = 0
    point_count = 0
    wireframe_edges = 0
    glyph_code_points = 0
    panel_metrics: list[dict[str, Any]] = []
    grammar_metrics: list[dict[str, Any]] = []
    regular_renderer_subtypes = {
        "grid_partition",
        "filled_region_composition",
        "dot_marker_composition",
        "circle_polygon_composition",
        "circle_composition",
        "closed_polygon_composition",
        "straight_line_network",
        "irregular_curve_composition",
        "curve_line_composition",
        "line_curve_composition",
    }
    for index, box in enumerate(boxes):
        crop, _, _ = _box_crop(gray, box)
        if grammar == "regular_geometry" and classification.get("regularSubtype") == "grid_partition":
            crop = gray[max(0, box.y):min(gray.shape[0], box.y + box.h), max(0, box.x):min(gray.shape[1], box.x + box.w)]
        missing_panel = _is_question_mark_panel(crop)
        if missing_panel:
            primitives = []
            metrics = {"grammar": grammar, "subtype": classification.get("regularSubtype"), "status": "valid", "missingMarker": "question_mark_component_geometry"}
        elif grammar == "textured_pie":
            primitives, metrics = extract_textured_pie_primitives(crop, f"sha256:{row['sha256']}")
            if not primitives:
                primitives, fallback_metrics = extract_primitives(crop, f"sha256:{row['sha256']}", allow_circles=False)
                metrics = {**fallback_metrics, "grammar": "textured_pie", "status": "repair_pending", "fallback": "regular_geometry_only"}
        elif grammar == "regular_geometry":
            if classification.get("regularSubtype") in regular_renderer_subtypes:
                if classification.get("regularSubtype") == "grid_partition":
                    primitives, metrics = extract_grid_primitives(crop)
                elif classification.get("regularSubtype") == "line_curve_composition":
                    primitives, metrics = extract_line_curve_primitives(crop, f"sha256:{row['sha256']}")
                elif classification.get("regularSubtype") in {"irregular_curve_composition", "curve_line_composition"}:
                    primitives, metrics = extract_stroke_graph_primitives(crop, f"sha256:{row['sha256']}")
                else:
                    primitives, metrics = extract_regular_primitives(crop, f"sha256:{row['sha256']}", str(classification["regularSubtype"]))
            else:
                primitives = []
                metrics = {
                    "grammar": "regular_geometry",
                    "subtype": classification.get("regularSubtype"),
                    "status": "repair_pending",
                    "fallback": "disabled_no_generic_contour_for_unvalidated_subtype",
                }
        else:
            primitives, metrics = extract_primitives(crop, f"sha256:{row['sha256']}", allow_circles=(grammar == "curve_circle_arc"))
            if grammar == "curve_circle_arc":
                extras = extract_curve_extras(crop)
                primitives.extend(extras)
                metrics["grammar"] = "curve_circle_arc"
                metrics["curveExtras"] = len(extras)
            elif grammar == "folded_3d":
                wireframe = extract_wireframe_primitive(crop)
                if wireframe is not None:
                    primitives.insert(0, wireframe)
                metrics["grammar"] = "folded_3d"
                metrics["wireframeExtracted"] = wireframe is not None
            else:
                metrics["grammar"] = grammar
        role = "missing" if missing_panel else (roles[index] if index < len(roles) else "sequence")
        panel_id = f"panel-{index + 1}"
        panel: dict[str, Any] = {"id": panel_id, "role": role, "position": index + 1, "primitives": primitives}
        if isinstance(metrics.get("topology"), dict):
            panel["topology"] = metrics["topology"]
        if classification["layoutType"] in {"matrix_3x3", "classification_2x3"}:
            panel["row"] = index // 3
            panel["column"] = index % 3
        panels.append(panel)
        grammar_metrics.append({"id": panel_id, "grammar": grammar, **{key: value for key, value in metrics.items() if key in {"status", "subtype", "missingMarker", "angleMethod", "sectorAngles", "texturePrimitives", "curveExtras", "wireframeExtracted", "fallback", "polygonCount", "filledFaceCount", "circleCount", "ellipseCount", "dotCount", "rawHoughLineCount", "lineCountAfterNmsAndEdgeSuppression", "gridColumns", "gridRows", "verticalLineCount", "horizontalLineCount", "filledCells", "nodeCount", "edgeCount", "endpointCount", "junctionCount", "loopCount", "lineCount", "curveCount", "rectCount", "frameDetected", "maximumCurveFitErrorPixels", "forbiddenPrimitiveCounts", "cubicBezierCount", "wideInkRatio", "rasterVerification", "reason"}}})
        primitive_total += len(primitives)
        for primitive in primitives:
            if primitive["type"] == "pathGroup":
                path_commands += sum(len(path["commands"]) for path in primitive["paths"])
            if primitive["type"] in {"polyline", "polygon", "face"}:
                point_count += len(primitive["points"])
            if primitive["type"] == "line":
                point_count += 2
            if primitive["type"] == "quadraticBezier":
                point_count += 3
            if primitive["type"] == "cubicBezier":
                point_count += 4
            if primitive["type"] == "wireframe":
                wireframe_edges += len(primitive["edges"])
            if primitive["type"] == "textGlyph":
                glyph_code_points += len(primitive["content"])
        panel_metrics.append({"id": panel_id, "box": box.as_dict(gray.shape[1], gray.shape[0]), **metrics})
    visual = {
        "schemaVersion": PRIMITIVE_SCHEMA,
        "kind": "primitivePanels",
        "visualGrammar": "primitive_scene",
        "fidelityTier": "deterministic_vector",
        "layoutType": classification["layoutType"],
        "coordinateSpace": {"width": 1000, "height": 1000},
        "panels": panels,
        "evidence": {
            "sourceAsset": row["assetId"],
            "sourceSha256": row["sha256"],
            "sourcePaths": row["localPaths"],
            "sourceDimensions": {"width": int(gray.shape[1]), "height": int(gray.shape[0])},
            "detector": "opencv-grammar-specific-deterministic-v3",
            "semanticSource": classification.get("semanticSource"),
            "semanticCategories": classification.get("semanticCategories", []),
            "qwenCandidate": classification.get("qwenCandidate"),
            "qwenCandidateValidation": classification.get("qwenCandidateValidation"),
            "visualGrammar": grammar,
            "regularSubtype": classification.get("regularSubtype"),
            "regularSubtypeFeatures": classification.get("regularSubtypeFeatures"),
            "grammarRenderer": (
                "stroke_graph_centerline_bezier_v1"
                if classification.get("regularSubtype") in {"irregular_curve_composition", "curve_line_composition"}
                else "typed_rect_line_cubic_topology_v1"
                if classification.get("regularSubtype") == "line_curve_composition"
                else classification.get("regularSubtypeRenderer") if grammar == "regular_geometry" else classification.get("grammarRenderer")
            ),
            "grammarMetrics": grammar_metrics,
            "panelBoxes": [box.as_dict(gray.shape[1], gray.shape[0]) for box in boxes],
            "panelMetrics": panel_metrics,
            "textDetection": {"method": "cv_heuristic", "detected": False, "requiresQwenForGlyphSemantics": True},
            "directionDetection": {"method": "cv_hough_prior", "detected": "arrow_direction" in classification.get("categories", [])},
        },
        "complexity": {
            "primitiveCount": primitive_total,
            "pathCommandCount": path_commands,
            "pointCount": point_count,
            "wireframeEdgeCount": wireframe_edges,
            "glyphCodePointCount": glyph_code_points,
            "estimatedDrawCommandCount": primitive_total + path_commands,
        },
    }
    return visual, {"panelMetrics": panel_metrics, "primitiveCount": primitive_total}


def _svg_point(point: dict[str, Any], box: Box) -> tuple[float, float]:
    return box.x + float(point["x"]) / 1000 * box.w, box.y + float(point["y"]) / 1000 * box.h


def render_svg(visual: dict[str, Any], classification: dict[str, Any], width: int, height: int) -> str:
    raw_boxes = visual.get("evidence", {}).get("panelBoxes") or classification["panelBoxes"]
    boxes = [Box(int(item["x"]), int(item["y"]), int(item["width"]), int(item["height"])) for item in raw_boxes]
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="#ffffff"/>']
    for index, panel in enumerate(visual["panels"]):
        if index >= len(boxes):
            break
        box = boxes[index]
        lines.append(f'<rect x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h}" fill="none" stroke="#101828" stroke-width="2"/>')
        for primitive in panel.get("primitives", []):
            style = primitive.get("style", {})
            stroke = style.get("stroke") or "none"
            fill = style.get("fill") or "none"
            stroke_width = float(style.get("lineWidth") or 1)
            if primitive["type"] == "line":
                a, b = _svg_point(primitive["from"], box), _svg_point(primitive["to"], box)
                lines.append(f'<line x1="{a[0]:.2f}" y1="{a[1]:.2f}" x2="{b[0]:.2f}" y2="{b[1]:.2f}" stroke="{stroke}" stroke-width="{stroke_width}" fill="none"/>')
            elif primitive["type"] in {"polygon", "polyline", "face"}:
                points = " ".join(f"{_svg_point(point, box)[0]:.2f},{_svg_point(point, box)[1]:.2f}" for point in primitive["points"])
                lines.append(f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] in {"rect", "roundedRect"}:
                primitive_box = primitive["box"]
                x = box.x + float(primitive_box["x"]) / 1000 * box.w
                y = box.y + float(primitive_box["y"]) / 1000 * box.h
                rect_width = float(primitive_box["width"]) / 1000 * box.w
                rect_height = float(primitive_box["height"]) / 1000 * box.h
                radius = float(primitive.get("radius") or 0) / 1000 * max(box.w, box.h)
                lines.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{rect_width:.2f}" height="{rect_height:.2f}" rx="{radius:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] == "circle":
                center = _svg_point(primitive["center"], box)
                radius = float(primitive["radius"]) / 1000 * max(box.w, box.h)
                lines.append(f'<circle cx="{center[0]:.2f}" cy="{center[1]:.2f}" r="{radius:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] == "ellipse":
                center = _svg_point(primitive["center"], box)
                radius_x = float(primitive["radiusX"]) / 1000 * box.w
                radius_y = float(primitive["radiusY"]) / 1000 * box.h
                rotation = float(primitive.get("rotationDegrees") or 0)
                lines.append(f'<ellipse cx="{center[0]:.2f}" cy="{center[1]:.2f}" rx="{radius_x:.2f}" ry="{radius_y:.2f}" transform="rotate({rotation:.2f} {center[0]:.2f} {center[1]:.2f})" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] == "dot":
                center = _svg_point(primitive["center"], box)
                radius = float(primitive["radius"]) / 1000 * max(box.w, box.h)
                lines.append(f'<circle cx="{center[0]:.2f}" cy="{center[1]:.2f}" r="{radius:.2f}" fill="{fill or stroke}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] == "arc":
                center = _svg_point(primitive["center"], box)
                rx = float(primitive["radiusX"]) / 1000 * box.w
                ry = float(primitive["radiusY"]) / 1000 * box.h
                start = math.radians(float(primitive["startDegrees"]))
                end = math.radians(float(primitive["endDegrees"]))
                sx, sy = center[0] + rx * math.cos(start), center[1] + ry * math.sin(start)
                ex, ey = center[0] + rx * math.cos(end), center[1] + ry * math.sin(end)
                sweep = 0 if primitive.get("anticlockwise") else 1
                span = abs(float(primitive["endDegrees"]) - float(primitive["startDegrees"])) % 360
                large = 1 if span > 180 else 0
                lines.append(f'<path d="M {sx:.2f} {sy:.2f} A {rx:.2f} {ry:.2f} 0 {large} {sweep} {ex:.2f} {ey:.2f}" fill="none" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] == "quadraticBezier":
                a = _svg_point(primitive["from"], box)
                c = _svg_point(primitive["control"], box)
                b = _svg_point(primitive["to"], box)
                lines.append(f'<path d="M {a[0]:.2f} {a[1]:.2f} Q {c[0]:.2f} {c[1]:.2f} {b[0]:.2f} {b[1]:.2f}" fill="none" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] == "cubicBezier":
                a = _svg_point(primitive["from"], box)
                c1 = _svg_point(primitive["control1"], box)
                c2 = _svg_point(primitive["control2"], box)
                b = _svg_point(primitive["to"], box)
                lines.append(f'<path d="M {a[0]:.2f} {a[1]:.2f} C {c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {b[0]:.2f} {b[1]:.2f}" fill="none" stroke="{stroke}" stroke-width="{stroke_width}"/>')
            elif primitive["type"] == "wireframe":
                vertices = [_svg_point(vertex, box) for vertex in primitive.get("vertices", [])]
                for start, end in primitive.get("edges", []):
                    if start < len(vertices) and end < len(vertices):
                        a, b = vertices[start], vertices[end]
                        lines.append(f'<line x1="{a[0]:.2f}" y1="{a[1]:.2f}" x2="{b[0]:.2f}" y2="{b[1]:.2f}" stroke="{stroke}" stroke-width="{stroke_width}" fill="none"/>')
            elif primitive["type"] == "pathGroup":
                for path in primitive.get("paths", []):
                    chunks: list[str] = []
                    for command in path.get("commands", []):
                        if command["command"] == "Z":
                            chunks.append("Z")
                        else:
                            point = _svg_point(command, box)
                            chunks.append(("M" if command["command"] == "M" else "L") + f" {point[0]:.2f} {point[1]:.2f}")
                    lines.append(f'<path d="{" ".join(chunks)}" fill="{fill}" fill-rule="{path.get("fillRule", "evenodd")}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _font() -> ImageFont.ImageFont:
    for path in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).is_file():
            return ImageFont.truetype(path, 24)
    return ImageFont.load_default()


def render_png(visual: dict[str, Any], classification: dict[str, Any], width: int, height: int) -> Image.Image:
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    raw_boxes = visual.get("evidence", {}).get("panelBoxes") or classification["panelBoxes"]
    boxes = [Box(int(item["x"]), int(item["y"]), int(item["width"]), int(item["height"])) for item in raw_boxes]
    for index, panel in enumerate(visual["panels"]):
        if index >= len(boxes):
            break
        box = boxes[index]
        draw.rectangle((box.x, box.y, box.x + box.w, box.y + box.h), outline="#101828", width=2)
        for primitive in panel.get("primitives", []):
            style = primitive.get("style", {})
            stroke = style.get("stroke") or "black"
            fill = style.get("fill") or None
            stroke_width = max(1, round(float(style.get("lineWidth") or 1)))
            if primitive["type"] == "line":
                draw.line([_svg_point(primitive["from"], box), _svg_point(primitive["to"], box)], fill=stroke, width=stroke_width)
            elif primitive["type"] in {"polygon", "polyline", "face"}:
                points = [_svg_point(point, box) for point in primitive["points"]]
                if fill:
                    draw.polygon(points, fill=fill)
                draw.line(points + ([points[0]] if primitive["type"] != "polyline" and points else []), fill=stroke, width=stroke_width, joint="curve")
            elif primitive["type"] in {"rect", "roundedRect"}:
                primitive_box = primitive["box"]
                x = box.x + float(primitive_box["x"]) / 1000 * box.w
                y = box.y + float(primitive_box["y"]) / 1000 * box.h
                rect_width = float(primitive_box["width"]) / 1000 * box.w
                rect_height = float(primitive_box["height"]) / 1000 * box.h
                radius = float(primitive.get("radius") or 0) / 1000 * max(box.w, box.h)
                if primitive["type"] == "roundedRect":
                    draw.rounded_rectangle((x, y, x + rect_width, y + rect_height), radius=radius, fill=fill, outline=stroke if style.get("stroke") else None, width=stroke_width)
                else:
                    draw.rectangle((x, y, x + rect_width, y + rect_height), fill=fill, outline=stroke if style.get("stroke") else None, width=stroke_width)
            elif primitive["type"] == "circle":
                center = _svg_point(primitive["center"], box)
                radius = float(primitive["radius"]) / 1000 * max(box.w, box.h)
                draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=fill, outline=stroke, width=stroke_width)
            elif primitive["type"] == "ellipse":
                center = _svg_point(primitive["center"], box)
                radius_x = float(primitive["radiusX"]) / 1000 * box.w
                radius_y = float(primitive["radiusY"]) / 1000 * box.h
                rotation = float(primitive.get("rotationDegrees") or 0)
                layer_size = max(4, int(math.ceil(max(radius_x, radius_y) * 2 + stroke_width * 4)))
                layer = Image.new("RGBA", (layer_size, layer_size), (255, 255, 255, 0))
                layer_draw = ImageDraw.Draw(layer)
                layer_center = layer_size / 2
                layer_draw.ellipse((layer_center - radius_x, layer_center - radius_y, layer_center + radius_x, layer_center + radius_y), fill=fill, outline=stroke, width=stroke_width)
                rotated = layer.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
                canvas.paste(rotated, (round(center[0] - rotated.width / 2), round(center[1] - rotated.height / 2)), rotated)
            elif primitive["type"] == "dot":
                center = _svg_point(primitive["center"], box)
                radius = float(primitive["radius"]) / 1000 * max(box.w, box.h)
                draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=fill or stroke, outline=stroke, width=stroke_width)
            elif primitive["type"] == "arc":
                center = _svg_point(primitive["center"], box)
                rx = float(primitive["radiusX"]) / 1000 * box.w
                ry = float(primitive["radiusY"]) / 1000 * box.h
                draw.arc((center[0] - rx, center[1] - ry, center[0] + rx, center[1] + ry), float(primitive["startDegrees"]), float(primitive["endDegrees"]), fill=stroke, width=stroke_width)
            elif primitive["type"] == "quadraticBezier":
                start = _svg_point(primitive["from"], box)
                control = _svg_point(primitive["control"], box)
                end = _svg_point(primitive["to"], box)
                curve = []
                for step in range(21):
                    t = step / 20
                    curve.append(((1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t ** 2 * end[0], (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t ** 2 * end[1]))
                draw.line(curve, fill=stroke, width=stroke_width, joint="curve")
            elif primitive["type"] == "cubicBezier":
                start = np.array(_svg_point(primitive["from"], box))
                control1 = np.array(_svg_point(primitive["control1"], box))
                control2 = np.array(_svg_point(primitive["control2"], box))
                end = np.array(_svg_point(primitive["to"], box))
                curve = []
                for step in range(31):
                    t = step / 30
                    point = (1 - t) ** 3 * start + 3 * (1 - t) ** 2 * t * control1 + 3 * (1 - t) * t ** 2 * control2 + t ** 3 * end
                    curve.append((float(point[0]), float(point[1])))
                draw.line(curve, fill=stroke, width=stroke_width, joint="curve")
            elif primitive["type"] == "wireframe":
                vertices = [_svg_point(vertex, box) for vertex in primitive.get("vertices", [])]
                for start, end in primitive.get("edges", []):
                    if start < len(vertices) and end < len(vertices):
                        draw.line([vertices[start], vertices[end]], fill=stroke, width=stroke_width)
            elif primitive["type"] == "pathGroup":
                for path in primitive.get("paths", []):
                    points = [_svg_point(command, box) for command in path.get("commands", []) if command["command"] != "Z"]
                    if len(points) >= 2:
                        if fill:
                            draw.polygon(points, fill=fill)
                        draw.line(points + [points[0]], fill=stroke, width=stroke_width, joint="curve")
            elif primitive["type"] == "textGlyph":
                draw.text(_svg_point(primitive["position"], box), primitive["content"], fill=stroke, font=_font(), anchor="mm")
    return canvas


def make_comparison(source_path: Path, redrawn: Image.Image, output_path: Path) -> dict[str, Any]:
    source = Image.open(source_path).convert("RGB")
    target_height = max(160, min(720, max(source.height, redrawn.height)))
    source_view = ImageOps.contain(source, (target_height * 2, target_height), method=Image.Resampling.LANCZOS)
    redrawn_view = ImageOps.contain(redrawn, (target_height * 2, target_height), method=Image.Resampling.LANCZOS)
    width = source_view.width + redrawn_view.width + 24
    canvas = Image.new("RGB", (width, target_height + 36), "#f3f4f6")
    draw = ImageDraw.Draw(canvas)
    canvas.paste(source_view, (8, 26))
    canvas.paste(redrawn_view, (source_view.width + 16, 26))
    draw.text((8, 6), "source", fill="#101828", font=_font())
    draw.text((source_view.width + 16, 6), "deterministic_vector", fill="#101828", font=_font())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return {"width": canvas.width, "height": canvas.height, "sha256": sha256_file(output_path)}


def _norm_point(x: float, y: float, width: int, height: int) -> dict[str, float]:
    return {"x": round(float(np.clip(x / max(1, width) * 1000, 0, 1000)), 3), "y": round(float(np.clip(y / max(1, height) * 1000, 0, 1000)), 3)}


def _local_circle(crop: np.ndarray, allow_hough: bool = False) -> tuple[float, float, float] | None:
    height, width = crop.shape[:2]
    if min(width, height) < 40:
        return None
    mask = cv2.threshold(crop, 235, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = _circle_candidates(contours, width, height, limit=2)
    if candidates:
        return max(candidates, key=lambda item: item[2])
    if not allow_hough:
        return None
    circles = cv2.HoughCircles(crop, cv2.HOUGH_GRADIENT, dp=1.2, minDist=max(20, min(width, height) // 3), param1=90, param2=34, minRadius=max(8, min(width, height) // 8), maxRadius=max(10, min(width, height) // 2))
    if circles is not None:
        candidates = sorted((float(cx), float(cy), float(radius)) for cx, cy, radius in np.round(circles[0, :6]).astype(float))
        if candidates:
            return max(candidates, key=lambda item: item[2])
    return None


def _radial_angles(crop: np.ndarray, center: tuple[float, float, float]) -> list[float]:
    height, width = crop.shape[:2]
    cx, cy, radius = center
    mask = cv2.threshold(crop, 215, 255, cv2.THRESH_BINARY_INV)[1]
    lines = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=max(10, int(radius * 0.25)), minLineLength=max(12, int(radius * 0.45)), maxLineGap=4)
    angles: list[float] = []
    if lines is not None:
        for line in lines[:80]:
            x0, y0, x1, y1 = [float(value) for value in line[0]]
            d0 = math.hypot(x0 - cx, y0 - cy)
            d1 = math.hypot(x1 - cx, y1 - cy)
            near = min(d0, d1)
            far_x, far_y = (x0, y0) if d0 > d1 else (x1, y1)
            if near <= radius * 0.3 and max(d0, d1) >= radius * 0.55:
                angle = (math.degrees(math.atan2(far_y - cy, far_x - cx)) + 360) % 360
                if all(abs(((angle - existing + 180) % 360) - 180) > 8 for existing in angles):
                    angles.append(angle)
    return sorted(angles)


def _sector_texture(crop: np.ndarray, center: tuple[float, float, float], start: float, end: float) -> tuple[str, list[tuple[float, float]]]:
    height, width = crop.shape[:2]
    cx, cy, radius = center
    points = [(cx, cy)]
    span = (end - start) % 360
    for index in range(9):
        angle = math.radians(start + span * index / 8)
        points.append((cx + radius * 0.92 * math.cos(angle), cy + radius * 0.92 * math.sin(angle)))
    polygon = np.array(points, dtype=np.int32)
    sector_mask = np.zeros(crop.shape, dtype=np.uint8)
    cv2.fillPoly(sector_mask, [polygon], 255)
    roi = crop[sector_mask > 0]
    dark = float((roi < 160).mean()) if roi.size else 0
    small = cv2.threshold(crop, 190, 255, cv2.THRESH_BINARY_INV)[1]
    components, _, stats, centroids = cv2.connectedComponentsWithStats(small, 8)
    dots: list[tuple[float, float]] = []
    for index in range(1, components):
        x, y, w, h, area = [int(value) for value in stats[index]]
        px, py = [float(value) for value in centroids[index]]
        if 2 <= area <= max(80, int(radius * 0.08)) and sector_mask[min(height - 1, int(py)), min(width - 1, int(px))] > 0 and math.hypot(px - cx, py - cy) < radius * 0.85:
            dots.append((px, py))
    if len(dots) >= 2:
        return "dots", dots[:16]
    radial = cv2.HoughLinesP(small, 1, np.pi / 180, threshold=max(8, int(radius * 0.2)), minLineLength=max(8, int(radius * 0.25)), maxLineGap=3)
    if radial is not None and len(radial) >= 3:
        return "diagonal", []
    return ("solid" if dark > 0.28 else "none"), []


def extract_textured_pie_primitives(crop: np.ndarray, source_asset: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    height, width = crop.shape[:2]
    center = _local_circle(crop, allow_hough=True)
    if center is None:
        return [], {"grammar": "textured_pie", "status": "repair_pending", "reason": "circle_not_found", "sectorAngles": [], "texturePrimitives": []}
    cx, cy, radius = center
    angles = _radial_angles(crop, center)
    if len(angles) < 3:
        angles = [float(index * 45) for index in range(8)]
        angle_status = "equal_angle_fallback_repair_pending"
    else:
        angle_status = "cv_radial_line_fit"
    primitives: list[dict[str, Any]] = [{"type": "circle", "center": _norm_point(cx, cy, width, height), "radius": round(radius / max(width, height) * 1000, 3), "style": identity_style(None, "#101828", 1.6), "transform": identity_transform()}]
    sector_evidence: list[dict[str, Any]] = []
    for index, start in enumerate(angles):
        end = angles[(index + 1) % len(angles)]
        if end <= start:
            end += 360
        texture, dots = _sector_texture(crop, center, start, end)
        fan_points = [_norm_point(cx, cy, width, height)]
        span = end - start
        for step in range(5):
            angle = math.radians(start + span * step / 4)
            fan_points.append(_norm_point(cx + radius * 0.94 * math.cos(angle), cy + radius * 0.94 * math.sin(angle), width, height))
        fill = "#d1d5db" if texture == "solid" else None
        primitives.append({"type": "face", "points": fan_points, "style": identity_style(fill, "#101828", 1.1), "transform": identity_transform()})
        primitives.append({"type": "arc", "center": _norm_point(cx, cy, width, height), "radiusX": round(radius / width * 1000, 3), "radiusY": round(radius / height * 1000, 3), "rotationDegrees": 0, "startDegrees": round(start, 3), "endDegrees": round(end, 3), "anticlockwise": False, "style": {"stroke": "#101828", "fill": None, "lineWidth": 1.1, "dash": [4, 3] if texture == "diagonal" else [], "opacity": 1}, "transform": identity_transform()})
        if texture == "dots":
            for px, py in dots:
                primitives.append({"type": "dot", "center": _norm_point(px, py, width, height), "radius": 10, "style": identity_style("#101828", "#101828", 0.8), "transform": identity_transform()})
        elif texture == "diagonal":
            for step in (0.25, 0.5, 0.75):
                a0 = math.radians(start + span * step)
                a1 = math.radians(start + span * min(1, step + 0.16))
                primitives.append({"type": "line", "from": _norm_point(cx + radius * 0.32 * math.cos(a0), cy + radius * 0.32 * math.sin(a0), width, height), "to": _norm_point(cx + radius * 0.88 * math.cos(a1), cy + radius * 0.88 * math.sin(a1), width, height), "style": {"stroke": "#101828", "fill": None, "lineWidth": 1.0, "dash": [3, 3], "opacity": 1}, "transform": identity_transform()})
        sector_evidence.append({"startDegrees": round(start % 360, 3), "endDegrees": round(end % 360, 3), "texture": texture, "dotCount": len(dots)})
    return primitives[:MAX_PRIMITIVES], {"grammar": "textured_pie", "status": "valid" if angle_status == "cv_radial_line_fit" else "repair_pending", "angleMethod": angle_status, "center": _norm_point(cx, cy, width, height), "radius": round(radius / max(width, height) * 1000, 3), "sectorAngles": sector_evidence, "texturePrimitives": sorted({item["texture"] for item in sector_evidence})}


def extract_wireframe_primitive(crop: np.ndarray) -> dict[str, Any] | None:
    height, width = crop.shape[:2]
    if min(width, height) < 32:
        return None
    mask = cv2.threshold(crop, 220, 255, cv2.THRESH_BINARY_INV)[1]
    lines = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=max(10, min(width, height) // 10), minLineLength=max(12, min(width, height) // 7), maxLineGap=5)
    if lines is None:
        return None
    vertices: list[tuple[float, float]] = []
    edges: list[list[int]] = []
    for line in lines[:64]:
        x0, y0, x1, y1 = [float(value) for value in line[0]]
        if math.hypot(x1 - x0, y1 - y0) < min(width, height) * 0.16:
            continue
        pair: list[int] = []
        for px, py in ((x0, y0), (x1, y1)):
            existing = next((index for index, (vx, vy) in enumerate(vertices) if math.hypot(px - vx, py - vy) < 7), None)
            if existing is None:
                vertices.append((px, py))
                existing = len(vertices) - 1
            pair.append(existing)
        if pair[0] != pair[1] and pair not in edges and pair[::-1] not in edges:
            edges.append(pair)
    if len(vertices) < 4 or len(edges) < 3:
        return None
    return {"type": "wireframe", "vertices": [_norm_point(x, y, width, height) for x, y in vertices[:128]], "edges": edges[:256], "interpretation": "geometry_only", "style": identity_style(None, "#101828", 1.2), "transform": identity_transform()}


def extract_curve_extras(crop: np.ndarray) -> list[dict[str, Any]]:
    height, width = crop.shape[:2]
    extras: list[dict[str, Any]] = []
    center = _local_circle(crop)
    if center is not None:
        cx, cy, radius = center
        extras.append({"type": "arc", "center": _norm_point(cx, cy, width, height), "radiusX": round(radius / width * 1000, 3), "radiusY": round(radius / height * 1000, 3), "rotationDegrees": 0, "startDegrees": 0, "endDegrees": 300, "anticlockwise": False, "style": identity_style(None, "#101828", 1.3), "transform": identity_transform()})
    mask = cv2.threshold(crop, 225, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if contours:
        contour = max(contours, key=cv2.contourArea)
        points = contour[:, 0, :].astype(float)
        if len(points) >= 12:
            first, last = points[0], points[-1]
            midpoint = points[len(points) // 2]
            extras.append({"type": "quadraticBezier", "from": _norm_point(first[0], first[1], width, height), "control": _norm_point(midpoint[0], midpoint[1], width, height), "to": _norm_point(last[0], last[1], width, height), "style": identity_style(None, "#101828", 1.2), "transform": identity_transform()})
    return extras[:4]


def validate_visual(visual: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if visual.get("schemaVersion") != PRIMITIVE_SCHEMA:
        errors.append("schemaVersion")
    if visual.get("visualGrammar") != "primitive_scene":
        errors.append("visualGrammar")
    if visual.get("fidelityTier") != "deterministic_vector":
        errors.append("fidelityTier")
    if visual.get("layoutType") not in {"matrix_3x3", "sequence", "classification_2x3"}:
        errors.append("layoutType")
    if visual.get("coordinateSpace") != {"width": 1000, "height": 1000}:
        errors.append("coordinateSpace")
    panels = visual.get("panels")
    if not isinstance(panels, list) or not panels:
        errors.append("panels")
        return errors
    primitive_count = 0
    for pindex, panel in enumerate(panels):
        if not isinstance(panel, dict) or panel.get("role") not in {"stem", "sequence", "missing", "option", "sample"}:
            errors.append(f"panels[{pindex}]")
            continue
        for index, primitive in enumerate(panel.get("primitives", [])):
            primitive_count += 1
            if not isinstance(primitive, dict) or primitive.get("type") not in {"line", "polyline", "polygon", "rect", "roundedRect", "circle", "ellipse", "arc", "quadraticBezier", "cubicBezier", "pathGroup", "dot", "textGlyph", "semanticSymbol", "wireframe", "face"}:
                errors.append(f"panels[{pindex}].primitives[{index}].type")
                continue
            if not isinstance(primitive.get("style"), dict) or not isinstance(primitive.get("transform"), dict):
                errors.append(f"panels[{pindex}].primitives[{index}].style_transform")
            if primitive.get("type") == "pathGroup":
                evidence = primitive.get("sourceEvidence")
                if not isinstance(evidence, dict) or evidence.get("extractionMethod") != "cv_contour" or evidence.get("verified") is not True:
                    errors.append(f"panels[{pindex}].primitives[{index}].sourceEvidence")
    if primitive_count != visual.get("complexity", {}).get("primitiveCount"):
        errors.append("complexity.primitiveCount")
    if primitive_count > MAX_PRIMITIVES * max(1, len(panels)):
        errors.append("primitive_limit")
    return errors


def _semantic_line_curve_candidate(candidate: Any) -> bool:
    if not isinstance(candidate, dict) or candidate.get("contains_filled_regions") is True or candidate.get("contains_text") is True:
        return False
    motifs = {
        str(motif)
        for group in candidate.get("groups", []) if isinstance(group, dict)
        for motif in group.get("motifs", []) if isinstance(motif, str)
    }
    return bool({"straight_line"} & motifs and {"curved_line", "quadrant_arc", "U_shape", "ellipse"} & motifs)


def process_assets(limit: int | None = None, selected: set[str] | None = None, manifest_path: Path = MANIFEST_PATH, repair_path: Path = REPAIR_PATH, errors_path: Path = ERRORS_PATH, semantic_candidates: dict[str, dict[str, Any]] | None = None) -> None:
    inventory = {row["assetId"]: row for row in (json.loads(line) for line in INVENTORY_PATH.open(encoding="utf-8"))}
    classifications = {row["assetId"]: row for row in (json.loads(line) for line in CLASSIFICATION_PATH.open(encoding="utf-8"))}
    exclusions = load_exclusions()
    errors: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    asset_ids = sorted(classifications)
    if selected:
        asset_ids = [asset_id for asset_id in asset_ids if asset_id in selected]
    if limit is not None:
        asset_ids = asset_ids[:limit]
    for asset_id in asset_ids:
        if asset_id in exclusions:
            continue
        row = inventory[asset_id]
        classification = dict(classifications[asset_id])
        semantic_row = (semantic_candidates or {}).get(asset_id)
        if semantic_row and semantic_row.get("status") == "schema_valid":
            classification["qwenCandidate"] = semantic_row.get("candidate")
            classification["qwenCandidateValidation"] = {"status": "valid", "errors": [], "countAudit": semantic_row.get("countAudit")}
            classification["semanticSource"] = "deterministic_cv+qwen_stroke_grouping_candidate"
        if classification.get("regularSubtypeStatus") == "excluded_by_user":
            continue
        source = Path(row["localPaths"][0])
        output_dir = OUTPUT_ROOT / asset_id
        try:
            if not source.is_file():
                raise FileNotFoundError(source)
            gray = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise ValueError("cv2 could not decode image")
            occurrence_roles = {str(item.get("role") or "") for item in row.get("occurrences", [])}
            if occurrence_roles == {"option"}:
                fresh_boxes = [Box(0, 0, int(gray.shape[1]), int(gray.shape[0]))]
                fresh_info = {"selectedCount": 1, "roleOverride": "standalone_option_image"}
                layout_type, fresh_roles, fresh_option_count = "sequence", ["option"], 1
            else:
                fresh_boxes, fresh_info = detect_panels(gray)
                layout_type, fresh_roles, fresh_option_count = infer_layout(fresh_boxes, int(gray.shape[1]), int(gray.shape[0]))
            classification.update({
                "layoutType": layout_type,
                "panelRoles": fresh_roles,
                "expectedPanelCount": len(fresh_boxes),
                "expectedOptionCount": fresh_option_count,
                "panelBoxes": [box.as_dict(int(gray.shape[1]), int(gray.shape[0])) for box in fresh_boxes],
                "panelDetection": {**classification.get("panelDetection", {}), **fresh_info, "refreshedDuringProcess": True},
            })
            if _semantic_line_curve_candidate(classification.get("qwenCandidate")):
                classification.update({
                    "visualGrammar": "regular_geometry",
                    "regularSubtype": "line_curve_composition",
                    "regularSubtypeSource": "qwen_semantic_candidate+deterministic_cv_geometry",
                    "regularSubtypeRenderer": ["rect", "line", "cubicBezier", "typed_topology"],
                })
            qwen_validation = classification.get("qwenCandidateValidation")
            if isinstance(qwen_validation, dict) and isinstance(classification.get("qwenCandidate"), dict):
                qwen_validation["countAudit"] = {
                    "cvPanelCount": len(fresh_boxes),
                    "qwenPanelCount": classification["qwenCandidate"].get("panel_count"),
                    "panelCountAgreement": classification["qwenCandidate"].get("panel_count") == len(fresh_boxes),
                    "cvOptionCount": fresh_option_count,
                    "qwenOptionCount": classification["qwenCandidate"].get("option_count"),
                    "optionCountAgreement": classification["qwenCandidate"].get("option_count") == fresh_option_count,
                    "authority": "deterministic_cv",
                }
            visual, metrics = build_scene(row, classification, gray)
            validation_errors = validate_visual(visual)
            output_dir.mkdir(parents=True, exist_ok=True)
            structured_path = output_dir / "structured.json"
            write_json(structured_path, visual)
            svg = render_svg(visual, classification, int(gray.shape[1]), int(gray.shape[0]))
            svg_path = output_dir / "redrawn.svg"
            svg_path.write_text(svg, encoding="utf-8")
            redrawn = render_png(visual, classification, int(gray.shape[1]), int(gray.shape[0]))
            redrawn_png_path = output_dir / "redrawn.png"
            redrawn.save(redrawn_png_path, format="PNG", optimize=True)
            comparison_info = make_comparison(source, redrawn, output_dir / "comparison.png")
            evidence = {
                "schemaVersion": "graphic-reasoning-acceptance-v1",
                "assetId": asset_id,
                "sourceSha256": row["sha256"],
                "sourcePath": str(source),
                "sourceDimensions": {"width": int(gray.shape[1]), "height": int(gray.shape[0])},
                "detectedPanelCount": classification.get("expectedPanelCount", 0),
                "detectedOptionCount": classification.get("expectedOptionCount", 0),
                "topology": {"layoutType": classification.get("layoutType"), "panelRoles": classification.get("panelRoles"), "panelBoxes": visual["evidence"].get("panelBoxes")},
                "regularSubtype": classification.get("regularSubtype"),
                "semanticCandidate": classification.get("qwenCandidate"),
                "semanticCandidateValidation": classification.get("qwenCandidateValidation"),
                "text": visual["evidence"]["textDetection"],
                "direction": visual["evidence"]["directionDetection"],
                "relativePositions": visual["evidence"].get("panelBoxes", []),
                "primitiveCount": metrics["primitiveCount"],
                "structuredSha256": sha256_file(structured_path),
                "redrawnSha256": sha256_file(redrawn_png_path),
                "comparison": comparison_info,
                "validation": {"status": "valid" if not validation_errors else "invalid", "errors": validation_errors, "method": "python_contract_subset_v1"},
                "completion": "deterministic_vector_generated",
            }
            repair_reasons = list(classification.get("repairReasons", []))
            if classification.get("grammarStatus") != "validated":
                repair_reasons.append("grammar_renderer_pending")
            qwen_validation = classification.get("qwenCandidateValidation")
            if isinstance(qwen_validation, dict) and qwen_validation.get("status") != "valid":
                repair_reasons.append("qwen_semantic_candidate_invalid")
            if metrics["primitiveCount"] == 0:
                repair_reasons.append("no_deterministic_primitives_extracted")
            failed_panel_metrics = [
                item.get("id") for item in visual.get("evidence", {}).get("grammarMetrics", [])
                if item.get("status") == "repair_pending"
            ]
            if failed_panel_metrics:
                repair_reasons.append("grammar_specific_panel_gate_failed:" + ",".join(str(item) for item in failed_panel_metrics))
            if validation_errors:
                errors.append({"stage": "validate", "assetId": asset_id, "error": ";".join(validation_errors)})
                repair_reasons.append("contract_validation_failed")
            repair_reasons = sorted(set(repair_reasons))
            evidence["completion"] = "deterministic_vector_generated_repair_pending" if repair_reasons else "deterministic_vector_generated"
            evidence["repairStatus"] = {"status": "repair_pending" if repair_reasons else "clear", "reasons": repair_reasons}
            write_json(output_dir / "acceptance.json", evidence)
            if repair_reasons:
                repairs.append({"assetId": asset_id, "sha256": row["sha256"], "status": "deterministic_vector_generated_repair_pending", "reasons": repair_reasons, "output": str(output_dir)})
            manifest.append({"assetId": asset_id, "sha256": row["sha256"], "sourcePath": str(source), "structured": str(structured_path), "redrawnSvg": str(svg_path), "redrawnPng": str(redrawn_png_path), "comparison": str(output_dir / "comparison.png"), "acceptance": str(output_dir / "acceptance.json"), "fidelityTier": "deterministic_vector", "status": "repair_pending" if repair_reasons else ("valid" if not validation_errors else "error")})
        except Exception as exc:
            errors.append({"stage": "process", "assetId": asset_id, "error": f"{type(exc).__name__}: {exc}", "sourcePath": str(source)})
    # Merge prior classification errors only when this is a full run; selected runs
    # are explicitly marked partial in the summary and don't erase earlier evidence.
    write_jsonl(manifest_path, manifest)
    write_jsonl(repair_path, repairs)
    write_jsonl(errors_path, errors)
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.is_file() else {}
    if manifest_path == MANIFEST_PATH:
        summary.update({"processedAssets": len(manifest), "processedErrors": len(errors), "repairQueue": len(repairs), "batchMode": "partial" if limit is not None or selected else "full"})
    elif manifest_path == REGULAR_REPRESENTATIVE_MANIFEST_PATH:
        summary.update({"regularRepresentativeProcessedAssets": len(manifest), "regularRepresentativeErrors": len(errors), "regularRepresentativeRepairQueue": len(repairs)})
    elif SUBTYPE_BATCH_ROOT in manifest_path.parents:
        summary.update({
            "lastRegularSubtypeBatch": manifest_path.parent.name,
            "lastRegularSubtypeBatchAssets": len(manifest),
            "lastRegularSubtypeBatchErrors": len(errors),
            "lastRegularSubtypeBatchRepairQueue": len(repairs),
            "lastRegularSubtypeBatchStatus": "first_pass_repair_pending",
        })
    elif MICROBATCH_ROOT in manifest_path.parents:
        summary.update({
            "lastMicrobatch": manifest_path.parent.name,
            "lastMicrobatchAssets": len(manifest),
            "lastMicrobatchErrors": len(errors),
            "lastMicrobatchRepairQueue": len(repairs),
            "lastMicrobatchStatus": "visual_review_pending",
        })
    else:
        summary.update({"representativeProcessedAssets": len(manifest), "representativeErrors": len(errors), "representativeRepairQueue": len(repairs)})
    write_json(SUMMARY_PATH, summary)
    print(json.dumps({"processed": len(manifest), "errors": len(errors), "repairQueue": len(repairs)}, ensure_ascii=False))


def audit_subtype_batch(subtype: str) -> None:
    if not re.fullmatch(r"[a-z0-9_]+", subtype):
        raise ValueError("invalid subtype")
    batch_root = SUBTYPE_BATCH_ROOT / subtype
    manifest_path = batch_root / "manifest.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = [json.loads(line) for line in manifest_path.open(encoding="utf-8")]
    audit_rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    candidate_count = 0
    total_panels = 0
    missing_panels = 0
    stroke_panels_passed = 0
    stroke_panels_failed = 0
    for row in manifest:
        visual = json.loads(Path(row["structured"]).read_text(encoding="utf-8"))
        validation_errors = validate_visual(visual)
        primitive_count = 0
        line_count = 0
        polygon_count = 0
        filled_count = 0
        empty_non_missing = 0
        maximum_panel_primitives = 0
        for panel in visual.get("panels", []):
            total_panels += 1
            primitives = panel.get("primitives", [])
            maximum_panel_primitives = max(maximum_panel_primitives, len(primitives))
            if panel.get("role") == "missing":
                missing_panels += 1
            elif not primitives:
                empty_non_missing += 1
            for primitive in primitives:
                primitive_count += 1
                type_counts[primitive["type"]] += 1
                line_count += primitive["type"] == "line"
                polygon_count += primitive["type"] == "polygon"
                filled_count += bool(primitive.get("style", {}).get("fill"))
        reasons: list[str] = []
        if validation_errors:
            reasons.append("contract_validation_failed")
        if empty_non_missing:
            reasons.append("empty_non_missing_panel")
        if subtype in {"irregular_curve_composition", "curve_line_composition"}:
            if maximum_panel_primitives > 64 or primitive_count > max(128, len(visual.get("panels", [])) * 64):
                reasons.append("dense_primitive_output")
        elif primitive_count > 80 or maximum_panel_primitives > 48:
            reasons.append("dense_primitive_output")
        if subtype not in {"irregular_curve_composition", "curve_line_composition"} and line_count > 60 and line_count > polygon_count * 4:
            reasons.append("hough_line_dominance")
        if subtype == "closed_polygon_composition" and filled_count:
            reasons.append("unexpected_fill_in_closed_polygon")
        if subtype in {"irregular_curve_composition", "curve_line_composition"}:
            for metric in visual.get("evidence", {}).get("panelMetrics", []):
                if metric.get("missingMarker"):
                    continue
                if metric.get("status") == "renderer_candidate":
                    stroke_panels_passed += 1
                    continue
                stroke_panels_failed += 1
                metric_reasons = metric.get("reason")
                if isinstance(metric_reasons, list):
                    reasons.extend(f"stroke_graph:{reason}" for reason in metric_reasons)
                else:
                    reasons.append("stroke_graph:panel_gate_failed")
        reasons = sorted(set(reasons))
        if not reasons:
            status = "candidate_for_manual_review"
            candidate_count += 1
        else:
            status = "repair_pending"
            reason_counts.update(reasons)
        audit_rows.append({
            "assetId": row["assetId"],
            "sha256": row["sha256"],
            "status": status,
            "notFinal": True,
            "reasons": reasons,
            "primitiveCount": primitive_count,
            "lineCount": line_count,
            "polygonCount": polygon_count,
            "filledPrimitiveCount": filled_count,
            "emptyNonMissingPanels": empty_non_missing,
            "maximumPanelPrimitives": maximum_panel_primitives,
            "comparison": row["comparison"],
        })
    write_jsonl(batch_root / "audit.jsonl", audit_rows)
    review = {
        "schemaVersion": "graphic-reasoning-subtype-first-pass-review-v1",
        "subtype": subtype,
        "status": "first_pass_reviewed_not_final",
        "assets": len(manifest),
        "candidateForManualReview": candidate_count,
        "repairPending": len(manifest) - candidate_count,
        "reasonCounts": dict(sorted(reason_counts.items())),
        "primitiveTypes": dict(sorted(type_counts.items())),
        "totalPanels": total_panels,
        "missingPanels": missing_panels,
        "strokeGraphPanelsPassed": stroke_panels_passed,
        "strokeGraphPanelsFailed": stroke_panels_failed,
        "finalAcceptedAssets": 0,
        "gate": "Machine-simple candidates still require source/redraw visual review; repair rows must be rerouted before acceptance.",
    }
    write_json(batch_root / "first-pass-review.json", review)
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.is_file() else {}
    summary.update({
        "lastRegularSubtypeAudit": subtype,
        "lastRegularSubtypeAuditCandidates": candidate_count,
        "lastRegularSubtypeAuditRepairPending": len(manifest) - candidate_count,
        "lastRegularSubtypeAuditFinalAccepted": 0,
    })
    write_json(SUMMARY_PATH, summary)
    print(json.dumps(review, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "classify", "route", "regular-route", "regular-qwen", "stroke-qwen", "process", "audit-subtype", "validate"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--qwen-limit", type=int, default=0, help="sequential semantic candidates for the first N classified assets; never supplies coordinates")
    parser.add_argument("--cpu-only", action="store_true", help="skip every model endpoint health/request path")
    parser.add_argument("--representative", action="store_true", help="write selected sample evidence to representative-* files without replacing the full manifest")
    parser.add_argument("--regular-representative", action="store_true", help="process routed regular-geometry representatives into an isolated manifest")
    parser.add_argument("--subtype", default=None, help="process exactly one routed regular subtype into subtype-batches/<subtype>")
    parser.add_argument("--batch-name", default=None, help="write explicitly selected assets to an isolated microbatches/<name> manifest")
    parser.add_argument("--representatives-per-subtype", type=int, default=3)
    parser.add_argument("--manifest", default=None, help="manifest path for validate; defaults to the full manifest")
    args = parser.parse_args()
    if args.command == "scan":
        scan_inventory(check_model_health=not args.cpu_only)
    elif args.command == "classify":
        classify_inventory(args.limit, args.qwen_limit)
    elif args.command == "route":
        route_visual_grammar()
    elif args.command == "regular-route":
        route_regular_subtypes(args.representatives_per_subtype)
    elif args.command == "regular-qwen":
        semanticize_regular_representatives(args.qwen_limit if args.qwen_limit > 0 else None)
    elif args.command == "stroke-qwen":
        if not args.batch_name or not re.fullmatch(r"[a-z0-9_]+", args.batch_name):
            parser.error("stroke-qwen requires a valid --batch-name")
        if not args.asset:
            parser.error("stroke-qwen requires one or more --asset values")
        semanticize_stroke_assets(set(args.asset), args.batch_name)
    elif args.command == "process":
        if sum(bool(value) for value in (args.regular_representative, args.subtype, args.batch_name)) > 1:
            parser.error("--regular-representative, --subtype, and --batch-name are mutually exclusive")
        selected = set(args.asset) if args.asset else None
        manifest_path = MANIFEST_PATH
        repair_path = REPAIR_PATH
        errors_path = ERRORS_PATH
        semantic_candidates: dict[str, dict[str, Any]] | None = None
        if args.batch_name:
            if not re.fullmatch(r"[a-z0-9_]+", args.batch_name):
                parser.error("--batch-name must contain only lowercase letters, digits, and underscores")
            if selected is None:
                parser.error("--batch-name requires one or more --asset values")
            batch_root = MICROBATCH_ROOT / args.batch_name
            manifest_path = batch_root / "manifest.jsonl"
            repair_path = batch_root / "repair-queue.jsonl"
            errors_path = batch_root / "errors.jsonl"
            semantic_path = batch_root / "qwen-semantic.jsonl"
            if semantic_path.is_file():
                semantic_candidates = {row["assetId"]: row for row in (json.loads(line) for line in semantic_path.open(encoding="utf-8"))}
        elif args.regular_representative:
            manifest_path = REGULAR_REPRESENTATIVE_MANIFEST_PATH
            repair_path = REGULAR_REPRESENTATIVE_REPAIR_PATH
            errors_path = REGULAR_REPRESENTATIVE_ERRORS_PATH
            if selected is None and REGULAR_SUBTYPE_REPRESENTATIVES_PATH.is_file():
                classification_rows = {row["assetId"]: row for row in (json.loads(line) for line in CLASSIFICATION_PATH.open(encoding="utf-8"))}
                selected = {
                    row["assetId"]
                    for row in (json.loads(line) for line in REGULAR_SUBTYPE_REPRESENTATIVES_PATH.open(encoding="utf-8"))
                    if classification_rows.get(row["assetId"], {}).get("regularSubtypeStatus") == "renderer_candidate"
                }
        elif args.subtype:
            if not re.fullmatch(r"[a-z0-9_]+", args.subtype):
                parser.error("--subtype must contain only lowercase letters, digits, and underscores")
            classification_rows = {row["assetId"]: row for row in (json.loads(line) for line in CLASSIFICATION_PATH.open(encoding="utf-8"))}
            selected = {
                asset_id
                for asset_id, row in classification_rows.items()
                if row.get("regularSubtype") == args.subtype and row.get("regularSubtypeStatus") != "excluded_by_user"
            }
            batch_root = SUBTYPE_BATCH_ROOT / args.subtype
            manifest_path = batch_root / "manifest.jsonl"
            repair_path = batch_root / "repair-queue.jsonl"
            errors_path = batch_root / "errors.jsonl"
        process_assets(
            args.limit,
            selected,
            manifest_path if not args.representative else ROOT / "representative-manifest.jsonl",
            repair_path if not args.representative else ROOT / "representative-repair-queue.jsonl",
            errors_path if not args.representative else ROOT / "representative-errors.jsonl",
            semantic_candidates,
        )
    elif args.command == "audit-subtype":
        if not args.subtype:
            parser.error("audit-subtype requires --subtype")
        audit_subtype_batch(args.subtype)
    else:
        validation_manifest = Path(args.manifest) if args.manifest else MANIFEST_PATH
        rows = [json.loads(line) for line in validation_manifest.open(encoding="utf-8")] if validation_manifest.is_file() else []
        errors = []
        for row in rows:
            path = Path(row["structured"])
            if not path.is_file():
                errors.append({"assetId": row["assetId"], "error": "structured_missing"})
                continue
            errors.extend({"assetId": row["assetId"], "error": error} for error in validate_visual(json.loads(path.read_text(encoding="utf-8"))))
        write_jsonl(ERRORS_PATH, errors)
        print(json.dumps({"validated": len(rows), "errors": len(errors)}, ensure_ascii=False))
        return 1 if errors else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
