from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.docket.schemas import DocketCard, DocketOption


@dataclass(frozen=True)
class DocketRecord:
    docket_id: str
    user_id: str
    docket_date: date
    status: str
    created_at: datetime
    updated_at: datetime


class DocketRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active_docket_for_date(
        self,
        *,
        user_id: str,
        docket_date: date,
    ) -> DocketRecord | None:
        row = self._session.execute(
            text(
                """
                select
                  docket_id::text as docket_id,
                  user_id::text as user_id,
                  docket_date,
                  status,
                  created_at,
                  updated_at
                from learner.dockets
                where user_id = cast(:user_id as uuid)
                  and docket_date = :docket_date
                  and status = 'active'
                limit 1
                """
            ),
            {"user_id": user_id, "docket_date": docket_date},
        ).mappings().first()
        return _docket_record(row) if row else None

    def create_docket(
        self,
        *,
        user_id: str,
        docket_date: date,
    ) -> DocketRecord:
        row = self._session.execute(
            text(
                """
                insert into learner.dockets (
                  docket_id,
                  user_id,
                  docket_date,
                  status,
                  created_at,
                  updated_at
                ) values (
                  cast(:docket_id as uuid),
                  cast(:user_id as uuid),
                  :docket_date,
                  'active',
                  now(),
                  now()
                )
                on conflict (user_id, docket_date, status)
                do update set updated_at = learner.dockets.updated_at
                returning
                  docket_id::text as docket_id,
                  user_id::text as user_id,
                  docket_date,
                  status,
                  created_at,
                  updated_at
                """
            ),
            {
                "docket_id": str(uuid4()),
                "user_id": user_id,
                "docket_date": docket_date,
            },
        ).mappings().one()
        self._session.commit()
        return _docket_record(row)

    def list_cards(self, *, docket_id: str) -> list[DocketCard]:
        rows = self._session.execute(
            text(
                """
                select
                  dc.card_id::text as card_id,
                  dc.question_id,
                  dc.type,
                  dc.track,
                  dc.module_name,
                  dc.world_label,
                  dc.title,
                  dc.narrative,
                  dc.interaction,
                  dc.source_grounding,
                  dc.payload,
                  q.stem_html,
                  q.material_html,
                  (
                    select jsonb_agg(
                      jsonb_build_object(
                        'label', qo.label,
                        'text', qo.option_text,
                        'html', qo.option_html
                      )
                      order by qo.option_index
                    )
                    from product.question_options qo
                    where qo.question_id = dc.question_id
                  ) as product_options
                from learner.docket_cards dc
                left join product.questions q on q.question_id = dc.question_id
                where dc.docket_id = cast(:docket_id as uuid)
                order by dc.sort_order
                """
            ),
            {"docket_id": docket_id},
        ).mappings().all()
        return [_card_from_row(dict(row)) for row in rows]

    def create_cards(
        self,
        *,
        docket_id: str,
        cards: list[DocketCard],
    ) -> None:
        for index, card in enumerate(cards, start=1):
            self._session.execute(
                text(
                    """
                    insert into learner.docket_cards (
                      card_id,
                      docket_id,
                      question_id,
                      type,
                      track,
                      module_name,
                      world_label,
                      title,
                      narrative,
                      interaction,
                      source_grounding,
                      status,
                      sort_order,
                      payload,
                      created_at,
                      updated_at
                    ) values (
                      cast(:card_id as uuid),
                      cast(:docket_id as uuid),
                      :question_id,
                      :type,
                      :track,
                      :module_name,
                      :world_label,
                      :title,
                      :narrative,
                      :interaction,
                      :source_grounding,
                      'pending',
                      :sort_order,
                      cast(:payload as jsonb),
                      now(),
                      now()
                    )
                    """
                ),
                {
                    "card_id": card.card_id,
                    "docket_id": docket_id,
                    "question_id": card.question_id,
                    "type": card.type,
                    "track": card.track,
                    "module_name": card.module_name,
                    "world_label": card.world_label,
                    "title": card.title,
                    "narrative": card.narrative,
                    "interaction": card.interaction,
                    "source_grounding": card.source_grounding,
                    "sort_order": index,
                    "payload": json.dumps(_card_payload(card), ensure_ascii=False),
                },
            )
        self._session.commit()


def _docket_record(row: Any) -> DocketRecord:
    return DocketRecord(
        docket_id=str(row["docket_id"]),
        user_id=str(row["user_id"]),
        docket_date=row["docket_date"],
        status=str(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _card_payload(card: DocketCard) -> dict[str, Any]:
    return {
        "stem": card.stem,
        "stemHtml": card.stem_html,
        "materialText": card.material_text,
        "materialHtml": card.material_html,
        "options": [
            option.model_dump(by_alias=True)
            for option in card.options
        ],
        "sourceSummary": card.source_summary,
        "occurrenceCount": card.occurrence_count,
        "latestYear": card.latest_year,
    }


def _card_from_row(row: dict[str, Any]) -> DocketCard:
    payload = dict(row["payload"] or {})
    return DocketCard(
        card_id=str(row["card_id"]),
        type=row["type"],
        track=row["track"],
        interaction=row["interaction"],
        question_id=row["question_id"],
        module_name=row["module_name"],
        world_label=row["world_label"],
        title=row["title"],
        narrative=row["narrative"],
        stem=str(payload["stem"]),
        stem_html=_optional_str(row.get("stem_html")) or _optional_str(payload.get("stemHtml")),
        material_text=payload.get("materialText"),
        material_html=(
            _optional_str(row.get("material_html"))
            or _optional_str(payload.get("materialHtml"))
        ),
        options=_options_from_row(row=row, payload=payload),
        source_grounding=row["source_grounding"],
        source_summary=str(payload["sourceSummary"]),
        occurrence_count=payload.get("occurrenceCount"),
        latest_year=payload.get("latestYear"),
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _options_from_row(*, row: dict[str, Any], payload: dict[str, Any]) -> list[DocketOption]:
    product_options = _coerce_json_array(row.get("product_options"))
    if product_options:
        return [DocketOption(**option) for option in product_options]
    return [
        DocketOption(**option)
        for option in payload.get("options", [])
    ]


def _coerce_json_array(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        loaded = json.loads(value)
    else:
        loaded = value
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]
