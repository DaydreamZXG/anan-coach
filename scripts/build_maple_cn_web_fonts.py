#!/usr/bin/env python3
"""Build the two Maple Mono CN assets used by the WeChat product."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from fontTools.ttLib import TTFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "apps/ios/Gongkao/Gongkao/Resources/Fonts"
SELECTED_FACES = (
    (
        "MapleMono-CN-Regular.ttf",
        "maple-mono-cn-regular-v7.9.woff",
        400,
        "0fcc5139bfd5ab14ff989bb7dfd5236d0cafc10e60b9828569e6883613ab4085",
    ),
    (
        "MapleMono-CN-Bold.ttf",
        "maple-mono-cn-bold-v7.9.woff",
        700,
        "ca79870485fb382e01f4e70edb08bccd3cf1dbdf74b82023fdf2f0c95db55725",
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for source_name, output_name, expected_weight, expected_source_hash in SELECTED_FACES:
        source = SOURCE_DIR / source_name
        if not source.is_file():
            raise FileNotFoundError(f"Missing vendored font source: {source}")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if source_hash != expected_source_hash:
            raise ValueError(
                f"Source hash mismatch for {source_name}: {source_hash}, expected {expected_source_hash}"
            )

        font = TTFont(source, recalcTimestamp=False)
        actual_weight = font["OS/2"].usWeightClass
        if actual_weight != expected_weight:
            raise ValueError(
                f"Unexpected weight for {source_name}: {actual_weight}, expected {expected_weight}"
            )
        if not has_family_name(font, "Maple Mono CN"):
            raise ValueError(f"Unexpected family name in {source_name}")

        destination = args.output_dir / output_name
        font.flavor = "woff"
        font.save(destination, reorderTables=False)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        print(f"{digest}  {destination}")


def has_family_name(font: TTFont, expected: str) -> bool:
    for record in font["name"].names:
        if record.nameID not in (1, 16):
            continue
        try:
            if record.toUnicode() == expected:
                return True
        except UnicodeDecodeError:
            continue
    return False


if __name__ == "__main__":
    main()
