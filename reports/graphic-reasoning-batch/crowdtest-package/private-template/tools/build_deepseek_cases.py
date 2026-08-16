#!/usr/bin/env python3
"""Build answer-free DeepSeek cases from a model submission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission-root", type=Path, required=True, help="Unpacked public package containing output/assets")
    parser.add_argument("--question-key", type=Path, default=Path("question-key-private.jsonl"))
    parser.add_argument("--sample", type=Path, default=Path("deepseek-sample-private.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("deepseek-cases-answer-free.jsonl"))
    args = parser.parse_args()

    keys = {row["questionId"]: row for row in load_jsonl(args.question_key)}
    sample = load_jsonl(args.sample)
    lines: list[str] = []
    for sample_row in sample:
        question_id = sample_row["questionId"]
        key = keys[question_id]
        assets = []
        for ref in key["assets"]:
            asset_path = args.submission_root / "output" / "assets" / f'{ref["assetId"]}.json'
            scene = json.loads(asset_path.read_text(encoding="utf-8"))
            assets.append({
                "assetId": ref["assetId"],
                "role": ref["role"],
                "field": ref["field"],
                "optionLabel": ref.get("optionLabel"),
                "semanticScene": scene,
            })
        case = {
            "questionId": question_id,
            "visualGrammar": sample_row["visualGrammar"],
            "assets": assets,
        }
        lines.append(json.dumps(case, ensure_ascii=False, separators=(",", ":")))
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"caseCount": len(lines), "output": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
