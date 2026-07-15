from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class EssayRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_training_question(self) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select
                  q.question_id,
                  q.question_type,
                  q.stem,
                  q.latest_year,
                  q.occurrence_count,
                  count(kc.knowledge_chunk_id) filter (where kc.chunk_kind = 'material')
                    as material_count
                from product.questions q
                left join product.knowledge_chunks kc on kc.question_id = q.question_id
                where q.status = 'active'
                  and q.domain = 'essay'
                  and q.question_type in ('summary', 'comprehensive_analysis', 'countermeasure')
                group by q.question_id, q.question_type, q.stem, q.latest_year, q.occurrence_count
                order by
                  count(kc.knowledge_chunk_id) filter (where kc.chunk_kind = 'material') desc,
                  q.latest_year desc nulls last,
                  q.occurrence_count desc nulls last,
                  q.question_id
                limit 1
                """
            )
        ).mappings().first()
        return dict(row) if row else None

    def get_question(self, *, question_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select
                  question_id,
                  question_type,
                  stem,
                  latest_year,
                  occurrence_count
                from product.questions
                where question_id = :question_id
                  and domain = 'essay'
                  and status = 'active'
                """
            ),
            {"question_id": question_id},
        ).mappings().first()
        return dict(row) if row else None

    def list_material_chunks(self, *, question_id: str) -> list[dict[str, Any]]:
        rows = self._session.execute(
            text(
                """
                select
                  chunk_index,
                  title,
                  content,
                  metadata
                from product.knowledge_chunks
                where question_id = :question_id
                  and chunk_kind = 'material'
                order by chunk_index nulls last, knowledge_chunk_id
                """
            ),
            {"question_id": question_id},
        ).mappings().all()
        return [dict(row) for row in rows]
