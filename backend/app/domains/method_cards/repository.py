from __future__ import annotations

from dataclasses import dataclass, field
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
    knowledge_nodes: list[str] = field(default_factory=list)


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

    def list_catalog(
        self,
        *,
        track: str,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        filters = "status in ('draft', 'published') and track = :track"
        total = int(
            self._session.execute(
                text(f"select count(*) from product.method_cards where {filters}"),
                {"track": track},
            ).scalar_one()
        )
        rows = self._session.execute(
            text(
                f"""
                select id, track, module_name, title, recognition,
                       method_steps, pitfall, status
                from product.method_cards
                where {filters}
                order by
                  case status when 'published' then 0 else 1 end,
                  priority desc,
                  module_name,
                  id
                limit :limit offset :offset
                """
            ),
            {"track": track, "limit": limit, "offset": offset},
        ).mappings()
        return total, [dict(row) for row in rows]

    def upsert_cards(self, cards: list[MethodCardRecord]) -> int:
        """Write cards through the METHOD-CARD-REPLAY@v1 database contract.

        The INSERT path is intentional: migration 73's BEFORE INSERT guard
        accepts canonical exact replays for released cards and rejects drift
        before PostgreSQL reaches the conflict update. The conflict predicate
        then preserves both timestamps for exact replay while retaining
        contract-compliant updates for unreferenced drafts.
        """
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
                      knowledge_nodes,
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
                      cast(:knowledge_nodes as jsonb),
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
                      knowledge_nodes = excluded.knowledge_nodes,
                      updated_at = now()
                    where (
                      product.method_cards.exam_scope,
                      product.method_cards.track,
                      product.method_cards.module_name,
                      product.method_cards.tag_path,
                      product.method_cards.title,
                      product.method_cards.recognition,
                      product.method_cards.applicability,
                      product.method_cards.method_steps,
                      product.method_cards.error_signatures,
                      product.method_cards.fix_action,
                      product.method_cards.time_budget_sec,
                      product.method_cards.accuracy_rule,
                      product.method_cards.pitfall,
                      product.method_cards.author_note,
                      product.method_cards.validation,
                      product.method_cards.priority,
                      product.method_cards.status,
                      product.method_cards.knowledge_nodes
                    ) is distinct from (
                      excluded.exam_scope,
                      excluded.track,
                      excluded.module_name,
                      excluded.tag_path,
                      excluded.title,
                      excluded.recognition,
                      excluded.applicability,
                      excluded.method_steps,
                      excluded.error_signatures,
                      excluded.fix_action,
                      excluded.time_budget_sec,
                      excluded.accuracy_rule,
                      excluded.pitfall,
                      excluded.author_note,
                      excluded.validation,
                      excluded.priority,
                      excluded.status,
                      excluded.knowledge_nodes
                    )
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
                    "knowledge_nodes": _json(card.knowledge_nodes),
                },
            )
        self._session.commit()
        return len(cards)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
