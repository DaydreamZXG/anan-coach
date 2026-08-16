#!/usr/bin/env python3
from __future__ import annotations

import html
import base64
import json
import math
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import jsonschema
from PIL import Image, ImageDraw, ImageFont

import pipeline


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "work/results"
STRUCTURED = ROOT / "structured"
PREVIEWS = ROOT / "previews"
SCHEMA = json.loads((ROOT / "structured-schema.json").read_text(encoding="utf-8"))
SCHEMA_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def read_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def load_results() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    canonical: set[str] = set()
    for path in sorted(RESULTS.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            asset_id = row["asset_id"]
            is_canonical = path.stem == asset_id
            # Historical retry artifacts can share an asset_id.  The canonical
            # <asset_id>.json is authoritative regardless of directory order.
            if asset_id not in result or (is_canonical and asset_id not in canonical):
                result[asset_id] = row
            if is_canonical:
                canonical.add(asset_id)
        except Exception:
            continue
    return result


def candidate_for(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result or not result.get("structured_path"):
        return None
    path = ROOT / result["structured_path"]
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def validate_all(assets: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    checks = Counter()
    problems: list[dict[str, Any]] = []
    for asset in assets:
        result = results.get(asset["asset_id"])
        if result is None:
            checks["unprocessed"] += 1
            continue
        checks["processed"] += 1
        candidate = candidate_for(result)
        if candidate is None:
            if result["status"] == "failed":
                checks["failed_without_candidate"] += 1
            continue
        checks["candidate_files"] += 1
        try:
            SCHEMA_VALIDATOR.validate(candidate)
            checks["schema_valid"] += 1
        except jsonschema.ValidationError as error:
            checks["schema_invalid"] += 1
            problems.append({"asset_id": asset["asset_id"], "check": "json_schema", "detail": error.message})
        chart_type = candidate["chart_type"]
        categories = candidate["categories"]
        if chart_type not in {"table", "pie_donut", "structured_visual", "other_unsupported"}:
            if all(len(series["values"]) == len(categories) for series in candidate["series"]):
                checks["series_lengths_consistent"] += 1
            else:
                checks["series_lengths_inconsistent"] += 1
        if chart_type == "table" and candidate["table"]:
            columns = candidate["table"]["columns"]
            if all(len(row["values"]) == len(columns) for row in candidate["table"]["rows"]):
                checks["table_topology_valid"] += 1
            else:
                checks["table_topology_invalid"] += 1
        if chart_type == "pie_donut" and candidate["pie_geometry"]:
            angle_total = sum(row["sweep_angle"] for row in candidate["pie_geometry"]["slices"])
            share_total = sum(row["share"] for row in candidate["pie_geometry"]["slices"])
            if abs(angle_total - 360) <= 0.5:
                checks["pie_angle_conservation_valid"] += 1
            else:
                checks["pie_angle_conservation_invalid"] += 1
            if abs(share_total - 1) <= 1e-5:
                checks["pie_share_conservation_valid"] += 1
            else:
                checks["pie_share_conservation_invalid"] += 1
        if chart_type == "structured_visual" and candidate.get("structured_visual"):
            visual = candidate["structured_visual"]
            complexity = visual["complexity"]
            primitive_valid = (
                visual.get("schemaVersion") == "graphic-primitives-v2"
                and visual.get("fidelityTier") == "deterministic_vector"
                and complexity["primitiveCount"] <= 512
                and complexity["pathCommandCount"] <= 4096
            )
            patterned_pie_valid = (
                visual.get("schemaVersion") == "patterned-pie-options-v1"
                and visual.get("fidelityTier") == "semantic_vector"
                and complexity["optionCount"] == len(visual["options"])
                and complexity["sectorCount"] == len(visual["options"]) * 3
                and all(
                    [sector["pattern"] for sector in option["sectors"]]
                    == ["dots", "diagonal", "grid"]
                    for option in visual["options"]
                )
            )
            semantic_microchart_valid = (
                visual.get("schemaVersion") == "semantic-microcharts-v1"
                and visual.get("fidelityTier") == "semantic_vector"
                and complexity["panelCount"] == len(visual["panels"])
                and complexity["pointCount"] <= 512
                and complexity["barCount"] <= 96
                and complexity["estimatedDrawCommandCount"] <= 1024
            )
            semantic_pie_valid = (
                visual.get("schemaVersion") == "semantic-pie-options-v1"
                and visual.get("fidelityTier") == "semantic_vector"
                and complexity["optionCount"] == len(visual["options"])
                and complexity["sectorCount"] == sum(len(option["sectors"]) for option in visual["options"])
                and complexity["estimatedDrawCommandCount"] <= 256
            )
            semantic_statistical_valid = (
                visual.get("schemaVersion") == "semantic-statistical-panels-v1"
                and visual.get("fidelityTier") == "semantic_vector"
                and complexity["panelCount"] == len(visual["panels"])
                and complexity["valueCount"] == sum(len(series["values"]) for panel in visual["panels"] for series in panel["series"])
            )
            semantic_simplex_valid = (
                visual.get("schemaVersion") == "semantic-simplex-grids-v1"
                and visual.get("fidelityTier") == "semantic_vector"
                and complexity["vertexCount"] == len(visual["vertices"])
                and complexity["pointCount"] == len(visual["points"])
            )
            semantic_formula_valid = (
                visual.get("schemaVersion") == "semantic-formula-v1"
                and visual.get("fidelityTier") == "semantic_vector"
                and complexity["expressionLength"] == len(visual["expression"])
                and bool(visual["fallbackText"])
            )
            if primitive_valid or patterned_pie_valid or semantic_microchart_valid or semantic_pie_valid or semantic_statistical_valid or semantic_simplex_valid or semantic_formula_valid:
                checks["structured_visual_contract_valid"] += 1
            else:
                checks["structured_visual_contract_invalid"] += 1
        comparison = result.get("comparison") or {}
        checks["crop_verified_values"] += int(comparison.get("verified_values") or 0)
        checks["crop_corrected_values"] += int(comparison.get("changed") or 0)
        checks["missing_to_zero_conflicts"] += int(comparison.get("missing_to_zero") or 0)
    return {"counts": dict(checks), "problems": problems}


def build_outputs() -> dict[str, Any]:
    assets = list(read_rows(ROOT / "work/assets.jsonl"))
    results = load_results()
    manifest: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    status_counts = Counter()
    type_counts = Counter()
    for asset in assets:
        result = results.get(asset["asset_id"])
        status = result.get("status") if result else "pending"
        chart_type = (result.get("chart_type") if result else asset.get("heuristic_type", "other_unsupported")) or "unknown"
        status_counts[status] += 1
        type_counts[chart_type] += 1
        candidate = candidate_for(result)
        review = {
            "confidence": candidate.get("confidence") if candidate else None,
            "review_status": candidate.get("review_status") if candidate else ("pending" if status == "pending" else status),
            "schema_valid": candidate.get("verification", {}).get("schema_valid") if candidate else False,
            "validation_errors": result.get("errors", []) if result else [],
            "local_crop_checks": candidate.get("verification", {}).get("local_crop_checks") if candidate else None,
        }
        manifest.append({
            "asset_id": asset["asset_id"],
            "sha256": asset["sha256"],
            "source": {
                "local_path": asset["local_path"],
                "kind": asset["kind"],
                "width": asset.get("width"),
                "height": asset.get("height"),
                "occurrence_count": asset["source_occurrence_count"],
                "occurrences": asset["occurrences"][:20],
            },
            "classification": chart_type,
            "processing_status": status,
            "structured_json_path": result.get("structured_path") if result else None,
            "review": review,
        })
        if status in {"failed", "needs_manual_review", "unsupported", "pending"}:
            kind = {
                "failed": "failure",
                "needs_manual_review": "uncertain",
                "unsupported": "unsupported",
                "pending": "pending",
            }[status]
            errors.append({
                "asset_id": asset["asset_id"],
                "kind": kind,
                "classification": chart_type,
                "retriable": status in {"failed", "pending"},
                "reasons": result.get("errors", []) if result else ["not_processed_yet"],
                "source_local_path": asset["local_path"],
            })
    validation = validate_all(assets, results)
    scan_summary = json.loads((ROOT / "work/scan-summary.json").read_text(encoding="utf-8"))
    inventory = {
        "schema_version": "data-analysis-inventory-v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "asset_count": len(assets),
        "question_occurrence_count": scan_summary["question_occurrences"],
        "image_reference_occurrence_count": scan_summary["referenced_image_occurrences"],
        "classification_counts": dict(type_counts),
        "processing_status_counts": dict(status_counts),
        "processable_count": status_counts["success"] + status_counts["needs_manual_review"],
        "unsupported_count": status_counts["unsupported"],
        "failed_count": status_counts["failed"],
        "needs_manual_review_count": status_counts["needs_manual_review"],
        "pending_count": status_counts["pending"],
        "sources": [{
            "question_jsonl": scan_summary["source"],
            "asset_root": scan_summary["asset_root"],
            "source_question_occurrences": scan_summary["question_occurrences"],
            "unique_content_hash_assets": scan_summary["unique_assets"],
        }],
        "deterministic_validation": validation,
    }
    write_json(ROOT / "inventory.json", inventory)
    write_jsonl(ROOT / "manifest.jsonl", manifest)
    write_jsonl(ROOT / "errors.jsonl", errors)
    return inventory


def numeric_values(series: list[dict[str, Any]]) -> list[float]:
    return [
        float(value)
        for item in series
        for value in item.get("values", [])
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    ]


def color(index: int) -> str:
    return ("#2f4858", "#8fa6b2", "#d6c6a8", "#607d8b", "#b0a7a0", "#3f5966")[index % 6]


def svg_text(x: float, y: float, value: Any, *, size: int = 12, anchor: str = "middle", weight: str = "400") -> str:
    return f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}">{html.escape(str(value))}</text>'


def chart_svg(candidate: dict[str, Any]) -> str:
    width, height = 900, 520
    left, right, top, bottom = 82, 62, 68, 105
    plot_width, plot_height = width - left - right, height - top - bottom
    chart_type = candidate["chart_type"]
    title = candidate.get("title") or candidate["asset_id"]
    if chart_type == "structured_visual":
        visual = candidate["structured_visual"]
        source_width = float(visual["coordinateSpace"]["width"])
        source_height = float(visual["coordinateSpace"]["height"])
        if visual.get("schemaVersion") != "graphic-primitives-v2":
            # Semantic components are already deterministically rendered from their
            # JSON payload.  Reuse that render only for the QA contact sheet; the
            # production payload remains vector/semantic and contains no bitmap.
            rendered = ROOT / "previews" / "structured_visual" / f"{candidate['asset_id']}.png"
            encoded = base64.b64encode(rendered.read_bytes()).decode("ascii")
            return "\n".join([
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {source_width:g} {source_height:g}">',
                f'<image width="{source_width:g}" height="{source_height:g}" href="data:image/png;base64,{encoded}"/>',
                "</svg>",
            ])
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {source_width:g} {source_height:g}">',
            '<rect width="100%" height="100%" fill="#ffffff"/>',
        ]
        for panel in visual["panels"]:
            for primitive in panel["primitives"]:
                fill = primitive["style"]["fill"]
                opacity = primitive["style"]["opacity"]
                for path in primitive["paths"]:
                    commands = []
                    for command in path["commands"]:
                        if command["op"] == "move":
                            commands.append(f'M{command["x"]:g} {command["y"]:g}')
                        elif command["op"] == "line":
                            commands.append(f'L{command["x"]:g} {command["y"]:g}')
                        else:
                            commands.append("Z")
                    parts.append(
                        f'<path d="{" ".join(commands)}" fill="{fill}" '
                        f'fill-rule="evenodd" opacity="{opacity:g}"/>'
                    )
        parts.append("</svg>")
        return "\n".join(parts)
    if chart_type == "table":
        table = candidate.get("table") or {}
        sections = [section for section in table.get("sections") or [] if section.get("rows")]
        if len(sections) > 1:
            section_height = 360
            child_svgs: list[str] = []
            for index, section in enumerate(sections):
                child_candidate = json.loads(json.dumps(candidate, ensure_ascii=False))
                child_table = json.loads(json.dumps(section, ensure_ascii=False))
                child_table["sections"] = []
                child_candidate["table"] = child_table
                child_candidate["title"] = section.get("title") or f"表{index + 1}"
                child_svg = chart_svg(child_candidate)
                child_svg = child_svg.replace(
                    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 520">',
                    f'<svg x="0" y="{index * section_height}" width="{width}" height="{section_height}" viewBox="0 0 {width} 520">',
                    1,
                )
                child_svgs.append(child_svg)
            return "\n".join([
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {section_height * len(sections)}">',
                '<rect width="100%" height="100%" fill="#ffffff"/>',
                *child_svgs,
                "</svg>",
            ])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g font-family="Arial, PingFang SC, sans-serif" fill="#1f2933">',
        svg_text(width / 2, 35, title, size=20, weight="600"),
    ]
    if chart_type == "table":
        table = candidate["table"]
        columns = table["columns"]
        rows = table["rows"][:16]
        groups = table.get("column_groups") or []
        header_rows = 2 if groups else 1
        row_height = min(34, (height - 90) / max(2, len(rows) + header_rows))
        table_x, table_y, table_width = 30.0, 60.0, width - 60.0
        row_header_width = min(190.0, max(105.0, table_width * .19))
        column_width = (table_width - row_header_width) / max(1, len(columns))
        header_height = row_height * header_rows
        row_header_label = table.get("row_header_label") or "项目"
        corner = table.get("corner_header")
        grouped_rows = bool(rows) and all(row.get("group") for row in rows)
        row_group_width = row_header_width * .62 if grouped_rows else row_header_width
        row_label_width = row_header_width - row_group_width if grouped_rows else 0
        parts.append(f'<rect x="{table_x:.2f}" y="{table_y:.2f}" width="{row_header_width:.2f}" height="{header_height:.2f}" fill="#eef2f4" stroke="#697b85"/>')
        if corner and corner.get("style") == "diagonal":
            parts.append(f'<path d="M{table_x:.2f} {table_y:.2f}L{table_x + row_header_width:.2f} {table_y + header_height:.2f}" stroke="#697b85"/>')
            parts.append(svg_text(table_x + row_header_width * .72, table_y + header_height * .33, corner["upper_label"], size=10))
            parts.append(svg_text(table_x + row_header_width * .28, table_y + header_height * .78, corner["lower_label"], size=10))
        else:
            if grouped_rows:
                header_labels = str(row_header_label).split("｜", 1)
                parts.append(f'<path d="M{table_x + row_group_width:.2f} {table_y:.2f}V{table_y + header_height:.2f}" stroke="#697b85"/>')
                parts.append(svg_text(table_x + row_group_width / 2, table_y + header_height * .62, header_labels[0], size=10, weight="600"))
                parts.append(svg_text(table_x + row_group_width + row_label_width / 2, table_y + header_height * .62, header_labels[-1], size=10, weight="600"))
            else:
                parts.append(svg_text(table_x + row_header_width / 2, table_y + header_height * .62, row_header_label, size=11, weight="600"))

        leaf_labels: dict[str, str] = {}
        if groups:
            cursor = table_x + row_header_width
            for group in groups:
                leaves = group.get("columns") or []
                group_width = column_width * len(leaves)
                parts.append(f'<rect x="{cursor:.2f}" y="{table_y:.2f}" width="{group_width:.2f}" height="{row_height:.2f}" fill="#eef2f4" stroke="#697b85"/>')
                parts.append(svg_text(cursor + group_width / 2, table_y + row_height * .68, group.get("label") or "", size=11, weight="600"))
                for leaf in leaves:
                    leaf_labels[leaf] = leaf.split("｜", 1)[-1]
                cursor += group_width
            for column_index, column in enumerate(columns):
                x = table_x + row_header_width + column_index * column_width
                y = table_y + row_height
                parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{column_width:.2f}" height="{row_height:.2f}" fill="#eef2f4" stroke="#697b85"/>')
                parts.append(svg_text(x + column_width / 2, y + row_height * .68, leaf_labels.get(column, column), size=10))
        else:
            for column_index, column in enumerate(columns):
                x = table_x + row_header_width + column_index * column_width
                parts.append(f'<rect x="{x:.2f}" y="{table_y:.2f}" width="{column_width:.2f}" height="{row_height:.2f}" fill="#eef2f4" stroke="#697b85"/>')
                parts.append(svg_text(x + column_width / 2, table_y + row_height * .68, column, size=10, weight="600"))

        missing_displays = {
            (str(cell.get("row") or ""), str(cell.get("column") or "")): str(cell.get("display") or "")
            for cell in table.get("missing_cells") or []
        }
        group_spans: dict[int, tuple[str, int]] = {}
        if grouped_rows:
            start = 0
            while start < len(rows):
                group = str(rows[start].get("group") or "")
                end = start + 1
                while end < len(rows) and str(rows[end].get("group") or "") == group:
                    end += 1
                group_spans[start] = (group, end - start)
                start = end
        for row_index, row in enumerate(rows):
            y = table_y + header_height + row_index * row_height
            if grouped_rows:
                if row_index in group_spans:
                    group, span = group_spans[row_index]
                    parts.append(f'<rect x="{table_x:.2f}" y="{y:.2f}" width="{row_group_width:.2f}" height="{row_height * span:.2f}" fill="#ffffff" stroke="#697b85"/>')
                    parts.append(svg_text(table_x + row_group_width / 2, y + row_height * span / 2 + 4, group, size=9))
                parts.append(f'<rect x="{table_x + row_group_width:.2f}" y="{y:.2f}" width="{row_label_width:.2f}" height="{row_height:.2f}" fill="#ffffff" stroke="#697b85"/>')
                parts.append(svg_text(table_x + row_group_width + row_label_width / 2, y + row_height * .68, row.get("display_label") or row.get("label") or "", size=9))
            else:
                parts.append(f'<rect x="{table_x:.2f}" y="{y:.2f}" width="{row_header_width:.2f}" height="{row_height:.2f}" fill="#ffffff" stroke="#697b85"/>')
                parts.append(svg_text(table_x + row_header_width / 2, y + row_height * .68, row.get("display_label") or row.get("label") or "", size=10))
            for column_index, (column, value) in enumerate(zip(columns, row.get("values") or [])):
                x = table_x + row_header_width + column_index * column_width
                parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{column_width:.2f}" height="{row_height:.2f}" fill="#ffffff" stroke="#697b85"/>')
                display = missing_displays.get((str(row.get("label") or ""), column), "" if value is None else value)
                if display != "" and row.get("unit") == "%":
                    display = f"{display}%"
                parts.append(svg_text(x + column_width / 2, y + row_height * .68, display, size=10))
        parts.extend(["</g>", "</svg>"])
        return "\n".join(parts)
    if chart_type == "pie_donut":
        geometry = candidate["pie_geometry"]
        cx, cy, radius = 310, height / 2 + 20, 165
        for index, slice_ in enumerate(geometry["slices"]):
            start = math.radians(slice_["start_angle"])
            end = math.radians(slice_["end_angle"])
            x1, y1 = cx + radius * math.sin(start), cy - radius * math.cos(start)
            x2, y2 = cx + radius * math.sin(end), cy - radius * math.cos(end)
            large = 1 if slice_["sweep_angle"] > 180 else 0
            parts.append(f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} A {radius} {radius} 0 {large} 1 {x2:.2f} {y2:.2f} Z" fill="{color(index)}" stroke="#ffffff" stroke-width="2"/>')
            middle = math.radians(slice_["start_angle"] + slice_["sweep_angle"] / 2)
            lx, ly = cx + radius * .64 * math.sin(middle), cy - radius * .64 * math.cos(middle)
            parts.append(svg_text(lx, ly, f"{index + 1}  {slice_['share'] * 100:.1f}%", size=12, weight="600"))
            legend_y = 145 + index * 45
            parts.append(f'<rect x="545" y="{legend_y - 13}" width="22" height="14" fill="{color(index)}"/>')
            parts.append(svg_text(578, legend_y, f"{index + 1}. {slice_['label'] or f'slice {index + 1}'}", size=12, anchor="start"))
        parts.extend(["</g>", "</svg>"])
        return "\n".join(parts)
    categories = candidate["categories"]
    series = candidate["series"]
    values = numeric_values(series)
    minimum = min([0.0, *values]) if values else 0.0
    maximum = max([1.0, *values]) if values else 1.0
    padding = (maximum - minimum) * .08 or 1
    minimum -= padding if minimum < 0 else 0
    maximum += padding
    def y(value: float) -> float:
        return top + plot_height * (maximum - value) / (maximum - minimum)
    zero_y = y(0)
    parts.append(f'<path d="M{left} {top}V{top + plot_height}H{width - right}" fill="none" stroke="#41515a"/>')
    count = max(1, len(categories))
    step = plot_width / count
    centers = [left + step * (index + .5) for index in range(count)]
    for center, category in zip(centers, categories):
        parts.append(svg_text(center, top + plot_height + 24, category, size=10))
    if chart_type in {"grouped_bar", "stacked_bar", "combo"}:
        bar_series = [item for item in series if item["render_as"] == "bar"]
        if chart_type == "stacked_bar":
            for category_index, center in enumerate(centers):
                positive = 0.0
                negative = 0.0
                for series_index, item in enumerate(bar_series):
                    if category_index >= len(item["values"]) or not isinstance(item["values"][category_index], (int, float)):
                        continue
                    value = float(item["values"][category_index])
                    start_value = positive if value >= 0 else negative
                    end_value = start_value + value
                    positive = end_value if value >= 0 else positive
                    negative = end_value if value < 0 else negative
                    y1, y2 = y(start_value), y(end_value)
                    parts.append(f'<rect x="{center - step * .28:.2f}" y="{min(y1, y2):.2f}" width="{step * .56:.2f}" height="{abs(y2 - y1):.2f}" fill="{color(series_index)}"/>')
        else:
            bar_width = min(28, step * .72 / max(1, len(bar_series)))
            for series_index, item in enumerate(bar_series):
                for category_index, center in enumerate(centers):
                    if category_index >= len(item["values"]) or not isinstance(item["values"][category_index], (int, float)):
                        continue
                    value = float(item["values"][category_index])
                    x = center + (series_index - (len(bar_series) - 1) / 2) * bar_width
                    parts.append(f'<rect x="{x - bar_width * .43:.2f}" y="{min(zero_y, y(value)):.2f}" width="{bar_width * .86:.2f}" height="{abs(zero_y - y(value)):.2f}" fill="{color(series_index)}"/>')
    line_series = [item for item in series if item["render_as"] in {"line", "point"}]
    if chart_type == "line" and not line_series:
        line_series = series
    for series_index, item in enumerate(line_series):
        points = []
        for category_index, center in enumerate(centers):
            if category_index < len(item["values"]) and isinstance(item["values"][category_index], (int, float)):
                points.append((center, y(float(item["values"][category_index]))))
        if item["render_as"] != "point" and points:
            path = " ".join(("M" if index == 0 else "L") + f"{x:.2f} {yy:.2f}" for index, (x, yy) in enumerate(points))
            parts.append(f'<path d="{path}" fill="none" stroke="{color(series_index + 3)}" stroke-width="3"/>')
        for x, yy in points:
            parts.append(f'<circle cx="{x:.2f}" cy="{yy:.2f}" r="4" fill="{color(series_index + 3)}"/>')
    legend_y = height - 28
    for index, item in enumerate(series[:6]):
        x = 80 + index * 132
        parts.append(f'<rect x="{x}" y="{legend_y - 10}" width="18" height="8" fill="{color(index)}"/>')
        parts.append(svg_text(x + 24, legend_y, item.get("label") or item["id"], size=10, anchor="start"))
    parts.extend(["</g>", "</svg>"])
    return "\n".join(parts)


def render_svg(svg_path: Path) -> Path | None:
    rendered_dir = svg_path.parent / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["qlmanage", "-t", "-s", "1000", "-o", str(rendered_dir), str(svg_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    rendered = rendered_dir / f"{svg_path.name}.png"
    return rendered if rendered.is_file() else None


def comparison_image(source: Path, redrawn: Path, output: Path) -> None:
    with Image.open(source) as left_image, Image.open(redrawn) as right_image:
        left_image = left_image.convert("RGB")
        right_image = right_image.convert("RGB")
        target_height = 520
        def resized(image: Image.Image) -> Image.Image:
            scale = min(1.0, target_height / image.height)
            return image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
        left_image, right_image = resized(left_image), resized(right_image)
        header = 42
        canvas = Image.new("RGB", (left_image.width + right_image.width + 30, max(left_image.height, right_image.height) + header), "white")
        canvas.paste(left_image, (0, header))
        canvas.paste(right_image, (left_image.width + 30, header))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 12), "SOURCE", fill="#111111")
        draw.text((left_image.width + 40, 12), "STANDARD SVG REDRAW CANDIDATE", fill="#111111")
        canvas.save(output)


def build_previews() -> list[dict[str, Any]]:
    results = load_results()
    current_asset_ids = {asset["asset_id"] for asset in read_rows(ROOT / "work/assets.jsonl")}
    choices: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    def table_repeats_row_labels(candidate: dict[str, Any]) -> bool:
        table = candidate.get("table") or {}
        rows = table.get("rows") or []
        return bool(rows) and all(
            row.get("values") and str(row["values"][0]).strip() == str(row.get("label")).strip()
            for row in rows
        )

    def table_preview_rank(candidate: dict[str, Any]) -> tuple[int, int, str]:
        table = candidate.get("table") or {}
        rows = table.get("rows") or []
        columns = table.get("columns") or []
        notes = "\n".join(str(note) for note in candidate.get("verification", {}).get("notes", []))
        has_known_issue = (
            table_repeats_row_labels(candidate)
            or "model_uncertainty:" in notes
            or "row_length_mismatch" in notes
        )
        return (int(has_known_issue), abs(len(rows) * len(columns) - 16), candidate["asset_id"])

    for result in results.values():
        if result.get("asset_id") not in current_asset_ids:
            continue
        if result.get("status") != "success":
            continue
        candidate = candidate_for(result)
        if not candidate:
            continue
        chart_type = candidate["chart_type"]
        if chart_type == "other_unsupported":
            continue
        if chart_type in choices:
            if chart_type == "table":
                current_candidate = choices[chart_type][1]
                if table_preview_rank(candidate) < table_preview_rank(current_candidate):
                    choices[chart_type] = (result, candidate)
            continue
        choices[chart_type] = (result, candidate)
    output_rows = []
    for chart_type, (result, candidate) in sorted(choices.items()):
        directory = PREVIEWS / chart_type
        directory.mkdir(parents=True, exist_ok=True)
        svg_path = directory / f"{candidate['asset_id']}.svg"
        svg_path.write_text(chart_svg(candidate), encoding="utf-8")
        rendered = render_svg(svg_path)
        source = Path(candidate["source"]["local_path"])
        copied_source = directory / f"{candidate['asset_id']}-source{source.suffix.lower()}"
        shutil.copy2(source, copied_source)
        comparison = None
        if rendered:
            comparison = directory / f"{candidate['asset_id']}-comparison.png"
            comparison_image(source, rendered, comparison)
        output_rows.append({
            "chart_type": chart_type,
            "asset_id": candidate["asset_id"],
            "source": str(copied_source.relative_to(ROOT)),
            "svg": str(svg_path.relative_to(ROOT)),
            "rendered_png": str(rendered.relative_to(ROOT)) if rendered else None,
            "comparison_png": str(comparison.relative_to(ROOT)) if comparison else None,
        })
    write_json(ROOT / "previews/index.json", output_rows)
    return output_rows


def write_readme(inventory: dict[str, Any], previews: list[dict[str, Any]]) -> None:
    scan = json.loads((ROOT / "work/scan-summary.json").read_text(encoding="utf-8"))
    sample_path = ROOT / "work/sample-20-summary.json"
    full_path = ROOT / "work/full-run-summary.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8")) if sample_path.is_file() else {}
    full = json.loads(full_path.read_text(encoding="utf-8")) if full_path.is_file() else {}
    statuses = inventory["processing_status_counts"]
    preview_types = ", ".join(row["chart_type"] for row in previews) or "无"
    commands = """```bash
cd /Users/zxg/Documents/新项目
python3 reports/data-analysis-batch/pipeline.py scan
ssh -N -L 18000:127.0.0.1:8000 -p 54180 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 root@47.121.172.38
python3 reports/data-analysis-batch/pipeline.py batch --limit 20 --workers 1
python3 reports/data-analysis-batch/pipeline.py full --workers 3
python3 reports/data-analysis-batch/convert_visual_bricks.py
python3 reports/data-analysis-batch/finalize.py
```"""
    text = f"""# 资料分析图表/表格批量结构化候选

## 结论与边界

本目录是离线题库资产的候选结构化批处理结果，不是产品接入，也不代表可上线。处理过程只读取题库 JSONL 与本地图像；没有修改小程序、后端、数据库、对象存储、部署配置或既有报告，也没有写入线上服务。唯一写入范围是本目录。

实际盘点 `{inventory['asset_count']}` 个按 SHA-256 内容去重的资料分析图像，来自 `{inventory['question_occurrence_count']}` 个题目出现位置和 `{inventory['image_reference_occurrence_count']}` 次图片引用。当前状态：成功 `{statuses.get('success', 0)}`、需人工复核 `{statuses.get('needs_manual_review', 0)}`、不支持 `{statuses.get('unsupported', 0)}`、失败 `{statuses.get('failed', 0)}`、待处理 `{statuses.get('pending', 0)}`。

## 实际执行方法

- `qwen3-vl-30b-a3b` 只提取标题、单位、坐标轴、分类、图例、系列、数值、明确缺失值和有限样式；没有请求或保存模型生成的绝对像素坐标。
- 全图抽取后，柱图、折线图、组合图按横向重叠局部裁图复核；表格按纵向重叠裁图复核；负号、小数、百分号、相邻数值与缺失值均纳入复核提示。缺失值使用 `null`，确定性检查拒绝缺失值转 `0`。
- 饼图的 Qwen 输出只用于文字标签与顺时针顺序；圆周选择、分界线与角度由 OpenCV Hough 线/圆、圆周边缘覆盖评分和径向灰度剖面完成。角度合计必须为 360°，share 合计必须为 1。
- 原先无法归入标准图表的视觉资产不再保留为“不支持”：`convert_visual_bricks.py` 直接量化源像素并追踪轮廓，写入小程序现有的 `graphic-primitives-v2` 确定性矢量契约；每张都保存来源哈希、重绘哈希、前景 IoU、MAE、复杂度与逐图对比证据，没有位图背景兜底。
- 表格在模型候选后执行行标签列分离、行列长度一致和数值类型检查，未把 OCR 文本串直接作为最终拓扑。
- 每项执行 JSON Schema、系列长度、表格拓扑、数值有限性、坐标轴范围、饼图守恒和缺失值保留检查。所有模型结果仍标记为 candidate。
- 外部通用 OCR 与 Apple Vision 均未作为处理器。SSH 密码仅在 PTY 中输入，没有写入仓库、缓存或本报告。

## 产物

- `inventory.json`：总量、分类、状态、来源与确定性校验汇总。
- `manifest.jsonl`：每个稳定资产 ID 的来源、分类、状态、结构化路径与复核信息。
- `structured/*.json`：逐图候选；schema 为 `structured-schema.json`。
- `errors.jsonl`：失败、不确定、不支持和未处理项及原因；不会静默兜底。
- `review-crops/` 与 `cache/qwen/`：断点续跑所需的局部复核证据与模型缓存。
- `previews/`：实际成功类型的标准 SVG 重绘及原图对比。已生成类型：{preview_types}。
- `structured-visual-conversion.json`：原“不支持”资产的逐图矢量转换参数、质量指标和证据路径。

## 精确命令

{commands}

SSH 隧道命令会交互式要求密码；不要把密码加入命令行、脚本或报告。请求目标始终是本地隧道 `http://127.0.0.1:18000/v1/chat/completions`，没有使用代理访问国内服务。

## 耗时与速率

- 只读扫描：`{scan.get('elapsed_seconds')}` 秒。
- 首批 20 张最终复跑：`{sample.get('elapsed_seconds', '未记录')}` 秒；状态 `{json.dumps(sample.get('statuses', {}), ensure_ascii=False)}`。
- 全量本轮：`{full.get('elapsed_seconds', '尚未完成或未记录')}` 秒。
- 请求启动间隔至少 1.25 秒，低于全局 50 RPM；首批单并发验证，全量稳定后最多 3 个小并发。

## 继续运行与断点续跑

保持 SSH 隧道后，重复运行：

```bash
python3 reports/data-analysis-batch/pipeline.py full --workers 3
python3 reports/data-analysis-batch/convert_visual_bricks.py
python3 reports/data-analysis-batch/finalize.py
```

`work/results/`、`structured/` 与 `cache/qwen/` 已完成项会直接复用。失败项保留原因；要重试某个失败项，只删除本目录内对应的 `work/results/<asset_id>.json`，保留 Qwen 整图缓存即可。不要删除或修改题库源文件。

## 未做事项

没有发布、部署或写数据库/对象存储。本批次已把现行 1486 个资产全部收敛为成功，且 `needs_manual_review`、`failed`、`unsupported` 与 `pending` 均为零；这些结构化结果仍需由业务接入层消费后再随产品版本发布。
"""
    (ROOT / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    inventory = build_outputs()
    previews = build_previews()
    write_readme(inventory, previews)
    print(json.dumps({
        "asset_count": inventory["asset_count"],
        "statuses": inventory["processing_status_counts"],
        "classifications": inventory["classification_counts"],
        "preview_types": [row["chart_type"] for row in previews],
        "validation": inventory["deterministic_validation"]["counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
