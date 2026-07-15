from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.docket.repository import DocketRepository
from app.domains.docket.schemas import DocketOpenCourtResponse
from app.domains.docket.service import DocketService
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import (
    ExamPracticeQuestionsUnavailableError,
    KnowledgeMapUnavailableError,
    KnowledgeService,
    QuestionNotFoundError,
)
from app.domains.progression.deps import get_progression_service
from app.domains.progression.service import ProgressionService

router = APIRouter(prefix="/api/v1/dockets", tags=["dockets"])


def get_docket_service(
    session: Annotated[Session, Depends(get_db_session)],
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    progression_service: Annotated[ProgressionService, Depends(get_progression_service)],
) -> DocketService:
    return DocketService(
        repository=DocketRepository(session),
        knowledge_service=knowledge_service,
        progression_service=progression_service,
    )


@router.post("/open-court", response_model=DocketOpenCourtResponse)
def open_court(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[DocketService, Depends(get_docket_service)],
) -> DocketOpenCourtResponse:
    try:
        return service.open_court(user_id=current_user.user_id)
    except (KnowledgeMapUnavailableError, ExamPracticeQuestionsUnavailableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Docket source questions are not available",
        ) from exc
    except QuestionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Docket selected a missing question",
        ) from exc
