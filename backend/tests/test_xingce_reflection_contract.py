from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domains.xingce_learning.real_attempt.schemas import ReflectionCommand


def test_reflection_accepts_selected_cause_without_free_text() -> None:
    command = ReflectionCommand.model_validate(
        {
            "expectedRevision": 1,
            "rawText": "   ",
            "tags": ["misread"],
        }
    )

    assert command.raw_text == ""
    assert command.tags == ["misread"]


def test_reflection_still_requires_a_non_blank_cause_tag() -> None:
    with pytest.raises(ValidationError, match="unique non-blank reflection tags"):
        ReflectionCommand.model_validate(
            {
                "expectedRevision": 1,
                "rawText": "",
                "tags": ["  "],
            }
        )
