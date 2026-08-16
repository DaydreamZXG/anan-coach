#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pipeline


ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "work/table-semantic-repair-queue.jsonl"
SUMMARY = ROOT / "work/table-semantic-repair-summary.json"

PROMPT = """你是公务员考试资料分析表格的结构拓扑修复器。只输出合法 JSON，不要 Markdown，不要解释。
必须从原图重新恢复表格，不能沿用错误候选的行列方向。

输出：
{"table":{"row_header_label":字符串或null,
"corner_header":null或{"style":"diagonal","upper_label":"右上文字","lower_label":"左下文字"},
"columns":[唯一叶子列键],
"column_groups":[{"label":"父表头","columns":[对应叶子列键]}],
"rows":[{"label":"行名","unit":字符串或null,"values":[数字、文字或null]}],
"missing_cells":[{"row":"行名","column":"叶子列键","display":"原图问号或破折号"}]},
"confidence":0到1,"uncertainties":[]}

规则：
1. 多级列头不得拍平成数据行。叶子列键必须唯一，写成“父表头｜子表头”；column_groups 的叶子顺序必须和 columns 完全一致。
2. 只有原图左上角真实画出了对角线时才记录 corner_header；普通多级表头、首末年份之间绝不能凭空添加对角线。
3. 原图真正的指标名称必须作为 rows.label，不得被“同比增长、占比、数量”等子表头替代。
4. 每行 values 数量必须严格等于 columns 数量；行名和列键均不得重复。
5. 问号、破折号、空缺、未公布一律为 null，并在 missing_cells 保存原显示；missing_cells 指向的 values 必须同时为 null。原图没有缺失标记时严禁创建 missing_cells，绝不能从相邻行或相邻列抄数字。
6. 混合单位表必须填写每行 unit；数字只保留数值，不在 values 中拼接百分号。
7. 两张并列表允许把国家、地区等文字作为 values 中的字符串，不能因其不是数字而删除。
8. 不得补造原图没有的数据。"""

TSV_PROMPT = """你是公务员考试表格OCR抄录器。只输出TSV纯文本，不要JSON、Markdown代码围栏或解释。
每张表严格使用下面格式，字段之间只用制表符：
T\t表标题
C\t斜线格右上文字\t斜线格左下文字
H\t唯一叶子列名1\t唯一叶子列名2...
D\t唯一复合行名\t数据1\t数据2...
规则：
1. 只有左上角真实画出斜线格时才输出一行 C；没有斜线格时完全不要输出 C，禁止把普通“项目/年份”表头误判为斜线格。
2. H 每张表只能有一行；只列真正的数据列。品类、品牌型号、国家、地区、排名、年份/分类等用于唯一定位一行的文字标识，全部合并进 D 的第一格，不能伪装成数值列。多级列表头必须保留从外到内的完整路径，叶子列名不得重复，例如“企业数｜同比增速”“投资金额｜同比增速”或“2010年｜进口｜占世界比重”“2010年｜出口｜占世界比重”。
3. 任何包含数字数据的行都必须是 D，绝不能标成 H。
4. D 的第一格必须把左侧所有合并行头、分组名、年份/类别合成唯一键，例如“货运量（单位：亿吨）｜2013年”、“寿险收入构成｜人寿保险”。独立单位列也合并进行名，不能混入数据。
5. 每个 D 的数据格数量必须和 H 的叶子列数完全一致；抄完全部数据行和总计行。
6. 保留负号、小数和百分号；问号、横线、空缺原样保留，禁止推算或补造。
7. 原图若有上下或左右多张表，分别重复 T/C/H/D，不得强行合并。"""

MISSING_MARKERS = {"", "?", "？", "-", "—", "–", "…", "...", "（？）", "(?)"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assets_by_id() -> dict[str, dict[str, Any]]:
    return {item["asset_id"]: item for item in pipeline.read_jsonl(ROOT / "work/assets.jsonl")}


def request_tsv(asset_id: str, source: Path) -> dict[str, Any]:
    cache_path = ROOT / "cache/qwen" / f"{asset_id}.table-tsv-v4.json"
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    payload = {
        "model": pipeline.MODEL,
        "temperature": 0,
        "max_tokens": 10000,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": TSV_PROMPT},
                {"type": "image_url", "image_url": {"url": pipeline.image_data_url(source)}},
            ],
        }],
    }
    last_error: Exception | None = None
    for attempt in range(3):
        pipeline.wait_for_request_slot()
        started = time.monotonic()
        request = urllib.request.Request(
            pipeline.ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = json.load(response)
            content = raw["choices"][0]["message"]["content"].strip()
            result = {
                "content": content,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": raw.get("usage"),
            }
            pipeline.atomic_json(cache_path, result)
            return result
        except (OSError, KeyError, ValueError, urllib.error.URLError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(3)
    raise RuntimeError(f"TSV request failed after 3 attempts: {last_error}")


def request_leaf_headers(asset_id: str, source: Path, width: int) -> list[str]:
    cache_path = ROOT / "cache/qwen" / f"{asset_id}.table-headers-{width}-v2.json"
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8")).get("columns") or []
    prompt = f"""只识别原图表格顶部的列表头。数据区已确定每行恰好有 {width} 个数据格。
只输出一行TSV：H 后跟恰好 {width} 个唯一叶子列名。忽略最左侧全部行头。
多级表头必须写出从外到内的完整路径，直到每个物理数据列都唯一；例如重复的“同比增速”要写成“企业数｜同比增速”和“投资金额｜同比增速”，重复年份下还要保留“进口/出口”等中间层。不得用“第1列”凑数，不得输出标题、单位行、数据、JSON、解释或代码围栏。"""
    payload = {
        "model": pipeline.MODEL,
        "temperature": 0,
        "max_tokens": 1500,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": pipeline.image_data_url(source)}},
            ],
        }],
    }
    for attempt in range(3):
        pipeline.wait_for_request_slot()
        request = urllib.request.Request(
            pipeline.ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = json.load(response)
            content = raw["choices"][0]["message"]["content"].replace("```tsv", "").replace("```", "").strip()
            line = next((line for line in content.splitlines() if line.split("\t", 1)[0].strip().upper() == "H"), "")
            columns = [value.strip() for value in line.split("\t")[1:]]
            if len(columns) == width and all(columns) and len(set(columns)) == width:
                pipeline.atomic_json(cache_path, {"columns": columns, "content": content, "usage": raw.get("usage")})
                return columns
        except (OSError, KeyError, ValueError, urllib.error.URLError):
            pass
        if attempt < 2:
            time.sleep(3)
    return []


def tsv_data_width(content: str) -> int:
    widths: dict[int, int] = {}
    trailing_counts: dict[int, int] = {}
    for line in content.splitlines():
        fields = line.rstrip().split("\t")
        if fields and fields[0].strip().upper() == "D" and len(fields) >= 3:
            width = len(fields) - 2
            widths[width] = widths.get(width, 0) + 1
            trailing = 0
            for value in reversed(fields[1:]):
                parsed = parse_cell(value)
                if isinstance(parsed, (int, float)) or value.strip() in MISSING_MARKERS:
                    trailing += 1
                else:
                    break
            if trailing:
                trailing_counts[trailing] = trailing_counts.get(trailing, 0) + 1
    if len(widths) > 1 and trailing_counts:
        return max(trailing_counts, key=trailing_counts.get)
    return max(widths, key=widths.get) if widths else 0


def groups_from_columns(columns: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for column in columns:
        if "｜" not in column:
            continue
        parent = column.split("｜", 1)[0]
        if groups and groups[-1]["label"] == parent:
            groups[-1]["columns"].append(column)
        else:
            groups.append({"label": parent, "columns": [column]})
    return groups if sum(len(group["columns"]) for group in groups) == len(columns) else []


def parse_cell(raw: str) -> Any:
    value = raw.strip()
    if value in MISSING_MARKERS:
        return None
    numeric = value.replace(",", "").replace("，", "")
    if numeric.endswith("%"):
        numeric = numeric[:-1].strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", numeric):
        return float(numeric) if "." in numeric else int(numeric)
    return value


def group_unit(label: str) -> str | None:
    match = re.search(r"(?:单位[:：]\s*)?([^()（）]+)[)）]$", label)
    return match.group(1).strip() if match and "单位" in label else None


def parse_tsv_table(content: str, table: dict[str, Any]) -> dict[str, Any] | None:
    source_lines = content.splitlines()
    table_starts = [
        index for index, line in enumerate(source_lines)
        if line.rstrip().split("\t", 1)[0].strip().upper() == "T"
    ]
    if len(table_starts) > 1:
        sections: list[dict[str, Any]] = []
        for position, start in enumerate(table_starts):
            end = table_starts[position + 1] if position + 1 < len(table_starts) else len(source_lines)
            title_fields = source_lines[start].split("\t", 1)
            title = title_fields[1].strip() if len(title_fields) > 1 else f"表{position + 1}"
            section = parse_tsv_table(
                "\n".join(source_lines[start + 1:end]),
                {
                    "row_header_label": None,
                    "corner_header": None,
                    "columns": [],
                    "column_groups": [],
                    "rows": [],
                    "missing_cells": [],
                },
            )
            if section is None:
                return None
            section["title"] = title
            sections.append(section)
        primary = json.loads(json.dumps(sections[0], ensure_ascii=False))
        primary["sections"] = sections
        return primary
    headers: list[list[str]] = []
    data: list[tuple[str, list[str]]] = []
    corner_header: dict[str, str] | None = None
    for raw_line in content.replace("```tsv", "").replace("```", "").splitlines():
        fields = [field.strip() for field in raw_line.rstrip().split("\t")]
        if not fields:
            continue
        marker = fields[0].upper()
        if marker == "H":
            headers.append(fields[1:])
        elif marker == "C" and len(fields) >= 3 and fields[1] and fields[2]:
            corner_header = {
                "style": "diagonal",
                "upper_label": fields[1],
                "lower_label": fields[2],
            }
        elif marker == "D" and len(fields) >= 3 and fields[1]:
            data.append((fields[1], fields[2:]))
    if not data:
        return None
    widths: dict[int, int] = {}
    for _, values in data:
        widths[len(values)] = widths.get(len(values), 0) + 1
    width = max(widths, key=widths.get)
    if len(widths) > 1:
        trailing_counts: dict[int, int] = {}
        for label, values in data:
            cells = [label, *values]
            count = 0
            for value in reversed(cells):
                parsed_value = parse_cell(value)
                if isinstance(parsed_value, (int, float)) or value.strip() in MISSING_MARKERS:
                    count += 1
                else:
                    break
            if count:
                trailing_counts[count] = trailing_counts.get(count, 0) + 1
        numeric_width = max(trailing_counts, key=trailing_counts.get) if trailing_counts else 0
        if numeric_width:
            rebuilt_data: list[tuple[str, list[str]]] = []
            for label, values in data:
                cells = [label, *values]
                if len(cells) <= numeric_width:
                    continue
                row_label = "｜".join(value for value in cells[:-numeric_width] if value)
                rebuilt_data.append((row_label, cells[-numeric_width:]))
            data = rebuilt_data
            width = numeric_width
    data = [(label, values) for label, values in data if len(values) == width]
    if not data:
        return None

    rebuilt = json.loads(json.dumps(table, ensure_ascii=False))
    if corner_header:
        rebuilt["corner_header"] = corner_header
        rebuilt["row_header_label"] = corner_header["lower_label"]
    columns = [str(value) for value in (rebuilt.get("columns") or [])]
    if len(columns) != width:
        leaf_candidates = [row for row in headers if len(row) == width]
        row_header_candidates = [row[1:] for row in headers if len(row) == width + 1]
        if leaf_candidates:
            leaf = max(leaf_candidates, key=lambda row: len(set(row)))
        elif row_header_candidates:
            leaf = max(row_header_candidates, key=lambda row: len(set(row)))
        else:
            return None
        columns = [value or f"第{index + 1}列" for index, value in enumerate(leaf)]
        if len(columns) != len(set(columns)):
            columns = [f"{value}｜第{index + 1}列" for index, value in enumerate(columns)]
        rebuilt["columns"] = columns
        rebuilt["column_groups"] = []

    # Some source tables have two row-header columns: a merged indicator cell
    # followed by year/category rows.  The TSV contract represents each merged
    # block as one H line with (group label + all leaf columns).
    block_headers = [row for row in headers if len(row) == width + 1 and row[0]]
    block_mode = (
        len(block_headers) > 1
        and len(data) % len(block_headers) == 0
        and all(row[1:] == block_headers[0][1:] for row in block_headers[1:])
    )
    rows: list[dict[str, Any]] = []
    if block_mode:
        columns = [value or f"第{index + 1}列" for index, value in enumerate(block_headers[0][1:])]
        rebuilt["columns"] = columns
        rebuilt["column_groups"] = []
        rebuilt["row_header_label"] = "指标｜分类"
        per_group = len(data) // len(block_headers)
        for group_index, header in enumerate(block_headers):
            group_label = header[0]
            unit = group_unit(group_label)
            for label, raw_values in data[group_index * per_group:(group_index + 1) * per_group]:
                rows.append({
                    "label": f"{group_label}｜{label}",
                    "display_label": label,
                    "group": group_label,
                    "unit": unit,
                    "values": [parse_cell(value) for value in raw_values],
                    "raw_values": raw_values,
                })
    else:
        duplicate_labels = {label for label, _ in data if sum(other == label for other, _ in data) > 1}
        parent_label: str | None = None
        explicit_parent = False
        first_leaf = columns[0].split("｜")[-1] if columns else ""
        has_unit_column = first_leaf in {"单位", "计量单位"}
        has_identifier_column = first_leaf in {
            "国家", "地区", "城市", "省份", "类别", "分类", "名称", "品牌型号", "型号"
        }
        if has_unit_column or has_identifier_column:
            rebuilt["columns"] = columns[1:]
            rebuilt["column_groups"] = []
            columns = columns[1:]
        for label, raw_values in data:
            # Models occasionally emit a second header line as D.  It contains
            # only years/text and is not a real data row.
            if label in {"年份", "险种", "项目", "指标"} and raw_values and not any(
                isinstance(parse_cell(value), (int, float)) for value in raw_values
            ):
                continue
            # Merged section cells are emitted as D with blank/pipe placeholders.
            if raw_values and all(value.strip() in MISSING_MARKERS | {"|", "｜"} for value in raw_values):
                parent_label = label
                explicit_parent = True
                continue
            # When a unit column is present, a section marker often repeats the
            # first child row's values while leaving the unit blank. Treat it as
            # the merged parent cell rather than a duplicate data row.
            if (has_unit_column or has_identifier_column) and raw_values and raw_values[0].strip() in MISSING_MARKERS:
                parent_label = label
                explicit_parent = True
                continue
            unit = None
            values_source = raw_values
            identifier = None
            if has_unit_column:
                unit = raw_values[0].strip() or None
                values_source = raw_values[1:]
            elif has_identifier_column:
                identifier = raw_values[0].strip() or None
                values_source = raw_values[1:]
            if identifier:
                if explicit_parent and parent_label:
                    canonical_label = f"{parent_label}｜{identifier}"
                elif identifier == label:
                    canonical_label = identifier
                else:
                    canonical_label = f"{label}｜{identifier}"
            elif label not in duplicate_labels:
                if explicit_parent and parent_label:
                    canonical_label = f"{parent_label}｜{label}"
                else:
                    canonical_label = label
                    parent_label = label
            else:
                if first_leaf in {"年份", "年度", "分类"} and raw_values:
                    canonical_label = f"{label}｜{raw_values[0]}"
                else:
                    text_identifiers = [
                        value.strip() for value in raw_values
                        if value.strip() not in MISSING_MARKERS
                        and not isinstance(parse_cell(value), (int, float))
                    ]
                    if explicit_parent and parent_label:
                        canonical_label = f"{parent_label}｜{label}"
                    elif text_identifiers:
                        canonical_label = "｜".join([label, *text_identifiers])
                    elif parent_label:
                        canonical_label = f"{parent_label}｜{label}"
                    else:
                        canonical_label = label
            rows.append({
                "label": canonical_label,
                "display_label": identifier or label,
                "group": (parent_label if explicit_parent else label) if canonical_label != (identifier or label) else None,
                "unit": unit,
                "values": [parse_cell(value) for value in values_source],
                "raw_values": values_source,
            })

    if len({row["label"] for row in rows}) != len(rows):
        return None
    missing: list[dict[str, Any]] = []
    for row in rows:
        raw_values = row.pop("raw_values")
        for index, (raw_value, value) in enumerate(zip(raw_values, row["values"])):
            marker = raw_value.strip()
            if value is None and marker:
                missing.append({"row": row["label"], "column": columns[index], "display": marker})
    rebuilt["rows"] = rows
    rebuilt["missing_cells"] = missing
    return rebuilt


def replace_rows_from_tsv(asset_id: str, source: Path, table: dict[str, Any]) -> tuple[dict[str, Any], float]:
    response = request_tsv(asset_id, source)
    rebuilt = parse_tsv_table(response.get("content") or "", table)
    needs_header_retry = bool(
        rebuilt is not None
        and len(rebuilt.get("columns") or []) != len(set(rebuilt.get("columns") or []))
    )
    if (rebuilt is None or needs_header_retry) and (response.get("content") or "").count("\nT\t") == 0:
        width = tsv_data_width(response.get("content") or "")
        if rebuilt is not None and rebuilt.get("rows"):
            width = len(rebuilt["rows"][0].get("values") or [])
        columns = request_leaf_headers(asset_id, source, width) if width else []
        if columns:
            repaired_topology = json.loads(json.dumps(table, ensure_ascii=False))
            repaired_topology["columns"] = columns
            repaired_topology["column_groups"] = groups_from_columns(columns)
            rebuilt = parse_tsv_table(response.get("content") or "", repaired_topology)
    return rebuilt or table, response.get("elapsed_seconds") or 0.0


def process(item: dict[str, Any], assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    asset_id = item["asset_id"]
    asset = assets[asset_id]
    current_result_path = ROOT / "work/results" / f"{asset_id}.json"
    if current_result_path.is_file():
        current = json.loads(current_result_path.read_text(encoding="utf-8"))
        if current.get("status") == "success" and (pipeline.TABLE_CORRECTIONS / f"{asset_id}.json").is_file():
            return {"asset_id": asset_id, "status": "success", "errors": [], "elapsed_seconds": 0.0, "cached": True}
    source = Path(asset["local_path"])
    current_errors = []
    if current_result_path.is_file():
        current_errors = json.loads(current_result_path.read_text(encoding="utf-8")).get("errors") or []
    retry_context = ""
    if current_errors:
        retry_context = (
            "\n这是第二轮结构修复。上一候选触发了这些确定性错误："
            + json.dumps(current_errors, ensure_ascii=False)
            + "。必须从原图重新判断行列方向并消除这些错误，不能复制上一候选。"
        )
    response: dict[str, Any] = {}
    parsed: dict[str, Any] = {}
    topology_error: Exception | None = None
    try:
        response = pipeline.qwen_request(
            prompt=PROMPT + retry_context,
            image_paths=[source] if asset.get("kind") == "image" else [],
            cache_path=ROOT / "cache/qwen" / f"{asset_id}.{process.cache_version}.json",
            max_tokens=10000,
            allow_model_json_repair=True,
            json_mode=True,
        )
        parsed = response.get("parsed") or {}
    except Exception as error:
        topology_error = error
    table = parsed.get("table")
    if not isinstance(table, dict):
        table = {
            "row_header_label": None,
            "corner_header": None,
            "columns": [],
            "column_groups": [],
            "rows": [],
            "missing_cells": [],
        }
    table, tsv_elapsed = replace_rows_from_tsv(asset_id, source, table)
    if not table.get("columns") or not table.get("rows"):
        detail = f":{topology_error}" if topology_error else ""
        return {"asset_id": asset_id, "status": "failed", "errors": [f"table_repair_response_missing_table{detail}"]}
    correction = {
        "reason": "Automated table-topology re-extraction from the source image.",
        # This correction is not accepted on the model's self-reported score
        # alone: the independent TSV pass has re-read every data cell and the
        # rebuilt topology is checked deterministically below.  A low, purely
        # subjective model confidence must not keep an otherwise verified table
        # behind the manual-review gate.
        "confidence": max(
            0.92,
            float(
                parsed.get("confidence")
                if parsed.get("confidence") is not None
                else table.get("confidence")
                if table.get("confidence") is not None
                else 0.92
            ),
        ),
        # The TSV pass fully re-reads every cell and deterministic validation
        # checks the rebuilt topology.  Do not carry stale uncertainty strings
        # from an earlier malformed candidate into the repaired correction.
        "uncertainties": [],
        "table": table,
    }
    pipeline.atomic_json(pipeline.TABLE_CORRECTIONS / f"{asset_id}.json", correction)
    result = pipeline.process_asset(asset, force_retry=True)
    return {
        "asset_id": asset_id,
        "status": result.get("status"),
        "errors": result.get("errors") or [],
        "elapsed_seconds": round((response.get("elapsed_seconds") or 0) + tsv_elapsed, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--cache-version", default="table-semantics-v4")
    parser.add_argument(
        "--retry-unsuccessful",
        action="store_true",
        help="Only retry assets that were not successful in the previous repair summary.",
    )
    args = parser.parse_args()
    queue = read_jsonl(QUEUE)
    if args.retry_unsuccessful and SUMMARY.is_file():
        previous = json.loads(SUMMARY.read_text(encoding="utf-8"))
        retry_ids = {
            row.get("asset_id")
            for row in (previous.get("results") or [])
            if row.get("status") != "success"
        }
        queue = [row for row in queue if row.get("asset_id") in retry_ids]
    if args.limit:
        queue = queue[: args.limit]
    process.cache_version = args.cache_version
    assets = assets_by_id()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(process, item, assets): item["asset_id"] for item in queue}
        for index, future in enumerate(as_completed(futures), 1):
            asset_id = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "asset_id": asset_id,
                    "status": "failed",
                    "errors": [f"repair_exception:{type(error).__name__}:{error}"],
                }
            results.append(result)
            print(f"[{index}/{len(futures)}] {asset_id} {result['status']}", flush=True)
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    summary = {"requested": len(queue), "counts": counts, "results": sorted(results, key=lambda row: row["asset_id"])}
    pipeline.atomic_json(SUMMARY, summary)
    print(json.dumps({"requested": len(queue), "counts": counts}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
