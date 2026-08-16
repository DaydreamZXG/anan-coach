#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import mimetypes
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import cv2
import jsonschema
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCE_JSONL = REPO / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613/paper_questions.jsonl"
ASSET_ROOT = REPO / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613-assets"
WORK = ROOT / "work"
CACHE = ROOT / "cache/qwen"
CROPS = ROOT / "review-crops"
STRUCTURED = ROOT / "structured"
PREVIEWS = ROOT / "previews"
SCAN_PATH = WORK / "assets.jsonl"
SCHEMA_PATH = ROOT / "structured-schema.json"
TABLE_CORRECTIONS = ROOT / "table-corrections"
ENDPOINT = "http://127.0.0.1:18000/v1/chat/completions"
MODEL = "qwen3-vl-30b-a3b"
CHART_TYPES = (
    "grouped_bar",
    "stacked_bar",
    "line",
    "combo",
    "pie_donut",
    "scatter",
    "table",
    "other_unsupported",
)
MISSING_TOKENS = {"", "-", "--", "—", "–", "…", "...", "n/a", "na", "null", "无", "缺失", "未公布"}
MIN_REQUEST_INTERVAL_SECONDS = 1.25
LAST_REQUEST_AT = 0.0
REQUEST_START_LOCK = threading.Lock()


class ImageTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            self.images.append({key.lower(): value or "" for key, value in attrs})


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


def load_table_correction(asset_id: str) -> dict[str, Any] | None:
    path = TABLE_CORRECTIONS / f"{asset_id}.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_image_tags(fragment: str | None, *, exclude_tex: bool) -> list[dict[str, str]]:
    if not fragment:
        return []
    parser = ImageTagParser()
    try:
        parser.feed(fragment)
    except Exception:
        return []
    result = []
    for item in parser.images:
        source = html.unescape(item.get("src", "")).strip()
        if not source:
            continue
        if exclude_tex and item.get("flag", "").lower() == "tex":
            continue
        result.append(item | {"src": source})
    return result


def context_text(question: dict[str, Any]) -> str:
    taxonomy = question.get("primary_taxonomy") or {}
    parts = [
        question.get("final_analysis") or "",
        question.get("original_analysis") or "",
        question.get("stem_text") or "",
        question.get("material_text") or "",
        taxonomy.get("catalog_leaf") or "",
        " ".join(taxonomy.get("catalog_path") or []),
        question.get("raw_knowledge_point") or "",
    ]
    return " ".join(str(part) for part in parts if part)


def heuristic_type(text: str, has_table_html: bool = False) -> str:
    lowered = text.lower()
    signals = {
        "stacked_bar": bool(re.search(r"堆[叠积]|累计柱|stacked", lowered)),
        "pie_donut": bool(re.search(r"饼(?:状)?图|环形图|圆环图|pie|donut", lowered)),
        "scatter": bool(re.search(r"散点图|scatter", lowered)),
        "table": has_table_html or bool(re.search(r"统计表|表格|下表|如下表|表\s*\d", lowered)),
        "grouped_bar": bool(re.search(r"柱状图|条形图|柱形图|bar chart", lowered)),
        "line": bool(re.search(r"折线图|曲线图|趋势图|line chart", lowered)),
    }
    if signals["stacked_bar"]:
        return "stacked_bar"
    if signals["grouped_bar"] and signals["line"]:
        return "combo"
    for key in ("pie_donut", "scatter", "table", "grouped_bar", "line"):
        if signals[key]:
            return key
    return "other_unsupported"


def image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def scan_assets() -> dict[str, Any]:
    started = time.monotonic()
    assets: dict[str, dict[str, Any]] = {}
    path_hashes: dict[str, str] = {}
    question_count = 0
    referenced_image_count = 0
    inline_table_count = 0
    unresolved_count = 0

    with SOURCE_JSONL.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            question = json.loads(raw)
            if question.get("section") != "资料分析":
                continue
            question_count += 1
            source_path = Path(question.get("source_path") or "")
            source_dir = ASSET_ROOT / source_path.parent
            occurrence_base = {
                "question_id": str(question.get("id") or ""),
                "paper_title": str(question.get("paper_title") or ""),
                "question_number": question.get("number") if isinstance(question.get("number"), int) else None,
                "source_jsonl_line": line_number,
            }
            context = {
                "question_id": occurrence_base["question_id"],
                "stem": str(question.get("stem_text") or "")[:500],
                "analysis": str(question.get("final_analysis") or question.get("original_analysis") or "")[:1800],
                "text_hints": context_text(question)[:3000],
            }
            field_specs = (
                ("material_html", question.get("material_html"), True),
                ("stem_html", question.get("stem_html"), True),
            )
            for field, fragment, exclude_tex in field_specs:
                for image in extract_image_tags(fragment, exclude_tex=exclude_tex):
                    referenced_image_count += 1
                    reference = image["src"]
                    local_path = (source_dir / reference).resolve()
                    occurrence = occurrence_base | {"html_field": field, "image_ref": reference}
                    if not local_path.is_file():
                        unresolved_count += 1
                        digest = hashlib.sha256(f"missing\0{source_path.parent}\0{reference}".encode()).hexdigest()
                        asset_id = "da_" + digest[:24]
                        record = assets.setdefault(asset_id, {
                            "asset_id": asset_id,
                            "sha256": digest,
                            "kind": "missing_image",
                            "local_path": str(local_path),
                            "size_bytes": None,
                            "width": None,
                            "height": None,
                            "heuristic_votes": Counter(),
                            "occurrences": [],
                            "contexts": [],
                        })
                    else:
                        key = str(local_path)
                        digest = path_hashes.get(key)
                        if digest is None:
                            digest = sha256_file(local_path)
                            path_hashes[key] = digest
                        asset_id = "da_" + digest[:24]
                        width, height = image_size(local_path)
                        record = assets.setdefault(asset_id, {
                            "asset_id": asset_id,
                            "sha256": digest,
                            "kind": "image",
                            "local_path": str(local_path),
                            "size_bytes": local_path.stat().st_size,
                            "width": width,
                            "height": height,
                            "heuristic_votes": Counter(),
                            "occurrences": [],
                            "contexts": [],
                        })
                    vote = heuristic_type(context["text_hints"])
                    record["heuristic_votes"][vote] += 1
                    if occurrence not in record["occurrences"]:
                        record["occurrences"].append(occurrence)
                    if len(record["contexts"]) < 8 and context not in record["contexts"]:
                        record["contexts"].append(context)

            material_html = question.get("material_html") or ""
            if "<table" in material_html.lower():
                inline_table_count += 1
                normalized = re.sub(r"\s+", " ", material_html).strip()
                digest = hashlib.sha256(("inline_table\0" + normalized).encode()).hexdigest()
                asset_id = "da_" + digest[:24]
                occurrence = occurrence_base | {"html_field": "material_html", "image_ref": "inline:<table>"}
                record = assets.setdefault(asset_id, {
                    "asset_id": asset_id,
                    "sha256": digest,
                    "kind": "inline_table",
                    "local_path": f"{SOURCE_JSONL}#{line_number}:material_html",
                    "inline_html": normalized,
                    "size_bytes": len(normalized.encode()),
                    "width": None,
                    "height": None,
                    "heuristic_votes": Counter(),
                    "occurrences": [],
                    "contexts": [],
                })
                record["heuristic_votes"]["table"] += 1
                if occurrence not in record["occurrences"]:
                    record["occurrences"].append(occurrence)
                if len(record["contexts"]) < 8 and context not in record["contexts"]:
                    record["contexts"].append(context)

            if question_count % 2000 == 0:
                print(f"scan questions={question_count} unique_assets={len(assets)}", flush=True)

    rows = []
    for record in assets.values():
        votes = record.pop("heuristic_votes")
        record["heuristic_votes"] = dict(votes)
        record["heuristic_type"] = votes.most_common(1)[0][0] if votes else "other_unsupported"
        record["source_occurrence_count"] = len(record["occurrences"])
        rows.append(record)
    rows.sort(key=lambda item: item["asset_id"])
    write_jsonl(SCAN_PATH, rows)
    summary = {
        "question_occurrences": question_count,
        "referenced_image_occurrences": referenced_image_count,
        "inline_table_occurrences": inline_table_count,
        "unique_assets": len(rows),
        "resolved_images": sum(row["kind"] == "image" for row in rows),
        "inline_tables": sum(row["kind"] == "inline_table" for row in rows),
        "unresolved_assets": sum(row["kind"] == "missing_image" for row in rows),
        "unresolved_occurrences": unresolved_count,
        "heuristic_types": dict(Counter(row["heuristic_type"] for row in rows)),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "source": str(SOURCE_JSONL),
        "asset_root": str(ASSET_ROOT),
    }
    atomic_json(WORK / "scan-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def extract_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response contains no JSON object")
    parsed = json.loads(cleaned[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model response root is not an object")
    return parsed


def deterministic_json_repair(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("malformed response contains no object bounds")
    candidate = cleaned[start:end + 1]
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    for _ in range(20):
        try:
            parsed = json.loads(candidate)
            if not isinstance(parsed, dict):
                raise ValueError("repaired response root is not object")
            return parsed
        except json.JSONDecodeError as error:
            if error.msg == "Expecting ',' delimiter" and 0 < error.pos < len(candidate):
                candidate = candidate[:error.pos] + "," + candidate[error.pos:]
                continue
            raise
    raise ValueError("deterministic JSON repair exceeded edit limit")


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    with Image.open(path) as image:
        image = image.convert("RGB")
        max_dimension = max(image.size)
        if max_dimension > 2600:
            scale = 2600 / max_dimension
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
        from io import BytesIO
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=92, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def wait_for_request_slot() -> None:
    global LAST_REQUEST_AT
    with REQUEST_START_LOCK:
        delay = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        LAST_REQUEST_AT = time.monotonic()


def repair_malformed_json(content: str) -> tuple[dict[str, Any], str, float]:
    prompt = """只输出合法 JSON 对象，不要 Markdown、解释或新增事实。修复下方模型输出的 JSON 语法，保持全部键和值原意；
缺失值仍为 null，禁止把缺失值改成 0，禁止添加像素坐标。\n\n""" + content
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }
    wait_for_request_slot()
    started = time.monotonic()
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = json.load(response)
    repaired_content = raw["choices"][0]["message"]["content"]
    return extract_json_object(repaired_content), repaired_content, round(time.monotonic() - started, 3)


def qwen_request(
    *,
    prompt: str,
    image_paths: list[Path],
    cache_path: Path,
    max_tokens: int = 5000,
    allow_model_json_repair: bool = True,
    json_mode: bool = False,
) -> dict[str, Any]:
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for path in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_data_url(path)}})
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        payload["structured_outputs"] = {
            "json_object": True,
            "disable_fallback": True,
        }
    last_error: Exception | None = None
    for attempt in range(3):
        wait_for_request_slot()
        started = time.monotonic()
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = json.load(response)
            message = raw["choices"][0]["message"]["content"]
            # A partial object can still contain an earlier closing brace. The
            # old extractor sliced at that brace and silently accepted a table
            # with missing rows. Retry the vision request instead of repairing
            # or caching an unfinished response.
            if not message.strip().removesuffix("```").rstrip().endswith("}"):
                raise ValueError("incomplete_model_json")
            repair_content = None
            repair_elapsed = None
            try:
                parsed = extract_json_object(message)
            except (ValueError, json.JSONDecodeError):
                try:
                    parsed = deterministic_json_repair(message)
                    repair_content = "deterministic_missing_comma_or_trailing_comma_repair"
                    repair_elapsed = 0.0
                except (ValueError, json.JSONDecodeError):
                    if not allow_model_json_repair:
                        raise RuntimeError("malformed_model_json_after_deterministic_repair")
                    parsed, repair_content, repair_elapsed = repair_malformed_json(message)
            result = {
                "model": MODEL,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "content": message,
                "parsed": parsed,
                "json_repair_content": repair_content,
                "json_repair_elapsed_seconds": repair_elapsed,
                "usage": raw.get("usage"),
            }
            atomic_json(cache_path, result)
            return result
        except (OSError, KeyError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(3.0)
    raise RuntimeError(f"Qwen request failed after 3 attempts: {last_error}")


EXTRACTION_PROMPT = """你是公务员考试资料分析图表的结构化数据抽取器。只输出一个 JSON 对象，不要 Markdown，不要解释。
图型必须从 grouped_bar、stacked_bar、line、combo、pie_donut、scatter、table、other_unsupported 中选择。
只提取语义数据：标题、单位、坐标轴、分类、图例、系列、数值、明确的缺失值、有限样式；严禁输出任何像素坐标。
缺失值必须是 null，绝不能补成 0。只有图中明确标为 0 才能输出数字 0。保留负号、小数与百分号的数值语义。
饼图只输出 labels_clockwise（从12点方向所在扇区开始，顺时针）和文字标签，严禁输出圆心、角度、边界、面积或 share；饼图几何由 OpenCV 负责。
表格必须恢复完整的行列拓扑，不能把 OCR 文本串直接当表格。多级表头不能拍平成重复数据行：
- columns 使用唯一的叶子列键；多级表头的叶子列键写成“父表头｜子表头”；
- column_groups 记录父表头及其包含的叶子列键；
- 左上角若是斜线表头，corner_header 必须记录 upper_label、lower_label；
- row_header_label 记录行标题；图中问号、破折号等未知值必须写 null，并在 missing_cells 中保留原显示文字。
若图中数据不足以可靠恢复，仍返回候选并在 uncertainties 中逐项说明，禁止编造。
返回键：chart_type,title,unit,axes:{x:{title,unit,min,max,ticks},y:[同结构，最多2条]},categories,
series:[{label,render_as(bar|line|point|slice|cell),axis(left|right|none),unit,values}],
table:null或{row_header_label,corner_header:null或{style:"diagonal",upper_label,lower_label},
columns,column_groups:[{label,columns}],rows:[{label,unit,values}],missing_cells:[{row,column,display}]},
legend:[{label,style:{fill,stroke,dash,marker}}],
labels_clockwise,confidence(0到1),uncertainties,explicit_missing_values。
"""


def create_verification_crops(asset: dict[str, Any], parsed: dict[str, Any]) -> list[Path]:
    if asset["kind"] != "image":
        return []
    chart_type = parsed.get("chart_type")
    if chart_type == "other_unsupported":
        return []
    source = Path(asset["local_path"])
    output = CROPS / asset["asset_id"]
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = image.convert("RGB")
        width, height = image.size
        paths: list[Path] = []
        if chart_type == "table":
            count = min(4, max(3, len((parsed.get("table") or {}).get("rows") or []) // 4 + 1))
            for index in range(count):
                band = height / count
                top = max(0, int(index * band - band * 0.20))
                bottom = min(height, int((index + 1) * band + band * 0.20))
                crop = image.crop((0, top, width, bottom))
                path = output / f"row_band_{index + 1:02d}.jpg"
                crop.save(path, quality=94)
                paths.append(path)
        elif chart_type == "pie_donut":
            for index, (left, top, right, bottom) in enumerate((
                (0, 0, .62, .62), (.38, 0, 1, .62), (.38, .38, 1, 1), (0, .38, .62, 1)
            ), 1):
                crop = image.crop((int(left * width), int(top * height), int(right * width), int(bottom * height)))
                path = output / f"quadrant_{index:02d}.jpg"
                crop.save(path, quality=94)
                paths.append(path)
        else:
            category_count = len(parsed.get("categories") or [])
            count = min(4, max(3, category_count if 0 < category_count <= 4 else 4))
            for index in range(count):
                band = width / count
                left = max(0, int(index * band - band * 0.28))
                right = min(width, int((index + 1) * band + band * 0.28))
                crop = image.crop((left, 0, right, height))
                path = output / f"column_band_{index + 1:02d}.jpg"
                crop.save(path, quality=94)
                paths.append(path)
        return paths


def verification_prompt(initial: dict[str, Any], chart_type: str, crop_count: int) -> str:
    orientation = "从左到右的重叠竖向局部裁图" if chart_type not in {"table", "pie_donut"} else (
        "从上到下的重叠横向局部裁图" if chart_type == "table" else "按顺时针覆盖的四个局部裁图"
    )
    if chart_type == "table":
        table = initial.get("table") if isinstance(initial.get("table"), dict) else {}
        candidate = {
            "columns": table.get("columns") or [],
            "rows": table.get("rows") or [],
        }
        contract = """只返回：
{"cell_corrections":[{"row":"候选行名","column":"候选列名","value":数值或null}],"notes":[]}
只列出首轮候选确实错误的单元格；正确值不要重复输出。行名和列名必须来自候选。"""
    elif chart_type == "pie_donut":
        candidate = {"labels_clockwise": initial.get("labels_clockwise") or []}
        contract = """只返回：
{"label_corrections":[{"index":从0开始的整数,"value":"正确文字"}],"notes":[]}
只纠正错误文字；严禁输出圆心、边界、角度、share 或像素坐标。"""
    else:
        candidate = {
            "categories": initial.get("categories") or [],
            "series": [
                {"label": item.get("label"), "values": item.get("values") or []}
                for item in (initial.get("series") or [])
                if isinstance(item, dict)
            ],
        }
        contract = """只返回：
{"value_corrections":[{"category":"候选分类","series":"候选系列名","value":数值或null}],"notes":[]}
只列出首轮候选确实错误的数据点；正确值不要重复输出。分类和系列名必须来自候选。"""
    return f"""只输出合法 JSON 对象。以下 {crop_count} 张图是同一原图的{orientation}。
首轮候选：{json.dumps(candidate, ensure_ascii=False, separators=(',', ':'))}
逐项复核相邻数字、负号、小数、百分号和缺失值；缺失必须是 null，不能补 0。
{contract}
禁止返回完整候选，禁止解释，禁止新增首轮图中不存在的行、列、分类或系列。"""


def apply_verification_delta(initial: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Apply a small, allowlisted verification delta to a complete first-pass candidate."""
    merged = json.loads(json.dumps(initial, ensure_ascii=False))
    notes = [str(item) for item in (delta.get("notes") or [])]
    chart_type = merged.get("chart_type")
    if chart_type == "table":
        table = merged.get("table") if isinstance(merged.get("table"), dict) else {}
        columns = [str(value) for value in (table.get("columns") or [])]
        rows = [row for row in (table.get("rows") or []) if isinstance(row, dict)]
        rows_by_label = {str(row.get("label") or ""): row for row in rows}
        for correction in delta.get("cell_corrections") or []:
            if not isinstance(correction, dict):
                continue
            row_label = str(correction.get("row") or "")
            column_label = str(correction.get("column") or "")
            row = rows_by_label.get(row_label)
            if row is None or column_label not in columns:
                notes.append(f"ignored_unknown_table_cell:{row_label}:{column_label}")
                continue
            values = row.get("values") if isinstance(row.get("values"), list) else []
            index = columns.index(column_label)
            if index >= len(values):
                notes.append(f"ignored_out_of_range_table_cell:{row_label}:{column_label}")
                continue
            values[index] = correction.get("value")
            row["values"] = values
    elif chart_type == "pie_donut":
        labels = merged.get("labels_clockwise") if isinstance(merged.get("labels_clockwise"), list) else []
        for correction in delta.get("label_corrections") or []:
            if not isinstance(correction, dict) or not isinstance(correction.get("index"), int):
                continue
            index = correction["index"]
            value = correction.get("value")
            if not (0 <= index < len(labels)) or not isinstance(value, str) or not value.strip():
                notes.append(f"ignored_invalid_pie_label_correction:{index}")
                continue
            labels[index] = value.strip()
        merged["labels_clockwise"] = labels
    else:
        categories = [str(value) for value in (merged.get("categories") or [])]
        series = [item for item in (merged.get("series") or []) if isinstance(item, dict)]
        series_by_label = {str(item.get("label") or ""): item for item in series}
        for correction in delta.get("value_corrections") or []:
            if not isinstance(correction, dict):
                continue
            category = str(correction.get("category") or "")
            label = str(correction.get("series") or "")
            target = series_by_label.get(label)
            if category not in categories or target is None:
                notes.append(f"ignored_unknown_chart_point:{category}:{label}")
                continue
            values = target.get("values") if isinstance(target.get("values"), list) else []
            index = categories.index(category)
            if index >= len(values):
                notes.append(f"ignored_out_of_range_chart_point:{category}:{label}")
                continue
            values[index] = correction.get("value")
            target["values"] = values
    merged["verification_notes"] = ["compact_delta_verification", *notes]
    return merged


def verify_chart_per_crop(asset_id: str, initial: dict[str, Any], crops: list[Path]) -> dict[str, Any]:
    merged = json.loads(json.dumps(initial, ensure_ascii=False))
    categories = [str(value) for value in (merged.get("categories") or [])]
    series = [item for item in (merged.get("series") or []) if isinstance(item, dict)]
    series_by_label = {str(item.get("label") or ""): item for item in series}
    elapsed = 0.0
    notes: list[str] = ["combined_crop_response_malformed;used_individual_crop_verification"]
    for index, crop in enumerate(crops, 1):
        prompt = f"""只输出合法 JSON。此图是原图从左到右第 {index}/{len(crops)} 个重叠局部裁图。
整图候选 categories={json.dumps(categories, ensure_ascii=False)}；series labels={json.dumps(list(series_by_label), ensure_ascii=False)}。
只抄录本裁图中清晰可见的数据标签，复核相邻数字、负号、小数、百分号和缺失值。
返回 {{"observed":[{{"category":"必须来自候选categories","values":{{"系列名":数值或null}}}}],"notes":[]}}。
看不清就不要返回该点；缺失必须是 null，不能补 0；禁止像素坐标。"""
        response = qwen_request(
            prompt=prompt,
            image_paths=[crop],
            cache_path=CACHE / f"{asset_id}.verify.part{index:02d}.json",
            max_tokens=1400,
            allow_model_json_repair=False,
        )
        elapsed += float(response.get("elapsed_seconds") or 0)
        parsed = response["parsed"]
        notes.extend(str(item) for item in (parsed.get("notes") or []))
        for observation in parsed.get("observed") or []:
            if not isinstance(observation, dict):
                continue
            category = str(observation.get("category") or "")
            if category not in categories:
                notes.append(f"ignored_unknown_crop_category:{category}")
                continue
            category_index = categories.index(category)
            values = observation.get("values") if isinstance(observation.get("values"), dict) else {}
            for label, raw_value in values.items():
                target = series_by_label.get(str(label))
                if target is None:
                    notes.append(f"ignored_unknown_crop_series:{label}")
                    continue
                target_values = target.get("values") or []
                if category_index >= len(target_values):
                    notes.append(f"ignored_out_of_range_crop_value:{category}:{label}")
                    continue
                target_values[category_index] = raw_value
                target["values"] = target_values
    merged["verification_notes"] = notes
    return {
        "model": MODEL,
        "elapsed_seconds": round(elapsed, 3),
        "content": "individual_crop_verification",
        "parsed": merged,
        "usage": None,
    }


def verify_table_per_crop(asset_id: str, initial: dict[str, Any], crops: list[Path]) -> dict[str, Any]:
    merged = json.loads(json.dumps(initial, ensure_ascii=False))
    table = merged.get("table") if isinstance(merged.get("table"), dict) else {"columns": [], "rows": []}
    columns = table.get("columns") or []
    rows = [row for row in (table.get("rows") or []) if isinstance(row, dict)]
    rows_by_label = {str(row.get("label") or ""): row for row in rows}
    notes: list[str] = ["combined_crop_response_malformed;used_individual_table_crop_verification"]
    elapsed = 0.0
    for index, crop in enumerate(crops, 1):
        prompt = f"""只输出合法 JSON。此图是表格从上到下第 {index}/{len(crops)} 个重叠局部裁图。
候选 columns={json.dumps(columns, ensure_ascii=False)}；候选 row labels={json.dumps(list(rows_by_label), ensure_ascii=False)}。
恢复本裁图清晰可见的行列拓扑，逐格复核负号、小数、百分号和缺失值。
返回 {{"rows":[{{"label":"必须来自候选row labels","values":[与columns对应的数值或null]}}],"notes":[]}}。
看不清的行不要返回；缺失必须为 null，禁止补 0，禁止像素坐标。"""
        response = qwen_request(
            prompt=prompt,
            image_paths=[crop],
            cache_path=CACHE / f"{asset_id}.verify.table.part{index:02d}.json",
            max_tokens=1800,
            allow_model_json_repair=False,
        )
        elapsed += float(response.get("elapsed_seconds") or 0)
        parsed = response["parsed"]
        notes.extend(str(item) for item in (parsed.get("notes") or []))
        for row in parsed.get("rows") or []:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "")
            if label not in rows_by_label:
                notes.append(f"ignored_unknown_table_row:{label}")
                continue
            values = row.get("values") if isinstance(row.get("values"), list) else []
            if len(values) != len(columns):
                notes.append(f"ignored_table_row_length_mismatch:{label}:{len(values)}!={len(columns)}")
                continue
            rows_by_label[label]["values"] = values
    merged["table"] = table
    merged["verification_notes"] = notes
    return {"model": MODEL, "elapsed_seconds": round(elapsed, 3), "content": "individual_table_crop_verification", "parsed": merged, "usage": None}


def verify_pie_per_crop(asset_id: str, initial: dict[str, Any], crops: list[Path]) -> dict[str, Any]:
    merged = json.loads(json.dumps(initial, ensure_ascii=False))
    labels = [str(value) for value in (merged.get("labels_clockwise") or [])]
    seen: set[str] = set()
    notes: list[str] = ["combined_crop_response_malformed;used_individual_pie_label_crop_verification"]
    elapsed = 0.0
    for index, crop in enumerate(crops, 1):
        prompt = f"""只输出合法 JSON。此图是饼图第 {index}/{len(crops)} 个局部裁图。
候选 labels_clockwise={json.dumps(labels, ensure_ascii=False)}。
只核对本裁图清晰可见的文字标签或图例，返回 {{"labels_seen":["必须来自候选labels_clockwise"],"notes":[]}}。
严禁输出圆心、边界、角度、share 或像素坐标；看不清就不返回。"""
        response = qwen_request(
            prompt=prompt,
            image_paths=[crop],
            cache_path=CACHE / f"{asset_id}.verify.pie.part{index:02d}.json",
            max_tokens=900,
            allow_model_json_repair=False,
        )
        elapsed += float(response.get("elapsed_seconds") or 0)
        parsed = response["parsed"]
        notes.extend(str(item) for item in (parsed.get("notes") or []))
        seen.update(str(value) for value in (parsed.get("labels_seen") or []) if str(value) in labels)
    missing = [label for label in labels if label not in seen]
    if missing:
        notes.append("pie_crop_labels_not_all_seen:" + "|".join(missing))
    merged["verification_notes"] = notes
    return {"model": MODEL, "elapsed_seconds": round(elapsed, 3), "content": "individual_pie_crop_verification", "parsed": merged, "usage": None}


def verify_individual_crops(asset_id: str, initial: dict[str, Any], crops: list[Path]) -> dict[str, Any]:
    chart_type = initial.get("chart_type")
    if chart_type == "table":
        return verify_table_per_crop(asset_id, initial, crops)
    if chart_type == "pie_donut":
        return verify_pie_per_crop(asset_id, initial, crops)
    return verify_chart_per_crop(asset_id, initial, crops)


def parse_number(value: Any) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    text = str(value).strip()
    if text.lower() in MISSING_TOKENS:
        return None
    compact = text.replace(",", "").replace("，", "").replace("％", "%")
    if compact.endswith("%"):
        compact = compact[:-1].strip()
    if re.fullmatch(r"[−-]?\d+(?:\.\d+)?", compact):
        compact = compact.replace("−", "-")
        number = float(compact) if "." in compact else int(compact)
        return number
    return text


def normalize_axis(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    ticks = [parse_number(item) for item in (value.get("ticks") or [])]
    ticks = [item for item in ticks if isinstance(item, (int, float))]
    return {
        "title": str(value["title"]).strip() if value.get("title") not in (None, "") else None,
        "unit": str(value["unit"]).strip() if value.get("unit") not in (None, "") else None,
        "min": parse_number(value.get("min")) if isinstance(parse_number(value.get("min")), (int, float)) else None,
        "max": parse_number(value.get("max")) if isinstance(parse_number(value.get("max")), (int, float)) else None,
        "ticks": ticks,
    }


def normalize_style(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {key: (str(value[key]).strip() if value.get(key) not in (None, "") else None) for key in ("fill", "stroke", "dash", "marker")}


def value_signature(parsed: dict[str, Any]) -> list[Any]:
    result: list[Any] = []
    for series in parsed.get("series") or []:
        if isinstance(series, dict):
            result.extend(parse_number(item) for item in (series.get("values") or []))
    table = parsed.get("table") or {}
    if isinstance(table, dict):
        for row in table.get("rows") or []:
            if isinstance(row, dict):
                result.extend(parse_number(item) for item in (row.get("values") or []))
    return result


def compare_signatures(initial: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any]:
    before = value_signature(initial)
    after = value_signature(verified)
    compared = min(len(before), len(after))
    changed = sum(before[index] != after[index] for index in range(compared)) + abs(len(before) - len(after))
    missing_to_zero = sum(before[index] is None and after[index] == 0 for index in range(compared))
    return {
        "initial_values": len(before),
        "verified_values": len(after),
        "changed": changed,
        "change_ratio": round(changed / max(1, max(len(before), len(after))), 6),
        "missing_to_zero": missing_to_zero,
    }


def cluster_angles(angles: list[float], tolerance: float = 5.5) -> list[float]:
    if not angles:
        return []
    values = sorted(angle % 360 for angle in angles)
    groups: list[list[float]] = [[values[0]]]
    for angle in values[1:]:
        if angle - groups[-1][-1] <= tolerance:
            groups[-1].append(angle)
        else:
            groups.append([angle])
    if len(groups) > 1 and groups[0][0] + 360 - groups[-1][-1] <= tolerance:
        wrapped = [value + 360 for value in groups[0]] + groups[-1]
        groups = groups[1:-1] + [wrapped]
    centers = [sum(group) / len(group) % 360 for group in groups]
    return sorted(0.0 if center >= 357 or center <= 3 else center for center in centers)


def circle_edge_coverage(edges: np.ndarray, x: float, y: float, radius: float) -> float:
    hits = 0
    total = 0
    for angle in np.linspace(0, 2 * np.pi, 720, endpoint=False):
        column = int(round(x + radius * math.cos(angle)))
        row = int(round(y + radius * math.sin(angle)))
        if 1 <= row < edges.shape[0] - 1 and 1 <= column < edges.shape[1] - 1:
            total += 1
            if edges[row - 1:row + 2, column - 1:column + 2].max() > 0:
                hits += 1
    return hits / max(1, total)


def radial_profile_boundaries(gray: np.ndarray, x: float, y: float, radius: float, count: int) -> list[float]:
    if count < 2:
        return []
    samples_per_circle = 720
    profile = []
    for index in range(samples_per_circle):
        angle = math.radians(index * 360 / samples_per_circle)
        values = []
        for radial in np.linspace(radius * .18, radius * .82, 90):
            column = int(round(x + radial * math.sin(angle)))
            row = int(round(y - radial * math.cos(angle)))
            if 0 <= row < gray.shape[0] and 0 <= column < gray.shape[1]:
                values.append(float(gray[row, column]))
        profile.append(sum(values) / max(1, len(values)))
    profile_array = np.asarray(profile)
    window = 7
    extended = np.r_[profile_array[-window:], profile_array, profile_array[:window]]
    smooth = np.convolve(extended, np.ones(2 * window + 1) / (2 * window + 1), mode="valid")
    derivative = np.abs(np.roll(smooth, -4) - np.roll(smooth, 4))
    candidates = np.argsort(derivative)[::-1]
    selected: list[float] = []
    for candidate in candidates:
        angle = float(candidate) * 360.0 / samples_per_circle
        if all(min((angle - existing) % 360, (existing - angle) % 360) >= 8.0 for existing in selected):
            selected.append(angle)
        if len(selected) >= count:
            break
    return sorted(selected)


def pie_geometry(path: Path, labels: list[str]) -> tuple[dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    image = cv2.imread(str(path))
    if image is None:
        return None, ["opencv_decode_failed"]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.medianBlur(gray, 5)
    minimum = min(gray.shape)
    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1.2, minDist=max(20, minimum / 3),
        param1=100, param2=35, minRadius=max(10, int(minimum * .10)), maxRadius=int(minimum * .49),
    )
    if circles is None:
        return None, ["hough_circle_not_found"]
    candidates = circles[0]
    height, width = gray.shape
    edges = cv2.Canny(blurred, 50, 150)
    scored_circles = [
        (circle_edge_coverage(edges, float(item[0]), float(item[1]), float(item[2])), item)
        for item in candidates
    ]
    strong_circles = [item for coverage, item in scored_circles if coverage >= 0.65]
    if len(strong_circles) >= 2 and width / max(1, height) >= 2.5:
        return None, [f"multiple_pie_options_detected:{len(strong_circles)}"]
    coverage, chosen = max(scored_circles, key=lambda value: value[0])
    center_x, center_y, radius = map(float, chosen)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=max(22, int(radius * .32)),
        minLineLength=max(12, int(radius * .42)), maxLineGap=max(4, int(radius * .08)),
    )
    raw_angles: list[float] = []
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            d1 = math.hypot(x1 - center_x, y1 - center_y)
            d2 = math.hypot(x2 - center_x, y2 - center_y)
            near, far = ((x1, y1, d1), (x2, y2, d2)) if d1 < d2 else ((x2, y2, d2), (x1, y1, d1))
            if near[2] <= radius * .30 and far[2] >= radius * .55:
                angle = math.degrees(math.atan2(far[0] - center_x, -(far[1] - center_y))) % 360
                raw_angles.append(angle)
    angles = cluster_angles(raw_angles)
    if labels:
        profile_angles = radial_profile_boundaries(gray, center_x, center_y, radius, len(labels))
        # Strong Hough lines through the center anchor clean boundaries; radial-profile
        # transitions recover boundaries hidden by hatch fills.
        anchored = []
        if profile_angles:
            for angle in angles:
                close = min(
                    (abs((angle - candidate + 180) % 360 - 180), candidate)
                    for candidate in profile_angles
                )
                if close[0] <= 8:
                    anchored.append(angle)
        else:
            notes.append("radial_profile_boundaries_empty")
        combined = anchored[:]
        for angle in profile_angles:
            if all(min((angle - existing) % 360, (existing - angle) % 360) >= 8 for existing in combined):
                combined.append(angle)
        if len(combined) >= len(labels):
            angles = sorted(combined[:len(labels)])
    if len(angles) < 2 or len(angles) > 20:
        return None, [*notes, f"radial_boundaries_invalid:{len(angles)}"]
    if 0.0 in angles:
        boundaries = angles + [360.0]
    else:
        boundaries = [angles[-1] - 360.0] + angles
    sweeps = [boundaries[index + 1] - boundaries[index] for index in range(len(boundaries) - 1)]
    if any(sweep <= 2 or sweep >= 358 for sweep in sweeps):
        return None, ["degenerate_slice_detected"]
    if len(labels) != len(sweeps):
        notes.append(f"label_geometry_count_mismatch:{len(labels)}!={len(sweeps)}")
    aligned = [labels[index] if index < len(labels) else None for index in range(len(sweeps))]
    slices = []
    for index, sweep in enumerate(sweeps):
        slices.append({
            "label": aligned[index],
            "start_angle": round(boundaries[index], 3),
            "end_angle": round(boundaries[index + 1], 3),
            "sweep_angle": round(sweep, 3),
            "share": round(sweep / 360.0, 8),
        })
    geometry = {
        "rotation_convention": "top_clockwise",
        "boundaries_degrees": [round(value, 3) for value in boundaries],
        "slices": slices,
        "geometry_owner": "opencv_hough_radial_scan",
    }
    notes.extend([
        f"circle_edge_coverage:{coverage:.4f}",
        f"hough_circle_radius_ratio:{radius / minimum:.4f}",
        f"radial_line_candidates:{len(raw_angles)}",
        f"detected_boundaries:{len(angles)}",
    ])
    return geometry, notes


def candidate_title_is_empty(parsed: dict[str, Any]) -> bool:
    return parsed.get("title") in (None, "")


def normalize_corner_header(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    style = str(raw.get("style") or "").strip()
    upper = str(raw.get("upper_label") or "").strip() or None
    lower = str(raw.get("lower_label") or "").strip() or None
    if style != "diagonal" or not upper or not lower:
        return None
    return {"style": "diagonal", "upper_label": upper, "lower_label": lower}


def normalize_column_groups(raw: Any, columns: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    known = set(columns)
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip() or None
        leaves = [str(value).strip() for value in (item.get("columns") or []) if str(value).strip()]
        if label is None or not leaves or any(value not in known for value in leaves):
            continue
        groups.append({"label": label, "columns": leaves})
    flattened = [column for group in groups for column in group["columns"]]
    return groups if flattened == columns else []


def canonicalize_grouped_table_columns(raw_table: dict[str, Any]) -> dict[str, Any]:
    """Give grouped leaf columns stable unique keys while retaining display labels in the groups."""
    table = json.loads(json.dumps(raw_table, ensure_ascii=False))
    groups = [item for item in (table.get("column_groups") or []) if isinstance(item, dict)]
    raw_columns = [str(value).strip() for value in (table.get("columns") or [])]
    grouped_leaves = [
        (str(group.get("label") or "").strip(), str(leaf).strip())
        for group in groups
        for leaf in (group.get("columns") or [])
    ]
    if not groups or len(grouped_leaves) != len(raw_columns):
        return table
    canonical: list[str] = []
    rebuilt_groups: list[dict[str, Any]] = []
    cursor = 0
    for group in groups:
        group_label = str(group.get("label") or "").strip()
        group_columns: list[str] = []
        for leaf in group.get("columns") or []:
            # Models use both the full-width and ASCII pipe for parent/leaf keys.
            # Strip either form before rebuilding the canonical key, otherwise a
            # second repair pass can produce labels such as “2019年｜2019|增长”.
            leaf_label = str(leaf).strip().replace("|", "｜")
            prefix = f"{group_label}｜"
            if group_label and leaf_label.startswith(prefix):
                leaf_label = leaf_label[len(prefix):]
            key = f"{group_label}｜{leaf_label}" if group_label else leaf_label
            group_columns.append(key)
            canonical.append(key)
            cursor += 1
        rebuilt_groups.append({"label": group_label, "columns": group_columns})
    if len(canonical) == len(set(canonical)):
        table["columns"] = canonical
        table["column_groups"] = rebuilt_groups
        remapped_missing: list[dict[str, Any]] = []
        for missing in table.get("missing_cells") or []:
            old_column = str(missing.get("column") or "").strip().replace("|", "｜")
            matches = [
                new_column for old_value, new_column in zip(raw_columns, canonical)
                if str(old_value).strip().replace("|", "｜") == old_column
            ]
            if len(matches) == 1:
                remapped = dict(missing)
                remapped["column"] = matches[0]
                remapped_missing.append(remapped)
            elif old_column in canonical:
                remapped_missing.append(dict(missing))
        table["missing_cells"] = remapped_missing
    return table


def qualify_repeated_context_columns(raw_table: dict[str, Any]) -> dict[str, Any]:
    """Disambiguate repeated derived metrics by the immediately preceding base metric."""
    table = json.loads(json.dumps(raw_table, ensure_ascii=False))
    columns = [str(value).strip().replace("|", "｜") for value in (table.get("columns") or [])]
    duplicates = {column for column in columns if columns.count(column) > 1}
    derived_pattern = re.compile(r"增长|增速|增减|占比|比重|比例|药费")
    replacements = list(columns)
    changed = False
    for duplicate in duplicates:
        leaf = duplicate.split("｜")[-1]
        if not derived_pattern.search(leaf):
            continue
        for index, column in enumerate(columns):
            if column != duplicate or index == 0:
                continue
            previous = columns[index - 1]
            prefix = duplicate.split("｜")[:-1]
            previous_leaf = previous.split("｜")[-1]
            replacements[index] = "｜".join([*prefix, previous_leaf, leaf])
            changed = True
    if not changed or len(replacements) != len(set(replacements)):
        return table

    rows = [row for row in (table.get("rows") or []) if isinstance(row, dict)]
    remapped_missing: list[dict[str, Any]] = []
    used: set[tuple[str, str, int]] = set()
    for missing in table.get("missing_cells") or []:
        row_label = str(missing.get("row") or "")
        old_column = str(missing.get("column") or "").strip().replace("|", "｜")
        row = next((item for item in rows if str(item.get("label") or "") == row_label), None)
        candidate_indices = [index for index, value in enumerate(columns) if value == old_column]
        if row is not None:
            values = row.get("values") or []
            null_indices = [
                index for index in candidate_indices
                if index < len(values) and values[index] is None and (row_label, old_column, index) not in used
            ]
            if null_indices:
                candidate_indices = null_indices
        if candidate_indices:
            index = candidate_indices[0]
            used.add((row_label, old_column, index))
            remapped = dict(missing)
            remapped["column"] = replacements[index]
            remapped_missing.append(remapped)
    table["columns"] = replacements
    table["column_groups"] = []
    table["missing_cells"] = remapped_missing
    return table


def canonicalize_repeated_column_groups_as_rows(raw_table: dict[str, Any]) -> dict[str, Any]:
    """Recover merged row groups that OCR incorrectly described as repeated column groups."""
    table = json.loads(json.dumps(raw_table, ensure_ascii=False))
    columns = [str(value).strip().replace("|", "｜") for value in (table.get("columns") or [])]
    groups = [group for group in (table.get("column_groups") or []) if isinstance(group, dict)]
    rows = [row for row in (table.get("rows") or []) if isinstance(row, dict)]
    if not columns or not groups or not rows:
        return table

    # Parallel rankings are often drawn as two three-column tables side by side.
    # OCR sometimes merges “排名/城市” into one leaf and therefore emits four
    # headers for six physical values.
    if (
        len(groups) == 2
        and all(len(group.get("columns") or []) == 2 for group in groups)
        and all(len(row.get("values") or []) == 6 for row in rows)
    ):
        rebuilt_columns: list[str] = []
        rebuilt_groups: list[dict[str, Any]] = []
        for group in groups:
            label = str(group.get("label") or "").strip()
            leaves = [str(value).strip().replace("|", "｜") for value in group.get("columns") or []]
            first_parts = [part for part in leaves[0].split("｜") if part]
            if len(first_parts) != 2:
                return table
            group_columns = [
                f"{label}｜{first_parts[0]}",
                f"{label}｜{first_parts[1]}",
                f"{label}｜{leaves[1].split('｜')[-1]}",
            ]
            rebuilt_columns.extend(group_columns)
            rebuilt_groups.append({"label": label, "columns": group_columns})
        table["columns"] = rebuilt_columns
        table["column_groups"] = rebuilt_groups
        return table

    if not all(
        [str(value).strip().replace("|", "｜") for value in (group.get("columns") or [])] == columns
        for group in groups
    ):
        return table
    group_labels = [str(group.get("label") or "").strip() for group in groups]
    if any(not label for label in group_labels):
        return table

    if len(groups) == 2 and len(rows) % 2 == 0 and all(
        str(rows[index].get("label") or "") == str(rows[index + 1].get("label") or "")
        for index in range(0, len(rows), 2)
    ):
        rebuilt_rows = []
        for index, row in enumerate(rows):
            parent = str(row.get("label") or "")
            subtype = group_labels[index % 2]
            rebuilt_rows.append({
                **row,
                "label": f"{parent}｜{subtype}",
                "display_label": subtype,
                "group": parent,
            })
        table["rows"] = rebuilt_rows
        table["row_header_label"] = "项目｜类型"
        table["column_groups"] = []
        return table

    # A merged metric cell followed by country/category rows. The duplicated
    # metric row is an OCR artefact and is removed rather than treated as data.
    if len(rows) % len(groups) == 0:
        block_size = len(rows) // len(groups)
        marker_mode = block_size >= 2 and all(
            (rows[index * block_size].get("values") or [None])[0] is None
            and all(
                isinstance((rows[index * block_size + offset].get("values") or [None])[0], str)
                for offset in range(1, block_size)
            )
            for index in range(len(groups))
        )
        if marker_mode and len(columns) >= 2:
            rebuilt_rows: list[dict[str, Any]] = []
            for group_index, group_label in enumerate(group_labels):
                for row in rows[group_index * block_size + 1:(group_index + 1) * block_size]:
                    values = list(row.get("values") or [])
                    identifier = str(values[0]).strip()
                    rebuilt_rows.append({
                        **row,
                        "label": f"{group_label}｜{identifier}",
                        "display_label": identifier,
                        "group": group_label,
                        "values": values[1:],
                    })
            table["columns"] = columns[1:]
            table["row_header_label"] = "指标｜分类"
            table["rows"] = rebuilt_rows
            table["column_groups"] = []
            table["missing_cells"] = []
            return table

        block_labels = [str(row.get("label") or "") for row in rows[:block_size]]
        if block_labels and all(
            [str(row.get("label") or "") for row in rows[index * block_size:(index + 1) * block_size]] == block_labels
            for index in range(1, len(groups))
        ):
            remove_unit_column = columns[0] in {"计算单位", "单位", "计量单位"} and all(
                (row.get("values") or [None])[0] is None for row in rows
            )
            rebuilt_rows = []
            for index, row in enumerate(rows):
                group_label = group_labels[index // block_size]
                values = list(row.get("values") or [])
                rebuilt_rows.append({
                    **row,
                    "label": f"{group_label}｜{row.get('label')}",
                    "display_label": row.get("display_label") or row.get("label"),
                    "group": group_label,
                    "values": values[1:] if remove_unit_column else values,
                })
            table["columns"] = columns[1:] if remove_unit_column else columns
            table["row_header_label"] = "类别｜指标"
            table["rows"] = rebuilt_rows
            table["column_groups"] = []
            table["missing_cells"] = []
            return table

    return table


def apply_explicit_table_missing_values(
    table: dict[str, Any],
    explicit_missing_values: Any,
    normalization_notes: list[str],
) -> None:
    columns = table.get("columns") or []
    rows = {str(row.get("label") or ""): row for row in (table.get("rows") or [])}
    missing_cells = list(table.get("missing_cells") or [])
    for item in explicit_missing_values or []:
        if not isinstance(item, dict):
            continue
        row_label = str(item.get("row") or "").strip()
        column_label = str(item.get("column") or item.get("col") or "").strip()
        row = rows.get(row_label)
        if row is None or column_label not in columns:
            normalization_notes.append(f"ignored_unknown_explicit_missing_cell:{row_label}:{column_label}")
            continue
        column_index = columns.index(column_label)
        values = row.get("values") or []
        if column_index >= len(values):
            normalization_notes.append(f"ignored_out_of_range_explicit_missing_cell:{row_label}:{column_label}")
            continue
        if values[column_index] is not None:
            normalization_notes.append(f"explicit_missing_value_applied:{row_label}:{column_label}")
        values[column_index] = None
        marker = str(item.get("display") or "（？）").strip() or "（？）"
        if not any(cell.get("row") == row_label and cell.get("column") == column_label for cell in missing_cells):
            missing_cells.append({"row": row_label, "column": column_label, "display": marker})
    table["missing_cells"] = missing_cells


def normalize_candidate(
    asset: dict[str, Any],
    parsed: dict[str, Any],
    qwen_meta: dict[str, Any],
    crop_meta: dict[str, Any] | None,
    crop_paths: list[Path],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    chart_type = parsed.get("chart_type") if parsed.get("chart_type") in CHART_TYPES else "other_unsupported"
    categories = [str(value).strip() for value in (parsed.get("categories") or []) if str(value).strip()]
    series = []
    for index, raw in enumerate(parsed.get("series") or []):
        if not isinstance(raw, dict):
            continue
        render_as = raw.get("render_as")
        if render_as not in {"bar", "line", "point", "slice", "cell"}:
            render_as = {"line": "line", "scatter": "point", "pie_donut": "slice"}.get(chart_type, "bar")
        axis = raw.get("axis") if raw.get("axis") in {"left", "right", "none"} else "left"
        label = str(raw["label"]).strip() if raw.get("label") not in (None, "") else None
        series.append({
            "id": re.sub(r"[^0-9A-Za-z_-]+", "_", label or f"series_{index + 1}").strip("_") or f"series_{index + 1}",
            "label": label,
            "render_as": render_as,
            "axis": axis,
            "unit": str(raw["unit"]).strip() if raw.get("unit") not in (None, "") else None,
            "values": [parse_number(value) for value in (raw.get("values") or [])],
            "style": normalize_style(raw.get("style")),
        })
    normalization_notes: list[str] = []
    table_correction = load_table_correction(asset["asset_id"])
    raw_table = table_correction.get("table") if table_correction else parsed.get("table")
    if table_correction:
        normalization_notes.append("curated_table_topology_correction_applied")
    table = None
    if isinstance(raw_table, dict):
        raw_table = canonicalize_repeated_column_groups_as_rows(raw_table)
        raw_table = canonicalize_grouped_table_columns(raw_table)
        raw_table = qualify_repeated_context_columns(raw_table)
        series_units = {
            str(item.get("label") or "").strip(): str(item.get("unit") or "").strip() or None
            for item in (parsed.get("series") or []) if isinstance(item, dict)
        }
        table = {
            "row_header_label": str(raw_table.get("row_header_label") or "").strip() or None,
            "corner_header": normalize_corner_header(raw_table.get("corner_header")),
            "columns": [str(value).strip() for value in (raw_table.get("columns") or [])],
            "column_groups": [],
            "rows": [
                {
                    "label": str(row["label"]).strip() if row.get("label") not in (None, "") else None,
                    "display_label": (
                        str(row.get("display_label") or "").strip()
                        or (str(row["label"]).strip().split("｜")[-1] if row.get("label") not in (None, "") else None)
                    ),
                    "group": str(row.get("group") or "").strip() or None,
                    "unit": (
                        str(row.get("unit") or "").strip()
                        or series_units.get(str(row.get("label") or "").strip())
                        or None
                    ),
                    "values": [parse_number(value) for value in (row.get("values") or [])],
                }
                for row in (raw_table.get("rows") or []) if isinstance(row, dict)
            ],
            "missing_cells": [
                {
                    "row": str(cell.get("row") or "").strip(),
                    "column": str(cell.get("column") or cell.get("col") or "").strip(),
                    "display": str(cell.get("display") or "（？）").strip() or "（？）",
                }
                for cell in (raw_table.get("missing_cells") or [])
                if isinstance(cell, dict) and str(cell.get("row") or "").strip()
                and str(cell.get("column") or cell.get("col") or "").strip()
            ],
        }
        if table_correction:
            # categories belongs to ordinary chart axes.  Old table extraction
            # occasionally populated it with OCR fragments; after a complete
            # topology correction those fragments must not invalidate the table.
            categories = []
        value_lengths = {len(row["values"]) for row in table["rows"]}
        if len(value_lengths) == 1 and table["columns"] and value_lengths == {len(table["columns"]) - 1}:
            removed = table["columns"].pop(0)
            normalization_notes.append(f"table_row_label_column_removed:{removed or '<blank>'}")
            table["row_header_label"] = table["row_header_label"] or removed or None
        table["column_groups"] = normalize_column_groups(raw_table.get("column_groups"), table["columns"])
        table["title"] = str(raw_table.get("title") or "").strip() or None
        table["sections"] = []
        for raw_section in raw_table.get("sections") or []:
            if not isinstance(raw_section, dict):
                continue
            raw_section = canonicalize_grouped_table_columns(raw_section)
            section_columns = [str(value).strip() for value in (raw_section.get("columns") or [])]
            section = {
                "title": str(raw_section.get("title") or "").strip() or None,
                "row_header_label": str(raw_section.get("row_header_label") or "").strip() or None,
                "corner_header": normalize_corner_header(raw_section.get("corner_header")),
                "columns": section_columns,
                "column_groups": normalize_column_groups(raw_section.get("column_groups"), section_columns),
                "rows": [
                    {
                        "label": str(row.get("label") or "").strip() or None,
                        "display_label": str(row.get("display_label") or row.get("label") or "").strip() or None,
                        "group": str(row.get("group") or "").strip() or None,
                        "unit": str(row.get("unit") or "").strip() or None,
                        "values": [parse_number(value) for value in (row.get("values") or [])],
                    }
                    for row in (raw_section.get("rows") or []) if isinstance(row, dict)
                ],
                "missing_cells": [
                    {
                        "row": str(cell.get("row") or "").strip(),
                        "column": str(cell.get("column") or "").strip(),
                        "display": str(cell.get("display") or "（？）").strip() or "（？）",
                    }
                    for cell in (raw_section.get("missing_cells") or []) if isinstance(cell, dict)
                ],
                "sections": [],
            }
            if section["columns"] and section["rows"]:
                table["sections"].append(section)
        apply_explicit_table_missing_values(table, parsed.get("explicit_missing_values"), normalization_notes)
    raw_axes = parsed.get("axes") if isinstance(parsed.get("axes"), dict) else {}
    raw_y = raw_axes.get("y") or []
    if isinstance(raw_y, dict):
        raw_y = [raw_y]
    axes = {"x": normalize_axis(raw_axes.get("x")), "y": [normalize_axis(axis) for axis in raw_y[:2]]}
    legend = []
    for raw in parsed.get("legend") or []:
        if isinstance(raw, dict) and raw.get("label") not in (None, ""):
            legend.append({"label": str(raw["label"]).strip(), "style": normalize_style(raw.get("style"))})
    labels = [str(value).strip() for value in (parsed.get("labels_clockwise") or [])]
    geometry = None
    geometry_notes: list[str] = []
    option_series = {series_item["label"] for series_item in series}
    if (
        chart_type in {"grouped_bar", "stacked_bar", "line", "combo", "scatter"}
        and not categories
        and candidate_title_is_empty(parsed)
        and (option_series.issubset({"A", "B", "C", "D", None}) or (asset.get("width") or 9999) < 320)
    ):
        chart_type = "other_unsupported"
        normalization_notes.append("non_data_option_sparkline")
    if (
        chart_type == "pie_donut"
        and set(labels).issubset({"A", "B", "C", "D"})
        and len(labels) >= 2
        and (asset.get("width") or 0) / max(1, (asset.get("height") or 1)) >= 2.5
    ):
        chart_type = "other_unsupported"
        normalization_notes.append("non_data_multiple_choice_pies")
    if chart_type == "pie_donut" and asset["kind"] == "image":
        geometry, geometry_notes = pie_geometry(Path(asset["local_path"]), labels)
        if geometry is None and any(note.startswith("multiple_pie_options_detected") for note in geometry_notes):
            chart_type = "other_unsupported"
            normalization_notes.append("non_data_multiple_choice_pies")
        if geometry:
            categories = [item["label"] or f"slice_{index + 1}" for index, item in enumerate(geometry["slices"])]
            series = [{
                "id": "share",
                "label": "share",
                "render_as": "slice",
                "axis": "none",
                "unit": None,
                "values": [item["share"] for item in geometry["slices"]],
                "style": normalize_style(None),
            }]
    occurrences = [
        {key: occurrence[key] for key in ("question_id", "paper_title", "question_number", "source_jsonl_line", "html_field", "image_ref")}
        for occurrence in asset["occurrences"][:20]
    ]
    crop_total = len(crop_paths)
    changed = int(comparison.get("changed") or 0)
    candidate = {
        "schema_version": "data-analysis-chart-candidate-v1",
        "asset_id": asset["asset_id"],
        "chart_type": chart_type,
        "title": str(parsed["title"]).strip() if parsed.get("title") not in (None, "") else None,
        "unit": str(parsed["unit"]).strip() if parsed.get("unit") not in (None, "") else None,
        "axes": axes,
        "categories": categories,
        "series": series,
        "table": table,
        "pie_geometry": geometry,
        "structured_visual": None,
        "legend": legend,
        "source": {
            "sha256": asset["sha256"],
            "local_path": asset["local_path"],
            "occurrences": occurrences,
        },
        "confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0.0))),
        "review_status": "candidate_auto",
        "verification": {
            "schema_valid": False,
            "ocr_engine": MODEL,
            "ocr_mean_confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0.0))),
            "local_crop_checks": {
                "total": crop_total,
                "matched": max(0, crop_total - (1 if changed else 0)),
                "mismatched": 1 if changed else 0,
                "reasons": ["adjacent_numeric", "negative", "decimal", "percent", "missing_value"],
                "artifacts": [str(path.relative_to(ROOT)) for path in crop_paths],
            },
            "missing_values_preserved": True,
            "notes": [
                f"whole_image_elapsed_seconds:{qwen_meta.get('elapsed_seconds')}",
                f"crop_verification_elapsed_seconds:{crop_meta.get('elapsed_seconds') if crop_meta else None}",
                f"candidate_value_changes_after_crop:{changed}",
                *[f"model_uncertainty:{item}" for item in (parsed.get("uncertainties") or [])],
                *[f"crop_note:{item}" for item in (parsed.get("verification_notes") or [])],
                *geometry_notes,
                *normalization_notes,
            ],
        },
    }
    return candidate


def deterministic_errors(candidate: dict[str, Any], comparison: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    chart_type = candidate["chart_type"]
    categories = candidate["categories"]
    if chart_type not in {"table", "pie_donut", "other_unsupported"}:
        if not categories:
            errors.append("categories_empty")
        if not candidate["series"]:
            errors.append("series_empty")
        for series in candidate["series"]:
            if len(series["values"]) != len(categories):
                errors.append(f"series_length_mismatch:{series['id']}:{len(series['values'])}!={len(categories)}")
    if chart_type == "table":
        table = candidate["table"]
        if not table or not table["columns"] or not table["rows"]:
            errors.append("table_topology_empty")
        else:
            columns = table["columns"]
            rows = table["rows"]
            row_labels = [str(row.get("label") or "").strip() for row in rows]
            if any(len(row["values"]) != len(columns) for row in rows):
                errors.append("table_row_column_length_mismatch")
            if len(columns) != len(set(columns)):
                errors.append("table_column_labels_not_unique")
            if any(not label for label in row_labels):
                errors.append("table_row_label_empty")
            if len(row_labels) != len(set(row_labels)):
                errors.append("table_row_labels_not_unique")
            groups = table.get("column_groups") or []
            if groups:
                grouped_columns = [column for group in groups for column in (group.get("columns") or [])]
                if grouped_columns != columns:
                    errors.append("table_column_group_leaf_order_mismatch")
            corner = table.get("corner_header")
            if corner and (
                corner.get("style") != "diagonal"
                or not corner.get("upper_label")
                or not corner.get("lower_label")
            ):
                errors.append("table_diagonal_corner_header_incomplete")
            row_label_set = set(row_labels)
            column_set = set(columns)
            categories = {str(value).strip() for value in candidate.get("categories") or [] if str(value).strip()}
            if categories and not (categories <= row_label_set or categories <= column_set):
                errors.append("table_categories_not_represented_in_topology")
            for missing in table.get("missing_cells") or []:
                row_label = str(missing.get("row") or "")
                column_label = str(missing.get("column") or "")
                matching_row = next((row for row in rows if str(row.get("label") or "") == row_label), None)
                if matching_row is None or column_label not in columns:
                    errors.append(f"table_missing_cell_reference_invalid:{row_label}:{column_label}")
                elif matching_row["values"][columns.index(column_label)] is not None:
                    errors.append(f"table_missing_cell_not_null:{row_label}:{column_label}")
            for section_index, section in enumerate(table.get("sections") or [], 1):
                section_columns = section.get("columns") or []
                section_rows = section.get("rows") or []
                prefix = f"table_section_{section_index}"
                if not section_columns or not section_rows:
                    errors.append(f"{prefix}_topology_empty")
                    continue
                section_labels = [str(row.get("label") or "").strip() for row in section_rows]
                if any(len(row.get("values") or []) != len(section_columns) for row in section_rows):
                    errors.append(f"{prefix}_row_column_length_mismatch")
                if len(section_columns) != len(set(section_columns)):
                    errors.append(f"{prefix}_column_labels_not_unique")
                if any(not label for label in section_labels):
                    errors.append(f"{prefix}_row_label_empty")
                if len(section_labels) != len(set(section_labels)):
                    errors.append(f"{prefix}_row_labels_not_unique")
                section_groups = section.get("column_groups") or []
                if section_groups:
                    section_grouped = [
                        column for group in section_groups for column in (group.get("columns") or [])
                    ]
                    if section_grouped != section_columns:
                        errors.append(f"{prefix}_column_group_leaf_order_mismatch")
                for missing in section.get("missing_cells") or []:
                    row_label = str(missing.get("row") or "")
                    column_label = str(missing.get("column") or "")
                    matching_row = next(
                        (row for row in section_rows if str(row.get("label") or "") == row_label),
                        None,
                    )
                    if matching_row is None or column_label not in section_columns:
                        errors.append(f"{prefix}_missing_cell_reference_invalid:{row_label}:{column_label}")
                    elif matching_row["values"][section_columns.index(column_label)] is not None:
                        errors.append(f"{prefix}_missing_cell_not_null:{row_label}:{column_label}")
    if chart_type == "pie_donut":
        geometry = candidate["pie_geometry"]
        if geometry is None:
            errors.append("pie_geometry_missing")
        else:
            sweep_total = sum(item["sweep_angle"] for item in geometry["slices"])
            share_total = sum(item["share"] for item in geometry["slices"])
            if abs(sweep_total - 360) > 0.5:
                errors.append(f"pie_angle_conservation_failed:{sweep_total}")
            if abs(share_total - 1) > 1e-5:
                errors.append(f"pie_share_conservation_failed:{share_total}")
            if len(geometry["boundaries_degrees"]) != len(geometry["slices"]) + 1:
                errors.append("pie_boundary_slice_length_mismatch")
            if any(item["label"] is None for item in geometry["slices"]):
                errors.append("pie_label_geometry_count_mismatch")
    for series in candidate["series"]:
        for value in series["values"]:
            if isinstance(value, float) and not math.isfinite(value):
                errors.append("non_finite_series_value")
    for axis in [candidate["axes"]["x"], *candidate["axes"]["y"]]:
        if axis["min"] is not None and axis["max"] is not None and axis["min"] > axis["max"]:
            errors.append("axis_min_greater_than_max")
    if comparison.get("missing_to_zero", 0):
        errors.append(f"missing_value_overwritten_with_zero:{comparison['missing_to_zero']}")
    return sorted(set(errors))


def persist_result(path: Path, result: dict[str, Any]) -> None:
    history: list[dict[str, Any]] = []
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            history.extend(previous.get("retry_history") or [])
            if previous.get("status") in {"failed", "needs_manual_review"}:
                history.append({
                    "status": previous.get("status"),
                    "errors": previous.get("errors") or [],
                    "chart_type": previous.get("chart_type"),
                })
        except Exception:
            history.append({"status": "unreadable_previous_result", "errors": ["previous_result_json_unreadable"]})
    if history:
        result["retry_history"] = history
    atomic_json(path, result)


def process_asset(asset: dict[str, Any], *, force_retry: bool = False) -> dict[str, Any]:
    structured_path = STRUCTURED / f"{asset['asset_id']}.json"
    result_path = WORK / "results" / f"{asset['asset_id']}.json"
    if result_path.is_file() and not force_retry:
        cached_result = json.loads(result_path.read_text(encoding="utf-8"))
        if cached_result.get("status") == "failed" or structured_path.is_file():
            return cached_result
    if asset["kind"] == "missing_image":
        result = {"asset_id": asset["asset_id"], "status": "failed", "errors": ["source_image_missing"], "structured_path": None}
        persist_result(result_path, result)
        return result
    prompt = EXTRACTION_PROMPT
    image_paths: list[Path] = []
    if asset["kind"] == "image":
        image_paths = [Path(asset["local_path"])]
    else:
        prompt += "\n待恢复的 HTML 表格：\n" + asset.get("inline_html", "")
    try:
        table_correction = load_table_correction(asset["asset_id"])
        correction_is_confident = bool(
            table_correction
            and float(table_correction.get("confidence") or 0.0) >= 0.9
            and not (table_correction.get("uncertainties") or [])
            and (table_correction.get("table") or {}).get("columns")
            and (table_correction.get("table") or {}).get("rows")
        )
        if correction_is_confident:
            whole = {
                "model": MODEL,
                "elapsed_seconds": 0.0,
                "content": "high_confidence_table_correction",
                "parsed": {
                    "chart_type": "table",
                    "title": None,
                    "unit": None,
                    "axes": {"x": {}, "y": []},
                    "categories": [],
                    "series": [],
                    "table": table_correction["table"],
                    "legend": [],
                    "labels_clockwise": [],
                    "confidence": float(table_correction.get("confidence") or 0.92),
                    "uncertainties": [],
                    "explicit_missing_values": [],
                },
            }
        else:
            whole = qwen_request(prompt=prompt, image_paths=image_paths, cache_path=CACHE / f"{asset['asset_id']}.whole.json")
        initial = whole["parsed"]
        crops = [] if correction_is_confident else create_verification_crops(asset, initial)
        crop_meta = None
        verified = initial
        if crops:
            try:
                crop_meta = qwen_request(
                    prompt=verification_prompt(initial, initial.get("chart_type", "other_unsupported"), len(crops)),
                    image_paths=crops,
                    cache_path=CACHE / f"{asset['asset_id']}.verify.json",
                    max_tokens=1800,
                    allow_model_json_repair=False,
                )
            except RuntimeError as error:
                if (
                    "malformed_model_json" in str(error)
                    and initial.get("chart_type") != "other_unsupported"
                ):
                    crop_meta = verify_individual_crops(asset["asset_id"], initial, crops)
                else:
                    raise
            if (
                crop_meta.get("parsed", {}).get("chart_type") in CHART_TYPES
                or crop_meta.get("content") == "individual_crop_verification"
                or str(crop_meta.get("content", "")).startswith("individual_")
            ):
                verified = crop_meta["parsed"]
            else:
                verified = apply_verification_delta(initial, crop_meta["parsed"])
        comparison = compare_signatures(initial, verified)
        candidate = normalize_candidate(asset, verified, whole, crop_meta, crops, comparison)
        validation_errors = deterministic_errors(candidate, comparison)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(candidate, schema)
            candidate["verification"]["schema_valid"] = True
            jsonschema.validate(candidate, schema)
        except jsonschema.ValidationError as error:
            validation_errors.append(f"json_schema:{error.json_path}:{error.message}")
        if candidate["chart_type"] == "other_unsupported":
            status = "unsupported"
            candidate["review_status"] = "unsupported"
        elif validation_errors or candidate["confidence"] < 0.75 or (
            comparison["change_ratio"] > 0.25 and not correction_is_confident
        ):
            status = "needs_manual_review"
            candidate["review_status"] = "needs_manual_review"
        else:
            status = "success"
            candidate["review_status"] = "candidate_auto"
        candidate["verification"]["notes"].extend(f"validation_error:{item}" for item in validation_errors)
        atomic_json(structured_path, candidate)
        result = {
            "asset_id": asset["asset_id"],
            "status": status,
            "chart_type": candidate["chart_type"],
            "structured_path": str(structured_path.relative_to(ROOT)),
            "errors": validation_errors,
            "confidence": candidate["confidence"],
            "comparison": comparison,
            "whole_elapsed_seconds": whole["elapsed_seconds"],
            "verify_elapsed_seconds": crop_meta["elapsed_seconds"] if crop_meta else None,
        }
        persist_result(result_path, result)
        return result
    except Exception as error:
        result = {"asset_id": asset["asset_id"], "status": "failed", "errors": [f"processing_exception:{type(error).__name__}:{error}"], "structured_path": None}
        persist_result(result_path, result)
        return result


SAMPLE_TARGETS = {
    "grouped_bar": 4,
    "stacked_bar": 2,
    "line": 3,
    "combo": 3,
    "pie_donut": 3,
    "scatter": 1,
    "table": 3,
    "other_unsupported": 1,
}


def select_sample(assets: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        if asset["kind"] == "missing_image":
            continue
        if asset.get("width") is not None and asset.get("height") is not None:
            if asset["width"] < 140 or asset["height"] < 100:
                continue
        by_type[asset["heuristic_type"]].append(asset)
    for values in by_type.values():
        values.sort(key=lambda item: (-item["source_occurrence_count"], -int(item.get("size_bytes") or 0), item["asset_id"]))
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for chart_type, target in SAMPLE_TARGETS.items():
        for asset in by_type.get(chart_type, [])[:target]:
            if asset["asset_id"] not in selected_ids and len(selected) < limit:
                selected.append(asset)
                selected_ids.add(asset["asset_id"])
    if len(selected) < limit:
        remainder = sorted(
            (asset for asset in assets if asset["asset_id"] not in selected_ids and asset["kind"] != "missing_image"),
            key=lambda item: (-item["source_occurrence_count"], -int(item.get("size_bytes") or 0), item["asset_id"]),
        )
        selected.extend(remainder[:limit - len(selected)])
    return selected[:limit]


def process_batch(limit: int, full: bool = False, workers: int = 1) -> dict[str, Any]:
    if not SCAN_PATH.is_file():
        scan_assets()
    assets = list(read_jsonl(SCAN_PATH))
    selected = assets if full else select_sample(assets, limit)
    atomic_json(WORK / ("full-selection.json" if full else f"sample-{limit}-selection.json"), {
        "asset_ids": [asset["asset_id"] for asset in selected],
        "heuristic_types": dict(Counter(asset["heuristic_type"] for asset in selected)),
    })
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(3, workers))) as executor:
        future_assets = {executor.submit(process_asset, asset): asset for asset in selected}
        for index, future in enumerate(as_completed(future_assets), 1):
            asset = future_assets[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "asset_id": asset["asset_id"],
                    "status": "failed",
                    "errors": [f"executor_exception:{type(error).__name__}:{error}"],
                    "structured_path": None,
                }
            results.append(result)
            counts = Counter(item["status"] for item in results)
            if index % 10 == 0 or index == len(selected) or result["status"] in {"failed", "needs_manual_review"}:
                print(
                    f"processed={index}/{len(selected)} success={counts['success']} review={counts['needs_manual_review']} "
                    f"unsupported={counts['unsupported']} failed={counts['failed']} asset={asset['asset_id']} type={result.get('chart_type')}",
                    flush=True,
                )
    summary = {
        "requested": len(selected),
        "statuses": dict(Counter(result["status"] for result in results)),
        "actual_types": dict(Counter(result.get("chart_type", "unknown") for result in results)),
        "heuristic_types": dict(Counter(asset["heuristic_type"] for asset in selected)),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }
    atomic_json(WORK / ("full-run-summary.json" if full else f"sample-{limit}-summary.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only data-analysis chart inventory and Qwen/OpenCV candidate pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan")
    batch = subparsers.add_parser("batch")
    batch.add_argument("--limit", type=int, default=20)
    batch.add_argument("--workers", type=int, default=1, choices=(1, 2, 3))
    full = subparsers.add_parser("full")
    full.add_argument("--workers", type=int, default=2, choices=(1, 2, 3))
    args = parser.parse_args()
    if args.command == "scan":
        scan_assets()
    elif args.command == "batch":
        process_batch(args.limit, workers=args.workers)
    elif args.command == "full":
        process_batch(0, full=True, workers=args.workers)


if __name__ == "__main__":
    main()
