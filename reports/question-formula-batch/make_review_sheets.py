#!/usr/bin/env python3
"""Render source/candidate/OCR contact sheets for human formula review."""

from __future__ import annotations

import argparse
import json
import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_ids(path: Path) -> set[str]:
    payload = load_json(path)
    return {row["asset_id"] for row in payload.get("accepted", [])}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def fit_image(path: Path, width: int, height: int) -> Image.Image:
    with Image.open(path) as raw:
        rgba = raw.convert("RGBA")
    image = Image.new("RGBA", rgba.size, "white")
    image.alpha_composite(rgba)
    image = image.convert("RGB")
    scale = min(width / image.width, height / image.height, 3.0)
    target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(target, Image.Resampling.LANCZOS)


def wrap(value: str, width: int = 90) -> list[str]:
    return textwrap.wrap(value, width=width, replace_whitespace=False) or [""]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exclude-report", type=Path)
    parser.add_argument("--list-key", choices=("accepted", "rejected"), default="accepted")
    parser.add_argument("--reason")
    parser.add_argument("--rows", type=int, default=12)
    args = parser.parse_args()

    accepted = load_json(args.report).get(args.list_key, [])
    excluded = load_ids(args.exclude_report) if args.exclude_report else set()
    rows = [row for row in accepted if row["asset_id"] not in excluded]
    if args.reason:
        rows = [row for row in rows if row.get("reason") == args.reason]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for old in args.output_dir.glob("sheet-*.png"):
        old.unlink()

    card_w, card_h, columns = 1500, 250, 2
    sheet_size = args.rows * columns
    title_font, text_font, ocr_font = font(20), font(24), font(21)
    index = []

    for sheet_no in range(math.ceil(len(rows) / sheet_size)):
        subset = rows[sheet_no * sheet_size : (sheet_no + 1) * sheet_size]
        canvas = Image.new("RGB", (card_w * columns, card_h * args.rows), "white")
        draw = ImageDraw.Draw(canvas)
        for offset, row in enumerate(subset):
            col, line = offset % columns, offset // columns
            x, y = col * card_w, line * card_h
            asset_id = row["asset_id"]
            result = load_json(args.results / f"{asset_id}.json")
            source_path = Path(result["source_path"])
            source = fit_image(source_path, width=card_w - 40, height=105)
            canvas.paste(source, (x + 20, y + 8))
            current_y = y + 118
            draw.text(
                (x + 20, current_y),
                (
                    f"{asset_id} conf={row.get('qwen_confidence', 0)}"
                    + (f" reason={row['reason']}" if row.get("reason") else "")
                ),
                fill="#6b7280",
                font=title_font,
            )
            current_y += 28
            for text_line in wrap(row.get("expression", ""))[:2]:
                draw.text((x + 20, current_y), text_line, fill="#111827", font=text_font)
                current_y += 29
            ocr_text = "OCR " + row.get("ocr_text", "")
            for text_line in wrap(ocr_text)[:2]:
                draw.text((x + 20, current_y), text_line, fill="#0891b2", font=ocr_font)
                current_y += 25
            draw.line((x, y + card_h - 1, x + card_w, y + card_h - 1), fill="#e5e7eb")
            index.append({"sheet": sheet_no + 1, "asset_id": asset_id})

        output = args.output_dir / f"sheet-{sheet_no + 1:02d}.png"
        canvas.save(output, optimize=True)

    with (args.output_dir / "index.json").open("w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"row_count": len(rows), "sheet_count": math.ceil(len(rows) / sheet_size)}))


if __name__ == "__main__":
    main()
