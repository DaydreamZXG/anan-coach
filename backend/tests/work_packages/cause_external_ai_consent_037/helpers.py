from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.processor_disclosure.repository import ProcessorDisclosureRepository


USER_ID = "03700000-0000-4000-8000-000000000001"
POLICY_VERSION = "xingce-cause-external-ai@v1"
PROVIDER_RELEASE = "deepseek-official-v4-pro"
PROVIDER_ENDPOINT = "https://api.deepseek.com"
PROVIDER_ENDPOINT_FINGERPRINT = endpoint_fingerprint(PROVIDER_ENDPOINT)
PURPOSE = "cause_candidate_generate"
DATA_CATEGORIES = ("learning_evidence", "learner_answer")


def create_user(session: Session, *, user_id: str = USER_ID) -> None:
    session.execute(
        text(
            "insert into learner.users (user_id, display_name) "
            "values (cast(:user_id as uuid), 'Consent Tester')"
        ),
        {"user_id": user_id},
    )


def accept(
    session: Session,
    *,
    user_id: str = USER_ID,
    command_key: str = "external-ai-consent-accept-037",
    at: datetime | None = None,
):
    return ProcessorDisclosureRepository(session).record_external_ai_consent(
        user_id=user_id,
        action="accepted",
        command_key=command_key,
        surface="reflection_first_use",
        generated_at=at or datetime.now(UTC),
    )


def withdraw(
    session: Session,
    *,
    user_id: str = USER_ID,
    command_key: str = "external-ai-consent-withdraw-037",
    at: datetime | None = None,
):
    return ProcessorDisclosureRepository(session).record_external_ai_consent(
        user_id=user_id,
        action="withdrawn",
        command_key=command_key,
        surface="privacy_settings",
        generated_at=at or datetime.now(UTC),
    )


def insert_model_call(session: Session, *, user_id: str = USER_ID) -> UUID:
    call_id = uuid4()
    session.execute(
        text(
            """
            insert into ai_audit.model_call_ledger (
              call_id, call_key, user_id, feature, operation,
              provider, model, prompt_version, schema_version, price_version,
              input_rate_cny_per_million,
              cached_input_rate_cny_per_million,
              output_rate_cny_per_million,
              input_fingerprint, status, started_at
            ) values (
              cast(:call_id as uuid), :call_key, cast(:user_id as uuid),
              'xingce_cause_agent', 'cause_candidate_generate',
              'deepseek_official', 'deepseek-chat', 'cause-v1', 'cause-v1',
              'test-price-v1', 1, 0, 1, repeat('a', 64), 'running', now()
            )
            """
        ),
        {
            "call_id": str(call_id),
            "call_key": f"external-ai-consent-037:{call_id}",
            "user_id": user_id,
        },
    )
    return call_id
