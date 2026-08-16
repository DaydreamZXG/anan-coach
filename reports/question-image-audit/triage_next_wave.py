#!/usr/bin/env python3
"""Build a conservative visual-family inventory for the next-wave images.

This script does not claim semantic correctness. It extracts deterministic image
features, assigns a review candidate family, and creates contact sheets so a
human can validate each family before any structured conversion is attempted.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "next-wave-manifest.jsonl"
INVENTORY = ROOT / "next-wave-visual-inventory.jsonl"
SUMMARY = ROOT / "next-wave-visual-summary.json"
SHEET_ROOT = ROOT / "next-wave-contact-sheets"

THUMB_W = 260
THUMB_H = 190
LABEL_H = 54
COLS = 4
ROWS = 4
PER_SHEET = COLS * ROWS


def load_rows() -> list[dict]:
    rows = []
    with MANIFEST.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def entropy(gray: np.ndarray) -> float:
    hist = np.bincount(gray.reshape(-1), minlength=256).astype(np.float64)
    probs = hist[hist > 0] / hist.sum()
    return float(-(probs * np.log2(probs)).sum())


def line_features(binary_ink: np.ndarray) -> tuple[int, int, float]:
    height, width = binary_ink.shape
    min_len = max(18, min(height, width) // 4)
    lines = cv2.HoughLinesP(
        binary_ink,
        1,
        np.pi / 180,
        threshold=max(18, min_len // 2),
        minLineLength=min_len,
        maxLineGap=4,
    )
    horizontal = 0
    vertical = 0
    total_length = 0.0
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            dx = abs(int(x2) - int(x1))
            dy = abs(int(y2) - int(y1))
            total_length += math.hypot(dx, dy)
            if dx >= max(8, 4 * dy):
                horizontal += 1
            elif dy >= max(8, 4 * dx):
                vertical += 1
    area = max(1, height * width)
    return horizontal, vertical, round(total_length / area, 5)


def analyze_image(path: Path) -> dict:
    with Image.open(path) as source:
        rgb = ImageOps.exif_transpose(source).convert("RGB")
        width, height = rgb.size
        preview = rgb.copy()
        preview.thumbnail((640, 640), Image.Resampling.LANCZOS)
        array = np.asarray(preview)

    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(array, cv2.COLOR_RGB2HSV)
    edges = cv2.Canny(gray, 80, 180)
    binary_ink = np.where(gray < 225, 255, 0).astype(np.uint8)
    horizontal, vertical, line_coverage = line_features(binary_ink)

    ink_fraction = float(np.mean(gray < 235))
    dark_fraction = float(np.mean(gray < 96))
    color_fraction = float(np.mean((hsv[:, :, 1] > 38) & (hsv[:, :, 2] > 45)))
    edge_density = float(np.mean(edges > 0))
    aspect = width / max(1, height)

    return {
        "width": width,
        "height": height,
        "aspectRatio": round(aspect, 4),
        "inkFraction": round(ink_fraction, 5),
        "darkFraction": round(dark_fraction, 5),
        "colorFraction": round(color_fraction, 5),
        "edgeDensity": round(edge_density, 5),
        "entropy": round(entropy(gray), 4),
        "horizontalLineCount": horizontal,
        "verticalLineCount": vertical,
        "lineCoverage": line_coverage,
    }


def candidate_family(row: dict, feature: dict) -> tuple[str, list[str]]:
    """Return a deliberately conservative candidate family and evidence."""
    tracks = set(row.get("tracks") or [])
    sections = " ".join(row.get("sections") or [])
    knowledge = " ".join(row.get("knowledgePoints") or [])
    context = f"{sections} {knowledge}"
    evidence: list[str] = []

    color = feature["colorFraction"]
    ent = feature["entropy"]
    edge = feature["edgeDensity"]
    ink = feature["inkFraction"]
    hlines = feature["horizontalLineCount"]
    vlines = feature["verticalLineCount"]
    aspect = feature["aspectRatio"]

    if color >= 0.13 and ent >= 5.0:
        evidence.extend(["substantial_color", "high_visual_entropy"])
        return "photo_map_artifact_candidate", evidence

    if hlines >= 4 and vlines >= 3:
        evidence.extend(["repeated_horizontal_lines", "repeated_vertical_lines"])
        return "table_grid_candidate", evidence

    if "知觉速度" in sections or "perceptual" in tracks:
        evidence.append("perceptual_speed_context")
        return "symbol_or_text_grid_candidate", evidence

    if any(token in context for token in ("几何", "立体", "平面", "图形", "路线", "位置")):
        evidence.append("diagram_knowledge_context")
        return "geometry_or_logic_diagram_candidate", evidence

    if any(token in context for token in ("实验", "物理", "化学", "生物", "地理", "地图")):
        evidence.append("science_or_map_context")
        return "science_diagram_or_map_candidate", evidence

    if aspect >= 4.5 or aspect <= 0.22:
        evidence.append("extreme_aspect_ratio")
        return "text_strip_or_long_diagram_candidate", evidence

    if color < 0.03 and edge >= 0.035 and ink <= 0.38:
        evidence.extend(["near_monochrome", "line_art_profile"])
        return "monochrome_diagram_candidate", evidence

    if color < 0.03 and ink > 0.38:
        evidence.extend(["near_monochrome", "dense_ink"])
        return "dense_text_or_symbol_candidate", evidence

    evidence.append("ambiguous_deterministic_features")
    return "needs_visual_review", evidence


def safe_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def ellipsize(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def make_sheets(grouped: dict[str, list[dict]]) -> dict[str, int]:
    SHEET_ROOT.mkdir(parents=True, exist_ok=True)
    font = safe_font(17)
    small = safe_font(14)
    counts: dict[str, int] = {}

    for family, rows in sorted(grouped.items()):
        family_root = SHEET_ROOT / family
        family_root.mkdir(parents=True, exist_ok=True)
        counts[family] = math.ceil(len(rows) / PER_SHEET)
        for page, start in enumerate(range(0, len(rows), PER_SHEET), 1):
            page_rows = rows[start : start + PER_SHEET]
            sheet = Image.new(
                "RGB",
                (COLS * THUMB_W, ROWS * (THUMB_H + LABEL_H)),
                "white",
            )
            draw = ImageDraw.Draw(sheet)
            for index, row in enumerate(page_rows):
                col = index % COLS
                grid_row = index // COLS
                x = col * THUMB_W
                y = grid_row * (THUMB_H + LABEL_H)
                path = Path(row["localPath"])
                try:
                    with Image.open(path) as source:
                        image = ImageOps.exif_transpose(source).convert("RGB")
                        image.thumbnail((THUMB_W - 16, THUMB_H - 16), Image.Resampling.LANCZOS)
                        px = x + (THUMB_W - image.width) // 2
                        py = y + (THUMB_H - image.height) // 2
                        sheet.paste(image, (px, py))
                except Exception as exc:  # keep the inventory reviewable
                    draw.text((x + 8, y + 8), f"OPEN ERROR: {exc}", fill="#b91c1c", font=small)

                draw.rectangle((x, y, x + THUMB_W - 1, y + THUMB_H + LABEL_H - 1), outline="#cbd5e1")
                tracks = ",".join(row.get("tracks") or [])
                label_1 = f"{row['assetId']}  {tracks}"
                label_2 = ellipsize(" / ".join(row.get("knowledgePoints") or []), 31)
                draw.text((x + 7, y + THUMB_H + 4), label_1, fill="#111827", font=small)
                draw.text((x + 7, y + THUMB_H + 27), label_2, fill="#475569", font=small)

            filename = family_root / f"sheet-{page:03d}.jpg"
            sheet.save(filename, quality=88, optimize=True)
    return counts


def main() -> None:
    source_rows = load_rows()
    output_rows: list[dict] = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    failures: list[dict] = []

    for row in source_rows:
        path = Path(row["localPath"])
        try:
            feature = analyze_image(path)
            family, evidence = candidate_family(row, feature)
        except Exception as exc:
            feature = {}
            family = "needs_visual_review"
            evidence = ["image_analysis_failed"]
            failures.append({"assetId": row["assetId"], "error": str(exc)})

        enriched = {
            **row,
            "visualFamilyCandidate": family,
            "visualFamilyEvidence": evidence,
            "visualFeatures": feature,
            "routingState": "candidate_family_needs_human_validation",
        }
        output_rows.append(enriched)
        grouped[family].append(enriched)

    with INVENTORY.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    sheet_counts = make_sheets(grouped)
    summary = {
        "sourceManifest": str(MANIFEST),
        "assetCount": len(output_rows),
        "analysisFailureCount": len(failures),
        "familyCounts": dict(sorted(Counter(row["visualFamilyCandidate"] for row in output_rows).items())),
        "contactSheetCounts": sheet_counts,
        "routingState": "candidate_family_needs_human_validation",
        "note": "Candidate families are deterministic triage hints, not approved production routing.",
        "failures": failures,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
