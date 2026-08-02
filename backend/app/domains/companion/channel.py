from __future__ import annotations

from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.ai.audit import AIAuditRepository
from app.domains.ai.hunyuan_companion import companion_provider
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.companion.commands import CompanionCommandConflict
from app.domains.companion.repository import CompanionRepository
from app.domains.companion.schemas import CompanionAskRequest
from app.domains.companion.service import CompanionCommandFailure, CompanionService
from app.domains.learning_brain.service import (
    LearningBrainContractError,
    LearningBrainService,
)
from app.domains.qualification.repository import PositionRuleReleaseUnavailable


class ChannelCompanionUnavailable(RuntimeError):
    pass


class ChannelCompanionResponder(Protocol):
    def reply(
        self,
        session: Session,
        *,
        user_id: UUID,
        source_message_id: str,
        question: str,
    ) -> str: ...


class AccountScopedCompanionResponder:
    """Expose the product Companion service to an already-linked message channel."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def reply(
        self,
        session: Session,
        *,
        user_id: UUID,
        source_message_id: str,
        question: str,
    ) -> str:
        user_ref = str(user_id)
        try:
            profile = LearningBrainService().get_or_create_profile(
                session,
                user_id=user_ref,
            )
            commerce = CommerceService(
                repository=CommerceRepository(session),
                app_store_verifier=AppStoreSignedTransactionVerifier(self._settings),
                commercial_mode=self._settings.commercial_mode,
                usage_access_mode=self._settings.usage_access_mode,
            )
            result = CompanionService(
                repository=CompanionRepository(session),
                audit_repository=AIAuditRepository(session),
                commerce_service=commerce,
                ai_provider=companion_provider(self._settings),
                session=session,
                settings=self._settings,
            ).ask(
                user_id=user_ref,
                learner_key=user_ref,
                idempotency_key=_message_command_id(source_message_id),
                request=CompanionAskRequest(
                    questionText=question,
                    context={
                        "routeId": "pages/anan/index",
                        "workspace": "profile",
                        "objectRefs": [],
                        "learnerStateVersion": str(profile.profile_version_id),
                    },
                ),
            )
        except (
            CompanionCommandConflict,
            CompanionCommandFailure,
            LearningBrainContractError,
            PositionRuleReleaseUnavailable,
        ) as exc:
            session.rollback()
            raise ChannelCompanionUnavailable(str(exc)) from exc
        return result.answer.strip() or "这次没有形成可展示的回答，请稍后再问一次。"


def _message_command_id(source_message_id: str) -> str:
    digest = sha256(source_message_id.encode("utf-8")).hexdigest()
    return f"ilink-companion:{digest}"
