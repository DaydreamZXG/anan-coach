from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
from typing import Callable

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
        if int(raw.get("code", -1)) != 0:
            raise XfyunFormulaError(str(raw.get("message") or "公式识别失败。"))
        lines = tuple(
            parsed
            for region in raw.get("data", {}).get("region", [])
            if isinstance(region, dict) and region.get("type") == "text"
            if (parsed := _parse_line(region)) is not None
        )
        return FormulaRecognition(request_id=str(raw.get("sid") or "unknown"), lines=lines)


def _parse_line(region: dict[str, object]) -> FormulaLine | None:
    recog = region.get("recog")
    if not isinstance(recog, dict) or int(recog.get("exception", 0) or 0) != 0:
        return None
    content = str(recog.get("content") or "").strip()
    if not content:
        return None
    formulas = [match.group(1).strip() for match in _LATEX.finditer(content)]
    contains_formula = any(_is_meaningful_formula(formula) for formula in formulas)
    text = _LATEX.sub(lambda match: f"${match.group(1).strip()}$", content).strip()
    elements = recog.get("element")
    confidences = [
        float(item["conf"])
        for item in elements
        if isinstance(elements, list) and isinstance(item, dict) and "conf" in item
    ] if isinstance(elements, list) else []
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return FormulaLine(
        text=text,
        confidence=max(0.0, min(1.0, confidence)),
        contains_formula=contains_formula,
    )


def _is_meaningful_formula(latex: str) -> bool:
    compact = re.sub(r"\s+", "", latex)
    decorative = {r"\times", r"\cdot", r"\bullet", "×", "·", "•"}
    return bool(compact and compact not in decorative)
