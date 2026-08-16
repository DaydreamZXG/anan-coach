#!/usr/bin/env python3
"""Resolve a known graphic-reasoning question without a model call."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE / "known-question-runtime-v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按题目 ID 或原图字节哈希查找已知图形推理题。"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question-id")
    group.add_argument("--image", type=Path)
    group.add_argument("--source-sha256")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="仅返回命中信息，不返回官方答案和原解析。",
    )
    args = parser.parse_args()

    aliases = {
        row["questionId"]: row
        for row in read_jsonl(args.root / "aliases.jsonl")
    }
    asset_index = {
        row["sourceSha256"]: row
        for row in read_jsonl(args.root / "asset-index.jsonl")
    }
    manifest = {
        row["canonicalQuestionId"]: row
        for row in read_jsonl(args.root / "manifest.jsonl")
    }

    match_type: str
    matched_value: str
    canonical_ids: list[str]
    if args.question_id:
        match_type = "question_id"
        matched_value = args.question_id
        alias = aliases.get(args.question_id)
        canonical_ids = [alias["canonicalQuestionId"]] if alias else []
    else:
        match_type = "source_image_sha256"
        matched_value = args.source_sha256 or sha256_file(args.image)
        indexed = asset_index.get(matched_value)
        canonical_ids = indexed["canonicalQuestionIds"] if indexed else []

    result: dict[str, Any] = {
        "known": bool(canonical_ids),
        "matchType": match_type,
        "matchedValue": matched_value,
        "canonicalQuestionIds": canonical_ids,
        "route": "known_question_official_solution" if canonical_ids else "unknown_question_visual_pipeline",
    }
    if canonical_ids:
        result["matches"] = []
        for canonical_id in canonical_ids:
            row = manifest[canonical_id]
            item: dict[str, Any] = {
                "canonicalQuestionId": canonical_id,
                "visualGrammar": row["visualGrammar"],
                "solutionPath": row["solutionPath"],
            }
            if not args.metadata_only:
                item["record"] = json.loads(
                    (args.root / row["solutionPath"]).read_text(encoding="utf-8")
                )
            result["matches"].append(item)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if canonical_ids else 2


if __name__ == "__main__":
    raise SystemExit(main())
