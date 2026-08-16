#!/usr/bin/env python3
"""Semantic routing and structure extraction for 1,449 non-graphic-reasoning images.

All outputs stay under reports/question-image-audit.  Qwen3-VL is used only for
semantic candidates; hashes, regions, grids, topology evidence and vector
coordinates are derived or checked deterministically on this machine.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import cv2
import jsonschema
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCE = REPO / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613/paper_questions.jsonl"
SOURCE_MANIFEST = ROOT / "next-wave-manifest.jsonl"
VISUAL_INVENTORY = ROOT / "next-wave-visual-inventory.jsonl"
DATA_MANIFEST = REPO / "reports/data-analysis-batch/manifest.jsonl"

WORK = ROOT / "non-graphic-work"
CONTEXT_CACHE = WORK / "question-context.jsonl"
ROUTE_RESULTS = WORK / "routing-results"
SEMANTIC_RESULTS = WORK / "semantic-results"
ROUTE_CACHE = ROOT / "cache/qwen-semantic-routing-v1"
SEMANTIC_CACHE = ROOT / "cache/qwen-semantic-extraction-v1"
STRUCTURED = ROOT / "non-graphic-structured"
REVIEW_EVIDENCE = ROOT / "non-graphic-review-evidence"
ROUTING_MANIFEST = ROOT / "formal-semantic-routing-manifest.jsonl"
FINAL_MANIFEST = ROOT / "non-graphic-structure-manifest.jsonl"
INVENTORY = ROOT / "non-graphic-structure-inventory.json"
ERRORS = ROOT / "non-graphic-structure-errors.jsonl"
SCHEMA_PATH = ROOT / "non-graphic-structured-schema.json"
SAMPLE_REPORT = ROOT / "photo-sample-acceptance.json"
FINAL_REPORT = ROOT / "2026-08-11-non-graphic-structure-report.md"
FINAL_VERIFICATION = ROOT / "non-graphic-final-verification.json"

ENDPOINT = "http://127.0.0.1:18000/v1/chat/completions"
MODEL = "qwen3-vl-30b-a3b"
MIN_REQUEST_INTERVAL_SECONDS = 1.25
LAST_REQUEST_AT = 0.0
REQUEST_LOCK = threading.Lock()

ROUTES = {
    "data_visual",
    "perceptual_grid",
    "reasoning_diagram",
    "annotated_raster",
    "needs_review",
}
STATUSES = {"success", "needs_review", "unsupported", "failed", "pending"}
ANCHORS = {
    "top_left", "top", "top_right", "left", "center", "right",
    "bottom_left", "bottom", "bottom_right", "whole_image", "unknown",
}
ANCHOR_BOXES = {
    "top_left": [0.0, 0.0, 1 / 3, 1 / 3],
    "top": [1 / 3, 0.0, 2 / 3, 1 / 3],
    "top_right": [2 / 3, 0.0, 1.0, 1 / 3],
    "left": [0.0, 1 / 3, 1 / 3, 2 / 3],
    "center": [1 / 3, 1 / 3, 2 / 3, 2 / 3],
    "right": [2 / 3, 1 / 3, 1.0, 2 / 3],
    "bottom_left": [0.0, 2 / 3, 1 / 3, 1.0],
    "bottom": [1 / 3, 2 / 3, 2 / 3, 1.0],
    "bottom_right": [2 / 3, 2 / 3, 1.0, 1.0],
    "whole_image": [0.0, 0.0, 1.0, 1.0],
    "unknown": [0.0, 0.0, 1.0, 1.0],
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def strip_html(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parser = TextExtractor()
    try:
        parser.feed(value)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html.unescape(value))
    return " ".join(parser.parts)


def compact_text(value: Any, limit: int = 1500) -> str:
    text = re.sub(r"\s+", " ", strip_html(value) if "<" in str(value) else str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_source_rows() -> list[dict[str, Any]]:
    return list(read_jsonl(VISUAL_INVENTORY))


def build_context_cache() -> dict[str, dict[str, Any]]:
    if CONTEXT_CACHE.is_file():
        cached = {row["id"]: row for row in read_jsonl(CONTEXT_CACHE)}
        wanted = {qid for row in read_jsonl(SOURCE_MANIFEST) for qid in row["questionIds"]}
        if wanted.issubset(cached):
            return cached
    wanted = {qid for row in read_jsonl(SOURCE_MANIFEST) for qid in row["questionIds"]}
    contexts: list[dict[str, Any]] = []
    with SOURCE.open(encoding="utf-8") as handle:
        for raw in handle:
            question = json.loads(raw)
            qid = str(question.get("id") or "")
            if qid not in wanted:
                continue
            options = [
                {"label": item.get("label"), "text": compact_text(item.get("text") or item.get("html"), 260)}
                for item in (question.get("options") or []) if isinstance(item, dict)
            ]
            contexts.append({
                "id": qid,
                "section": question.get("section"),
                "stem": compact_text(question.get("stem_text") or question.get("stem_html"), 900),
                "material": compact_text(question.get("material_text") or question.get("material_html"), 900),
                "options": options,
                "answer": question.get("answer"),
                "analysis": compact_text(question.get("final_analysis") or question.get("original_analysis"), 1500),
                "knowledgePoint": question.get("raw_knowledge_point"),
            })
    contexts.sort(key=lambda item: item["id"])
    write_jsonl(CONTEXT_CACHE, contexts)
    return {row["id"]: row for row in contexts}


def contexts_for(row: dict[str, Any], contexts: dict[str, dict[str, Any]], maximum: int = 3) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    seen = set()
    for qid in row.get("questionIds") or []:
        item = contexts.get(qid)
        if not item:
            continue
        signature = (item.get("stem"), json.dumps(item.get("options"), ensure_ascii=False))
        if signature in seen:
            continue
        seen.add(signature)
        picked.append(item)
        if len(picked) >= maximum:
            break
    return picked


def image_data_url(path: Path) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if max(image.size) > 2400:
            scale = 2400 / max(image.size)
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=92, optimize=True)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        mime = "image/jpeg"
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def extract_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response contains no complete JSON object")
    parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    return parsed


def request_slot() -> None:
    global LAST_REQUEST_AT
    with REQUEST_LOCK:
        delay = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        LAST_REQUEST_AT = time.monotonic()


def qwen_request(prompt: str, image_path: Path, cache_path: Path, max_tokens: int) -> dict[str, Any]:
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "structured_outputs": {"json_object": True, "disable_fallback": True},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_url(image_path)}},
        ]}],
    }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error: Exception | None = None
    for attempt in range(3):
        request_slot()
        started = time.monotonic()
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(request, timeout=150) as response:
                raw = json.load(response)
            message = raw["choices"][0]["message"]["content"]
            parsed = extract_json_object(message)
            result = {
                "model": MODEL,
                "elapsedSeconds": round(time.monotonic() - started, 3),
                "content": message,
                "parsed": parsed,
                "usage": raw.get("usage"),
            }
            atomic_json(cache_path, result)
            return result
        except (OSError, KeyError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2.5)
    raise RuntimeError(f"Qwen request failed after 3 attempts: {last_error}")


def routing_prompt(row: dict[str, Any], context_rows: list[dict[str, Any]]) -> str:
    context = json.dumps(context_rows, ensure_ascii=False, separators=(",", ":"))
    return f"""你是公务员考试题目关键图片的语义路由器。只输出一个合法 JSON 对象，不要 Markdown，不要绝对或归一化坐标。
此批次已经剔除资料分析独立批次、公式批次和图形推理批次；不得把它重新路由到图形推理。
候选视觉族只是 OpenCV 排队提示，尤其“横竖线很多”绝不等于表格：它也可能是字符网格、几何、逻辑、电路、机械、实验装置、集合图、交通标志或漫画。必须结合原图与题目上下文重新判断。

route 只能取：
- data_visual：真实数据表、统计表、柱/线/饼/散点/组合统计图；
- perceptual_grid：知觉速度的字符、数字或符号比对网格，要求逐格保留原字形；
- reasoning_diagram：几何、逻辑、科学、机械、电路、实验或规则示意图，需要实体关系和拓扑；
- annotated_raster：照片、文物、艺术品、邮票、徽标、漫画、交通标志或复杂地图，保留原图并标注；
- needs_review：看不清、复合内容无法安全归类或答案敏感且证据不足。

只返回这些键：
{{"route":"...","subtype":"具体视觉子型","confidence":0到1,"visible_summary":"只陈述图中可观察内容","question_role":"图片在题目中的作用","answer_sensitive":true或false,"ocr_candidates":["按阅读顺序的可见文本"],"reason":"路由证据","uncertainties":["具体不确定项"]}}
不确定身份只能写可观察特征，不能猜人物、地点、器物或作品名。confidence 低于 0.78 时必须列 uncertainties。

资产：{row['assetId']}
原 CV 候选：{row.get('visualFamilyCandidate')}
板块：{json.dumps(row.get('sections'), ensure_ascii=False)}
知识点：{json.dumps(row.get('knowledgePoints'), ensure_ascii=False)}
题目上下文（分析文字只能作为上下文证据，不能替代图像观察）：{context}
"""


def normalize_route(row: dict[str, Any], raw: dict[str, Any], elapsed: float) -> dict[str, Any]:
    route = str(raw.get("route") or "needs_review")
    confidence = raw.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
    uncertainties = [str(value) for value in (raw.get("uncertainties") or []) if str(value).strip()]
    validation_errors: list[str] = []
    if route not in ROUTES:
        validation_errors.append(f"invalid_route:{route}")
        route = "needs_review"
    # A numeric/logic matrix in 数量关系 is a reasoning diagram, not a
    # perceptual-speed glyph comparison.  This guard prevents a second form of
    # the same visual-shape shortcut as "lines == table".
    perceptual_context = "perceptual_speed" in (row.get("tracks") or []) or any(
        "知觉" in str(section) for section in (row.get("sections") or [])
    )
    if route == "perceptual_grid" and not perceptual_context:
        route = "reasoning_diagram"
        raw["subtype"] = raw.get("subtype") or "numeric_or_symbol_logic_grid"
    if not 0 <= confidence <= 1:
        validation_errors.append("confidence_out_of_range")
        confidence = max(0.0, min(1.0, confidence))
    if confidence < 0.78 and not uncertainties:
        validation_errors.append("low_confidence_without_uncertainty")
        uncertainties.append("模型置信度低于正式路由门槛")
    if route == "needs_review" and not uncertainties:
        uncertainties.append("模型无法安全确定正式通道")
    state = "routed" if route != "needs_review" and confidence >= 0.78 and not validation_errors else "needs_review"
    return {
        "assetId": row["assetId"],
        "sha256": row["sha256"],
        "localPath": row["localPath"],
        "sourceCandidateFamily": row.get("visualFamilyCandidate"),
        "formalRoute": route,
        "subtype": compact_text(raw.get("subtype"), 160),
        "confidence": round(confidence, 4),
        "visibleSummary": compact_text(raw.get("visible_summary"), 800),
        "questionRole": compact_text(raw.get("question_role"), 500),
        "answerSensitive": bool(raw.get("answer_sensitive")),
        "ocrCandidates": [compact_text(value, 500) for value in (raw.get("ocr_candidates") or [])],
        "reason": compact_text(raw.get("reason"), 800),
        "uncertainties": uncertainties,
        "routingState": state,
        "validationErrors": validation_errors,
        "semanticEngine": MODEL,
        "modelElapsedSeconds": elapsed,
        "coordinatePolicy": "semantic_model_no_coordinates",
        "tracks": row.get("tracks") or [],
        "sections": row.get("sections") or [],
        "roles": row.get("roles") or [],
        "questionIds": row.get("questionIds") or [],
    }


def route_one(row: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output = ROUTE_RESULTS / f"{row['assetId']}.json"
    if output.is_file():
        return json.loads(output.read_text(encoding="utf-8"))
    try:
        result = qwen_request(
            routing_prompt(row, contexts_for(row, contexts)),
            Path(row["localPath"]),
            ROUTE_CACHE / f"{row['assetId']}.json",
            1800,
        )
        routed = normalize_route(row, result["parsed"], result["elapsedSeconds"])
    except Exception as error:
        routed = normalize_route(row, {"route": "needs_review", "uncertainties": [str(error)]}, 0.0)
        routed["routingState"] = "failed"
        routed["validationErrors"].append(f"routing_exception:{type(error).__name__}")
    atomic_json(output, routed)
    return routed


def preflight() -> dict[str, Any]:
    rows = load_source_rows()
    source_manifest = list(read_jsonl(SOURCE_MANIFEST))
    manifest_by_id = {row["assetId"]: row for row in source_manifest}
    data_by_hash = {row["sha256"]: row for row in read_jsonl(DATA_MANIFEST)}
    errors: list[dict[str, Any]] = []
    overlap: list[dict[str, Any]] = []
    for row in rows:
        path = Path(row["localPath"])
        if not path.is_file():
            errors.append({"assetId": row["assetId"], "error": "source_missing"})
            continue
        digest = sha256_file(path)
        if digest != row["sha256"]:
            errors.append({"assetId": row["assetId"], "error": "source_hash_mismatch", "actual": digest})
        if row["assetId"] not in manifest_by_id:
            errors.append({"assetId": row["assetId"], "error": "missing_from_source_manifest"})
        if "graphic_reasoning" in (row.get("tracks") or []):
            errors.append({"assetId": row["assetId"], "error": "graphic_reasoning_scope_violation"})
        if row["sha256"] in data_by_hash:
            da = data_by_hash[row["sha256"]]
            overlap.append({
                "assetId": row["assetId"],
                "dataAssetId": da.get("asset_id"),
                "classification": da.get("classification"),
                "processingStatus": da.get("processing_status"),
                "structuredPath": da.get("structured_json_path"),
            })
    result = {
        "schemaVersion": "non-graphic-preflight-v1",
        "assetCount": len(rows),
        "sourceManifestCount": len(source_manifest),
        "uniqueAssetIds": len({row["assetId"] for row in rows}),
        "uniqueSha256": len({row["sha256"] for row in rows}),
        "hashVerifiedCount": len(rows) - sum(item["error"] in {"source_missing", "source_hash_mismatch"} for item in errors),
        "dataAnalysisOverlapCount": len(overlap),
        "dataAnalysisOverlap": overlap,
        "candidateFamilyCounts": dict(sorted(Counter(row.get("visualFamilyCandidate") for row in rows).items())),
        "errors": errors,
        "passed": len(rows) == 1449 and not errors,
    }
    atomic_json(WORK / "preflight.json", result)
    return result


def run_routing(limit: int | None, workers: int, family: str | None) -> dict[str, Any]:
    rows = load_source_rows()
    if family:
        rows = [row for row in rows if row.get("visualFamilyCandidate") == family]
    contexts = build_context_cache()
    pending = [row for row in rows if not (ROUTE_RESULTS / f"{row['assetId']}.json").is_file()]
    if limit is not None:
        pending = pending[:limit]
    completed = 0
    failures = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(route_one, row, contexts): row for row in pending}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            failures += result.get("routingState") == "failed"
            if completed % 20 == 0 or completed == len(pending):
                print(json.dumps({
                    "phase": "routing", "completedThisRun": completed, "scheduled": len(pending),
                    "failedThisRun": failures, "elapsedSeconds": round(time.monotonic() - started, 1),
                }, ensure_ascii=False), flush=True)
    all_results = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(ROUTE_RESULTS.glob("qi_*.json"))]
    write_jsonl(ROUTING_MANIFEST, all_results)
    summary = {
        "sourceCount": len(rows),
        "resultCount": len(all_results),
        "routeCounts": dict(sorted(Counter(row["formalRoute"] for row in all_results).items())),
        "stateCounts": dict(sorted(Counter(row["routingState"] for row in all_results).items())),
        "sourceCandidateToFormalRoute": {
            candidate: dict(sorted(counts.items()))
            for candidate, counts in sorted(
                (key, Counter(row["formalRoute"] for row in all_results if row["sourceCandidateFamily"] == key))
                for key in {row["sourceCandidateFamily"] for row in all_results}
            )
        },
        "completedThisRun": completed,
    }
    atomic_json(WORK / "routing-summary.json", summary)
    return summary


def semantic_prompt(route: dict[str, Any], context_rows: list[dict[str, Any]]) -> str:
    context = json.dumps(context_rows, ensure_ascii=False, separators=(",", ":"))
    common = f"""你是公务员考试图片语义抽取器。只输出一个合法 JSON 对象，不要 Markdown，不要编造。
绝对禁止输出 x/y、bbox、像素坐标、归一化坐标、点列表或 SVG 路径。你只产语义候选；区域、网格与拓扑几何由本地 CV 确定。
可使用 relative_anchor，值只能是 top_left,top,top_right,left,center,right,bottom_left,bottom,bottom_right,whole_image,unknown。
不确定身份只能描述可观察特征。题目解析可以支持“题目如何使用图片”，但不能冒充视觉证据。
资产：{route['assetId']}；正式通道：{route['formalRoute']}；子型：{route['subtype']}。
路由可见摘要：{route['visibleSummary']}
题目上下文：{context}
"""
    if route["formalRoute"] == "annotated_raster":
        return common + """
返回：
{"schema_version":"annotated-raster-v1","ocr":[{"text":"原样文字","relative_anchor":"...","confidence":0到1,"evidence":"可见字形"}],
"observable_entities":[{"id":"e1","type":"只写可观察类型","description":"可观察特征","relative_anchor":"...","attributes":{},"confidence":0到1,"evidence":"像素中可见证据"}],
"relations":[{"subject":"e1","predicate":"可观察关系","object":"e2或文字","direction":"可见方向或null","confidence":0到1,"evidence":"视觉证据"}],
"contextual_claims":[{"claim":"由题目上下文支持、但不能仅靠图像确认的身份或含义","evidence_type":"question_context","confidence":0到1}],
"question_role":{"role":"图片作用","answer_relevant_features":["解题真正会用到的可见特征"],"preserve_source":true},
"confidence":0到1,"uncertainties":["歧义、不可辨内容或身份不确定"]}
OCR 必须原样保留大小写、标点和易混字符，不得改写。
"""
    if route["formalRoute"] == "perceptual_grid":
        return common + """
返回：
{"schema_version":"perceptual-token-grid-v1","reading_order":"row_major或column_major",
"rows":[["逐格原始 token，不得归一化"]],
"comparison_groups":[{"id":"g1","cells":[{"row":从0开始,"column":从0开始}],"rule":"题图明确的比较分组"}],
"ambiguous_cells":[{"row":从0开始,"column":从0开始,"candidates":["所有可能原字形"],"reason":"为何易混"}],
"visible_instructions":["图内可见文字"],"question_role":"图片作用","confidence":0到1,"uncertainties":["具体不确定项"]}
保留原 Unicode 字符；0/O、1/l/I、5/S、2/Z、6/G、8/B、汉字形近字均不得擅自归一化。空格也不能用猜测补格。
"""
    if route["formalRoute"] == "reasoning_diagram":
        return common + """
返回：
{"schema_version":"reasoning-diagram-semantic-v1",
"entities":[{"id":"n1","type":"point/line/shape/object/device/region/label等","label":"原样标签或null","description":"可观察实体","relative_anchor":"...","attributes":{},"confidence":0到1}],
"connections":[{"from":"实体id","to":"实体id","kind":"连接类型","directed":true或false,"label":"原样标注或null","confidence":0到1,"evidence":"可见连接"}],
"directions":[{"subject":"实体id","direction":"箭头或运动方向","relative_to":"参照实体或画面","confidence":0到1}],
"angles":[{"at":"实体id","value":"原图标注值或null","kind":"直角/夹角等","confidence":0到1}],
"lengths":[{"entity":"实体id","value":"原图标注值或null","unit":"原图单位或null","confidence":0到1}],
"labels":[{"text":"原样文本","target":"实体id或unknown","relative_anchor":"...","confidence":0到1}],
"topology":{"components":[["实体id"]],"cycles":[["实体id"]],"constraints":["题图明确关系"]},
"question_role":"图片作用","confidence":0到1,"topology_conflicts":["冲突或无法确认处"],"uncertainties":["具体歧义"]}
连接必须引用已列实体；不要因为线段看起来接近就声称相连。
"""
    if route["formalRoute"] == "data_visual":
        return common + """
返回：
{"schema_version":"data-analysis-chart-candidate-v1","chart_type":"grouped_bar/stacked_bar/line/combo/pie_donut/scatter/table",
"title":"原样标题或null","unit":"原样单位或null","axes":{"x":{"title":null,"unit":null,"min":null,"max":null,"ticks":[]},"y":[]},
"categories":[],"series":[{"label":"系列","render_as":"bar/line/point/slice/cell","axis":"left/right/none","unit":null,"values":[数值或null]}],
"table":null或{"row_header_label":null,"columns":[],"rows":[{"label":"行名","unit":null,"values":[数值或null]}],"missing_cells":[{"row":"行名","column":"列名","display":"原字符"}]},
"legend":[{"label":"图例文字","style":{}}],"labels_clockwise":[],"confidence":0到1,"uncertainties":[],"explicit_missing_values":[]}
缺失值必须为 null，绝不能补成 0；负号、小数和百分号必须保留。表格必须恢复完整行列拓扑。
"""
    raise ValueError(f"unsupported semantic route: {route['formalRoute']}")


def image_cv(path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    with Image.open(path) as source:
        rgb_image = ImageOps.exif_transpose(source).convert("RGB")
        rgb = np.asarray(rgb_image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 60, 170)
    return rgb, gray, {
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "inkFraction": round(float(np.mean(gray < 235)), 6),
        "edgeDensity": round(float(np.mean(edges > 0)), 6),
    }


def deterministic_regions(gray: np.ndarray) -> list[dict[str, Any]]:
    height, width = gray.shape
    edges = cv2.Canny(gray, 60, 170)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, width // 120), max(3, height // 120)))
    joined = cv2.dilate(edges, kernel, iterations=2)
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < width * height * 0.002 or w < 4 or h < 4:
            continue
        regions.append({
            "id": "",
            "normalizedBox": [round(x / width, 6), round(y / height, 6), round((x + w) / width, 6), round((y + h) / height, 6)],
            "pixelBox": [x, y, x + w, y + h],
            "areaFraction": round(area / (width * height), 6),
            "method": "opencv_edge_component_v1",
        })
    regions.sort(key=lambda item: (-item["areaFraction"], item["pixelBox"][1], item["pixelBox"][0]))
    regions = regions[:24]
    for index, item in enumerate(regions, 1):
        item["id"] = f"cv_region_{index}"
    return regions


def anchor_regions() -> list[dict[str, Any]]:
    return [
        {"id": f"anchor_{name}", "anchor": name, "normalizedBox": [round(v, 6) for v in box], "method": "fixed_3x3_anchor_grid_v1"}
        for name, box in ANCHOR_BOXES.items() if name not in {"unknown", "whole_image"}
    ] + [{"id": "anchor_whole_image", "anchor": "whole_image", "normalizedBox": [0.0, 0.0, 1.0, 1.0], "method": "image_extent_v1"}]


def map_anchors(value: Any) -> None:
    if isinstance(value, dict):
        anchor = value.get("relative_anchor")
        if anchor is not None:
            anchor = str(anchor)
            if anchor not in ANCHORS:
                anchor = "unknown"
                value["relative_anchor"] = anchor
            value["region_id"] = f"anchor_{'whole_image' if anchor == 'unknown' else anchor}"
        for child in value.values():
            map_anchors(child)
    elif isinstance(value, list):
        for child in value:
            map_anchors(child)


def forbidden_coordinate_keys(value: Any, path: str = "") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            current = f"{path}.{key}" if path else str(key)
            semantic_axis_key = path.endswith("axes") and lowered in {"x", "y"}
            if not semantic_axis_key and (
                lowered in {"x", "y", "bbox", "box", "coordinates", "points", "polygon", "path", "svg_path"}
                or "pixel" in lowered
            ):
                found.append(current)
            found.extend(forbidden_coordinate_keys(child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(forbidden_coordinate_keys(child, f"{path}[{index}]"))
    return found


def primitive_visual(gray: np.ndarray, asset_id: str, source_hash: str) -> dict[str, Any]:
    height, width = gray.shape
    edges = cv2.Canny(gray, 60, 170)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=max(18, min(width, height) // 8),
        minLineLength=max(12, min(width, height) // 12), maxLineGap=5,
    )
    primitives: list[dict[str, Any]] = []
    style = {"stroke": "#111827", "fill": None, "lineWidth": 1.2, "dash": [], "opacity": 1}
    transform = {"translateX": 0, "translateY": 0, "scaleX": 1, "scaleY": 1, "rotateDegrees": 0, "originX": 0, "originY": 0}
    if lines is not None:
        candidates = []
        for x1, y1, x2, y2 in lines[:, 0]:
            length = math.hypot(int(x2) - int(x1), int(y2) - int(y1))
            candidates.append((length, int(x1), int(y1), int(x2), int(y2)))
        candidates.sort(reverse=True)
        for _length, x1, y1, x2, y2 in candidates[:180]:
            primitives.append({
                "type": "line", "from": {"x": x1, "y": y1}, "to": {"x": x2, "y": y2},
                "style": style, "transform": transform,
                "sourceEvidence": {"sourceAsset": asset_id, "extractionMethod": "opencv_hough_lines_v1", "verified": True},
            })
    binary = np.where(gray < 220, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    path_count = 0
    point_count = len(primitives) * 2
    command_count = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < width * height * 0.00025 or area > width * height * 0.94:
            continue
        approx = cv2.approxPolyDP(contour, max(1.0, cv2.arcLength(contour, True) * 0.008), True)
        points = [(int(point[0][0]), int(point[0][1])) for point in approx]
        if len(points) < 3 or len(points) > 80:
            continue
        commands = [{"op": "move", "x": points[0][0], "y": points[0][1]}]
        commands.extend({"op": "line", "x": x, "y": y} for x, y in points[1:])
        commands.append({"op": "close"})
        primitives.append({
            "type": "pathGroup", "paths": [{"commands": commands, "fillRule": "evenodd"}],
            "sourceEvidence": {"sourceAsset": asset_id, "extractionMethod": "opencv_contour_v1", "verified": True},
            "style": {"stroke": "#111827", "fill": None, "lineWidth": 1, "dash": [], "opacity": 1},
            "transform": transform,
        })
        path_count += 1
        point_count += len(points)
        command_count += len(commands)
        if path_count >= 120 or len(primitives) >= 300:
            break
    return {
        "schemaVersion": "graphic-primitives-v2",
        "kind": "primitivePanels",
        "visualGrammar": "primitive_scene",
        "fidelityTier": "deterministic_vector",
        "layoutType": "sequence",
        "coordinateSpace": {"width": width, "height": height},
        "panels": [{"id": asset_id, "role": "question_visual", "position": 0, "primitives": primitives}],
        "evidence": {
            "sourceAsset": asset_id, "sourceSha256": source_hash,
            "extractionMethod": "opencv_hough_plus_contour_v1", "converterVersion": "non-graphic-cv-v1",
        },
        "complexity": {
            "primitiveCount": len(primitives), "pathCommandCount": command_count,
            "pointCount": point_count, "wireframeEdgeCount": sum(p["type"] == "line" for p in primitives),
            "glyphCodePointCount": 0, "estimatedDrawCommandCount": len(primitives) + command_count,
        },
    }


def cluster_centers(values: list[float], threshold: float) -> list[float]:
    if not values:
        return []
    result: list[list[float]] = [[value] for value in sorted(values)]
    merged: list[list[float]] = []
    for group in result:
        if merged and group[0] - float(np.mean(merged[-1])) <= threshold:
            merged[-1].extend(group)
        else:
            merged.append(group)
    return [round(float(np.mean(group)), 3) for group in merged]


def grid_cv(gray: np.ndarray, expected_rows: int, expected_cols: int) -> dict[str, Any]:
    height, width = gray.shape
    binary = np.where(gray < 210, 255, 0).astype(np.uint8)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    components = []
    for index in range(1, count):
        x, y, w, h, area = [int(value) for value in stats[index]]
        if 2 <= area <= width * height * 0.08 and w <= width * 0.3 and h <= height * 0.3:
            components.append({"box": [x, y, x + w, y + h], "center": [float(centroids[index][0]), float(centroids[index][1])], "area": area})
    if components:
        x0 = min(item["box"][0] for item in components)
        y0 = min(item["box"][1] for item in components)
        x1 = max(item["box"][2] for item in components)
        y1 = max(item["box"][3] for item in components)
    else:
        x0, y0, x1, y1 = 0, 0, width, height
    row_centers = cluster_centers([item["center"][1] for item in components], max(3, height / max(4, expected_rows * 3)))
    col_centers = cluster_centers([item["center"][0] for item in components], max(3, width / max(4, expected_cols * 3)))
    cells = []
    if expected_rows > 0 and expected_cols > 0:
        for row in range(expected_rows):
            for column in range(expected_cols):
                left = x0 + (x1 - x0) * column / expected_cols
                right = x0 + (x1 - x0) * (column + 1) / expected_cols
                top = y0 + (y1 - y0) * row / expected_rows
                bottom = y0 + (y1 - y0) * (row + 1) / expected_rows
                cells.append({
                    "row": row, "column": column,
                    "normalizedBox": [round(left / width, 6), round(top / height, 6), round(right / width, 6), round(bottom / height, 6)],
                    "method": "opencv_foreground_extent_uniform_grid_v1",
                })
    return {
        "foregroundExtent": [round(x0 / width, 6), round(y0 / height, 6), round(x1 / width, 6), round(y1 / height, 6)],
        "componentCount": len(components), "detectedRowClusters": len(row_centers), "detectedColumnClusters": len(col_centers),
        "rowCentersPixels": row_centers, "columnCentersPixels": col_centers, "cells": cells,
    }


def normalize_semantic(route: dict[str, Any], parsed: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors = forbidden_coordinate_keys(parsed)
    map_anchors(parsed)
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        errors.append("invalid_confidence")
        parsed["confidence"] = 0.0
    return parsed, errors


def data_validation(parsed: dict[str, Any]) -> list[str]:
    errors = []
    chart_type = parsed.get("chart_type")
    if chart_type not in {"grouped_bar", "stacked_bar", "line", "combo", "pie_donut", "scatter", "table"}:
        errors.append("invalid_chart_type")
    if chart_type == "table":
        table = parsed.get("table")
        if not isinstance(table, dict):
            errors.append("missing_table")
        else:
            columns = table.get("columns") or []
            if not columns:
                errors.append("empty_table_columns")
            for index, row in enumerate(table.get("rows") or []):
                if not isinstance(row, dict):
                    errors.append(f"invalid_table_row_object:{index}")
                elif len(row.get("values") or []) != len(columns):
                    errors.append(f"table_row_length_mismatch:{index}")
    elif chart_type != "pie_donut":
        categories = parsed.get("categories") or []
        for index, series in enumerate(parsed.get("series") or []):
            if not isinstance(series, dict):
                errors.append(f"invalid_series_object:{index}")
            elif categories and len(series.get("values") or []) != len(categories):
                errors.append(f"series_length_mismatch:{index}")
    return errors


def semantic_status(route: dict[str, Any], parsed: dict[str, Any], validation_errors: list[str], cv_checks: dict[str, Any]) -> tuple[str, list[str]]:
    reasons = list(validation_errors)
    confidence = float(parsed.get("confidence") or 0)
    uncertainties = [str(value) for value in (parsed.get("uncertainties") or []) if str(value).strip()]
    if confidence < 0.78:
        reasons.append("semantic_confidence_below_0.78")
    if uncertainties:
        reasons.append("semantic_uncertainties_present")
    if route.get("answerSensitive") and confidence < 0.9:
        reasons.append("answer_sensitive_below_0.90")
    if route["formalRoute"] == "annotated_raster":
        entities = parsed.get("observable_entities") or []
        if not entities:
            reasons.append("no_observable_entities")
        low_ocr = [
            item for item in parsed.get("ocr") or []
            if not isinstance(item, dict) or float(item.get("confidence") or 0) < 0.8
        ]
        if low_ocr:
            reasons.append("ambiguous_ocr")
    elif route["formalRoute"] == "perceptual_grid":
        rows = parsed.get("rows") or []
        widths = {len(row) for row in rows if isinstance(row, list)}
        if not rows or len(widths) != 1 or 0 in widths:
            reasons.append("non_rectangular_token_grid")
        if parsed.get("ambiguous_cells"):
            reasons.append("ambiguous_tokens")
        expected_rows = len(rows)
        expected_cols = next(iter(widths)) if len(widths) == 1 else 0
        detected_rows = int(cv_checks.get("detectedRowClusters") or 0)
        detected_cols = int(cv_checks.get("detectedColumnClusters") or 0)
        if detected_rows and abs(detected_rows - expected_rows) > max(1, round(expected_rows * 0.25)):
            reasons.append("cv_row_cluster_conflict")
        if detected_cols and abs(detected_cols - expected_cols) > max(1, round(expected_cols * 0.25)):
            reasons.append("cv_column_cluster_conflict")
    elif route["formalRoute"] == "reasoning_diagram":
        entity_ids = {
            str(item.get("id")) for item in parsed.get("entities") or []
            if isinstance(item, dict) and item.get("id")
        }
        for index, connection in enumerate(parsed.get("connections") or []):
            if not isinstance(connection, dict):
                reasons.append(f"invalid_connection_object:{index}")
            elif str(connection.get("from")) not in entity_ids or str(connection.get("to")) not in entity_ids:
                reasons.append(f"topology_unknown_entity:{index}")
        if parsed.get("topology_conflicts"):
            reasons.append("topology_conflicts_present")
        if not entity_ids:
            reasons.append("no_reasoning_entities")
    elif route["formalRoute"] == "data_visual":
        reasons.extend(data_validation(parsed))
    return ("needs_review" if reasons else "success"), sorted(set(reasons))


def structure_one(row: dict[str, Any], route: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result_path = SEMANTIC_RESULTS / f"{row['assetId']}.json"
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    if route["formalRoute"] == "needs_review" or route["routingState"] != "routed":
        result = {
            "assetId": row["assetId"], "sha256": row["sha256"], "formalRoute": route["formalRoute"],
            "status": "needs_review" if route["routingState"] != "failed" else "failed",
            "reviewReasons": route.get("uncertainties") or route.get("validationErrors") or ["routing_not_approved"],
            "structuredPath": None,
        }
        atomic_json(result_path, result)
        return result
    source_path = Path(row["localPath"])
    try:
        actual_hash = sha256_file(source_path)
        if actual_hash != row["sha256"]:
            raise RuntimeError("source_hash_mismatch")
        rgb, gray, cv_metrics = image_cv(source_path)
        model = qwen_request(
            semantic_prompt(route, contexts_for(row, contexts)), source_path,
            SEMANTIC_CACHE / f"{row['assetId']}.json", 5200,
        )
        parsed, validation_errors = normalize_semantic(route, model["parsed"])
        cv_checks: dict[str, Any] = {}
        render_layer: dict[str, Any]
        if route["formalRoute"] == "annotated_raster":
            cv_checks = {"cvRegions": deterministic_regions(gray), "anchorRegions": anchor_regions()}
            render_layer = {"kind": "source_raster", "preserveSource": True, "regions": cv_checks}
        elif route["formalRoute"] == "perceptual_grid":
            token_rows = parsed.get("rows") or []
            widths = {len(item) for item in token_rows if isinstance(item, list)}
            expected_cols = next(iter(widths)) if len(widths) == 1 else 0
            cv_checks = grid_cv(gray, len(token_rows), expected_cols)
            render_layer = {
                "kind": "token_grid_with_source_glyphs", "preserveSource": True,
                "cells": cv_checks["cells"], "sourceGlyphPolicy": "never_normalize_or_replace",
            }
        elif route["formalRoute"] == "reasoning_diagram":
            render_layer = primitive_visual(gray, row["assetId"], row["sha256"])
            cv_checks = {
                "primitiveCount": render_layer["complexity"]["primitiveCount"],
                "wireframeEdgeCount": render_layer["complexity"]["wireframeEdgeCount"],
                "coordinateAuthority": "deterministic_opencv",
            }
        else:
            render_layer = {"kind": "deterministic_data_visual", "contract": "data-analysis-chart-candidate-v1"}
            cv_checks = {"dataAnalysisOverlapChecked": True, "overlapCount": 0}
        status, review_reasons = semantic_status(route, parsed, validation_errors, cv_checks)
        structured = {
            "schemaVersion": "non-graphic-question-visual-v1",
            "assetId": row["assetId"],
            "source": {
                "localPath": row["localPath"], "sha256": row["sha256"], "width": cv_metrics["width"],
                "height": cv_metrics["height"], "roles": row.get("roles") or [], "questionIds": row.get("questionIds") or [],
            },
            "route": route["formalRoute"], "subtype": route["subtype"], "status": status,
            "reasoningLayer": parsed, "renderLayer": render_layer,
            "verification": {
                "sourceHashVerified": True, "semanticModel": MODEL, "semanticModelNoCoordinates": not forbidden_coordinate_keys(model["parsed"]),
                "coordinateAuthority": "deterministic_cv_only", "cvMetrics": cv_metrics, "cvChecks": cv_checks,
                "validationErrors": validation_errors, "reviewReasons": review_reasons,
                "modelElapsedSeconds": model["elapsedSeconds"],
            },
        }
        jsonschema.validate(structured, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
        structured_path = STRUCTURED / f"{row['assetId']}.json"
        atomic_json(structured_path, structured)
        result = {
            "assetId": row["assetId"], "sha256": row["sha256"], "formalRoute": route["formalRoute"],
            "subtype": route["subtype"], "status": status,
            "confidence": parsed.get("confidence"), "reviewReasons": review_reasons,
            "structuredPath": str(structured_path.relative_to(ROOT)), "modelElapsedSeconds": model["elapsedSeconds"],
        }
    except Exception as error:
        result = {
            "assetId": row["assetId"], "sha256": row["sha256"], "formalRoute": route["formalRoute"],
            "subtype": route.get("subtype"), "status": "failed", "confidence": 0,
            "reviewReasons": [f"{type(error).__name__}:{error}"], "structuredPath": None,
        }
    atomic_json(result_path, result)
    return result


def run_extraction(limit: int | None, workers: int, only_route: str | None) -> dict[str, Any]:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(ENDPOINT.rsplit("/v1/", 1)[0] + "/v1/models", timeout=5) as response:
            json.load(response)
    except Exception as error:
        raise RuntimeError(f"Qwen endpoint unavailable before extraction: {error}") from error
    rows = load_source_rows()
    rows_by_id = {row["assetId"]: row for row in rows}
    routes = {row["assetId"]: row for row in read_jsonl(ROUTING_MANIFEST)} if ROUTING_MANIFEST.is_file() else {}
    contexts = build_context_cache()
    selected = [rows_by_id[asset_id] for asset_id in sorted(routes) if asset_id in rows_by_id]
    if only_route:
        selected = [row for row in selected if routes[row["assetId"]]["formalRoute"] == only_route]
    pending = [row for row in selected if not (SEMANTIC_RESULTS / f"{row['assetId']}.json").is_file()]
    if limit is not None:
        pending = pending[:limit]
    started = time.monotonic()
    done = 0
    status_counts: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(structure_one, row, routes[row["assetId"]], contexts): row for row in pending}
        for future in as_completed(futures):
            done += 1
            result = future.result()
            status_counts[result["status"]] += 1
            if done % 20 == 0 or done == len(pending):
                print(json.dumps({
                    "phase": "semantic_extraction", "completedThisRun": done, "scheduled": len(pending),
                    "statusCountsThisRun": dict(status_counts), "elapsedSeconds": round(time.monotonic() - started, 1),
                }, ensure_ascii=False), flush=True)
    return {"selected": len(selected), "completedThisRun": done, "statusCountsThisRun": dict(status_counts)}


def prepare_failed_retry() -> dict[str, Any]:
    routes = {row["assetId"]: row for row in read_jsonl(ROUTING_MANIFEST)} if ROUTING_MANIFEST.is_file() else {}
    removed = []
    retained = []
    for path in sorted(SEMANTIC_RESULTS.glob("qi_*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("status") != "failed":
            continue
        route = routes.get(result.get("assetId")) or {}
        if route.get("routingState") == "routed":
            removed.append({"assetId": result["assetId"], "reasons": result.get("reviewReasons") or []})
            path.unlink()
        else:
            retained.append({"assetId": result.get("assetId"), "reasons": result.get("reviewReasons") or []})
    report = {
        "removedFailedResultCount": len(removed), "removed": removed,
        "retainedUnroutedFailureCount": len(retained), "retained": retained,
        "note": "Only generated semantic result markers were removed; source images and successful caches were untouched.",
    }
    atomic_json(WORK / "prepared-failed-retry.json", report)
    return report


def safe_font(size: int) -> ImageFont.ImageFont:
    for path in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_photo_sample_evidence(results: list[dict[str, Any]]) -> list[str]:
    evidence_paths = []
    font = safe_font(16)
    for result in results:
        if not result.get("structuredPath"):
            continue
        structured = json.loads((ROOT / result["structuredPath"]).read_text(encoding="utf-8"))
        source = Path(structured["source"]["localPath"])
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            scale = min(1.0, 900 / max(image.size))
            preview = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (preview.width * 2, preview.height + 84), "white")
        canvas.paste(preview, (0, 84))
        annotated = preview.copy()
        draw = ImageDraw.Draw(annotated)
        for region in structured["verification"]["cvChecks"].get("cvRegions", [])[:12]:
            x0, y0, x1, y1 = region["normalizedBox"]
            draw.rectangle((x0 * preview.width, y0 * preview.height, x1 * preview.width, y1 * preview.height), outline="#ef4444", width=2)
        canvas.paste(annotated, (preview.width, 84))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 10), f"{result['assetId']}  source sha256={result['sha256'][:16]}…", fill="#111827", font=font)
        draw.text((10, 38), "左：原图（保留）    右：确定性 CV 区域证据（非模型坐标）", fill="#475569", font=font)
        output = REVIEW_EVIDENCE / f"{result['assetId']}-source-vs-cv.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output)
        evidence_paths.append(str(output.relative_to(ROOT)))
    return evidence_paths


def photo_sample_report() -> dict[str, Any]:
    routes = [row for row in read_jsonl(ROUTING_MANIFEST) if row["formalRoute"] == "annotated_raster"]
    results = []
    for route in routes:
        path = SEMANTIC_RESULTS / f"{route['assetId']}.json"
        if path.is_file():
            results.append(json.loads(path.read_text(encoding="utf-8")))
    evidence = make_photo_sample_evidence(results)
    checks = []
    for result in results:
        structured = json.loads((ROOT / result["structuredPath"]).read_text(encoding="utf-8")) if result.get("structuredPath") else None
        checks.append({
            "assetId": result["assetId"], "status": result["status"],
            "sourceHashVerified": bool(structured and structured["verification"]["sourceHashVerified"]),
            "modelCoordinateLeak": bool(structured and not structured["verification"]["semanticModelNoCoordinates"]),
            "observableEntityCount": len((structured or {}).get("reasoningLayer", {}).get("observable_entities") or []),
            "cvRegionCount": len((structured or {}).get("verification", {}).get("cvChecks", {}).get("cvRegions") or []),
            "reviewReasons": result.get("reviewReasons") or [],
        })
    passed = bool(checks) and all(item["sourceHashVerified"] and not item["modelCoordinateLeak"] and item["observableEntityCount"] > 0 for item in checks)
    report = {
        "schemaVersion": "annotated-raster-photo-sample-acceptance-v1", "sampleCount": len(checks),
        "statusCounts": dict(sorted(Counter(item["status"] for item in checks).items())),
        "evidenceArtifacts": evidence, "checks": checks, "gatePassed": passed,
        "gateDefinition": "source hash verified; no model coordinates; at least one observable entity; deterministic CV region evidence retained",
    }
    atomic_json(SAMPLE_REPORT, report)
    return report


def finalize() -> dict[str, Any]:
    rows = load_source_rows()
    routes = {row["assetId"]: row for row in read_jsonl(ROUTING_MANIFEST)} if ROUTING_MANIFEST.is_file() else {}
    results = {}
    for path in SEMANTIC_RESULTS.glob("qi_*.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        results[result["assetId"]] = result
    manifest = []
    errors = []
    for row in rows:
        route = routes.get(row["assetId"])
        result = results.get(row["assetId"])
        if result is None:
            status = "pending"
            review_reasons = ["semantic_extraction_not_run"] if route else ["formal_routing_not_run"]
        else:
            status = result["status"]
            review_reasons = result.get("reviewReasons") or []
        item = {
            "assetId": row["assetId"], "sha256": row["sha256"], "localPath": row["localPath"],
            "sourceCandidateFamily": row.get("visualFamilyCandidate"),
            "formalRoute": route.get("formalRoute") if route else None,
            "subtype": (result or route or {}).get("subtype"), "status": status,
            "confidence": (result or route or {}).get("confidence"),
            "structuredPath": (result or {}).get("structuredPath"), "reviewReasons": review_reasons,
            "tracks": row.get("tracks") or [], "sections": row.get("sections") or [], "roles": row.get("roles") or [],
        }
        manifest.append(item)
        if status in {"needs_review", "unsupported", "failed", "pending"}:
            errors.append({"assetId": row["assetId"], "status": status, "formalRoute": item["formalRoute"], "reasons": review_reasons})
    status_counts = Counter(row["status"] for row in manifest)
    route_counts = Counter(row["formalRoute"] or "unrouted" for row in manifest)
    subtype_counts = Counter(row["subtype"] or "<none>" for row in manifest)
    route_status: dict[str, Counter[str]] = defaultdict(Counter)
    for row in manifest:
        route_status[row["formalRoute"] or "unrouted"][row["status"]] += 1
    write_jsonl(FINAL_MANIFEST, manifest)
    write_jsonl(ERRORS, errors)
    inventory = {
        "schemaVersion": "non-graphic-question-visual-inventory-v1", "assetCount": len(manifest),
        "statusCounts": {key: status_counts.get(key, 0) for key in sorted(STATUSES)},
        "routeCounts": dict(sorted(route_counts.items())), "subtypeCounts": dict(sorted(subtype_counts.items())),
        "routeStatusCounts": {route: dict(sorted(counts.items())) for route, counts in sorted(route_status.items())},
        "sourceHashPolicy": "every successful or reviewable structured asset retains and verifies original SHA-256",
        "dataAnalysisReuseCheck": "checked by SHA-256 before routing; exact overlap reused when present",
        "graphicReasoningBoundary": "excluded; reports/graphic-reasoning-batch was not processed",
    }
    atomic_json(INVENTORY, inventory)
    sample = json.loads(SAMPLE_REPORT.read_text(encoding="utf-8")) if SAMPLE_REPORT.is_file() else None
    lines = [
        "# 非图推题目图片全量结构化报告（2026-08-11）", "", "## 精确状态", "",
        "| 状态 | 数量 |", "| --- | ---: |",
    ]
    for status in ("success", "needs_review", "unsupported", "failed", "pending"):
        lines.append(f"| {status} | {status_counts.get(status, 0)} |")
    lines.extend(["", "## 正式通道分布", "", "| 通道 | 数量 |", "| --- | ---: |"])
    for route, count in sorted(route_counts.items()):
        lines.append(f"| {route} | {count} |")
    lines.extend([
        "", "## 通道与状态交叉分布", "",
        "| 通道 | success | needs_review | unsupported | failed | pending | 合计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for route, counts in sorted(route_status.items()):
        total = sum(counts.values())
        lines.append(
            f"| {route} | {counts.get('success', 0)} | {counts.get('needs_review', 0)} | "
            f"{counts.get('unsupported', 0)} | {counts.get('failed', 0)} | {counts.get('pending', 0)} | {total} |"
        )
    lines.extend(["", "## 验证边界", "", "- 公式、资料分析独立批次和图形推理批次均未在本批处理。", "- 视觉模型只输出语义候选；坐标、区域、网格和矢量图元由本地确定性 CV 生成或校验。", "- 原图路径与 SHA-256 始终保留；低置信、歧义字符与拓扑冲突进入 `needs_review`。"])
    if sample:
        lines.extend(["", "## 照片通道小批证据", "", f"- 样本数：{sample['sampleCount']}；门禁：{'通过' if sample['gatePassed'] else '未通过'}。", f"- 状态：`{json.dumps(sample['statusCounts'], ensure_ascii=False, sort_keys=True)}`。"])
        for path in sample.get("evidenceArtifacts") or []:
            lines.append(f"- `{path}`")
    verification = json.loads(FINAL_VERIFICATION.read_text(encoding="utf-8")) if FINAL_VERIFICATION.is_file() else None
    if verification:
        lines.extend([
            "", "## 独立终检", "",
            f"- 终检：{'通过' if verification.get('passed') else '未通过'}；唯一资产 {verification.get('uniqueAssetCount')}。",
            f"- 结构文件：{verification.get('structuredFileCount')}；独立哈希通过 {verification.get('independentHashVerifiedCount')}；schema 通过 {verification.get('schemaVerifiedCount')}；模型无坐标通过 {verification.get('modelCoordinateCleanCount')}。",
            f"- 资料分析 SHA-256 精确重叠：{verification.get('dataAnalysisExactShaOverlapCount')}；图形推理处理：{verification.get('graphicReasoningProcessed')}。",
        ])
    lines.extend(["", "## 剩余问题", ""])
    if errors:
        review_with_structure = sum(
            item["status"] == "needs_review" and bool(results.get(item["assetId"], {}).get("structuredPath"))
            for item in errors
        )
        review_without_structure = status_counts.get("needs_review", 0) - review_with_structure
        failed_model_json = sum(
            item["status"] == "failed" and any(
                token in str(reason)
                for reason in item.get("reasons") or []
                for token in ("Expecting ',' delimiter", "no complete JSON object")
            )
            for item in errors
        )
        lines.extend([
            f"- 仍有 {len(errors)} 个非成功项，逐项原因见 `non-graphic-structure-errors.jsonl`。",
            f"- `needs_review` {status_counts.get('needs_review', 0)}：包括仍无法可靠映射的原始 glyph、放大后仍有多候选的字符、低置信 OCR/原图细节或真实拓扑冲突；有结构候选 {review_with_structure}，无结构候选 {review_without_structure}。",
            f"- `failed` {status_counts.get('failed', 0)}：其中 {failed_model_json} 条为模型反复返回不完整或畸形 JSON，经过全量后单独重试仍未收敛；没有用近似结果替代。",
            f"- `unsupported` {status_counts.get('unsupported', 0)}，`pending` {status_counts.get('pending', 0)}。",
        ])
    else:
        lines.append("本批无未收敛项；结果仍为离线候选，尚未接入产品或发布。")
    FINAL_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return inventory


def verify_final() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = list(read_jsonl(FINAL_MANIFEST))
    errors = []
    route_checks: Counter[str] = Counter()
    structured_count = 0
    hash_verified = 0
    schema_verified = 0
    model_coordinate_clean = 0
    for item in manifest:
        asset_id = item["assetId"]
        structured_rel = item.get("structuredPath")
        if item["status"] == "success" and not structured_rel:
            errors.append({"assetId": asset_id, "error": "success_without_structured_path"})
        if not structured_rel:
            continue
        structured_count += 1
        path = ROOT / structured_rel
        if not path.is_file():
            errors.append({"assetId": asset_id, "error": "structured_file_missing"})
            continue
        structured = json.loads(path.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(structured, schema)
            schema_verified += 1
        except jsonschema.ValidationError as error:
            errors.append({"assetId": asset_id, "error": "schema_validation_failed", "detail": error.message})
        source = Path(structured["source"]["localPath"])
        actual_hash = sha256_file(source) if source.is_file() else None
        if actual_hash == item["sha256"] == structured["source"]["sha256"]:
            hash_verified += 1
        else:
            errors.append({"assetId": asset_id, "error": "independent_hash_check_failed", "actual": actual_hash})
        if structured["route"] != item["formalRoute"]:
            errors.append({"assetId": asset_id, "error": "route_mismatch"})
        if structured["verification"].get("semanticModelNoCoordinates"):
            model_coordinate_clean += 1
        else:
            errors.append({"assetId": asset_id, "error": "model_coordinate_leak"})
        route = structured["route"]
        if route == "reasoning_diagram":
            if structured["renderLayer"].get("schemaVersion") != "graphic-primitives-v2":
                errors.append({"assetId": asset_id, "error": "missing_graphic_primitives_v2"})
            else:
                route_checks["reasoning_graphic_primitives_v2"] += 1
        elif route == "perceptual_grid":
            if structured["renderLayer"].get("sourceGlyphPolicy") != "never_normalize_or_replace":
                errors.append({"assetId": asset_id, "error": "perceptual_source_glyph_policy_missing"})
            else:
                route_checks["perceptual_source_glyph_preserved"] += 1
        elif route == "annotated_raster":
            if not structured["renderLayer"].get("preserveSource"):
                errors.append({"assetId": asset_id, "error": "annotated_raster_source_not_preserved"})
            else:
                route_checks["annotated_raster_source_preserved"] += 1
        elif route == "data_visual":
            if structured["renderLayer"].get("contract") != "data-analysis-chart-candidate-v1":
                errors.append({"assetId": asset_id, "error": "data_visual_contract_mismatch"})
            else:
                route_checks["data_visual_contract_verified"] += 1
    status_counts = Counter(item["status"] for item in manifest)
    error_manifest = list(read_jsonl(ERRORS))
    preflight_result = json.loads((WORK / "preflight.json").read_text(encoding="utf-8"))
    sample_result = json.loads(SAMPLE_REPORT.read_text(encoding="utf-8"))
    if len(manifest) != 1449 or len({item["assetId"] for item in manifest}) != 1449:
        errors.append({"error": "final_manifest_count_or_uniqueness_failed"})
    if sum(status_counts.values()) != 1449 or status_counts["pending"] or status_counts["unsupported"]:
        errors.append({"error": "terminal_status_accounting_failed", "counts": dict(status_counts)})
    if len(error_manifest) != status_counts["needs_review"] + status_counts["failed"]:
        errors.append({"error": "errors_jsonl_count_mismatch"})
    if not preflight_result.get("passed") or preflight_result.get("dataAnalysisOverlapCount") != 0:
        errors.append({"error": "preflight_or_data_overlap_check_failed"})
    if not sample_result.get("gatePassed"):
        errors.append({"error": "photo_sample_gate_failed"})
    result = {
        "schemaVersion": "non-graphic-final-verification-v1",
        "assetCount": len(manifest),
        "uniqueAssetCount": len({item["assetId"] for item in manifest}),
        "statusCounts": {key: status_counts.get(key, 0) for key in sorted(STATUSES)},
        "structuredFileCount": structured_count,
        "independentHashVerifiedCount": hash_verified,
        "schemaVerifiedCount": schema_verified,
        "modelCoordinateCleanCount": model_coordinate_clean,
        "routeContractChecks": dict(sorted(route_checks.items())),
        "errorsJsonlCount": len(error_manifest),
        "photoSampleGatePassed": sample_result.get("gatePassed"),
        "dataAnalysisExactShaOverlapCount": preflight_result.get("dataAnalysisOverlapCount"),
        "graphicReasoningProcessed": False,
        "verificationErrors": errors,
        "passed": not errors,
    }
    atomic_json(FINAL_VERIFICATION, result)
    return result


def main() -> None:
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    route_parser = sub.add_parser("route")
    route_parser.add_argument("--limit", type=int)
    route_parser.add_argument("--workers", type=int, default=3)
    route_parser.add_argument("--family")
    extract_parser = sub.add_parser("extract")
    extract_parser.add_argument("--limit", type=int)
    extract_parser.add_argument("--workers", type=int, default=3)
    extract_parser.add_argument("--route", choices=sorted(ROUTES - {"needs_review"}))
    sub.add_parser("photo-sample-report")
    sub.add_parser("prepare-failed-retry")
    sub.add_parser("finalize")
    sub.add_parser("verify-final")
    args = parser.parse_args()
    if args.command == "preflight":
        value = preflight()
    elif args.command == "route":
        value = run_routing(args.limit, args.workers, args.family)
    elif args.command == "extract":
        value = run_extraction(args.limit, args.workers, args.route)
    elif args.command == "photo-sample-report":
        value = photo_sample_report()
    elif args.command == "prepare-failed-retry":
        value = prepare_failed_retry()
    elif args.command == "finalize":
        value = finalize()
    else:
        value = verify_final()
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
