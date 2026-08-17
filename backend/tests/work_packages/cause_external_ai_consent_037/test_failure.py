from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.external_processing import (
    ExternalProcessingAuthorizationError,
    ExternalProcessingRepository,
    validate_minimized_external_payload,
)
from tests.work_packages.cause_external_ai_consent_037.helpers import (
    DATA_CATEGORIES,
    PROVIDER_ENDPOINT_FINGERPRINT,
    PROVIDER_RELEASE,
    PURPOSE,
    USER_ID,
    create_user,
    insert_model_call,
)


def test_decline_or_missing_consent_blocks_before_external_dispatch(
    db_session: Session,
) -> None:
    create_user(db_session)
    call_id = insert_model_call(db_session)

    with pytest.raises(ExternalProcessingAuthorizationError, match="consent"):
        ExternalProcessingRepository().record_model_dispatch(
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

    assert (
        db_session.execute(
            text("select count(*) from ai_audit.external_processing_receipts")
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            text(
                "select status from ai_audit.model_call_ledger "
                "where call_id = cast(:call_id as uuid)"
            ),
            {"call_id": str(call_id)},
        ).scalar_one()
        == "running"
    )
    with pytest.raises(ValueError, match="ownerUserId"):
        validate_minimized_external_payload({"answer": "B", "evidence": {"ownerUserId": USER_ID}})
