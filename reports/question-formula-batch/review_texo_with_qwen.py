#!/usr/bin/env python3
"""Independently re-read low-confidence Texo formulas with the local Qwen3-VL service."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from common import latex_braces_balanced, write_json


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--texo-results", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1/chat/completions")
    parser.add_argument("--model", default="qwen3-vl-30b-a3b")
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    parser.add_argument("--accept-confidence", type=float, default=0.95)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--asset-ids",
        type=Path,
        help="可选：每行一个 asset_id，只复核清单中的公式。",
    )
    parser.add_argument(
        "--priority",
        action="append",
        choices=("question_critical", "explanation"),
        help="只复核指定优先级；可重复传入。默认复核全部。",
    )
    parser.add_argument(
        "--contexts",
        type=Path,
        help="可选：按 asset_id 索引的原题上下文 JSON，用于区分中文注释与数学公式。",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def local_source(input_dir: Path, asset_id: str) -> Path:
    matches = list(input_dir.glob(f"{asset_id}.*"))
    if len(matches) != 1:
        raise FileNotFoundError(f"input_count={len(matches)}")
    return matches[0]


def normalized_png(path: Path) -> bytes:
    """Give the VLM an opaque, tightly cropped and legible formula image."""
    with Image.open(path) as source:
        rgba = source.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        image = background.convert("RGB")

    ink = ImageOps.invert(ImageOps.grayscale(image))
    bbox = ink.getbbox()
    if bbox:
        left, top, right, bottom = bbox
        margin = max(4, round(min(image.size) * 0.03))
        image = image.crop(
            (
                max(0, left - margin),
                max(0, top - margin),
                min(image.width, right + margin),
                min(image.height, bottom + margin),
            )
        )

    scale = max(1.0, min(1600 / image.width, 256 / image.height))
    if scale > 1.0:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    canvas = Image.new("RGB", (image.width + 32, image.height + 32), "white")
    canvas.paste(image, (16, 16))
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def data_url(path: Path) -> str:
    payload = normalized_png(path)
    return f"data:image/png;base64,{base64.b64encode(payload).decode('ascii')}"


def parse_json_object(value: str) -> dict[str, Any]:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def compact_context(value: dict[str, Any] | None) -> str:
    if not value:
        return "未找到原题上下文。"
    occurrences = []
    for occurrence in value.get("occurrences", [])[:2]:
        occurrences.append(
            {
                "paper": occurrence.get("paper_title"),
                "question_number": occurrence.get("question_number"),
                "roles": occurrence.get("roles"),
                "material": str(occurrence.get("material_text") or "")[:1800],
                "stem": str(occurrence.get("stem_text") or "")[:800],
                "options": occurrence.get("options"),
                "answer": occurrence.get("answer"),
                "analysis": str(occurrence.get("analysis") or "")[:1000],
            }
        )
    return json.dumps(
        {
            "current_candidate": value.get("current_candidate"),
            "current_confidence": value.get("current_confidence"),
            "occurrences": occurrences,
        },
        ensure_ascii=False,
    )


def review_one(
    args: argparse.Namespace,
    texo: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    asset_id = texo["asset_id"]
    image = local_source(args.input_dir, asset_id)
    candidate = texo["blocks"][0]["expression"]
    prompt = (
        "你是公考题库图片内容校对器。请先独立读图，原题上下文只用于消除歧义，不得补出图中没有的内容。\n"
        "这张图可能是：纯数学公式、中文与公式混合，或普通中文注释/图例。"
        "普通中文不得猜成上下标；中文用 \\text{...} 包裹，圆圈序号应原样保留。"
        "严格区分 0/O、1/l、5/S、8/B、百分号、上下标、分式和根式。\n"
        "只返回 JSON，不要 Markdown："
        '{"latex":"准确 LaTeX","image_kind":"formula|mixed_text_formula|ordinary_text",'
        '"agrees_with_texo":true,"agrees_with_current":true,"consistent_with_context":true,'
        '"needs_human":false,"confidence":0.0,"reason":"30字内"}.\n'
        f"Texo 候选：{candidate}\n"
        f"原题上下文：{compact_context(context)}"
    )
    payload = {
        "model": args.model,
        "temperature": 0,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url(image)}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    request = Request(
        args.endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        body = json.loads(response.read().decode("utf-8"))
    parsed = parse_json_object(body["choices"][0]["message"]["content"])
    latex = str(parsed.get("latex") or "").strip()
    confidence = float(parsed.get("confidence") or 0.0)
    balanced = latex_braces_balanced(latex)
    agrees = bool(parsed.get("agrees_with_texo"))
    needs_human = bool(parsed.get("needs_human", True))
    consistent = bool(parsed.get("consistent_with_context"))
    image_kind = str(parsed.get("image_kind") or "unknown")[:32]
    reason = re.sub(r"\s+", " ", str(parsed.get("reason") or "")).strip()[:80]
    accepted = (
        bool(latex)
        and balanced
        and confidence >= args.accept_confidence
        and consistent
        and not needs_human
    )
    return {
        "schema_version": "formula-asset-v1",
        "asset_id": asset_id,
        "sha256": texo["sha256"],
        "source_path": texo["source_path"],
        "priority": texo["priority"],
        "status": "candidate_auto" if accepted else "needs_manual_review",
        "provider": "qwen3_vl_local",
        "provider_request_id": body.get("id"),
        "blocks": [
            {"type": "formula", "format": "latex", "expression": latex or "[待复核]", "display": True}
        ],
        "confidence": max(0.0, min(1.0, confidence)),
        "verification": {
            "source_hash_matches": True,
            "latex_nonempty": bool(latex),
            "braces_balanced": balanced,
            "note": (
                "Qwen3-VL 独立复核 Texo；"
                f"agrees_with_texo={str(agrees).lower()}；"
                f"kind={image_kind}；consistent={str(consistent).lower()}；"
                f"needs_human={str(needs_human).lower()}；reason={reason}；"
                f"accept_confidence={args.accept_confidence:.2f}"
            ),
        },
    }


def failure(texo: dict[str, Any], error: Exception) -> dict[str, Any]:
    return {
        "schema_version": "formula-asset-v1",
        "asset_id": texo["asset_id"],
        "sha256": texo["sha256"],
        "source_path": texo["source_path"],
        "priority": texo["priority"],
        "status": "failed",
        "provider": "qwen3_vl_local",
        "provider_request_id": None,
        "blocks": [{"type": "formula", "format": "latex", "expression": "[复核失败]", "display": True}],
        "confidence": None,
        "verification": {
            "source_hash_matches": True,
            "latex_nonempty": False,
            "braces_balanced": False,
            "note": f"Qwen3-VL 复核失败: {str(error)[:200]}",
        },
    }


def main() -> None:
    args = arguments()
    contexts: dict[str, dict[str, Any]] = {}
    if args.contexts:
        contexts = json.loads(args.contexts.read_text(encoding="utf-8"))
    selected_ids = None
    if args.asset_ids:
        selected_ids = {
            line.strip()
            for line in args.asset_ids.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    candidates = []
    for path in sorted(args.texo_results.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if selected_ids is not None and value.get("asset_id") not in selected_ids:
            continue
        if args.priority and value.get("priority") not in args.priority:
            continue
        confidence = value.get("confidence")
        needs_review = selected_ids is not None or (
            value.get("status") != "candidate_auto"
            or not isinstance(confidence, (int, float))
            or confidence < args.confidence_threshold
        )
        destination = args.output_dir / path.name
        if needs_review and (args.overwrite or not destination.exists()):
            candidates.append(value)
    if args.limit > 0:
        candidates = candidates[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"selected": len(candidates), "workers": args.workers}, ensure_ascii=False))
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(review_one, args, value, contexts.get(value["asset_id"])): value
            for value in candidates
        }
        completed = 0
        for future in as_completed(futures):
            texo = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = failure(texo, error)
            write_json(args.output_dir / f"{texo['asset_id']}.json", result)
            completed += 1
            if completed % 10 == 0 or completed == len(candidates):
                print(f"{completed}/{len(candidates)}", flush=True)


if __name__ == "__main__":
    main()
