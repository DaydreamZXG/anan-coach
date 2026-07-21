from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.private_knowledge.repository import (
    PrivateKnowledgeConflict,
    PrivateKnowledgeNotFound,
    PrivateKnowledgeRepository,
)
from app.domains.private_knowledge.projection import (
    PrivateKnowledgeProjectionError,
    build_projection_gateway,
)
from app.domains.private_knowledge.schemas import (
    CreatePrivateSourceCommand,
    MarkdownExport,
    PrivateSearchResult,
    PrivateSourceDeletionView,
    PrivateSourceList,
    PrivateSourceSummary,
)
from app.domains.private_knowledge.service import PrivateKnowledgeService
from app.internal_services.private_objects import PrivateObjectReader
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/private-knowledge",
    tags=["private-knowledge"],
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


def _service(
    session: Session,
    *,
    requires_reader: bool = False,
    requires_projection: bool = False,
) -> PrivateKnowledgeService:
    reader = PrivateObjectReader(get_settings()) if requires_reader else None
    return PrivateKnowledgeService(
        PrivateKnowledgeRepository(session),
        markdown_reader=reader,
        projection_gateway=(
            build_projection_gateway(session) if requires_projection else None
        ),
    )


@router.post("/sources", response_model=PublicApiEnvelope[PrivateSourceSummary])
def create_source(
    command: CreatePrivateSourceCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateSourceSummary]:
    try:
        artifact = PrivateKnowledgeRepository(session).artifact(
            owner_user_id=current_user.user_id,
            artifact_version_id=command.artifact_version_id,
        )
        result = _service(
            session,
            requires_reader=bool(artifact and artifact["mime_type"] == "text/markdown"),
            requires_projection=True,
        ).create(
            owner_user_id=current_user.user_id,
            command=command,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except (PrivateKnowledgeConflict, PrivateKnowledgeProjectionError, ValueError) as exc:
        session.rollback()
        status = 503 if isinstance(exc, PrivateKnowledgeProjectionError) and exc.retryable else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/sources", response_model=PublicApiEnvelope[PrivateSourceList])
def list_sources(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateSourceList]:
    result = _service(session).list(owner_user_id=current_user.user_id)
    return public_success_envelope(request=request, response=response, data=result)


@router.get("/search", response_model=PublicApiEnvelope[PrivateSearchResult])
def search_sources(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=12)] = 8,
) -> PublicApiEnvelope[PrivateSearchResult]:
    try:
        result = _service(session, requires_projection=True).search(
            owner_user_id=current_user.user_id, query=q, maximum_sources=limit
        )
        return public_success_envelope(request=request, response=response, data=result)
    except (PrivateKnowledgeConflict, PrivateKnowledgeProjectionError) as exc:
        status = 503 if isinstance(exc, PrivateKnowledgeProjectionError) and exc.retryable else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get(
    "/sources/{source_id}/markdown", response_model=PublicApiEnvelope[MarkdownExport]
)
def export_markdown(
    source_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[MarkdownExport]:
    try:
        result = _service(session).export_markdown(
            owner_user_id=current_user.user_id, source_id=source_id
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateKnowledgeNotFound as exc:
        raise HTTPException(status_code=404, detail="私人资料不存在。") from exc


@router.delete(
    "/sources/{source_id}", response_model=PublicApiEnvelope[PrivateSourceDeletionView]
)
def delete_source(
    source_id: UUID,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PrivateSourceDeletionView]:
    try:
        result = _service(session, requires_projection=True).request_delete(
            owner_user_id=current_user.user_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PrivateKnowledgeNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="私人资料不存在。") from exc
    except PrivateKnowledgeConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PrivateKnowledgeProjectionError as exc:
        session.rollback()
        status = 503 if exc.retryable else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get(
    "/deletions/{deletion_id}",
    response_model=PublicApiEnvelope[PrivateSourceDeletionView],
)
def deletion_status(
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
    except PrivateKnowledgeNotFound as exc:
        raise HTTPException(status_code=404, detail="删除任务不存在。") from exc
