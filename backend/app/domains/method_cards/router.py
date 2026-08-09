from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.method_cards.repository import MethodCardRepository
from app.domains.method_cards.schemas import MethodCard, MethodCardCatalog
from app.domains.method_cards.service import MethodCardNotFoundError, MethodCardService

router = APIRouter(prefix="/api/v1/method-cards", tags=["method-cards"])


def get_method_card_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> MethodCardService:
    return MethodCardService(MethodCardRepository(session))


@router.get("", response_model=MethodCardCatalog)
def list_method_card_catalog(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MethodCardService, Depends(get_method_card_service)],
    track: Literal["xingce", "shenlun"] = "xingce",
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MethodCardCatalog:
    del current_user
    return service.list_catalog(track=track, limit=limit, offset=offset)


@router.get("/{card_id}", response_model=MethodCard)
def get_method_card(
    card_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MethodCardService, Depends(get_method_card_service)],
) -> MethodCard:
    del current_user
    try:
        return service.get_card(card_id)
    except MethodCardNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Method card not found",
        ) from exc
