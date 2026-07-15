from __future__ import annotations

from hashlib import sha256
import json


def answer_binding_digest(value: str) -> str:
    payload = {"scope": "packet-answer-binding", "value": value.strip().upper()}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()
