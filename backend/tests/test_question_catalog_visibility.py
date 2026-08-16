from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session
import pytest

from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.knowledge.service import KnowledgeService, QuestionNotFoundError


@pytest.mark.parametrize("visible_status", ("active", "canonical"))
def test_public_detail_keeps_delivery_visible_questions(
    db_session: Session,
    visible_status: str,
) -> None:
    _insert_question(db_session, question_id=f"visible-{visible_status}", status=visible_status)
    service = KnowledgeService(KnowledgeRepository(db_session))

    detail = service.get_question(f"visible-{visible_status}")

    assert detail.question_id == f"visible-{visible_status}"
    assert detail.status == visible_status


@pytest.mark.parametrize(
    "hidden_status",
    (
        "quarantined",
        "merged",
        "needs_identity_review",
        "withdrawn",
        "invalidated",
    ),
)
def test_public_detail_hides_non_delivery_status_before_child_reads(
    db_session: Session,
    hidden_status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question_id = f"hidden-{hidden_status}"
    _insert_question(db_session, question_id=question_id, status=hidden_status)
    repository = KnowledgeRepository(db_session)

    def child_read_must_not_run(_question_id: str):
        raise AssertionError("hidden question reached child-resource lookup")

    for child_reader in (
        "get_explanation",
        "get_options",
        "get_occurrences",
        "get_tags",
        "get_knowledge_chunks",
        "get_assets",
    ):
        monkeypatch.setattr(repository, child_reader, child_read_must_not_run)
    service = KnowledgeService(repository)

    with pytest.raises(QuestionNotFoundError) as error:
        service.get_question(question_id)

    assert error.value.args == (question_id,)


def test_public_detail_hides_unknown_question() -> None:
    class MissingRepository:
        def get_question_row(self, question_id: str):
            return None

    service = KnowledgeService(MissingRepository())  # type: ignore[arg-type]

    with pytest.raises(QuestionNotFoundError):
        service.get_question("unknown")


def test_repository_query_has_closed_public_status_filter() -> None:
    class CapturingResult:
        def mappings(self):
            return self

        def first(self):
            return None

    class CapturingSession:
        statement = ""

        def execute(self, statement, _params):
            self.statement = str(statement)
            return CapturingResult()

    session = CapturingSession()
    repository = KnowledgeRepository(session)  # type: ignore[arg-type]

    assert repository.get_question_row("anything") is None
    normalized = " ".join(session.statement.lower().split())
    assert "where question_id = :question_id" in normalized
    assert "and status in ('active', 'canonical')" in normalized


def _insert_question(session: Session, *, question_id: str, status: str) -> None:
    session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer, shared_context_required, visual_required,
              needs_multimodal
            ) values (
              :question_id, 'objective', :status, '判断推理', '逻辑判断',
              '测试题干', 'A', false, false, false
            )
            """
        ),
        {"question_id": question_id, "status": status},
    )
