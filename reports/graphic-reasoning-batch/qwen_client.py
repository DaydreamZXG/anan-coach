#!/usr/bin/env python3
"""Optional Qwen semantic candidate client.

This module never emits coordinates.  It is intentionally separate from the
deterministic pipeline so a network/model outage cannot silently change vector
geometry or make a source image the rendering truth.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.request
from pathlib import Path
from typing import Any, Iterable

ENDPOINT = os.environ.get(
    "GRAPHIC_REASONING_VLM_ENDPOINT",
    "http://127.0.0.1:18000/v1/chat/completions",
)
MODEL = os.environ.get("GRAPHIC_REASONING_VLM_MODEL", "qwen3-vl-30b-a3b")


def image_url_item(image_path: str | Path) -> dict[str, Any]:
    path = Path(image_path)
    image = path.read_bytes()
    encoded = base64.b64encode(image).decode("ascii")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64," + encoded}}


def parse_json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    text = str(content).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("model response does not contain a JSON object")
    candidate, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(candidate, dict):
        raise ValueError("model JSON candidate must be an object")
    return candidate


def json_candidate(
    prompt: str,
    images: Iterable[tuple[str, str | Path]],
    *,
    max_tokens: int = 1536,
    timeout: int = 300,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for label, image_path in images:
        content.extend([
            {"type": "text", "text": label},
            image_url_item(image_path),
        ])
    body = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {"model": MODEL, "candidate": parse_json_object(payload["choices"][0]["message"]["content"]), "usage": payload.get("usage")}


def semantic_candidate(
    image_path: str | Path,
    prompt: str,
    timeout: int = 30,
    panel_evidence: Iterable[tuple[str, str | Path]] = (),
) -> dict[str, Any]:
    images: list[tuple[str, str | Path]] = [("完整题目：", image_path)]
    for label, evidence_path in panel_evidence:
        images.append((label, evidence_path))
    return json_candidate(
        prompt + "\n只输出 JSON 语义候选；禁止输出绝对坐标、画布布局或最终矢量路径。第一张图是完整题目，后续图是按顺序放大的单面板证据，不是额外面板。",
        images,
        max_tokens=8192,
        timeout=timeout,
    )
