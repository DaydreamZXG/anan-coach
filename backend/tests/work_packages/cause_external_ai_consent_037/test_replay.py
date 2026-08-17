from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.external_processing import ExternalProcessingRepository
from tests.work_packages.cause_external_ai_consent_037.helpers import (
    DATA_CATEGORIES,
    POLICY_VERSION,
    PROVIDER_ENDPOINT_FINGERPRINT,
    PROVIDER_RELEASE,
    PURPOSE,
    USER_ID,
    accept,
    create_user,
    insert_model_call,
)


def test_acceptance_and_dispatch_replays_are_idempotent(db_session: Session) -> None:
    create_user(db_session)
    first_acceptance = accept(db_session)
    replayed_acceptance = accept(
        db_session,
        command_key="external-ai-consent-accept-replay-037",
    )
    assert replayed_acceptance.consent_event_id == first_acceptance.consent_event_id
    assert (
        db_session.execute(
            text(
                "select count(*) from privacy.consent_events "
                "where user_id = cast(:user_id as uuid) and policy_version = :policy"
            ),
            {"user_id": USER_ID, "policy": POLICY_VERSION},
        ).scalar_one()
        == 1
    )

    call_id = insert_model_call(db_session)
    repository = ExternalProcessingRepository()
    arguments = {
        "call_id": call_id,
        "user_id": USER_ID,
        "scenario_key": "cause_candidate_generation",
        "purpose": PURPOSE,
        "data_categories": DATA_CATEGORIES,
        "declared_provider_release": PROVIDER_RELEASE,
        "provider_endpoint_fingerprint": PROVIDER_ENDPOINT_FINGERPRINT,
        "input_fingerprint": "a" * 64,
        "production_authorization_required": True,
    }
    first_receipt = repository.record_model_dispatch(db_session, **arguments)
    replayed_receipt = repository.record_model_dispatch(db_session, **arguments)
    assert replayed_receipt == first_receipt
    assert (
        db_session.execute(
            text(
                "select count(*) from ai_audit.external_processing_receipts "
                "where model_call_id = cast(:call_id as uuid)"
            ),
            {"call_id": str(call_id)},
        ).scalar_one()
        == 1
    )
