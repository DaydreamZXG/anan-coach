from __future__ import annotations

from collections.abc import Callable
from hashlib import sha512
import hmac
import json
from urllib.parse import urlsplit

import httpx

from app.domains.math_ink.schemas import RecognizeMathInkCommand


class MyScriptConfigurationError(RuntimeError):
    pass


class MyScriptResponseError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


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
