from __future__ import annotations

from app.domains.method_cards.repository import MethodCardRepository
from app.domains.method_cards.schemas import MethodCard, MethodCardCatalog


class MethodCardNotFoundError(LookupError):
    pass


class MethodCardService:
    def __init__(self, repository: MethodCardRepository) -> None:
        self._repository = repository

    def get_card(self, card_id: str) -> MethodCard:
        row = self._repository.get_published_card(card_id)
        if row is None:
            raise MethodCardNotFoundError(card_id)
        return MethodCard(**row)

    def list_catalog(self, *, track: str, limit: int, offset: int) -> MethodCardCatalog:
        total, rows = self._repository.list_catalog(track=track, limit=limit, offset=offset)
        return MethodCardCatalog(total=total, cards=rows)
