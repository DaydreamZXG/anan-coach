from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.governance.repository import (
    AIGovernanceRepository,
    generic_evidence_ref,
)
from app.domains.ai.model_cost import ModelCallLedgerRepository, ModelPriceCard
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.commerce.stage8_repository import (
    Stage8CommerceConflict,
    Stage8CommerceRepository,
)
from app.domains.commerce.stage8_schemas import (
    PolicyAcceptances,
    WechatOrderCreateRequest,
    WechatRefundRequest,
    WeeklyReflectionConfirmRequest,
)
from app.domains.commerce.stage8_service import (
    Stage8CommerceService,
    Stage8CommerceUnavailable,
)
from app.domains.commerce.wechat_pay import (
    WechatNotification,
    WechatOrderQueryResult,
    WechatPrepayResult,
    WechatRefundResult,
)
from app.domains.data_rights.repository import CONFIRMATION_PHRASE, DataRightsRepository
from app.domains.data_rights.schemas import (
    AccountDeletionCreateRequest,
    DataExportCreateRequest,
)
from app.domains.data_rights.service import DataRightsService
from app.domains.data_rights.worker import DataRightsWorker
from tests.test_planning_routes import _client
from tests.test_xingce_targeted_knowledge_selection import (
    _node,
    _seed_targeted_learner,
    _start,
    _targeted_task,
)


class FakeWechatPayGateway:
    def __init__(self, *, now: datetime) -> None:
        self.now = now
        self.orders: dict[str, dict[str, object]] = {}
        self.refunds: dict[str, WechatRefundResult] = {}

    def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        description: str,
        payer_open_id: str,
        amount_minor: int,
        currency: str,
        expires_at: datetime,
        attach: str,
    ) -> WechatPrepayResult:
        del description, expires_at, attach
        self.orders[out_trade_no] = {
            "payer": payer_open_id,
            "amount": amount_minor,
            "currency": currency,
            "state": "NOTPAY",
        }
        return self.sign_prepay(prepay_id=f"prepay-{out_trade_no}")

    def sign_prepay(self, *, prepay_id: str) -> WechatPrepayResult:
        return WechatPrepayResult(
            prepay_id=prepay_id,
            time_stamp="1234567890",
            nonce_str="nonce",
            package=f"prepay_id={prepay_id}",
            sign_type="RSA",
            pay_sign="signed",
        )

    def query_order(self, *, out_trade_no: str) -> WechatOrderQueryResult:
        row = self.orders[out_trade_no]
        state = str(row["state"])
        return WechatOrderQueryResult(
            out_trade_no=out_trade_no,
            trade_state=state,
            transaction_id=f"tx-{out_trade_no}" if state == "SUCCESS" else None,
            success_time=self.now if state == "SUCCESS" else None,
            amount_minor=int(row["amount"]),
            currency=str(row["currency"]),
            payer_open_id=str(row["payer"]),
        )

    def close_order(self, *, out_trade_no: str) -> None:
        self.orders[out_trade_no]["state"] = "CLOSED"

    def create_refund(
        self,
        *,
        out_trade_no: str,
        out_refund_no: str,
        amount_minor: int,
        total_minor: int,
        currency: str,
        reason: str,
    ) -> WechatRefundResult:
        del out_trade_no, total_minor, reason
        result = WechatRefundResult(
            refund_id=f"provider-{out_refund_no}",
            out_refund_no=out_refund_no,
            status="SUCCESS",
            amount_minor=amount_minor,
            currency=currency,
            success_time=self.now,
        )
        self.refunds[out_refund_no] = result
        return result

    def query_refund(self, *, out_refund_no: str) -> WechatRefundResult:
        return self.refunds[out_refund_no]

    def verify_notification(self, *, headers: object, body: bytes) -> WechatNotification:
        del headers, body
        raise AssertionError("notification verification is not used in this contract test")


class FakeStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        del operation_key
        self.deleted.append(object_ref)


def test_founder_order_payment_refund_and_unified_entitlement(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    user_id = _seed_wechat_user(db_session)
    settings = Settings(
        commercial_mode="paid",
        wechat_mini_app_id="stage8-test-app",
        wechat_pay_mch_id="stage8-test-merchant",
        wechat_pay_order_expiry_seconds=1800,
    )
    gateway = FakeWechatPayGateway(now=now)
    service = _commerce_service(db_session, gateway=gateway, settings=settings, now=now)

    catalog = service.catalog()
    offer = catalog.offers[0]
    assert offer.amount_minor == 6900
    assert offer.automatic_renewal is False
    assert offer.cohort_limit == 30
    assert {item.policy_kind for item in offer.policies} == {
        "service_terms",
        "refund",
        "process_reward",
        "marketing",
    }
    versions = {item.policy_kind: item.policy_version for item in offer.policies}
    created = service.create_order(
        user_id=user_id,
        request=WechatOrderCreateRequest(
            command_key="founder-order-command-0001",
            offer_version_id=offer.offer_version_id,
            expected_amount_minor=offer.amount_minor,
            accepted_policies=PolicyAcceptances(
                service_terms_version=versions["service_terms"],
                refund_policy_version=versions["refund"],
                reward_policy_version=versions["process_reward"],
                marketing_policy_version=versions["marketing"],
            ),
        ),
    )
    assert created.order.state == "awaiting_payment"
    assert created.order.payment is not None

    gateway.orders[created.order.out_trade_no]["state"] = "SUCCESS"
    reconciled = service.reconcile_order(user_id=user_id, order_id=created.order.order_id)
    assert reconciled.state == "paid"
    entitlement = service.dashboard(user_id=user_id).entitlement
    assert entitlement.tier == "pro"
    assert "founder_experience_30d" in entitlement.product_ids

    eligibility = service.refund_eligibility(user_id=user_id)
    assert eligibility is not None
    assert eligibility.eligible is True
    assert eligibility.score_evidence_required is False
    refunded = service.request_refund(
        user_id=user_id,
        order_id=created.order.order_id,
        request=WechatRefundRequest(
            command_key="founder-refund-command-0001", reason="not_suitable"
        ),
    )
    assert refunded.refund.state == "succeeded"
    assert refunded.refund.amount_minor == 6900
    assert refunded.entitlement.tier == "free"

    slot_state = db_session.execute(
        text(
            "select state from commerce.founder_cohort_slots "
            "where order_id = cast(:order_id as uuid)"
        ),
        {"order_id": str(created.order.order_id)},
    ).scalar_one()
    assert slot_state == "consumed"


def test_data_export_and_account_erasure_are_durable_jobs(db_session: Session) -> None:
    now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    privacy_factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    token = "privacy-erasure-xingce-token"
    xingce_fixture_now = datetime.now(UTC)
    user_id = _seed_targeted_learner(
        privacy_factory,
        token=token,
        now=xingce_fixture_now,
        nodes=(
            _node(
                "privacy-erasure-node",
                "micro_skill",
                "micro_skill",
                "隐私擦除",
                0.90,
                3,
            ),
        ),
        tags={"隐私擦除": (1, 2, 3)},
        question_ids_by_index={
            1: "privacy-erasure-question-1",
            2: "privacy-erasure-question-2",
            3: "privacy-erasure-question-3",
        },
    )
    with privacy_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.users
                set display_name = '阿岸', email = 'anan@example.test'
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.wechat_identities (
                  identity_id, user_id, app_id, open_id, union_id,
                  session_key_hash, last_login_at
                ) values (
                  gen_random_uuid(), cast(:user_id as uuid), 'stage8-test-app',
                  :open_id, :union_id, repeat('a', 64), now()
                )
                """
            ),
            {
                "user_id": user_id,
                "open_id": f"openid-{uuid4().hex}",
                "union_id": f"union-{uuid4().hex}",
            },
        )
    xingce_auth = {"Authorization": f"Bearer {token}"}
    xingce_client = _client(privacy_factory)
    xingce_task = _targeted_task(xingce_client, xingce_auth)
    xingce_session = _start(
        xingce_client,
        xingce_auth,
        xingce_task,
        key="privacy-erasure-xingce-start",
    )
    xingce_session_id = xingce_session["sessionId"]
    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from learning.xingce_question_exposures
                where user_id = cast(:user_id as uuid)
                  and session_id = cast(:session_id as uuid)
                """
            ),
            {"user_id": user_id, "session_id": xingce_session_id},
        ).scalar_one()
        == 3
    )
    evidence_id = uuid4()
    report_id = uuid4()
    experience_id = uuid4()
    task_resource_id = uuid4()
    task_lease_id = uuid4()
    db_session.execute(
        text(
            """
              insert into learner.preparation_experience_versions (
                experience_version_id, command_id, user_id, version_no,
                declaration_status, current_status, first_systematic, external_learning,
                prior_guokao_exam, retake, prior_exam_years, source_type,
                input_fingerprint, created_at
              ) values (
                cast(:experience_id as uuid), :command_id,
                cast(:user_id as uuid), 1, 'declared', 'parttime', false, true, true, true,
                '[2024]'::jsonb, 'user_confirmed', repeat('a', 64), :now
              )
            """
        ),
        {
            "experience_id": str(experience_id),
            "command_id": f"privacy-experience-{uuid4().hex}",
            "user_id": user_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            insert into learning.score_evidence (
              evidence_id, command_id, user_id, exam_code, exam_year,
              source_type, reported_context, xingce_score, shenlun_score,
              total_score, timed_status, verification_status, occurred_at,
              command_payload
            ) values (
              cast(:evidence_id as uuid), :command_id, cast(:user_id as uuid),
              'guokao', 2026, 'self_report', 'official_exam', 63, 57, 120,
              'timed', 'user_confirmed', :now,
              jsonb_build_object('privateNote', '我的成绩')
            )
            """
        ),
        {
            "evidence_id": str(evidence_id),
            "command_id": f"privacy-score-{uuid4().hex}",
            "user_id": user_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            insert into learning.report_projection_versions (
              projection_version_id, user_id, report_kind, subject_ref,
              chart_contract_version, input_fingerprint, payload,
              evidence_refs, next_plan_evidence_refs, generated_at
            ) values (
              cast(:report_id as uuid), cast(:user_id as uuid), 'global',
              'privacy-erasure-test', 'report-chart@v1', :fingerprint,
              jsonb_build_object('summary', '私人 AI 总结'),
              jsonb_build_array('private-evidence'),
              jsonb_build_array('private-next-plan'), :now
            )
            """
        ),
        {
            "report_id": str(report_id),
            "user_id": user_id,
            "fingerprint": "f" * 64,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            insert into ai_audit.interaction_logs (
              log_id, learner_key, feature, provider, model, status,
              request_summary, response_summary, latency_ms, metadata
            ) values (
              :log_id, :user_id, 'companion', 'test', 'test', 'succeeded',
              '私人问题', '私人回答', 10, '{}'::jsonb
            )
            """
        ),
        {"log_id": f"privacy-log-{uuid4().hex}", "user_id": user_id},
    )
    db_session.execute(
        text(
            """
            insert into learning.task_edit_leases (
              resource_type, resource_id, user_id, lease_id, generation,
              device_id, device_label, reconciliation_state, server_revision,
              acquired_at, last_seen_at
            ) values (
              'mock_exam_session', cast(:resource_id as uuid), cast(:user_id as uuid),
              cast(:lease_id as uuid), 1, 'privacy-device-00000000000001',
              '隐私测试 iPad', 'clean', 1, :now, :now
            )
            """
        ),
        {
            "resource_id": str(task_resource_id),
            "user_id": user_id,
            "lease_id": str(task_lease_id),
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            insert into learning.task_edit_events (
              event_id, user_id, resource_type, resource_id, lease_id,
              generation, device_id, device_label, event_type,
              resource_revision, metadata, created_at
            ) values (
              gen_random_uuid(), cast(:user_id as uuid), 'mock_exam_session',
              cast(:resource_id as uuid), cast(:lease_id as uuid), 1,
              'privacy-device-00000000000001', '隐私测试 iPad',
              'acquired', 1, '{}'::jsonb, :now
            )
            """
        ),
        {
            "resource_id": str(task_resource_id),
            "user_id": user_id,
            "lease_id": str(task_lease_id),
            "now": now,
        },
    )
    ModelCallLedgerRepository().begin(
        db_session,
        call_key="privacy-external-processing-receipt",
        user_id=user_id,
        job_id=None,
        feature="companion_ask",
        operation="companion_answer",
        provider="local-processor",
        model="local-model",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        price_card=ModelPriceCard(
            version="price-v1",
            input_cny_per_million=Decimal("1"),
            cached_input_cny_per_million=Decimal("0"),
            output_cny_per_million=Decimal("1"),
        ),
        input_fingerprint="9" * 64,
        attribution=AIGovernanceRepository().attribution(
            db_session,
            environment="local",
            scenario_key="tutoring_explanation",
            operation="companion_answer",
            provider="local-processor",
            model="local-model",
            prompt_version="prompt-v1",
            schema_version="schema-v1",
            evidence_refs=(
                generic_evidence_ref(
                    kind="privacy_test",
                    source_id="privacy-test",
                    version="v1",
                ),
            ),
            retrieval_bundle_id=None,
            reservation_id=None,
            reservation_source=None,
        ),
        processor_release_id="local-processor-release",
        processor_endpoint_fingerprint="8" * 64,
        processing_purpose="companion_answer",
        processing_data_categories=("learner_question",),
    )
    db_session.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(
            text(
                """
                update learning.report_projection_versions
                set payload = '{}'::jsonb
                where projection_version_id = cast(:report_id as uuid)
                """
            ),
            {"report_id": str(report_id)},
        )
    db_session.rollback()
    repository = DataRightsRepository(db_session)
    service = DataRightsService(repository, now=lambda: now)
    overview = service.overview(user_id=user_id)
    assert overview.policy_version == "personal-data-control@v1"
    assert any(item.category == "original_materials" for item in overview.categories)

    export = service.create_export(
        user_id=user_id,
        request=DataExportCreateRequest(command_key="personal-export-command-0001"),
    )
    assert export.state == "queued"
    assert (
        db_session.execute(
            text(
                """
                select job.run_after
                from privacy.data_export_requests request
                join platform.background_jobs job on job.job_id = request.job_id
                where request.export_request_id = cast(:export_id as uuid)
                """
            ),
            {"export_id": str(export.export_request_id)},
        ).scalar_one()
        == now
    )
    worker = DataRightsWorker(
        storage=FakeStorage(),
        settings=Settings(privacy_export_expiry_seconds=86400),
        now=lambda: now + timedelta(seconds=1),
    )
    completed_job = worker.process_next(db_session)
    assert completed_job is not None and completed_job.status == "succeeded"
    completed = service.export_status(user_id=user_id, export_id=export.export_request_id)
    assert completed.state == "completed"
    payload = service.export_payload(user_id=user_id, export_id=export.export_request_id)
    assert payload.payload["contractVersion"] == "personal-data-export@v1"
    assert payload.payload["preparationExperience"][0]["retake"] is True
    assert payload.payload["preparationExperience"][0]["prior_exam_years"] == [2024]
    assert (
        payload.payload["externalProcessingReceipts"][0]["declared_provider_release"]
        == "local-processor-release"
    )
    assert payload.payload["taskEditLeases"][0]["device_label"] == "隐私测试 iPad"
    assert payload.payload["taskEditEvents"][0]["event_type"] == "acquired"
    assert payload.payload_sha256 == completed.payload_sha256

    deletion = service.request_deletion(
        user_id=user_id,
        request=AccountDeletionCreateRequest(
            command_key="account-delete-command-0001",
            confirmation_phrase=CONFIRMATION_PHRASE,
        ),
    )
    assert deletion.state == "queued"
    assert (
        db_session.execute(
            text(
                """
                select job.run_after
                from privacy.account_deletion_requests request
                join platform.background_jobs job on job.job_id = request.job_id
                where request.deletion_request_id = cast(:deletion_id as uuid)
                """
            ),
            {"deletion_id": str(deletion.deletion_request_id)},
        ).scalar_one()
        == now
    )
    completed_deletion_job = worker.process_next(db_session)
    assert completed_deletion_job is not None
    assert completed_deletion_job.status == "succeeded", completed_deletion_job.last_error_message
    user = (
        db_session.execute(
            text(
                "select display_name, email, is_active, account_status "
                "from learner.users where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        )
        .mappings()
        .one()
    )
    assert user["display_name"] is None
    assert user["email"] is None
    assert user["is_active"] is False
    assert user["account_status"] == "disabled"
    erased_xingce_session = (
        db_session.execute(
            text(
                """
                select safe_bundle, scoring_bundle, checkpoint
                from learning.xingce_task_sessions
                where user_id = cast(:user_id as uuid)
                  and session_id = cast(:session_id as uuid)
                """
            ),
            {"user_id": user_id, "session_id": xingce_session_id},
        )
        .mappings()
        .one()
    )
    assert erased_xingce_session["safe_bundle"] == {}
    assert erased_xingce_session["scoring_bundle"] == {}
    assert erased_xingce_session["checkpoint"] == {"answers": []}
    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from learning.xingce_question_exposures
                where user_id = cast(:user_id as uuid)
                  and session_id = cast(:session_id as uuid)
                """
            ),
            {"user_id": user_id, "session_id": xingce_session_id},
        ).scalar_one()
        == 0
    )
    deletion_events = (
        db_session.execute(
            text(
                """
            select disposition from privacy.external_processing_deletion_events
            where deletion_request_id = cast(:deletion_id as uuid)
            """
            ),
            {"deletion_id": str(deletion.deletion_request_id)},
        )
        .scalars()
        .all()
    )
    assert deletion_events == ["not_dispatched_local_unreleased"]
    assert (
        db_session.execute(
            text("select count(*) from learner.profiles where user_id = cast(:user_id as uuid)"),
            {"user_id": user_id},
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            text(
                "select count(*) from learning.task_edit_leases "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            text(
                "select count(*) from learning.task_edit_events "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one()
        == 0
    )
    erased_score = (
        db_session.execute(
            text(
                """
            select xingce_score, shenlun_score, total_score,
                   command_payload, erased_at
            from learning.score_evidence
            where evidence_id = cast(:evidence_id as uuid)
            """
            ),
            {"evidence_id": str(evidence_id)},
        )
        .mappings()
        .one()
    )
    assert erased_score["xingce_score"] == 0
    assert erased_score["shenlun_score"] == 0
    assert erased_score["total_score"] == 0
    assert erased_score["command_payload"] == {}
    assert erased_score["erased_at"] is not None
    erased_experience = (
        db_session.execute(
            text(
                """
            select declaration_status, first_systematic, external_learning,
                   prior_guokao_exam, retake, prior_exam_years,
                   input_fingerprint, erased_at
            from learner.preparation_experience_versions
            where experience_version_id = cast(:experience_id as uuid)
            """
            ),
            {"experience_id": str(experience_id)},
        )
        .mappings()
        .one()
    )
    assert erased_experience["declaration_status"] == "legacy_not_collected"
    assert erased_experience["first_systematic"] is False
    assert erased_experience["external_learning"] is False
    assert erased_experience["prior_guokao_exam"] is False
    assert erased_experience["retake"] is False
    assert erased_experience["prior_exam_years"] == []
    assert erased_experience["input_fingerprint"] == "0" * 64
    assert erased_experience["erased_at"] is not None
    erased_report = (
        db_session.execute(
            text(
                """
            select payload, evidence_refs, next_plan_evidence_refs
            from learning.report_projection_versions
            where projection_version_id = cast(:report_id as uuid)
            """
            ),
            {"report_id": str(report_id)},
        )
        .mappings()
        .one()
    )
    assert erased_report["payload"] == {}
    assert erased_report["evidence_refs"] == []
    assert erased_report["next_plan_evidence_refs"] == []
    assert (
        db_session.execute(
            text("select count(*) from ai_audit.interaction_logs where learner_key = :user_id"),
            {"user_id": user_id},
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            text(
                """
            select count(*) from privacy.deletion_events
            where deletion_request_id = cast(:deletion_id as uuid)
              and phase = 'personal_rows_erased'
            """
            ),
            {"deletion_id": str(deletion.deletion_request_id)},
        ).scalar_one()
        == 1
    )
    assert (
        db_session.execute(
            text(
                "select count(*) from learner.wechat_identities "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one()
        == 0
    )


def test_free_cold_start_hides_offers_and_rejects_forged_order(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    user_id = _seed_wechat_user(db_session)
    service = _commerce_service(
        db_session,
        gateway=FakeWechatPayGateway(now=now),
        settings=Settings(
            commercial_mode="free_cold_start",
            usage_access_mode="unlimited",
            wechat_mini_app_id="stage8-test-app",
        ),
        now=now,
    )
    catalog = service.catalog()

    assert catalog.commercial_mode == "free_cold_start"
    assert catalog.ordering_enabled is False
    assert catalog.offers == []
    with pytest.raises(Stage8CommerceUnavailable, match="free cold start"):
        service.create_order(
            user_id=user_id,
            request=WechatOrderCreateRequest(
                command_key="forged-free-order-0001",
                offer_version_id=uuid4(),
                expected_amount_minor=6900,
                accepted_policies=PolicyAcceptances(
                    service_terms_version="forged-service",
                    refund_policy_version="forged-refund",
                    reward_policy_version="forged-reward",
                    marketing_policy_version="forged-marketing",
                ),
            ),
        )


def test_four_weekly_reflections_issue_process_reward_and_replay_safely(
    db_session: Session,
) -> None:
    paid_at = datetime.now(UTC)
    user_id = _seed_wechat_user(db_session)
    settings = Settings(
        commercial_mode="paid",
        wechat_mini_app_id="stage8-test-app",
        wechat_pay_mch_id="stage8-test-merchant",
        wechat_pay_order_expiry_seconds=1800,
    )
    gateway = FakeWechatPayGateway(now=paid_at)
    service = _commerce_service(db_session, gateway=gateway, settings=settings, now=paid_at)
    offer = service.catalog().offers[0]
    versions = {item.policy_kind: item.policy_version for item in offer.policies}
    created = service.create_order(
        user_id=user_id,
        request=WechatOrderCreateRequest(
            command_key="weekly-reward-order-command-0001",
            offer_version_id=offer.offer_version_id,
            expected_amount_minor=offer.amount_minor,
            accepted_policies=PolicyAcceptances(
                service_terms_version=versions["service_terms"],
                refund_policy_version=versions["refund"],
                reward_policy_version=versions["process_reward"],
                marketing_policy_version=versions["marketing"],
            ),
        ),
    )
    gateway.orders[created.order.out_trade_no]["state"] = "SUCCESS"
    service.reconcile_order(user_id=user_id, order_id=created.order.order_id)

    report_ids: list[object] = []
    for index in range(4):
        report_id = uuid4()
        report_ids.append(report_id)
        generated_at = paid_at + timedelta(days=(index * 7) + 1)
        db_session.execute(
            text(
                """
                insert into learning.report_projection_versions (
                  projection_version_id, user_id, report_kind, subject_ref,
                  chart_contract_version, input_fingerprint, payload,
                  evidence_refs, next_plan_evidence_refs, generated_at
                ) values (
                  cast(:report_id as uuid), cast(:user_id as uuid), 'weekly',
                  :subject_ref, 'report-chart@v1', :fingerprint,
                  '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, :generated_at
                )
                """
            ),
            {
                "report_id": str(report_id),
                "user_id": user_id,
                "subject_ref": f"stage8-week-{index}",
                "fingerprint": f"{index + 1:064x}",
                "generated_at": generated_at,
            },
        )
        db_session.commit()
        service = _commerce_service(
            db_session,
            gateway=gateway,
            settings=settings,
            now=generated_at,
        )
        confirmation = service.confirm_weekly_reflection(
            user_id=user_id,
            request=WeeklyReflectionConfirmRequest(
                command_key=f"weekly-reflection-command-{index:04d}",
                order_id=created.order.order_id,
                report_version_id=report_id,
            ),
        )

    assert confirmation.progress.weekly_reflections == 4
    assert "weekly_reflection_cny30" in confirmation.progress.awarded_rewards
    refund = (
        db_session.execute(
            text(
                """
            select state, amount_minor from commerce.wechat_refunds
            where order_id = cast(:order_id as uuid)
              and refund_kind = 'weekly_reflection_reward'
            """
            ),
            {"order_id": str(created.order.order_id)},
        )
        .mappings()
        .one()
    )
    assert refund["state"] == "succeeded"
    assert refund["amount_minor"] == 3000

    replayed = service.confirm_weekly_reflection(
        user_id=user_id,
        request=WeeklyReflectionConfirmRequest(
            command_key="weekly-reflection-command-0003",
            order_id=created.order.order_id,
            report_version_id=report_ids[-1],
        ),
    )
    assert replayed.confirmation_id == confirmation.confirmation_id

    with pytest.raises(Stage8CommerceConflict, match="different weekly reflection"):
        service.confirm_weekly_reflection(
            user_id=user_id,
            request=WeeklyReflectionConfirmRequest(
                command_key="weekly-reflection-command-0003",
                order_id=created.order.order_id,
                report_version_id=report_ids[0],
            ),
        )


def _commerce_service(
    session: Session,
    *,
    gateway: FakeWechatPayGateway,
    settings: Settings,
    now: datetime,
) -> Stage8CommerceService:
    base_repository = CommerceRepository(session)
    return Stage8CommerceService(
        repository=Stage8CommerceRepository(session),
        entitlement_service=CommerceService(
            repository=base_repository,
            app_store_verifier=AppStoreSignedTransactionVerifier(settings),
            commercial_mode=settings.commercial_mode,
            usage_access_mode=settings.usage_access_mode,
            now=lambda: now,
        ),
        gateway=gateway,
        settings=settings,
        now=lambda: now,
    )


def _seed_wechat_user(session: Session) -> str:
    user_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learner.users (user_id, display_name, email)
            values (cast(:user_id as uuid), '阿岸', 'anan@example.test')
            """
        ),
        {"user_id": user_id},
    )
    session.execute(
        text(
            """
            insert into learner.profiles (user_id)
            values (cast(:user_id as uuid))
            """
        ),
        {"user_id": user_id},
    )
    session.execute(
        text(
            """
            insert into learner.wechat_identities (
              identity_id, user_id, app_id, open_id, union_id,
              session_key_hash, last_login_at
            ) values (
              gen_random_uuid(), cast(:user_id as uuid), 'stage8-test-app',
              :open_id, :union_id, repeat('a', 64), now()
            )
            """
        ),
        {
            "user_id": user_id,
            "open_id": f"openid-{uuid4().hex}",
            "union_id": f"union-{uuid4().hex}",
        },
    )
    session.commit()
    return user_id
