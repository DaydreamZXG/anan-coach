from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.external_processing import ExternalProcessingRepository
from tests.work_packages.cause_external_ai_consent_037.helpers import (
    DATA_CATEGORIES,
    PROVIDER_ENDPOINT_FINGERPRINT,
    PROVIDER_RELEASE,
    PURPOSE,
    USER_ID,
    accept,
    create_user,
    insert_model_call,
)


def test_first_acceptance_silently_authorizes_later_dispatches(
    db_session: Session,
) -> None:
    create_user(db_session)
    accepted = accept(db_session)
    assert accepted.state == "accepted"
    assert accepted.consent_event_id is not None

    call_id = insert_model_call(db_session)
    receipt_id = ExternalProcessingRepository().record_model_dispatch(
        db_session,
        call_id=call_id,
        user_id=USER_ID,
        scenario_key="cause_candidate_generation",
        purpose=PURPOSE,
        data_categories=DATA_CATEGORIES,
        declared_provider_release=PROVIDER_RELEASE,
        provider_endpoint_fingerprint=PROVIDER_ENDPOINT_FINGERPRINT,
        input_fingerprint="a" * 64,
        production_authorization_required=True,
    )
    receipt = (
        db_session.execute(
            text(
                "select consent_event_id, authorization_state "
                "from ai_audit.external_processing_receipts "
                "where receipt_id = cast(:receipt_id as uuid)"
            ),
            {"receipt_id": str(receipt_id)},
        )
        .mappings()
        .one()
    )
    assert str(receipt["consent_event_id"]) == accepted.consent_event_id
    assert receipt["authorization_state"] == "real_user_approved"
