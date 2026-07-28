from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.progression.service import ProgressionService
from app.domains.rank_system.repository import RankRepository
from app.domains.rank_system.service import RankService
from app.domains.review_session.repository import ReviewSessionRepository
from app.domains.review_session.schemas import (
    ReviewVerdictRequest,
    ReviewVerdictResponse,
)
from app.domains.review_session.service import (
    ReviewCardNotFoundError,
    ReviewCardUnanswerableError,
    ReviewSessionService,
    ReviewVerdictConflictError,
)

router = APIRouter(prefix="/api/v1/review-sessions", tags=["review-sessions"])


def get_review_session_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> ReviewSessionService:
    return ReviewSessionService(
        repository=ReviewSessionRepository(session),
        progression_service=ProgressionService(
            rank_service=RankService(
                repository=RankRepository(
                    session,
                    auto_commit=False,
                )
            )
        ),
    )


@router.post(
    "/{docket_id}/cards/{card_id}/verdict",
    response_model=ReviewVerdictResponse,
)
def submit_card_verdict(
    docket_id: str,
    card_id: str,
    request: ReviewVerdictRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ReviewSessionService, Depends(get_review_session_service)],
    idempotency_key: Annotated[
        str | None,
        Header(
            min_length=1,
            max_length=236,
        ),
    ] = None,
) -> ReviewVerdictResponse:
    try:
        return service.submit_verdict(
            user_id=current_user.user_id,
            docket_id=docket_id,
            card_id=card_id,
            request=request,
            idempotency_key=idempotency_key,
        )
    except ReviewCardNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review card not found",
        ) from exc
    except ReviewCardUnanswerableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review card has no correct answer",
        ) from exc
    except ReviewVerdictConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
