#!/usr/bin/env python3
"""Build labeled contact sheets for real DevTools screenshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--directory", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--rows", type=int, default=4)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    allowed_root = (repo_root / "audit/subagents/c").resolve()
    output_root = args.output.resolve()
    if allowed_root not in output_root.parents:
        raise SystemExit(f"Output must be below {allowed_root}")

    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        records = [
            record
            for record in manifest["records"]
            if record["status"].startswith("captured") and record["screenshot"]
        ]
        expected_viewport = manifest.get("expectedViewport") or {}
        source_width = int(
            expected_viewport.get("width", manifest["systemInfo"]["windowWidth"])
        )
        source_height = int(
            expected_viewport.get("height", manifest["systemInfo"]["windowHeight"])
        )
    else:
        screenshots = sorted(args.directory.glob("*.png"))
        if not screenshots:
            raise SystemExit(f"No PNG screenshots in {args.directory}")
        with Image.open(screenshots[0]) as first:
            source_width, source_height = first.size
        records = [
            {
                "id": screenshot.stem,
                "routePath": "(read-only existing baseline)",
                "screenshotPath": screenshot,
            }
            for screenshot in screenshots
        ]
    output_root.mkdir(parents=True, exist_ok=True)

    thumb_width = 225
    header_height = 46
    thumb_height = round(source_height * thumb_width / source_width)
    cell_width = thumb_width + 16
    cell_height = thumb_height + header_height + 16
    page_size = args.columns * args.rows
    font = ImageFont.load_default()
    output_paths: list[str] = []

    for page_number, start in enumerate(range(0, len(records), page_size), 1):
        page_records = records[start : start + page_size]
        sheet = Image.new(
            "RGB",
            (cell_width * args.columns, cell_height * args.rows),
            "#202124",
        )
        draw = ImageDraw.Draw(sheet)
        for offset, record in enumerate(page_records):
            row, column = divmod(offset, args.columns)
            left = column * cell_width + 8
            top = row * cell_height + header_height
            screenshot_path = (
                repo_root / record["screenshot"]
                if "screenshot" in record
                else record["screenshotPath"]
            )
            with Image.open(screenshot_path) as source_image:
                screenshot = source_image.convert("RGB")
            screenshot.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
            sheet.paste(screenshot, (left, top))
            label = f"{start + offset + 1:03d} {record['id']}"
            route = record["routePath"]
            draw.text((left, row * cell_height + 8), label, fill="#ffffff", font=font)
            draw.text(
                (left, row * cell_height + 25),
                route[:38],
                fill="#b7bdc8",
                font=font,
            )
        output_path = output_root / f"sheet-{page_number:02d}.jpg"
        sheet.save(output_path, quality=90, optimize=True)
        output_paths.append(str(output_path))

    print(
        json.dumps(
            {
                "decodedScreenshots": len(records),
                "generatedSheets": len(output_paths),
                "viewport": {"width": source_width, "height": source_height},
                "output": str(output_root),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
