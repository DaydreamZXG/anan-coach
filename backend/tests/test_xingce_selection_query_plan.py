from __future__ import annotations

from typing import Any

from app.domains.xingce_learning.real_attempt.repository import (
    XingceAttemptRepository,
)


class _EmptyMappings:
    def mappings(self) -> _EmptyMappings:
        return self

    def all(self) -> list[Any]:
        return []


class _CapturingSession:
    def __init__(self) -> None:
        self.statement = ""

    def execute(
        self,
        statement: Any,
        parameters: dict[str, Any],
    ) -> _EmptyMappings:
        self.statement = str(statement)
        return _EmptyMappings()


def test_question_selection_limits_candidates_before_loading_content() -> None:
    session = _CapturingSession()

    selected = XingceAttemptRepository()._select_eligible_questions(
        session,  # type: ignore[arg-type]
        release_id="00000000-0000-0000-0000-000000000001",
        node_id=None,
        canonical_modules=None,
        limit=3,
        user_id="00000000-0000-0000-0000-000000000002",
        excluded_question_ids=None,
    )

    assert selected == []
    normalized = " ".join(session.statement.split())
    ranked = normalized.index("ranked_candidates as materialized")
    content_join = normalized.index("join product.question_content_versions")
    assert ranked < content_join
    assert normalized.index("limit :batch_size", ranked) < content_join
