from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha512
import hmac
import json
import math
import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

from app.domains.math_ink.schemas import RecognizeMathInkCommand
from app.domains.math_ink.rendering import InvalidInkError, render_math_ink
from app.domains.xfyun.formula import (
    FORMULA_ENDPOINT,
    XfyunFormulaClient,
    XfyunFormulaError,
)


class MathInkProviderConfigurationError(RuntimeError):
    pass


class MathInkProviderResponseError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


FORMULA_CONFIDENCE_POLICY_VERSION = "formula-confidence-policy@v1"
# Provider confidence is not calibrated against learner outcomes.  This high,
# frozen threshold only upgrades a complete, auditable candidate receipt from
# weak to medium; it never makes an unconfirmed formula strong.
FORMULA_CANDIDATE_MEDIUM_THRESHOLD = 0.95
FORMULA_CONFIDENCE_SEMANTICS = "provider_reported_uncalibrated"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_PROVIDERS = {
    "myscript_candidate": "MyScript",
    "xfyun_candidate": "Xfyun",
}


def formula_evidence_strength(
    *,
    state: str,
    source: str,
    recognition_metadata: Mapping[str, Any] | None,
) -> str:
    """Classify formula evidence without interpreting provider confidence.

    User confirmation is the only strong state.  A provider candidate can be
    medium only when its full immutable recognition receipt is present and its
    provider-reported (uncalibrated) confidence meets the frozen policy.
    """

    if state == "confirmed":
        return "strong"
    if state != "candidate" or not _valid_recognition_receipt(
        source=source,
        metadata=recognition_metadata,
    ):
        return "weak"
    confidence = recognition_metadata.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return "weak"
    numeric = float(confidence)
    return (
        "medium"
        if math.isfinite(numeric)
        and numeric >= FORMULA_CANDIDATE_MEDIUM_THRESHOLD
        else "weak"
    )


def _valid_recognition_receipt(
    *,
    source: str,
    metadata: Mapping[str, Any] | None,
) -> bool:
    if metadata is None or metadata.get("contractVersion") != (
        "formula-recognition-metadata@v1"
    ):
        return False
    expected_provider = _CANDIDATE_PROVIDERS.get(source)
    if (
        expected_provider is None
        or metadata.get("provider") != expected_provider
        or metadata.get("candidateSource") != source
        or metadata.get("rendererVersion") != "math-ink-renderer@v1"
    ):
        return False
    required_text = (
        "providerRelease",
        "modelRelease",
        "priceVersion",
        "unitPriceCny",
    )
    if any(not isinstance(metadata.get(key), str) or not metadata[key] for key in required_text):
        return False
    return all(
        isinstance(metadata.get(key), str) and _SHA256_RE.fullmatch(metadata[key])
        for key in ("vectorFingerprint", "renderFingerprint")
    )


class MyScriptConfigurationError(MathInkProviderConfigurationError):
    pass


class MyScriptResponseError(MathInkProviderResponseError):
    pass


@dataclass(frozen=True, slots=True)
class MathInkProviderResult:
    latex: str
    rendered_payload: bytes
    confidence: float | None
    provider_request_id: str | None
    vector_fingerprint: str
    render_fingerprint: str
    renderer_version: str


@dataclass(frozen=True, slots=True)
class MathInkProviderDescriptor:
    provider: str
    scenario_key: str
    candidate_source: str
    provider_release: str
    model_release: str
    endpoint: str
    price_version: str
    unit_price_cny: str
    deployment_id: str


class XfyunMathInkClient:
    """Provider-neutral vector adapter for Xfyun's image formula API."""

    def __init__(
        self,
        client: XfyunFormulaClient,
        *,
        provider_release: str,
        price_version: str,
        unit_price_cny: str,
        deployment_id: str = "",
    ) -> None:
        if not provider_release.strip():
            raise MathInkProviderConfigurationError(
                "Xfyun provider release is not declared"
            )
        if not price_version.strip() or not unit_price_cny.strip():
            raise MathInkProviderConfigurationError(
                "Xfyun measured price card is not declared"
            )
        self.client = client
        self.descriptor = MathInkProviderDescriptor(
            provider="Xfyun",
            scenario_key="xfyun_math_recognition",
            candidate_source="xfyun_candidate",
            provider_release=provider_release.strip(),
            model_release="teach-photo-print",
            endpoint=FORMULA_ENDPOINT,
            price_version=price_version.strip(),
            unit_price_cny=unit_price_cny.strip(),
            deployment_id=deployment_id.strip(),
        )

    @classmethod
    def from_environment(cls, client: XfyunFormulaClient) -> "XfyunMathInkClient":
        return cls(
            client,
            provider_release=os.environ.get(
                "XFYUN_FORMULA_PROVIDER_RELEASE_ID", ""
            ),
            price_version=os.environ.get("XFYUN_FORMULA_PRICE_VERSION", ""),
            unit_price_cny=os.environ.get("XFYUN_FORMULA_UNIT_PRICE_CNY", ""),
            deployment_id=os.environ.get("XFYUN_FORMULA_DEPLOYMENT_ID", ""),
        )

    def recognize(self, command: RecognizeMathInkCommand) -> MathInkProviderResult:
        try:
            rendered = render_math_ink(command)
        except InvalidInkError as exc:
            raise MathInkProviderResponseError(exc.code) from exc
        try:
            recognition = self.client.recognize(rendered.png)
        except XfyunFormulaError as exc:
            raise MathInkProviderResponseError("xfyun_provider_failed") from exc
        formulas = [
            formula
            for line in recognition.lines
            for formula in line.formulas
        ]
        if not formulas:
            raise MathInkProviderResponseError("xfyun_formula_not_found")
        latex = " \\; ".join(formulas).strip()
        if not latex or len(latex) > 4000 or "\x00" in latex:
            raise MathInkProviderResponseError("xfyun_response_contract_invalid")
        confidences = [line.confidence for line in recognition.lines if line.formulas]
        confidence = sum(confidences) / len(confidences) if confidences else None
        return MathInkProviderResult(
            latex=latex,
            rendered_payload=rendered.png,
            confidence=confidence,
            provider_request_id=recognition.request_id,
            vector_fingerprint=rendered.vector_fingerprint,
            render_fingerprint=rendered.render_fingerprint,
            renderer_version=rendered.renderer_version,
        )


class MyScriptMathClient:
    def __init__(
        self,
        *,
        endpoint: str,
        host_allowlist: str,
        application_key: str,
        hmac_key: str,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        try:
            parsed = urlsplit(endpoint)
            hostname = (parsed.hostname or "").casefold()
            _ = parsed.port
        except ValueError as exc:
            raise MyScriptConfigurationError("MyScript endpoint is invalid") from exc
        allowed = {
            value.strip().casefold()
            for value in host_allowlist.split(",")
            if value.strip()
        }
        if (
            parsed.scheme.casefold() != "https"
            or hostname not in allowed
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith("/api/v4.0/iink/recognize/")
        ):
            raise MyScriptConfigurationError(
                "MyScript endpoint must be the allowlisted HTTPS v4 recognizer"
            )
        if not application_key.strip() or not hmac_key.strip():
            raise MyScriptConfigurationError(
                "MyScript server credentials are incomplete"
            )
        self.endpoint = endpoint
        self.application_key = application_key
        self.hmac_key = hmac_key
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        )

    def recognize(self, command: RecognizeMathInkCommand) -> tuple[str, bytes]:
        body = {
            "scaleX": command.scale_x,
            "scaleY": command.scale_y,
            "contentType": "Math",
            "strokes": [
                {"x": list(stroke.x), "y": list(stroke.y), "t": list(stroke.t)}
                for stroke in command.strokes
            ],
        }
        encoded = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        signature = hmac.new(
            f"{self.application_key}{self.hmac_key}".encode("utf-8"),
            encoded,
            sha512,
        ).hexdigest()
        try:
            with self.client_factory() as client:
                response = client.post(
                    self.endpoint,
                    content=encoded,
                    headers={
                        "applicationKey": self.application_key,
                        "hmac": signature,
                        "Content-Type": "application/json",
                        "Accept": "application/x-latex,application/json",
                    },
                )
        except httpx.RequestError as exc:
            raise MyScriptResponseError("myscript_unavailable") from exc
        if response.status_code != 200:
            code = {
                400: "myscript_invalid_ink",
                401: "myscript_authentication_failed",
                403: "myscript_quota_or_account_blocked",
            }.get(response.status_code, "myscript_provider_failed")
            raise MyScriptResponseError(code, status_code=response.status_code)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type != "application/x-latex":
            raise MyScriptResponseError("myscript_response_contract_invalid")
        try:
            latex = response.content.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise MyScriptResponseError("myscript_response_contract_invalid") from exc
        if latex.startswith("$") and latex.endswith("$") and len(latex) >= 2:
            latex = latex[1:-1].strip()
        if not latex or len(latex) > 4000 or "\x00" in latex:
            raise MyScriptResponseError("myscript_response_contract_invalid")
        return latex, encoded
