from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import DatabaseConfigurationError, get_db_session
from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.knowledge.schemas import QuestionDetail, QuestionSearchResponse
from app.domains.knowledge.service import KnowledgeService, QuestionNotFoundError

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def get_knowledge_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> KnowledgeService:
    return KnowledgeService(KnowledgeRepository(session))


@router.get("/questions/search", response_model=QuestionSearchResponse)
def search_questions(
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    domain: str | None = Query(default=None),
    module_name: str | None = Query(default=None, alias="moduleName"),
    question_type: str | None = Query(default=None, alias="questionType"),
    tag: str | None = Query(default=None),
    province: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=1990, le=2100),
    requires_images: bool | None = Query(default=None, alias="requiresImages"),
    limit: int = Query(default=20, ge=1, le=100),
) -> QuestionSearchResponse:
    return service.search_questions(
        domain=domain,
        module_name=module_name,
        question_type=question_type,
        tag=tag,
        province=province,
        year=year,
        requires_images=requires_images,
        limit=limit,
    )


@router.get("/questions/{question_id}", response_model=QuestionDetail)
def get_question(
    question_id: str,
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> QuestionDetail:
    try:
        return service.get_question(question_id)
    except QuestionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found",
        ) from exc
    except DatabaseConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured",
        ) from exc
