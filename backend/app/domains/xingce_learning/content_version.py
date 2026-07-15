from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping


QUESTION_CONTENT_FIELDS = (
    "question_id",
    "module_name",
    "question_type",
    "stem",
    "stem_html",
    "answer",
    "material_text",
    "material_html",
    "preferred_analysis",
    "analysis_source",
    "analysis_status",
    "prefer_ai_analysis",
    "analysis_needs_review",
    "options",
    "assets",
)


def question_content_version_id(source: Mapping[str, Any]) -> str:
    """Return the canonical immutable content version used by every delivery path."""
    payload = {field: source.get(field) for field in QUESTION_CONTENT_FIELDS}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
