from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.domains.learning_packages.schemas import PacketRenderRequest, PacketRenderResult


class PacketRendererError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class PacketRenderer(Protocol):
    def render(
        self, *, request: PacketRenderRequest, idempotency_key: str
    ) -> PacketRenderResult: ...


@dataclass(frozen=True)
class PacketRendererConfiguration:
    base_url: str
    api_key: str
    release_id: str
    https_host_allowlist: tuple[str, ...]
    dpa_confirmed: bool
    idempotency_header: str
    request_id_header: str
    timeout_seconds: float = 45.0

    def validate(self) -> None:
        try:
            parsed = urlsplit(self.base_url)
            hostname = (parsed.hostname or "").casefold()
            _ = parsed.port
        except ValueError as exc:
            raise PacketRendererError("renderer_configuration_invalid", str(exc), retryable=False) from exc
        if (
            parsed.scheme.casefold() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise PacketRendererError(
                "renderer_configuration_invalid",
                "packet renderer requires a credential-free HTTPS base URL",
                retryable=False,
            )
        if hostname not in {item.casefold() for item in self.https_host_allowlist}:
            raise PacketRendererError("renderer_host_not_approved", "renderer host is not approved", retryable=False)
        if not self.dpa_confirmed or not self.api_key:
            raise PacketRendererError("renderer_processing_not_approved", "renderer data-processing approval is incomplete", retryable=False)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.release_id) is None:
            raise PacketRendererError("renderer_release_invalid", "renderer release identity is invalid", retryable=False)
        for header in (self.idempotency_header, self.request_id_header):
            if re.fullmatch(r"[A-Za-z0-9-]+", header) is None:
                raise PacketRendererError("renderer_header_invalid", "renderer audit headers are invalid", retryable=False)


class HttpPacketRenderer:
    def __init__(
        self,
        configuration: PacketRendererConfiguration,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        configuration.validate()
        self._configuration = configuration
        self._transport = transport

    def render(
        self, *, request: PacketRenderRequest, idempotency_key: str
    ) -> PacketRenderResult:
        headers = {
            "Authorization": f"Bearer {self._configuration.api_key}",
            self._configuration.idempotency_header: idempotency_key,
            "X-Renderer-Release": self._configuration.release_id,
        }
        try:
            with httpx.Client(
                timeout=self._configuration.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.post(
                    f"{self._configuration.base_url.rstrip('/')}/v1/private-packets/render",
                    headers=headers,
                    json=request.model_dump(by_alias=True, mode="json"),
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PacketRendererError("renderer_unreachable", "packet renderer is unavailable", retryable=True) from exc
        request_id = response.headers.get(self._configuration.request_id_header)
        if response.status_code >= 500:
            raise PacketRendererError("renderer_upstream_failure", "packet renderer failed", retryable=True)
        if response.status_code >= 400:
            raise PacketRendererError("renderer_request_rejected", "packet render request was rejected", retryable=False)
        if not request_id:
            raise PacketRendererError("renderer_receipt_missing", "renderer request receipt is missing", retryable=False)
        try:
            result = PacketRenderResult.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise PacketRendererError("renderer_response_invalid", "packet renderer returned an invalid contract", retryable=False) from exc
        if result.provider_request_id != request_id:
            raise PacketRendererError("renderer_receipt_mismatch", "renderer receipt does not match its response", retryable=False)
        return result
