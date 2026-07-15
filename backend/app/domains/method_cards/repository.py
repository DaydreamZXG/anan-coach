from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class MethodCardRecord:
    id: str
    exam_scope: str
    track: str
    module_name: str
    tag_path: str
    title: str
    recognition: str
    applicability: dict[str, list[str]]
    method_steps: list[str]
    error_signatures: list[dict[str, str]]
    fix_action: str
    time_budget_sec: int
    accuracy_rule: str
    pitfall: str | None
    author_note: str
    validation: dict[str, Any]
    priority: int
    status: str


class MethodCardRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_published_card(self, card_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select
                  id,
                  exam_scope,
                  track,
                  module_name,
                  tag_path,
                  title,
                  recognition,
                  applicability,
                  method_steps,
                  error_signatures,
                  fix_action,
                  time_budget_sec,
                  accuracy_rule,
                  pitfall,
                  priority,
                  status
                from product.method_cards
                where id = :card_id
                  and status = 'published'
                """
            ),
            {"card_id": card_id},
        ).mappings().first()
        return dict(row) if row else None

    def upsert_cards(self, cards: list[MethodCardRecord]) -> int:
        for card in cards:
            self._session.execute(
                text(
                    """
                    insert into product.method_cards (
                      id,
                      exam_scope,
                      track,
                      module_name,
                      tag_path,
                      title,
                      recognition,
                      applicability,
                      method_steps,
                      error_signatures,
                      fix_action,
                      time_budget_sec,
                      accuracy_rule,
                      pitfall,
                      author_note,
                      validation,
                      priority,
                      status,
                      created_at,
                      updated_at
                    ) values (
                      :id,
                      :exam_scope,
                      :track,
                      :module_name,
                      :tag_path,
                      :title,
                      :recognition,
                      cast(:applicability as jsonb),
                      cast(:method_steps as jsonb),
                      cast(:error_signatures as jsonb),
                      :fix_action,
                      :time_budget_sec,
                      :accuracy_rule,
                      :pitfall,
                      :author_note,
                      cast(:validation as jsonb),
                      :priority,
                      :status,
                      now(),
                      now()
                    )
                    on conflict (id) do update set
                      exam_scope = excluded.exam_scope,
                      track = excluded.track,
                      module_name = excluded.module_name,
                      tag_path = excluded.tag_path,
                      title = excluded.title,
                      recognition = excluded.recognition,
                      applicability = excluded.applicability,
                      method_steps = excluded.method_steps,
                      error_signatures = excluded.error_signatures,
                      fix_action = excluded.fix_action,
                      time_budget_sec = excluded.time_budget_sec,
                      accuracy_rule = excluded.accuracy_rule,
                      pitfall = excluded.pitfall,
                      author_note = excluded.author_note,
                      validation = excluded.validation,
                      priority = excluded.priority,
                      status = excluded.status,
                      updated_at = now()
                    """
                ),
                {
                    "id": card.id,
                    "exam_scope": card.exam_scope,
                    "track": card.track,
                    "module_name": card.module_name,
                    "tag_path": card.tag_path,
                    "title": card.title,
                    "recognition": card.recognition,
                    "applicability": _json(card.applicability),
                    "method_steps": _json(card.method_steps),
                    "error_signatures": _json(card.error_signatures),
                    "fix_action": card.fix_action,
                    "time_budget_sec": card.time_budget_sec,
                    "accuracy_rule": card.accuracy_rule,
                    "pitfall": card.pitfall,
                    "author_note": card.author_note,
                    "validation": _json(card.validation),
                    "priority": card.priority,
                    "status": card.status,
                },
            )
        self._session.commit()
        return len(cards)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
