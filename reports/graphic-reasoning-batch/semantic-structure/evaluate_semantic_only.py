#!/usr/bin/env python3
"""Blind-solve answer-isolated solver JSON without spending a second image call."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qwen_client import json_candidate


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--asset", action="append", required=True)
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    for asset_id in args.asset:
        asset_root = args.input_root.resolve() / "assets" / asset_id
        solver = json.loads((asset_root / "solver.json").read_text(encoding="utf-8"))
        evaluation = json.loads((asset_root / "evaluation.json").read_text(encoding="utf-8"))
        prompt = "下面是与题库答案隔离的图形推理语义 JSON。只依据面板、元素、位置、方向和跨面板关系选择 A/B/C/D；信息不足也给最可能选项。只输出 JSON：{\"answer\":\"A|B|C|D\",\"reason\":\"简短理由\",\"information_sufficient\":true或false}。solver=" + json.dumps(solver, ensure_ascii=False, separators=(",", ":"))
        result = json_candidate(prompt, [], max_tokens=800, timeout=180)
        prediction = result["candidate"]
        official = sorted({str(item.get("answerLabel") or "").upper() for item in evaluation["questionAnswers"] if item.get("answerLabel")})
        official_answer = official[0] if len(official) == 1 else None
        predicted_answer = str(prediction.get("answer") or "").upper()
        row = {
            "schemaVersion": "graphic-reasoning-semantic-only-evaluation-v1",
            "assetId": asset_id,
            "officialAnswer": official_answer,
            "prediction": prediction,
            "semanticJsonCorrect": bool(official_answer and predicted_answer == official_answer),
            "answerIsolation": True,
            "usage": result.get("usage"),
        }
        write_json(asset_root / "semantic-solver-evaluation.json", row)
        rows.append(row)
    state = {
        "schemaVersion": "graphic-reasoning-semantic-only-evaluation-state-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "assets": len(rows),
        "semanticJsonCorrect": sum(row["semanticJsonCorrect"] for row in rows),
        "rows": [{key: row[key] for key in ("assetId", "officialAnswer", "semanticJsonCorrect")} for row in rows],
    }
    write_json(args.input_root.resolve() / "semantic-only-evaluation-state.json", state)
    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    raise SystemExit(main())
