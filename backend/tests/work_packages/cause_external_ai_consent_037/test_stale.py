from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.external_processing import (
    ExternalProcessingAuthorizationError,
    ExternalProcessingRepository,
    endpoint_fingerprint,
)
from app.domains.processor_disclosure.repository import ProcessorDisclosureRepository
from tests.work_packages.cause_external_ai_consent_037.helpers import (
    DATA_CATEGORIES,
    PROVIDER_ENDPOINT_FINGERPRINT,
    PROVIDER_RELEASE,
    PURPOSE,
    USER_ID,
    accept,
    create_user,
    withdraw,
)


def test_withdrawal_and_new_policy_do_not_reuse_stale_acceptance(
    db_session: Session,
) -> None:
    create_user(db_session)
    accepted_at = datetime.now(UTC)
    accept(db_session, at=accepted_at)
    withdrawn = withdraw(db_session, at=accepted_at + timedelta(seconds=1))
    assert withdrawn.state == "withdrawn"

    with pytest.raises(ExternalProcessingAuthorizationError, match="consent"):
        ExternalProcessingRepository().require_subject_authorization(
            db_session,
            user_id=USER_ID,
            declared_provider_release=PROVIDER_RELEASE,
            provider_endpoint_fingerprint=PROVIDER_ENDPOINT_FINGERPRINT,
            purpose=PURPOSE,
            data_categories=DATA_CATEGORIES,
            at=accepted_at + timedelta(seconds=2),
        )

    db_session.execute(
        text(
            "update privacy.policy_versions set state = 'retired', retired_at = :at "
            "where policy_version = 'xingce-cause-external-ai@v1'"
        ),
        {"at": accepted_at + timedelta(seconds=3)},
    )
    db_session.execute(
        text(
            """
            insert into privacy.policy_versions (
              policy_version, title, content, content_sha256,
              state, published_at, retired_at
            ) values (
              'xingce-cause-external-ai@v2', '岸岸外部 AI 错因分析说明（新版）',
              '{
                "policyKind":"xingce_cause_external_ai",
                "purpose":"新版错因候选分析",
                "dataSent":["作答结果"],
                "providerRisk":"处理方规则可能变化",
                "withdrawal":"可随时撤回"
              }'::jsonb,
              repeat('b', 64), 'active', :at, null
            )
            """
        ),
        {"at": accepted_at + timedelta(seconds=3)},
    )
    new_endpoint = "https://v2.processor.example.com/v1"
    db_session.execute(
        text(
            """
            insert into ai_audit.processor_release_records (
              processor_release_record_id, provider_key, provider_release,
              display_name, operator_name, endpoint_fingerprint,
              transport_security, real_user_status, purposes, data_categories,
              data_region, model_training_use, retention_policy,
              retention_seconds, deletion_support, evidence,
              evidence_fingerprint, verified_at, valid_from, valid_until,
              published_at, authorization_mode, consent_policy_version
            ) values (
              'cause-policy-v2-test', 'policy_v2_test', 'cause-policy-v2-release',
              'Policy V2 Test', 'Test Operator', :endpoint_fingerprint,
              'tls', 'approved', '["cause_candidate_generate"]'::jsonb,
              '["learning_evidence","learner_answer"]'::jsonb, 'unverified',
              'unknown', 'unknown', null, 'unverified',
              '{
                "plainLanguageDisclosure":"xingce-cause-external-ai@v2",
                "trainingUse":"unknown", "retention":"unknown",
                "transportReview":"https-only", "remoteDeletion":"unverified"
              }'::jsonb,
              repeat('c', 64), :at, :at, null, :at,
              'user_consent', 'xingce-cause-external-ai@v2'
            )
            """
        ),
        {
            "endpoint_fingerprint": endpoint_fingerprint(new_endpoint),
            "at": accepted_at + timedelta(seconds=3),
        },
    )

    view = ProcessorDisclosureRepository(db_session).external_ai_consent(
        user_id=USER_ID,
        generated_at=accepted_at + timedelta(seconds=4),
    )
    assert view.policy_version == "xingce-cause-external-ai@v2"
    assert view.state == "not_accepted"
    with pytest.raises(ExternalProcessingAuthorizationError, match="consent"):
        ExternalProcessingRepository().require_subject_authorization(
            db_session,
            user_id=USER_ID,
            declared_provider_release="cause-policy-v2-release",
            provider_endpoint_fingerprint=endpoint_fingerprint(new_endpoint),
            purpose=PURPOSE,
            data_categories=DATA_CATEGORIES,
            at=accepted_at + timedelta(seconds=4),
        )
