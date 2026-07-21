from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from typing import Any, Callable
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MCP_PROTOCOL_VERSION = "2025-11-25"
BROWSE_TOOLS = frozenset(
    {
        "queryShopList",
        "searchProductForMcp",
        "switchProduct",
        "queryProductDetailInfo",
        "previewOrder",
    }
)
ORDER_TOOL = "createOrder"


class LuckinMcpError(RuntimeError):
    """Official Luckin MCP protocol, authorization, or tool boundary failed."""


@dataclass(frozen=True)
class EncryptedBearerToken:
    ciphertext: str
    key_id: str
    fingerprint: str


class EnvelopeTokenCipher:
    """Encrypt one user's Luckin bearer token with context-bound AES-256-GCM."""

    def __init__(self, *, key_id: str, encoded_key: str) -> None:
        if not key_id.strip():
            raise LuckinMcpError("Luckin encryption key ID is required")
        try:
            key = _decode_base64url(encoded_key)
        except ValueError as exc:
            raise LuckinMcpError("Luckin encryption key is invalid") from exc
        if len(key) != 32:
            raise LuckinMcpError("Luckin encryption key must contain 32 bytes")
        self._key_id = key_id
        self._cipher = AESGCM(key)

    def encrypt(
        self,
        token: str,
        *,
        user_id: str,
        connection_id: str,
    ) -> EncryptedBearerToken:
        if not token.strip():
            raise LuckinMcpError("Luckin bearer token is required")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            token.encode("utf-8"),
            self._aad(user_id=user_id, connection_id=connection_id),
        )
        return EncryptedBearerToken(
            ciphertext=_encode_base64url(nonce + ciphertext),
            key_id=self._key_id,
            fingerprint=sha256(token.encode("utf-8")).hexdigest(),
        )

    def decrypt(
        self,
        ciphertext: str,
        *,
        user_id: str,
        connection_id: str,
    ) -> str:
        try:
            payload = _decode_base64url(ciphertext)
            if len(payload) <= 28:
                raise ValueError("ciphertext is too short")
            plaintext = self._cipher.decrypt(
                payload[:12],
                payload[12:],
                self._aad(user_id=user_id, connection_id=connection_id),
            )
        except (InvalidTag, ValueError) as exc:
            raise LuckinMcpError("Luckin token ciphertext or context is invalid") from exc
        return plaintext.decode("utf-8")

    def _aad(self, *, user_id: str, connection_id: str) -> bytes:
        return f"luckin-token@v1:{self._key_id}:{user_id}:{connection_id}".encode()


@dataclass
class OrderConfirmation:
    confirmation_id: str
    user_id: str
    preview_fingerprint: str
    expires_at: datetime
    used_at: datetime | None = None

    def consume(self, *, user_id: str, preview_fingerprint: str, now: datetime) -> None:
        if self.user_id != user_id:
            raise LuckinMcpError("order confirmation belongs to another user")
        if self.preview_fingerprint != preview_fingerprint:
            raise LuckinMcpError("order confirmation does not match preview")
        if self.used_at is not None:
            raise LuckinMcpError("order confirmation was already used")
        if self.expires_at <= now.astimezone(UTC):
            raise LuckinMcpError("order confirmation expired")
        self.used_at = now.astimezone(UTC)


class LuckinMcpClient:
    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if endpoint != "https://gwmcp.lkcoffee.com/order/user/mcp":
            raise LuckinMcpError("untrusted Luckin MCP endpoint")
        if not bearer_token.strip():
            raise LuckinMcpError("Luckin user bearer token is required")
        self._endpoint = endpoint
        self._bearer_token = bearer_token
        self._client = client or httpx.Client(timeout=12.0, follow_redirects=False)
        self._now = now or (lambda: datetime.now(UTC))
        self._session_id: str | None = None
        self._request_id = 0

    def browse(self, tool: str, arguments: dict[str, object]) -> dict[str, Any]:
        if tool not in BROWSE_TOOLS:
            raise LuckinMcpError("tool is not permitted in browse flow")
        return self._call_tool(tool, arguments)

    def create_order(
        self,
        *,
        user_id: str,
        preview_payload: dict[str, object],
        arguments: dict[str, object],
        confirmation: OrderConfirmation,
    ) -> dict[str, Any]:
        preview_fingerprint = fingerprint(preview_payload)
        confirmation.consume(
            user_id=user_id,
            preview_fingerprint=preview_fingerprint,
            now=self._now(),
        )
        return self._call_tool(ORDER_TOOL, arguments)

    def _call_tool(self, tool: str, arguments: dict[str, object]) -> dict[str, Any]:
        self._ensure_initialized()
        response = self._rpc(
            "tools/call",
            {"name": tool, "arguments": arguments},
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise LuckinMcpError("Luckin tool result is invalid")
        if result.get("isError") is True:
            raise LuckinMcpError("Luckin tool returned an error")
        return result

    def _ensure_initialized(self) -> None:
        if self._session_id is not None:
            return
        response = self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "anan-backend", "version": "1.0.0"},
            },
            include_protocol_header=False,
        )
        result = response.get("result")
        if not isinstance(result, dict) or result.get("protocolVersion") != MCP_PROTOCOL_VERSION:
            raise LuckinMcpError("Luckin MCP protocol version mismatch")
        if self._session_id is None:
            raise LuckinMcpError("Luckin MCP did not issue a session ID")
        self._notification("notifications/initialized")

    def _notification(self, method: str) -> None:
        body = {"jsonrpc": "2.0", "method": method}
        response = self._client.post(
            self._endpoint,
            json=body,
            headers=self._headers(include_protocol=True),
        )
        if response.status_code not in {200, 202, 204}:
            raise LuckinMcpError("Luckin MCP initialization notification failed")

    def _rpc(
        self,
        method: str,
        params: dict[str, object],
        *,
        include_protocol_header: bool = True,
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = f"anan-{uuid4()}-{self._request_id}"
        response = self._client.post(
            self._endpoint,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            headers=self._headers(include_protocol=include_protocol_header),
        )
        if response.status_code != 200:
            raise LuckinMcpError(f"Luckin MCP returned status {response.status_code}")
        issued_session = response.headers.get("Mcp-Session-Id")
        if issued_session:
            self._session_id = issued_session
        payload = _decode_payload(response)
        if payload.get("id") != request_id or payload.get("jsonrpc") != "2.0":
            raise LuckinMcpError("Luckin MCP response identity mismatch")
        if payload.get("error") is not None:
            raise LuckinMcpError("Luckin MCP returned a JSON-RPC error")
        return payload

    def _headers(self, *, include_protocol: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }
        if include_protocol:
            headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers


def fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _encode_base64url(payload: bytes) -> str:
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_base64url(payload: str) -> bytes:
    normalized = payload.strip()
    if not normalized:
        raise ValueError("base64url value is empty")
    try:
        return urlsafe_b64decode(normalized + "=" * (-len(normalized) % 4))
    except Exception as exc:
        raise ValueError("base64url value is invalid") from exc


def _decode_payload(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        payload = response.json()
        if not isinstance(payload, dict):
            raise LuckinMcpError("Luckin MCP response must be an object")
        return payload
    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        value = line[5:].strip()
        if not value:
            continue
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            events.append(decoded)
    if len(events) != 1:
        raise LuckinMcpError("Luckin MCP SSE response must contain one JSON-RPC event")
    return events[0]
