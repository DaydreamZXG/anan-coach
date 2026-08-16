from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.private_knowledge.repository import PrivateKnowledgeRepository
from app.domains.private_knowledge.schemas import (
    MarkdownExport,
    PrivateSourceDeletionView,
)
from app.domains.private_question_books.repository import (
    PrivateQuestionBookRepository,
)
from app.domains.private_question_books.schemas import (
    CreatePrivateQuestionBookCommand,
    PrivateQuestionBookDetail,
    PrivateQuestionBookList,
    PrivateQuestionBookPracticeLaunch,
    StartPrivateQuestionBookPracticeCommand,
)
from app.domains.private_question_books.service import (
    PrivateQuestionBookError,
    PrivateQuestionBookService,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/question-books",
    tags=["private-question-books"],
    responses=COMMON_API_ERROR_RESPONSES,
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$",
    ),
]


def _service(session: Session) -> PrivateQuestionBookService:
    return PrivateQuestionBookService(
        PrivateQuestionBookRepository(session),
        PrivateKnowledgeRepository(session),
    )


@router.post("", response_model=PublicApiEnvelope[PrivateQuestionBookDetail])
def create_question_book(
    command: CreatePrivateQuestionBookCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateQuestionBookDetail]:
    try:
        result = _service(session).create(
            owner_user_id=current_user.user_id,
            command=command,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateQuestionBookError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("", response_model=PublicApiEnvelope[PrivateQuestionBookList])
def list_question_books(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateQuestionBookList]:
    result = _service(session).list(owner_user_id=current_user.user_id)
    return public_success_envelope(request=request, response=response, data=result)


@router.get(
    "/deletions/{deletion_id}",
    response_model=PublicApiEnvelope[PrivateSourceDeletionView],
)
def get_question_book_deletion(
    deletion_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateSourceDeletionView]:
    try:
        result = _service(session).deletion(
            owner_user_id=current_user.user_id, deletion_id=deletion_id
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateQuestionBookError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/{book_id}/practice-sessions",
    response_model=PublicApiEnvelope[PrivateQuestionBookPracticeLaunch],
)
def start_question_book_practice(
    book_id: UUID,
    command: StartPrivateQuestionBookPracticeCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateQuestionBookPracticeLaunch]:
    try:
        result = _service(session).start_practice(
            owner_user_id=current_user.user_id,
            book_id=book_id,
            command=command,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateQuestionBookError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/{book_id}/export", response_model=PublicApiEnvelope[MarkdownExport]
)
def export_question_book(
    book_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[MarkdownExport]:
    try:
        result = _service(session).export_markdown(
            owner_user_id=current_user.user_id, book_id=book_id
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateQuestionBookError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete(
    "/{book_id}", response_model=PublicApiEnvelope[PrivateSourceDeletionView]
)
def delete_question_book(
    book_id: UUID,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateSourceDeletionView]:
    try:
        result = _service(session).request_delete(
            owner_user_id=current_user.user_id,
            book_id=book_id,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateQuestionBookError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/{book_id}", response_model=PublicApiEnvelope[PrivateQuestionBookDetail])
def get_question_book(
    book_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateQuestionBookDetail]:
    try:
        result = _service(session).get(
            owner_user_id=current_user.user_id, book_id=book_id
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateQuestionBookError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
