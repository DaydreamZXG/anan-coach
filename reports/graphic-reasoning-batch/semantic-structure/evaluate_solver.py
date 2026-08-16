#!/usr/bin/env python3
"""Compare answer-isolated semantic JSON solving with original-image solving."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ENDPOINT = os.environ.get(
    "GRAPHIC_REASONING_VLM_ENDPOINT",
    "http://127.0.0.1:18000/v1/chat/completions",
)
MODEL = os.environ.get("GRAPHIC_REASONING_VLM_MODEL", "qwen3-vl-30b-a3b")


def post(content: list[dict[str, Any]], timeout: int = 120) -> tuple[dict[str, Any] | None, str]:
    body = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 800,
        "messages": [{"role": "user", "content": content}],
    }
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw = payload["choices"][0]["message"]["content"]
    if isinstance(raw, list):
        raw = "".join(str(item.get("text", "")) for item in raw if isinstance(item, dict))
    text = str(raw).strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json\n", "", 1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return parsed if isinstance(parsed, dict) else None, text


def solver_request(solver: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    prompt = """下面是从一道图形推理题原图提取的、与题库答案隔离的语义 JSON。只依据 JSON 中各题干面板、选项面板、元素数量/位置/方向和跨面板关系解题。选择 A/B/C/D；若信息不足也必须给出最可能选项并说明缺失信息。只输出 {\"answer\":\"A|B|C|D\",\"reason\":\"简短理由\",\"information_sufficient\":true或false}。
语义 JSON：
""" + json.dumps(solver, ensure_ascii=False, separators=(",", ":"))
    return post([{"type": "text", "text": prompt}])


def image_request(image_path: Path) -> tuple[dict[str, Any] | None, str]:
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = """请直接解答这道图形推理题。按从左到右、从上到下识别题干和 A/B/C/D 四个选项，找出规律并选择答案。只输出 {\"answer\":\"A|B|C|D\",\"reason\":\"简短理由\"}，不要输出坐标或 SVG。"""
    return post([
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
    ])


def answer(value: dict[str, Any] | None) -> str | None:
    result = str((value or {}).get("answer") or "").strip().upper()
    return result if result in {"A", "B", "C", "D"} else None


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-root",
        type=Path,
        default=HERE,
        help="Model-specific semantic result directory containing assets/.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--asset", action="append", default=[])
    args = parser.parse_args()
    input_root = args.input_root.resolve()
    asset_dirs = sorted(path.parent for path in input_root.glob("assets/gr_*/solver.json"))
    selected = set(args.asset)
    if selected:
        asset_dirs = [path for path in asset_dirs if path.name in selected]
    else:
        representative_order = json.loads((HERE / "queue-state.json").read_text(encoding="utf-8"))["representativeAssets"]
        by_id = {path.name: path for path in asset_dirs}
        asset_dirs = [by_id[asset_id] for asset_id in representative_order if asset_id in by_id]
    asset_dirs = asset_dirs[:max(0, args.limit)]
    rows: list[dict[str, Any]] = []
    for asset_dir in asset_dirs:
        solver = json.loads((asset_dir / "solver.json").read_text(encoding="utf-8"))
        evaluation = json.loads((asset_dir / "evaluation.json").read_text(encoding="utf-8"))
        official = sorted({str(item.get("answerLabel") or "").upper() for item in evaluation["questionAnswers"] if item.get("answerLabel")})
        official_answer = official[0] if len(official) == 1 else None
        semantic_result, semantic_raw = solver_request(solver)
        image_path = Path(solver["source"]["imagePaths"][0])
        image_result, image_raw = image_request(image_path)
        semantic_answer, image_answer = answer(semantic_result), answer(image_result)
        row = {
            "schemaVersion": "graphic-reasoning-semantic-solver-evaluation-v1",
            "assetId": asset_dir.name,
            "officialAnswer": official_answer,
            "semanticJsonPrediction": semantic_result,
            "semanticJsonRaw": semantic_raw,
            "semanticJsonCorrect": bool(official_answer and semantic_answer == official_answer),
            "originalImagePrediction": image_result,
            "originalImageRaw": image_raw,
            "originalImageCorrect": bool(official_answer and image_answer == official_answer),
            "answerIsolation": True,
            "model": MODEL,
        }
        write_json(asset_dir / "solver-evaluation.json", row)
        rows.append(row)
    state = {
        "schemaVersion": "graphic-reasoning-semantic-solver-evaluation-state-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "assets": len(rows),
        "semanticJsonCorrect": sum(row["semanticJsonCorrect"] for row in rows),
        "originalImageCorrect": sum(row["originalImageCorrect"] for row in rows),
        "bothCorrect": sum(row["semanticJsonCorrect"] and row["originalImageCorrect"] for row in rows),
        "rows": [{key: row[key] for key in ("assetId", "officialAnswer", "semanticJsonCorrect", "originalImageCorrect")} for row in rows],
    }
    write_json(input_root / "solver-evaluation-state.json", state)
    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
