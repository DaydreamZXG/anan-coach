from __future__ import annotations

from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
import unicodedata


_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _SPACE.sub("", normalized)
    return _PUNCT.sub("", normalized)


def text_similarity(left: str, right: str) -> float:
    a = normalize_text(left)
    b = normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    bigrams_a = {a[index : index + 2] for index in range(max(1, len(a) - 1))}
    bigrams_b = {b[index : index + 2] for index in range(max(1, len(b) - 1))}
    union = bigrams_a | bigrams_b
    jaccard = len(bigrams_a & bigrams_b) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return round((jaccard * 0.6) + (sequence * 0.4), 12)


def visual_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    best = 0.0
    for first in left:
        for second in right:
            xor = int(first, 16) ^ int(second, 16)
            score = 1 - (xor.bit_count() / 256)
            best = max(best, score)
    return round(best, 12)


def canonical_fingerprint(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()
