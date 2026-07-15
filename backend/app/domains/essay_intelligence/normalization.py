from __future__ import annotations

import unicodedata


NORMALIZATION_CONTRACT = "nfkc_whitespace_v1"


def normalize_grounding_text(value: str) -> str:
    """Apply the versioned normalization used by every grounded quote anchor."""

    return " ".join(unicodedata.normalize("NFKC", value).split())
