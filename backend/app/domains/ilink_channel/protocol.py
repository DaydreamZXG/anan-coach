from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
import json
import os
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import httpx
from PIL import Image, UnidentifiedImageError

from app.domains.ilink_channel.settings import ILinkSettings
from app.domains.ilink_channel.upstream import UPSTREAM_VERSION


TEXT_ITEM = 1
IMAGE_ITEM = 2
USER_MESSAGE = 1
BOT_MESSAGE = 2
MESSAGE_FINISH = 2


class ILinkProtocolError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        outcome_uncertain: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.outcome_uncertain = outcome_uncertain


@dataclass(frozen=True)
class MediaReference:
    encrypted_query_param: str
    full_url: str | None
    aes_key: str


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    sender_id: str
    context_token: str
    run_id: str | None
    text: str
    image: MediaReference | None


@dataclass(frozen=True)
class UpdatesBatch:
    messages: tuple[InboundMessage, ...]
    updates_buf: str
    long_poll_timeout_ms: int | None


@dataclass(frozen=True)
class ValidatedImage:
    payload: bytes
    mime_type: str
    width: int
    height: int


class ILinkProtocolClient:
    def __init__(
        self,
        settings: ILinkSettings,
        *,
        client_factory: Callable[..., httpx.Client] | None = None,
    ) -> None:
        settings.validate_worker()
        self._settings = settings
        self._client_factory = client_factory or httpx.Client

    def get_updates(self, *, updates_buf: str) -> UpdatesBatch:
        payload = self._post(
            "ilink/bot/getupdates",
            {
                "get_updates_buf": updates_buf,
                "base_info": self._base_info(),
            },
            timeout=self._settings.long_poll_timeout_seconds + 5,
            uncertain_on_network=False,
        )
        ret = _integer(payload.get("ret"), default=0)
        errcode = _integer(payload.get("errcode"), default=0)
        if ret != 0 or errcode != 0:
            if errcode == -14:
                raise ILinkProtocolError("bot_token_stale", retryable=False)
            raise ILinkProtocolError("get_updates_rejected", retryable=True)
        raw_messages = payload.get("msgs", [])
        if not isinstance(raw_messages, list):
            raise ILinkProtocolError("get_updates_contract_invalid")
        messages = tuple(
            message
            for raw in raw_messages
            if isinstance(raw, dict) and (message := parse_inbound_message(raw)) is not None
        )
        next_buf = payload.get("get_updates_buf", updates_buf)
        if not isinstance(next_buf, str):
            raise ILinkProtocolError("get_updates_cursor_invalid")
        timeout_ms = payload.get("longpolling_timeout_ms")
        return UpdatesBatch(
            messages=messages,
            updates_buf=next_buf,
            long_poll_timeout_ms=(
                int(timeout_ms) if isinstance(timeout_ms, int) and timeout_ms > 0 else None
            ),
        )

    def send_text(
        self,
        *,
        sender_id: str,
        context_token: str,
        text: str,
        client_id: str,
        run_id: str | None,
    ) -> None:
        if not sender_id or not context_token or not text or not client_id:
            raise ILinkProtocolError("send_message_input_invalid")
        payload = self._post(
            "ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": sender_id,
                    "client_id": client_id,
                    "message_type": BOT_MESSAGE,
                    "message_state": MESSAGE_FINISH,
                    "item_list": [{"type": TEXT_ITEM, "text_item": {"text": text}}],
                    "context_token": context_token,
                    **({"run_id": run_id} if run_id else {}),
                },
                "base_info": self._base_info(),
            },
            timeout=self._settings.request_timeout_seconds,
            uncertain_on_network=True,
        )
        if _integer(payload.get("ret"), default=0) != 0:
            raise ILinkProtocolError("send_message_rejected", retryable=False)

    def download_image(self, media: MediaReference) -> ValidatedImage:
        url = _validated_media_url(media, settings=self._settings)
        headers = {"Accept": "application/octet-stream"}
        try:
            with self._client_factory(
                timeout=httpx.Timeout(self._settings.request_timeout_seconds, connect=10.0),
                follow_redirects=False,
            ) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ILinkProtocolError(
                "media_download_rejected", retryable=exc.response.status_code >= 500
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ILinkProtocolError("media_download_unavailable", retryable=True) from exc
        if response.is_redirect:
            raise ILinkProtocolError("media_redirect_rejected")
        if len(response.content) > self._settings.maximum_image_bytes + 16:
            raise ILinkProtocolError("media_too_large")
        clear = decrypt_media(response.content, media.aes_key)
        if len(clear) > self._settings.maximum_image_bytes:
            raise ILinkProtocolError("media_too_large")
        return validate_image(clear)

    def _post(
        self,
        endpoint: str,
        body: dict[str, object],
        *,
        timeout: float,
        uncertain_on_network: bool,
    ) -> dict[str, Any]:
        url = f"{self._settings.base_url}/{endpoint}"
        try:
            with self._client_factory(
                timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
                follow_redirects=False,
            ) as client:
                response = client.post(
                    url,
                    headers=self._headers(),
                    content=json.dumps(body, ensure_ascii=False, sort_keys=True),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ILinkProtocolError(
                "ilink_http_rejected",
                retryable=exc.response.status_code >= 500 and not uncertain_on_network,
                outcome_uncertain=False,
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ILinkProtocolError(
                "ilink_network_unavailable",
                retryable=not uncertain_on_network,
                outcome_uncertain=uncertain_on_network,
            ) from exc
        if response.is_redirect:
            raise ILinkProtocolError("ilink_redirect_rejected")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ILinkProtocolError("ilink_response_invalid") from exc
        if not isinstance(payload, dict):
            raise ILinkProtocolError("ilink_response_invalid")
        return payload

    def _headers(self) -> dict[str, str]:
        random_uin = int.from_bytes(os.urandom(4), "big")
        encoded_uin = base64.b64encode(str(random_uin).encode("ascii")).decode("ascii")
        return {
            "Authorization": f"Bearer {self._settings.bot_token}",
            "AuthorizationType": "ilink_bot_token",
            "Content-Type": "application/json",
            "X-WECHAT-UIN": encoded_uin,
            "iLink-App-Id": self._settings.app_id,
            "iLink-App-ClientVersion": str(self._settings.app_client_version),
        }

    def _base_info(self) -> dict[str, str]:
        return {"channel_version": UPSTREAM_VERSION, "bot_agent": self._settings.bot_agent}


def parse_inbound_message(payload: dict[str, Any]) -> InboundMessage | None:
    if _integer(payload.get("message_type"), default=USER_MESSAGE) != USER_MESSAGE:
        return None
    message_id = payload.get("message_id")
    sender_id = payload.get("from_user_id")
    context_token = payload.get("context_token")
    items = payload.get("item_list")
    if (
        not isinstance(message_id, (str, int))
        or not str(message_id).strip()
        or not isinstance(sender_id, str)
        or not sender_id.strip()
        or not isinstance(context_token, str)
        or not context_token.strip()
        or not isinstance(items, list)
    ):
        raise ILinkProtocolError("inbound_message_contract_invalid")
    texts: list[str] = []
    images: list[MediaReference] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = _integer(item.get("type"), default=0)
        if item_type == TEXT_ITEM:
            text_item = item.get("text_item")
            value = text_item.get("text") if isinstance(text_item, dict) else None
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
        elif item_type == IMAGE_ITEM:
            image = item.get("image_item")
            if not isinstance(image, dict):
                raise ILinkProtocolError("image_item_contract_invalid")
            media = image.get("media")
            if not isinstance(media, dict):
                raise ILinkProtocolError("image_media_contract_invalid")
            encrypted_query = media.get("encrypt_query_param")
            full_url = media.get("full_url")
            raw_hex_key = image.get("aeskey")
            media_key = media.get("aes_key")
            aes_key = (
                base64.b64encode(bytes.fromhex(raw_hex_key)).decode("ascii")
                if isinstance(raw_hex_key, str)
                and len(raw_hex_key) == 32
                and all(char in "0123456789abcdefABCDEF" for char in raw_hex_key)
                else media_key
            )
            if (
                not isinstance(encrypted_query, str)
                or not encrypted_query
                or not isinstance(aes_key, str)
                or not aes_key
                or (full_url is not None and not isinstance(full_url, str))
            ):
                raise ILinkProtocolError("image_media_contract_invalid")
            images.append(
                MediaReference(
                    encrypted_query_param=encrypted_query,
                    full_url=full_url,
                    aes_key=aes_key,
                )
            )
    if len(images) > 1:
        raise ILinkProtocolError("multiple_images_not_supported")
    run_id = payload.get("run_id")
    return InboundMessage(
        message_id=str(message_id),
        sender_id=sender_id,
        context_token=context_token,
        run_id=run_id if isinstance(run_id, str) and run_id else None,
        text="\n".join(texts),
        image=images[0] if images else None,
    )


def decrypt_media(ciphertext: bytes, aes_key_base64: str) -> bytes:
    try:
        decoded = base64.b64decode(aes_key_base64, validate=True)
        if len(decoded) == 32 and all(
            chr(char) in "0123456789abcdefABCDEF" for char in decoded
        ):
            decoded = bytes.fromhex(decoded.decode("ascii"))
        if len(decoded) != 16 or not ciphertext or len(ciphertext) % 16:
            raise ValueError("invalid AES media shape")
        decryptor = Cipher(algorithms.AES(decoded), modes.ECB()).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except (ValueError, TypeError) as exc:
        raise ILinkProtocolError("media_decryption_failed") from exc


def validate_image(payload: bytes) -> ValidatedImage:
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
            image_format = image.format
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ILinkProtocolError("media_invalid_image") from exc
    mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(str(image_format))
    if mime is None or width < 64 or height < 64 or width * height > 40_000_000:
        raise ILinkProtocolError("media_unsupported_image")
    return ValidatedImage(payload=payload, mime_type=mime, width=width, height=height)


def redacted_protocol_payload(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for field in (
        "context_token",
        "bot_token",
        "Authorization",
        "aeskey",
        "aes_key",
        "full_url",
        "encrypt_query_param",
    ):
        text = _redact_json_field(text, field)
    return text


def _validated_media_url(media: MediaReference, *, settings: ILinkSettings) -> str:
    url = media.full_url or (
        f"{settings.cdn_base_url}/download?encrypted_query_param="
        f"{quote(media.encrypted_query_param, safe='')}"
    )
    parsed = urlsplit(url)
    base = urlsplit(settings.cdn_base_url)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname != base.hostname
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith(base.path.rstrip("/") + "/")
    ):
        raise ILinkProtocolError("media_origin_rejected")
    return url


def _integer(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    return int(value) if isinstance(value, (int, str)) and str(value).lstrip("-").isdigit() else default


def _redact_json_field(value: str, field: str) -> str:
    marker = f'"{field}":'
    start = 0
    while (index := value.find(marker, start)) >= 0:
        quote_start = value.find('"', index + len(marker))
        if quote_start < 0:
            break
        quote_end = value.find('"', quote_start + 1)
        if quote_end < 0:
            break
        value = value[: quote_start + 1] + "***" + value[quote_end:]
        start = quote_start + 4
    return value
