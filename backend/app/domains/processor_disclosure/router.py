from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.processor_disclosure.repository import ProcessorDisclosureRepository
from app.domains.processor_disclosure.schemas import ProcessorDisclosureList
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
        data=ProcessorDisclosureRepository(session).published(
            generated_at=datetime.now(UTC)
        ),
    )
