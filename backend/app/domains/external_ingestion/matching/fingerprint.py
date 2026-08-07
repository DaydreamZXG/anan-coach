from __future__ import annotations

from collections.abc import Callable
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
import unicodedata

from app.domains.external_ingestion.recognition.models import VisualFeatureHash


_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_VISUAL_DEPENDENT_PROMPT = re.compile(
    r"(?:左|右|上|下)(?:图|边)|图中|图形|纸盒|折叠|展开图|阴影|立体|"
    r"折线图|柱状图|饼图|示意图|表中"
)


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


def grouped_text_similarity(
    *,
    query_prompt: str,
    content_prompt: str,
    query_options: tuple[str, ...] = (),
    content_options: tuple[str, ...] = (),
    query_materials: tuple[str, ...] = (),
    content_materials: tuple[str, ...] = (),
    scorer: Callable[[str, str], float] | None = None,
) -> float:
    """Compare like-for-like OCR groups so page chrome cannot dilute a stem match."""

    pairs: list[tuple[float, str, str]] = []
    if normalize_text(query_prompt) and normalize_text(content_prompt):
        pairs.append((3.0, query_prompt, content_prompt))
    query_option_text = "\n".join(query_options)
    content_option_text = "\n".join(content_options)
    if normalize_text(query_option_text) and normalize_text(content_option_text):
        pairs.append((2.0, query_option_text, content_option_text))
    query_material_text = "\n".join(query_materials)
    content_material_text = "\n".join(content_materials)
    if normalize_text(query_material_text) and normalize_text(content_material_text):
        pairs.append((3.0, query_material_text, content_material_text))
    if not pairs:
        return 0.0
    compare = scorer or text_similarity
    total_weight = sum(weight for weight, _, _ in pairs)
    return round(
        sum(weight * compare(left, right) for weight, left, right in pairs)
        / total_weight,
        12,
    )


def requires_visual_evidence(prompt_text: str) -> bool:
    """Whether a question's answer depends on a referenced figure or table."""

    return bool(_VISUAL_DEPENDENT_PROMPT.search(normalize_text(prompt_text)))


def visual_similarity(
    left: tuple[VisualFeatureHash, ...], right: tuple[VisualFeatureHash, ...]
) -> float:
    if not left or not right:
        return 0.0
    best = 0.0
    for first in left:
        for second in right:
            score = sum(
                1
                - (
                    (
                        int(getattr(first, name), 16)
                        ^ int(getattr(second, name), 16)
                    ).bit_count()
                    / 64
                )
                for name in ("phash", "dhash", "ahash", "edge_dhash")
            ) / 4
            best = max(best, score)
    return round(best, 12)


def canonical_fingerprint(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()
