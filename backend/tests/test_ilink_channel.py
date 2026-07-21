from __future__ import annotations

import base64
from io import BytesIO

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import httpx
from PIL import Image
import pytest

from app.domains.ilink_channel.crypto import ILinkCipherError, ILinkIdentityCipher
from app.domains.ilink_channel.protocol import (
    ILinkProtocolClient,
    ILinkProtocolError,
    MediaReference,
    decrypt_media,
    parse_inbound_message,
    redacted_protocol_payload,
)
from app.domains.ilink_channel.settings import ILinkConfigurationError, ILinkSettings


def settings() -> ILinkSettings:
    return ILinkSettings(
        enabled=True,
        bot_account_id="anan-bot-1",
        bot_token="official-token-secret",
        base_url="https://ilinkai.weixin.qq.com",
        cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        app_id="bot",
        app_client_version=132102,
        bot_agent="Anan/1.0",
        identity_key_id="ilink-key-v1",
        identity_encryption_key=base64.b64encode(b"e" * 32).decode(),
        identity_hmac_key="h" * 40,
        binding_ttl_seconds=600,
        long_poll_timeout_seconds=35,
        request_timeout_seconds=15,
        maximum_image_bytes=10 * 1024 * 1024,
        transient_retention_seconds=900,
        query_retention_days=30,
        recognition_route="photo-query-approved-v1",
    )


def test_official_message_contract_prefers_raw_image_hex_key() -> None:
    message = parse_inbound_message(
        {
            "message_id": 901,
            "from_user_id": "sender-secret",
            "message_type": 1,
            "context_token": "context-secret",
            "item_list": [
                {
                    "type": 2,
                    "image_item": {
                        "aeskey": "00112233445566778899aabbccddeeff",
                        "media": {
                            "encrypt_query_param": "encrypted-param",
                            "aes_key": base64.b64encode(b"z" * 16).decode(),
                            "full_url": "https://novac2c.cdn.weixin.qq.com/c2c/download?id=1",
                        },
                    },
                }
            ],
        }
    )
    assert message is not None and message.image is not None
    assert base64.b64decode(message.image.aes_key) == bytes.fromhex(
        "00112233445566778899aabbccddeeff"
    )
    assert message.message_id == "901"


def test_aes_ecb_decrypt_accepts_both_official_key_encodings() -> None:
    key = bytes.fromhex("00112233445566778899aabbccddeeff")
    clear = b"question-image-bytes"
    padder = padding.PKCS7(128).padder()
    padded = padder.update(clear) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    assert decrypt_media(encrypted, base64.b64encode(key).decode()) == clear
    assert decrypt_media(
        encrypted, base64.b64encode(key.hex().encode("ascii")).decode()
    ) == clear


class FakeHttpClient:
    def __init__(self, *, get_response=None, post_response=None, post_error=None, **_kwargs):
        self.get_response = get_response
        self.post_response = post_response
        self.post_error = post_error
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _url, **_kwargs):
        return self.get_response

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.post_error:
            raise self.post_error
        return self.post_response


def test_download_requires_official_cdn_origin_and_validates_image() -> None:
    output = BytesIO()
    Image.new("RGB", (96, 96), "white").save(output, format="PNG")
    clear = output.getvalue()
    key = b"k" * 16
    padder = padding.PKCS7(128).padder()
    padded = padder.update(clear) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    response = httpx.Response(
        200,
        content=encrypted,
        request=httpx.Request("GET", "https://novac2c.cdn.weixin.qq.com/c2c/download"),
    )
    client = ILinkProtocolClient(
        settings(), client_factory=lambda **kwargs: FakeHttpClient(get_response=response, **kwargs)
    )
    image = client.download_image(
        MediaReference(
            encrypted_query_param="p",
            full_url="https://novac2c.cdn.weixin.qq.com/c2c/download?id=1",
            aes_key=base64.b64encode(key).decode(),
        )
    )
    assert image.mime_type == "image/png" and image.width == 96

    with pytest.raises(ILinkProtocolError, match="media_origin_rejected"):
        client.download_image(
            MediaReference(
                encrypted_query_param="p",
                full_url="https://attacker.example/c2c/download?id=1",
                aes_key=base64.b64encode(key).decode(),
            )
        )


def test_get_updates_preserves_cursor_and_send_timeout_is_uncertain() -> None:
    updates_response = httpx.Response(
        200,
        json={"ret": 0, "msgs": [], "get_updates_buf": "next-cursor"},
        request=httpx.Request("POST", "https://ilinkai.weixin.qq.com/ilink/bot/getupdates"),
    )
    client = ILinkProtocolClient(
        settings(),
        client_factory=lambda **kwargs: FakeHttpClient(
            post_response=updates_response, **kwargs
        ),
    )
    assert client.get_updates(updates_buf="old-cursor").updates_buf == "next-cursor"

    timeout = httpx.ReadTimeout(
        "timeout", request=httpx.Request("POST", "https://ilinkai.weixin.qq.com")
    )
    uncertain = ILinkProtocolClient(
        settings(),
        client_factory=lambda **kwargs: FakeHttpClient(post_error=timeout, **kwargs),
    )
    with pytest.raises(ILinkProtocolError) as error:
        uncertain.send_text(
            sender_id="sender",
            context_token="context",
            text="reply",
            client_id="client-1",
            run_id=None,
        )
    assert error.value.outcome_uncertain is True
    assert error.value.retryable is False


def test_protocol_redaction_removes_every_channel_secret() -> None:
    redacted = redacted_protocol_payload(
        {
            "context_token": "context-secret",
            "aes_key": "aes-secret",
            "full_url": "https://cdn/secret",
            "Authorization": "Bearer token-secret",
        }
    )
    for secret in ("context-secret", "aes-secret", "cdn/secret", "token-secret"):
        assert secret not in redacted


def test_identity_cipher_rejects_tampered_values() -> None:
    cipher = ILinkIdentityCipher(
        encryption_key=b"e" * 32,
        hmac_key="h" * 40,
        key_id="ilink-key-v1",
    )
    encrypted = cipher.encrypt("sender-secret", purpose="sender:link-1")
    payload = bytearray(base64.urlsafe_b64decode(encrypted))
    payload[-1] ^= 1
    tampered = base64.urlsafe_b64encode(payload).decode()
    with pytest.raises(ILinkCipherError):
        cipher.decrypt(tampered, purpose="sender:link-1")


def test_environment_number_errors_are_controlled_configuration_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ILINK_APP_CLIENT_VERSION", "not-a-number")
    with pytest.raises(ILinkConfigurationError, match="must be an integer"):
        ILinkSettings.from_environment()
