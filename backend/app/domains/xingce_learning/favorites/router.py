from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.xingce_learning.favorites.schemas import (
    QuestionFavoritePage,
    QuestionFavoriteReceipt,
    QuestionFavoriteState,
    SetQuestionFavoriteCommand,
)
from app.domains.xingce_learning.favorites.service import (
    QuestionFavoriteError,
    QuestionFavoriteService,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/xingce/question-favorites",
    tags=["xingce-question-favorites"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_question_favorite_service() -> QuestionFavoriteService:
    return QuestionFavoriteService()


def _idempotency_key(
    value: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=160),
    ],
) -> str:
    return value


@router.get(
    "",
    response_model=PublicApiEnvelope[QuestionFavoritePage],
)
def list_question_favorites(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[
        QuestionFavoriteService,
        Depends(get_question_favorite_service),
    ],
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=800,
            description=(
                "账户内实时 keyset 游标；并发取消或重新收藏可能把题目"
                "移到首屏，刷新列表可读取当前权威状态。"
            ),
        ),
    ] = None,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 30,
) -> PublicApiEnvelope[QuestionFavoritePage] | JSONResponse:
    try:
        result = service.list_favorites(
            session,
            user_id=current_user.user_id,
            cursor=cursor,
            page_size=page_size,
        )
        return public_success_envelope(
            request=request,
            response=response,
            data=result,
        )
    except QuestionFavoriteError as exc:
        session.rollback()
        return _error(request, exc)


@router.get(
    "/{question_id}",
    response_model=PublicApiEnvelope[QuestionFavoriteState],
)
def get_question_favorite(
    question_id: Annotated[str, Path(min_length=1, max_length=240)],
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[
        QuestionFavoriteService,
        Depends(get_question_favorite_service),
    ],
) -> PublicApiEnvelope[QuestionFavoriteState] | JSONResponse:
    try:
        result = service.get_state(
            session,
            user_id=current_user.user_id,
            question_id=question_id,
        )
        return public_success_envelope(
            request=request,
            response=response,
            data=result,
        )
    except QuestionFavoriteError as exc:
        session.rollback()
        return _error(request, exc)


@router.put(
    "/{question_id}",
    response_model=PublicApiEnvelope[QuestionFavoriteReceipt],
)
def set_question_favorite(
    question_id: Annotated[str, Path(min_length=1, max_length=240)],
    request: Request,
    response: Response,
    command: SetQuestionFavoriteCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[
        QuestionFavoriteService,
        Depends(get_question_favorite_service),
    ],
) -> PublicApiEnvelope[QuestionFavoriteReceipt] | JSONResponse:
    try:
        result = service.set_state(
            session,
            user_id=current_user.user_id,
            question_id=question_id,
            command_id=command_id,
            command=command,
        )
        session.commit()
        return public_success_envelope(
            request=request,
            response=response,
            data=result,
        )
    except QuestionFavoriteError as exc:
        session.rollback()
        return _error(request, exc)


def _error(request: Request, error: QuestionFavoriteError) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )
