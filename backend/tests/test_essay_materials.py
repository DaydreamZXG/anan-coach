from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.taskstream.service import TODAY_ESSAY_TASK_ID
from app.main import create_app


def test_essay_task_returns_full_materials_from_question_chunks(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_essay_question_with_material_chunks(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.get(f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["questionId"] == "cq_essay_material"
    assert body["materialIncomplete"] is False
    assert [material["title"] for material in body["materials"]] == ["材料1", "材料2"]
    assert body["materials"][0]["text"] == "第一段材料上半部分。\n第一段材料下半部分。"
    assert body["materials"][1]["text"] == "第二段完整材料。"
    assert body["material"] == (
        "材料1\n第一段材料上半部分。\n第一段材料下半部分。\n\n"
        "材料2\n第二段完整材料。"
    )


def _make_db_client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _seed_essay_question_with_material_chunks(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id,
                  domain,
                  status,
                  module_name,
                  question_type,
                  stem,
                  visual_required,
                  needs_multimodal,
                  shared_context_required,
                  latest_year,
                  occurrence_count
                ) values (
                  'cq_essay_material',
                  'essay',
                  'active',
                  '申论',
                  'summary',
                  '请概括给定资料的主要内容，不超过200字。',
                  false,
                  false,
                  true,
                  2026,
                  1
                )
                """
            )
        )
        chunks = [
            (
                "kc_material_1_1",
                1,
                "材料1（1/2）",
                "第一段材料上半部分。",
                '{"paperTitle":"fixture","materialPartCount":2,"materialPartIndex":1}',
            ),
            (
                "kc_material_1_2",
                2,
                "材料1（2/2）",
                "第一段材料下半部分。",
                '{"paperTitle":"fixture","materialPartCount":2,"materialPartIndex":2}',
            ),
            (
                "kc_material_2",
                3,
                "材料2",
                "第二段完整材料。",
                '{"paperTitle":"fixture","materialPartCount":1,"materialPartIndex":1}',
            ),
        ]
        for chunk_id, index, title, content, metadata in chunks:
            session.execute(
                text(
                    """
                    insert into product.knowledge_chunks (
                      knowledge_chunk_id,
                      question_id,
                      chunk_kind,
                      chunk_index,
                      title,
                      content,
                      metadata
                    ) values (
                      :chunk_id,
                      'cq_essay_material',
                      'material',
                      :chunk_index,
                      :title,
                      :content,
                      cast(:metadata as jsonb)
                    )
                    """
                ),
                {
                    "chunk_id": chunk_id,
                    "chunk_index": index,
                    "title": title,
                    "content": content,
                    "metadata": metadata,
                },
            )
        session.commit()
