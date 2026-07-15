from __future__ import annotations

from app.domains.method_cards.repository import MethodCardRepository
from app.domains.method_cards.schemas import MethodCard


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
