from __future__ import annotations

import base64
from hashlib import sha256
import hmac
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ILinkCipherError(RuntimeError):
    pass


class ILinkIdentityCipher:
    def __init__(self, *, encryption_key: bytes, hmac_key: str, key_id: str) -> None:
        if len(encryption_key) != 32:
            raise ValueError("iLink identity encryption requires AES-256")
        if len(hmac_key.encode("utf-8")) < 32 or not key_id:
            raise ValueError("iLink identity HMAC key and key ID are required")
        self._aead = AESGCM(encryption_key)
        self._hmac_key = hmac_key.encode("utf-8")
        self.key_id = key_id

    def encrypt(self, value: str, *, purpose: str) -> str:
        if not value or not purpose:
            raise ValueError("encrypted iLink values require content and purpose")
        nonce = os.urandom(12)
        encrypted = self._aead.encrypt(nonce, value.encode("utf-8"), purpose.encode())
        return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")

    def decrypt(self, value: str, *, purpose: str) -> str:
        try:
            payload = base64.urlsafe_b64decode(value.encode("ascii"))
            if len(payload) < 29:
                raise ValueError("ciphertext is too short")
            clear = self._aead.decrypt(
                payload[:12], payload[12:], purpose.encode("utf-8")
            )
            return clear.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise ILinkCipherError("iLink encrypted value is invalid") from exc

    def fingerprint(self, value: str, *, purpose: str) -> str:
        return hmac.new(
            self._hmac_key,
            f"{purpose}\0{value}".encode("utf-8"),
            sha256,
        ).hexdigest()

    def fingerprint_payload(self, value: object, *, purpose: str) -> str:
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return self.fingerprint(canonical, purpose=purpose)
