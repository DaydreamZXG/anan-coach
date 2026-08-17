from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.processor_disclosure.repository import ProcessorDisclosureRepository
from app.domains.processor_disclosure.schemas import (
    ExternalAIConsentCommand,
    ExternalAIConsentView,
    ProcessorDisclosureList,
)
from app.platform.http import PublicApiEnvelope, public_success_envelope


router = APIRouter(prefix="/api/v2/privacy", tags=["privacy"])


@router.get(
    "/external-ai-processors",
    response_model=PublicApiEnvelope[ProcessorDisclosureList],
)
def external_ai_processors(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ProcessorDisclosureList]:
    return public_success_envelope(
        request=request,
        response=response,
        data=ProcessorDisclosureRepository(session).published(generated_at=datetime.now(UTC)),
    )


@router.get(
    "/external-ai-consent",
    response_model=PublicApiEnvelope[ExternalAIConsentView],
)
def external_ai_consent(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalAIConsentView]:
    return public_success_envelope(
        request=request,
        response=response,
        data=ProcessorDisclosureRepository(session).external_ai_consent(
            user_id=current_user.user_id,
            generated_at=datetime.now(UTC),
        ),
    )


def _record_external_ai_consent(
    *,
    command: ExternalAIConsentCommand,
    action: str,
    request: Request,
    response: Response,
    current_user: AuthenticatedUser,
    session: Session,
) -> PublicApiEnvelope[ExternalAIConsentView]:
    try:
        view = ProcessorDisclosureRepository(session).record_external_ai_consent(
            user_id=current_user.user_id,
            action=action,
            command_key=command.command_key,
            surface=command.surface,
            generated_at=datetime.now(UTC),
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return public_success_envelope(
        request=request,
        response=response,
        data=view,
    )


@router.post(
    "/external-ai-consent/accept",
    response_model=PublicApiEnvelope[ExternalAIConsentView],
)
def accept_external_ai_consent(
    command: ExternalAIConsentCommand,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalAIConsentView]:
    return _record_external_ai_consent(
        command=command,
        action="accepted",
        request=request,
        response=response,
        current_user=current_user,
        session=session,
    )


@router.post(
    "/external-ai-consent/withdraw",
    response_model=PublicApiEnvelope[ExternalAIConsentView],
)
def withdraw_external_ai_consent(
    command: ExternalAIConsentCommand,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalAIConsentView]:
    return _record_external_ai_consent(
        command=command,
        action="withdrawn",
        request=request,
        response=response,
        current_user=current_user,
        session=session,
    )
