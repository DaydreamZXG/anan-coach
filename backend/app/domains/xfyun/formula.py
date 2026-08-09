from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from math import isfinite
import re
from typing import Any, Callable

import httpx

from app.domains.xfyun.auth import formula_headers
from app.domains.xfyun.config import XfyunCredentials


FORMULA_ENDPOINT = "https://rest-api.xfyun.cn/v2/itr"
_LATEX = re.compile(r"ifly-latex-begin(.*?)ifly-latex-end", re.DOTALL)


class XfyunFormulaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FormulaLine:
    text: str
    confidence: float
    contains_formula: bool
    formulas: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FormulaRecognition:
    request_id: str
    lines: tuple[FormulaLine, ...]

    @property
    def has_formula(self) -> bool:
        return any(line.contains_formula for line in self.lines)


class XfyunFormulaClient:
    def __init__(
        self,
        credentials: XfyunCredentials,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._credentials = credentials
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False)
        )

    def recognize(self, image: bytes) -> FormulaRecognition:
        if not image or len(base64.b64encode(image)) > 4 * 1024 * 1024:
            raise XfyunFormulaError("公式识别图片大小不符合接口限制。")
        payload = {
            "common": {"app_id": self._credentials.app_id},
            "business": {"ent": "teach-photo-print", "aue": "raw"},
            "data": {"image": base64.b64encode(image).decode()},
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        try:
            with self._client_factory() as client:
                response = client.post(
                    FORMULA_ENDPOINT,
                    content=body,
                    headers=formula_headers(body, self._credentials),
                )
                response.raise_for_status()
                raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise XfyunFormulaError("公式识别服务暂时不可用。") from exc
        try:
            return _parse_recognition(raw)
        except XfyunFormulaError:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise XfyunFormulaError("公式识别响应契约无效。") from exc


def _parse_recognition(raw: Any) -> FormulaRecognition:
    if not isinstance(raw, dict):
        raise XfyunFormulaError("公式识别响应根节点无效。")
    code = _strict_int(raw.get("code"), field="code")
    if code != 0:
        message = raw.get("message")
        raise XfyunFormulaError(
            message.strip() if isinstance(message, str) and message.strip()
            else "公式识别失败。"
        )
    request_id = raw.get("sid")
    data = raw.get("data")
    if not isinstance(request_id, str) or not request_id.strip():
        raise XfyunFormulaError("公式识别请求标识无效。")
    if not isinstance(data, dict):
        raise XfyunFormulaError("公式识别响应 data 无效。")
    regions = data.get("region")
    if not isinstance(regions, list):
        raise XfyunFormulaError("公式识别响应 region 无效。")
    parsed_lines: list[FormulaLine] = []
    for region in regions:
        if not isinstance(region, dict) or not isinstance(region.get("type"), str):
            raise XfyunFormulaError("公式识别区域契约无效。")
        if region["type"] != "text":
            continue
        parsed = _parse_line(region)
        if parsed is not None:
            parsed_lines.append(parsed)
    return FormulaRecognition(
        request_id=request_id.strip(),
        lines=tuple(parsed_lines),
    )


def _parse_line(region: dict[str, object]) -> FormulaLine | None:
    recog = region.get("recog")
    if not isinstance(recog, dict):
        raise XfyunFormulaError("公式识别区域 recog 无效。")
    if _strict_int(recog.get("exception", 0), field="exception") != 0:
        return None
    raw_content = recog.get("content")
    if not isinstance(raw_content, str):
        raise XfyunFormulaError("公式识别区域 content 无效。")
    content = raw_content.strip()
    if not content:
        return None
    formulas = [match.group(1).strip() for match in _LATEX.finditer(content)]
    contains_formula = any(_is_meaningful_formula(formula) for formula in formulas)
    text = _LATEX.sub(lambda match: f"${match.group(1).strip()}$", content).strip()
    elements = recog.get("element", [])
    if not isinstance(elements, list):
        raise XfyunFormulaError("公式识别区域 element 无效。")
    confidences: list[float] = []
    for item in elements:
        if not isinstance(item, dict) or "conf" not in item:
            raise XfyunFormulaError("公式识别置信度契约无效。")
        confidence = _strict_float(item["conf"], field="conf")
        confidences.append(confidence)
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return FormulaLine(
        text=text,
        confidence=max(0.0, min(1.0, confidence)),
        contains_formula=contains_formula,
        formulas=tuple(
            formula for formula in formulas if _is_meaningful_formula(formula)
        ),
    )


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise XfyunFormulaError(f"公式识别响应 {field} 无效。")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value)
    raise XfyunFormulaError(f"公式识别响应 {field} 无效。")


def _strict_float(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise XfyunFormulaError(f"公式识别响应 {field} 无效。")
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise XfyunFormulaError(
            f"公式识别响应 {field} 无效。"
        ) from exc
    if not isfinite(parsed):
        raise XfyunFormulaError(f"公式识别响应 {field} 无效。")
    return parsed


def _is_meaningful_formula(latex: str) -> bool:
    compact = re.sub(r"\s+", "", latex)
    decorative = {r"\times", r"\cdot", r"\bullet", "×", "·", "•"}
    return bool(compact and compact not in decorative)
