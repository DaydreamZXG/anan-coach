#!/usr/bin/env python3
"""Hash exported visual assets for review-only duplicate clusters.

The script reads asset refs exported from compaction.joint_exam_visual_asset_refs_v1
and writes:
  - one row per image with file metadata, SHA-256, aHash, and dHash
  - one row per candidate group with per-question image sequence signatures

It does not mutate the database or source assets.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from PIL import Image


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bits_to_hex(bits: Iterable[bool]) -> str:
    value = 0
    count = 0
    out: list[str] = []
    for bit in bits:
        value = (value << 1) | int(bit)
        count += 1
        if count == 4:
            out.append(format(value, "x"))
            value = 0
            count = 0
    if count:
        value <<= 4 - count
        out.append(format(value, "x"))
    return "".join(out)


def average_hash(image: Image.Image, size: int = 8) -> str:
    gray = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    return bits_to_hex(pixel >= avg for pixel in pixels)


def difference_hash(image: Image.Image, width: int = 9, height: int = 8) -> str:
    gray = image.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    bits: list[bool] = []
    for row in range(height):
        offset = row * width
        for col in range(width - 1):
            bits.append(pixels[offset + col] > pixels[offset + col + 1])
    return bits_to_hex(bits)


def sequence_digest(rows: list[dict[str, str]], field: str) -> str:
    digest = hashlib.sha256()
    for row in rows:
        parts = [
            row["part_kind"],
            row["part_index"],
            row["image_ordinal"],
            row.get(field, ""),
        ]
        digest.update("\t".join(parts).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def visual_signature(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row["candidate_group_id"],
        row["question_id"],
        row["part_kind"],
        f"{int(row['part_index']):04d}",
        f"{int(row['image_ordinal']):04d}",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="reports/question-bank/joint-exam-visual-asset-refs-v1-20260702.csv",
    )
    parser.add_argument(
        "--hash-output",
        default="reports/question-bank/joint-exam-visual-asset-hashes-v1-20260702.csv",
    )
    parser.add_argument(
        "--summary-output",
        default="reports/question-bank/joint-exam-visual-asset-hash-summary-v1-20260702.csv",
    )
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    input_path = root / args.input
    hash_output_path = root / args.hash_output
    summary_output_path = root / args.summary_output

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        refs = list(csv.DictReader(handle))

    hash_rows: list[dict[str, str]] = []
    for row in sorted(refs, key=visual_signature):
        local_relative_path = row["local_relative_path"]
        path = root / local_relative_path
        out = dict(row)
        out["local_absolute_path"] = str(path)
        out["exists"] = str(path.exists()).lower()
        out["size_bytes"] = ""
        out["image_width"] = ""
        out["image_height"] = ""
        out["image_mode"] = ""
        out["sha256"] = ""
        out["ahash_8x8"] = ""
        out["dhash_8x8"] = ""
        out["error"] = ""

        if not path.exists():
            out["error"] = "missing_file"
            hash_rows.append(out)
            continue

        try:
            out["size_bytes"] = str(path.stat().st_size)
            out["sha256"] = sha256_file(path)
            with Image.open(path) as image:
                out["image_width"] = str(image.width)
                out["image_height"] = str(image.height)
                out["image_mode"] = image.mode
                out["ahash_8x8"] = average_hash(image)
                out["dhash_8x8"] = difference_hash(image)
        except Exception as exc:  # pragma: no cover - evidence script.
            out["error"] = exc.__class__.__name__ + ": " + str(exc)

        hash_rows.append(out)

    hash_output_path.parent.mkdir(parents=True, exist_ok=True)
    if hash_rows:
        with hash_output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(hash_rows[0].keys()))
            writer.writeheader()
            writer.writerows(hash_rows)

    by_group_question: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in hash_rows:
        by_group_question[(row["candidate_group_id"], row["question_id"])].append(row)

    question_rows: list[dict[str, str]] = []
    for (group_id, question_id), rows in sorted(by_group_question.items()):
        ordered_rows = sorted(
            rows,
            key=lambda r: (
                r["part_kind"],
                int(r["part_index"]),
                int(r["image_ordinal"]),
                r["image_src"],
            ),
        )
        missing_count = sum(1 for r in ordered_rows if r["exists"] != "true" or r["error"])
        question_rows.append(
            {
                "candidate_group_id": group_id,
                "question_id": question_id,
                "image_count": str(len(ordered_rows)),
                "missing_or_error_count": str(missing_count),
                "exact_sequence_signature": sequence_digest(ordered_rows, "sha256"),
                "ahash_sequence_signature": sequence_digest(ordered_rows, "ahash_8x8"),
                "dhash_sequence_signature": sequence_digest(ordered_rows, "dhash_8x8"),
            }
        )

    by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in question_rows:
        by_group[row["candidate_group_id"]].append(row)

    summary_rows: list[dict[str, str]] = []
    for group_id, rows in sorted(by_group.items()):
        distinct_exact = {r["exact_sequence_signature"] for r in rows}
        distinct_ahash = {r["ahash_sequence_signature"] for r in rows}
        distinct_dhash = {r["dhash_sequence_signature"] for r in rows}
        missing_total = sum(int(r["missing_or_error_count"]) for r in rows)

        if missing_total:
            evidence_decision = "review_missing_asset"
        elif len(distinct_exact) == 1:
            evidence_decision = "asset_exact_match_supported"
        elif len(distinct_ahash) == 1 and len(distinct_dhash) == 1:
            evidence_decision = "perceptual_match_supported"
        else:
            evidence_decision = "visual_difference_review_required"

        summary_rows.append(
            {
                "candidate_group_id": group_id,
                "question_count": str(len(rows)),
                "image_count_total": str(sum(int(r["image_count"]) for r in rows)),
                "missing_or_error_count": str(missing_total),
                "distinct_exact_sequence_signatures": str(len(distinct_exact)),
                "distinct_ahash_sequence_signatures": str(len(distinct_ahash)),
                "distinct_dhash_sequence_signatures": str(len(distinct_dhash)),
                "evidence_decision": evidence_decision,
                "question_ids": " | ".join(r["question_id"] for r in rows),
            }
        )

    with summary_output_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "candidate_group_id",
            "question_count",
            "image_count_total",
            "missing_or_error_count",
            "distinct_exact_sequence_signatures",
            "distinct_ahash_sequence_signatures",
            "distinct_dhash_sequence_signatures",
            "evidence_decision",
            "question_ids",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"hashed_images={len(hash_rows)}")
    print(f"group_summaries={len(summary_rows)}")
    print(f"hash_output={hash_output_path}")
    print(f"summary_output={summary_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
