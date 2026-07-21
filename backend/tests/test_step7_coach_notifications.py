from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.coach_notifications.evaluator import (
    COACH_SEND_JOB_INPUT_VERSION,
    COACH_SEND_JOB_TYPE,
    CoachNotificationEvaluator,
)
from app.domains.coach_notifications.repository import CoachNotificationRepository
from app.domains.coach_notifications.schemas import (
    CoachNotificationOpenCommand,
    DisableCoachNotificationsCommand,
    UpdateCoachPreferenceCommand,
    WechatSubscriptionEventCommand,
)
from app.domains.coach_notifications.service import CoachNotificationService
from app.domains.coach_notifications.template import CoachTemplateRelease
from app.domains.coach_notifications.wechat import (
    WechatSendReceipt,
    WechatSubscribeMessageClient,
    WechatSubscribeMessageError,
)
from app.domains.coach_notifications.worker import CoachNotificationWorker
from app.domains.data_rights.repository import (
    CONFIRMATION_PHRASE,
    DataRightsRepository,
)
from app.domains.data_rights.schemas import AccountDeletionCreateRequest
from app.domains.data_rights.service import DataRightsService
from app.domains.data_rights.worker import DataRightsWorker
from app.domains.operations import router as operations_router
from app.platform.jobs import BackgroundJobRepository


NOW = datetime(2026, 7, 19, 2, 30, tzinfo=UTC)
TEMPLATE_JSON = json.dumps(
    {
        "releaseId": "coach-template-release-20260719",
        "templateId": "wechat-template-real-contract-id",
        "fields": {
            "title": "thing1",
            "reminderTime": "time2",
            "action": "thing3",
        },
    }
)


class AcceptedSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send(self, **kwargs: object) -> WechatSendReceipt:
        self.calls.append(kwargs)
        return WechatSendReceipt(accepted_at=NOW)


class UncertainSender:
    def send(self, **_kwargs: object) -> WechatSendReceipt:
        raise WechatSubscribeMessageError(
            code=None, transient=False, dispatch_uncertain=True
        )


class EmptyPrivateStorage:
    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        del object_ref, operation_key


def test_wechat_adapter_fetches_server_token_and_sends_exact_safe_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/cgi-bin/token":
            assert request.url.params["secret"] == "test-secret"
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        assert request.url.path == "/cgi-bin/message/subscribe/send"
        assert request.url.params["access_token"] == "token-1"
        body = json.loads(request.content)
        assert body == {
            "touser": "openid-1",
            "template_id": "template-1",
            "page": "pages/today/index?notificationDeliveryId=00000000-0000-4000-8000-000000000001",
            "data": {"thing1": {"value": "岸岸学习提醒"}},
            "miniprogram_state": "formal",
            "lang": "zh_CN",
        }
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    client = WechatSubscribeMessageClient(
        Settings(
            wechat_mini_app_secret="test-secret",
            wechat_api_base_url="https://wechat.test",
            wechat_coach_miniprogram_state="formal",
        ),
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
    )

    receipt = client.send(
        open_id="openid-1",
        template_id="template-1",
        page="pages/today/index?notificationDeliveryId=00000000-0000-4000-8000-000000000001",
        data={"thing1": {"value": "岸岸学习提醒"}},
    )

    assert receipt.accepted_at == NOW
    assert len(requests) == 2


def test_wechat_adapter_marks_post_transport_break_as_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/token":
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        raise httpx.ReadTimeout("response lost", request=request)

    client = WechatSubscribeMessageClient(
        Settings(
            wechat_mini_app_secret="test-secret",
            wechat_api_base_url="https://wechat.test",
        ),
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
    )

    with pytest.raises(WechatSubscribeMessageError) as captured:
        client.send(
            open_id="openid-1",
            template_id="template-1",
            page="pages/today/index?notificationDeliveryId=00000000-0000-4000-8000-000000000001",
            data={"thing1": {"value": "岸岸学习提醒"}},
        )

    assert captured.value.dispatch_uncertain is True
    assert captured.value.transient is False


def test_preference_authorization_send_open_and_disable_are_separate_facts(
    db_session: Session,
) -> None:
    user_id = _seed_user(db_session)
    settings = _settings()
    service = CoachNotificationService(
        CoachNotificationRepository(db_session), settings, clock=lambda: NOW
    )
    initial = service.settings(user_id=user_id)
    assert initial.preference.enabled is False
    assert initial.template.authorization_mode == "one_time"
    assert initial.template.available_grants == 0

    enabled = service.update_preference(
        user_id=user_id,
        idempotency_key="preference-enable-1",
        command=_enabled_command(initial.preference.preference_version_id),
    )
    authorization = service.record_subscription_event(
        user_id=user_id,
        idempotency_key="subscription-accept-1",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    replay = service.record_subscription_event(
        user_id=user_id,
        idempotency_key="subscription-accept-1",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    assert authorization.grant_created is True
    assert replay.event_id == authorization.event_id
    assert replay.available_grants == 1

    delivery_id = _queue_delivery(db_session, user_id=user_id, now=NOW)
    db_session.commit()
    sender = AcceptedSender()
    worker = CoachNotificationWorker(
        session=db_session,
        settings=settings,
        sender=sender,
        clock=lambda: NOW,
    )
    job = worker.process_next(now=NOW)
    assert job is not None and job.status == "succeeded"
    assert len(sender.calls) == 1
    assert sender.calls[0]["page"] == (
        f"pages/today/index?notificationDeliveryId={delivery_id}"
    )
    payload = sender.calls[0]["data"]
    assert payload == {
        "thing1": {"value": "岸岸学习提醒"},
        "time2": {"value": "2026年07月19日 10:30"},
        "thing3": {"value": "打开今日计划继续"},
    }
    states = db_session.execute(
        text(
            """
            select delivery.state, subscription_grant.state as grant_state
            from messaging.coach_notification_deliveries delivery
            join messaging.wechat_subscription_grants subscription_grant
              on subscription_grant.grant_id = delivery.grant_id
            where delivery.delivery_id = cast(:delivery_id as uuid)
            """
        ),
        {"delivery_id": delivery_id},
    ).mappings().one()
    assert dict(states) == {"state": "platform_accepted", "grant_state": "consumed"}

    opened = service.record_open(
        user_id=user_id,
        delivery_id=UUID(delivery_id),
        idempotency_key="delivery-open-1",
        command=CoachNotificationOpenCommand(),
    )
    assert opened.task_completion_changed is False
    history = service.settings(user_id=user_id).recent_deliveries
    assert history[0].opened_at == NOW

    second_authorization = service.record_subscription_event(
        user_id=user_id,
        idempotency_key="subscription-accept-2",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    assert second_authorization.available_grants == 1
    disabled = service.disable(
        user_id=user_id,
        idempotency_key="preference-disable-1",
        command=DisableCoachNotificationsCommand(
            basedOnPreferenceVersionId=enabled.preference_version_id
        ),
    )
    assert disabled.enabled is False
    assert CoachNotificationRepository(db_session).available_grants(user_id=user_id) == 0


def test_uncertain_wechat_dispatch_is_terminal_and_not_retried(db_session: Session) -> None:
    user_id = _seed_user(db_session)
    settings = _settings()
    service = CoachNotificationService(
        CoachNotificationRepository(db_session), settings, clock=lambda: NOW
    )
    initial = service.settings(user_id=user_id)
    service.update_preference(
        user_id=user_id,
        idempotency_key="preference-enable-2",
        command=_enabled_command(initial.preference.preference_version_id),
    )
    service.record_subscription_event(
        user_id=user_id,
        idempotency_key="subscription-accept-3",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    delivery_id = _queue_delivery(db_session, user_id=user_id, now=NOW)
    db_session.commit()
    worker = CoachNotificationWorker(
        session=db_session,
        settings=settings,
        sender=UncertainSender(),
        clock=lambda: NOW,
    )

    job = worker.process_next(now=NOW)

    assert job is not None and job.status == "failed"
    assert job.last_error_code == "wechat_dispatch_outcome_unknown"
    state = db_session.execute(
        text(
            "select state from messaging.coach_notification_deliveries "
            "where delivery_id = cast(:delivery_id as uuid)"
        ),
        {"delivery_id": delivery_id},
    ).scalar_one()
    assert state == "outcome_unknown"
    assert CoachNotificationRepository(db_session).settled_without_retry_for_fact(
        user_id=user_id,
        local_date=NOW.date(),
        category="task",
        fact_fingerprint="a" * 64,
    )
    assert worker.process_next(now=NOW) is None


def test_recovered_sending_delivery_becomes_unknown_without_duplicate_send(
    db_session: Session,
) -> None:
    user_id = _seed_user(db_session)
    settings = _settings()
    service = CoachNotificationService(
        CoachNotificationRepository(db_session), settings, clock=lambda: NOW
    )
    initial = service.settings(user_id=user_id)
    service.update_preference(
        user_id=user_id,
        idempotency_key="preference-enable-recovery",
        command=_enabled_command(initial.preference.preference_version_id),
    )
    service.record_subscription_event(
        user_id=user_id,
        idempotency_key="subscription-accept-recovery",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    delivery_id = _queue_delivery(db_session, user_id=user_id, now=NOW)
    db_session.execute(
        text(
            "update messaging.coach_notification_deliveries set state = 'sending' "
            "where delivery_id = cast(:delivery_id as uuid)"
        ),
        {"delivery_id": delivery_id},
    )
    db_session.commit()
    sender = AcceptedSender()
    worker = CoachNotificationWorker(
        session=db_session,
        settings=settings,
        sender=sender,
        clock=lambda: NOW,
    )

    job = worker.process_next(now=NOW)

    assert job is not None and job.status == "failed"
    assert sender.calls == []
    assert db_session.execute(
        text(
            "select state from messaging.coach_notification_deliveries "
            "where delivery_id = cast(:delivery_id as uuid)"
        ),
        {"delivery_id": delivery_id},
    ).scalar_one() == "outcome_unknown"


def test_template_rotation_cancels_old_delivery_and_invalidates_old_grant(
    db_session: Session,
) -> None:
    user_id = _seed_user(db_session)
    service = CoachNotificationService(
        CoachNotificationRepository(db_session), _settings(), clock=lambda: NOW
    )
    initial = service.settings(user_id=user_id)
    service.update_preference(
        user_id=user_id,
        idempotency_key="preference-enable-rotation",
        command=_enabled_command(initial.preference.preference_version_id),
    )
    for suffix in ("delivery", "spare"):
        service.record_subscription_event(
            user_id=user_id,
            idempotency_key=f"subscription-accept-rotation-{suffix}",
            command=WechatSubscriptionEventCommand.model_validate_json(
                json.dumps(
                    {
                        "source": "request_prompt",
                        "templateCode": "coach_reminder",
                        "templateId": "wechat-template-real-contract-id",
                        "result": "accept",
                    }
                )
            ),
        )
    delivery_id = _queue_delivery(db_session, user_id=user_id, now=NOW)
    db_session.commit()
    rotated = Settings(
        coach_notifications_enabled=True,
        wechat_mini_app_secret="test-secret",
        wechat_coach_template_release_json=json.dumps(
            {
                "releaseId": "coach-template-release-rotated",
                "templateId": "wechat-template-rotated-id",
                "fields": {
                    "title": "thing1",
                    "reminderTime": "time2",
                    "action": "thing3",
                },
            }
        ),
        wechat_coach_miniprogram_state="formal",
    )
    sender = AcceptedSender()
    worker = CoachNotificationWorker(
        session=db_session,
        settings=rotated,
        sender=sender,
        clock=lambda: NOW,
    )

    assert worker.run_once() is True

    assert sender.calls == []
    assert db_session.execute(
        text(
            "select state from messaging.coach_notification_deliveries "
            "where delivery_id = cast(:delivery_id as uuid)"
        ),
        {"delivery_id": delivery_id},
    ).scalar_one() == "canceled"
    assert CoachNotificationRepository(db_session).available_grants(
        user_id=user_id
    ) == 0


def test_evidence_policy_queues_one_privacy_safe_reflection_message(
    db_session: Session,
) -> None:
    user_id = _seed_user(db_session)
    _seed_plan_task(db_session, user_id=user_id)
    settings = _settings()
    service = CoachNotificationService(
        CoachNotificationRepository(db_session), settings, clock=lambda: NOW
    )
    initial = service.settings(user_id=user_id)
    service.update_preference(
        user_id=user_id,
        idempotency_key="preference-enable-3",
        command=_enabled_command(initial.preference.preference_version_id),
    )
    service.record_subscription_event(
        user_id=user_id,
        idempotency_key="subscription-accept-4",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    template = CoachTemplateRelease.from_json(TEMPLATE_JSON)
    assert template is not None
    evaluator = CoachNotificationEvaluator(template=template)

    result = evaluator.evaluate(db_session, user_id=user_id, now=NOW)
    repeated = evaluator.evaluate(db_session, user_id=user_id, now=NOW)

    assert result.outcome == "queued"
    assert result.category == "reflection"
    assert repeated.outcome == "already_queued"
    delivery = db_session.execute(
        text(
            "select payload, page_path from messaging.coach_notification_deliveries "
            "where delivery_id = cast(:delivery_id as uuid)"
        ),
        {"delivery_id": result.delivery_id},
    ).mappings().one()
    serialized = json.dumps(delivery["payload"], ensure_ascii=False)
    assert "130分" not in serialized
    assert "私人笔记" not in serialized
    assert delivery["page_path"].startswith("pages/today/index?")


def test_notification_facts_export_and_account_erasure_are_complete(
    db_session: Session,
) -> None:
    user_id = _seed_user(db_session)
    settings = _settings()
    coach_service = CoachNotificationService(
        CoachNotificationRepository(db_session), settings, clock=lambda: NOW
    )
    initial = coach_service.settings(user_id=user_id)
    coach_service.update_preference(
        user_id=user_id,
        idempotency_key="privacy-preference-enable-1",
        command=_enabled_command(initial.preference.preference_version_id),
    )
    coach_service.record_subscription_event(
        user_id=user_id,
        idempotency_key="privacy-subscription-accept-1",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    _queue_delivery(db_session, user_id=user_id, now=NOW)
    db_session.commit()

    rights_repository = DataRightsRepository(db_session)
    overview = rights_repository.overview(user_id=user_id, now=NOW)
    communications = next(
        item for item in overview.categories if item.category == "communications"
    )
    assert communications.item_count == 1
    export = rights_repository.build_export_payload(user_id=user_id, now=NOW)
    assert len(export["coachNotificationPreferences"]) == 2
    assert len(export["wechatSubscriptionEvents"]) == 1
    assert len(export["coachNotificationDeliveries"]) == 1
    assert len(export["coachNotificationDeliveryEvents"]) == 1

    rights_service = DataRightsService(rights_repository, now=lambda: NOW)
    deletion = rights_service.request_deletion(
        user_id=user_id,
        request=AccountDeletionCreateRequest(
            command_key="coach-account-delete-command-0001",
            confirmation_phrase=CONFIRMATION_PHRASE,
        ),
    )
    assert deletion.state == "queued"
    template = CoachTemplateRelease.from_json(TEMPLATE_JSON)
    assert template is not None
    inactive = CoachNotificationEvaluator(template=template).evaluate(
        db_session, user_id=user_id, now=NOW
    )
    assert inactive.outcome == "account_inactive"

    completed = DataRightsWorker(
        storage=EmptyPrivateStorage(),
        settings=Settings(privacy_export_expiry_seconds=86400),
        now=lambda: datetime.now(UTC) + timedelta(seconds=1),
    ).process_next(db_session)
    assert completed is not None and completed.status == "succeeded"
    for table in (
        "coach_preference_versions",
        "coach_evaluation_cursors",
        "wechat_subscription_events",
        "wechat_subscription_grants",
        "coach_notification_decisions",
        "coach_notification_deliveries",
        "coach_notification_delivery_events",
        "coach_notification_open_events",
    ):
        assert db_session.execute(
            text(
                f"select count(*) from messaging.{table} "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 0
    assert db_session.execute(
        text(
            "select count(*) from platform.background_jobs "
            "where job_type = 'coach_notification_send_v1' "
            "and payload->>'deliveryId' is not null"
        )
    ).scalar_one() == 0


def test_operations_metrics_expose_coach_delivery_health(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    metric_settings = Settings(
        operations_metrics_token="coach-metrics-token",
        wechat_coach_template_release_json=TEMPLATE_JSON,
    )
    monkeypatch.setattr(operations_router, "get_settings", lambda: metric_settings)

    payload = operations_router.metrics(
        db_session, authorization="Bearer coach-metrics-token"
    )

    assert "anan_coach_notification_outcome_unknown 0" in payload
    assert "anan_coach_notification_enabled_users 0" in payload
    assert "anan_coach_notification_enabled_without_grant 0" in payload
    assert "anan_coach_notification_stale_claims 0" in payload


def test_database_rejects_delivery_state_skips(db_session: Session) -> None:
    user_id = _seed_user(db_session)
    service = CoachNotificationService(
        CoachNotificationRepository(db_session), _settings(), clock=lambda: NOW
    )
    initial = service.settings(user_id=user_id)
    service.update_preference(
        user_id=user_id,
        idempotency_key="preference-enable-state-machine",
        command=_enabled_command(initial.preference.preference_version_id),
    )
    service.record_subscription_event(
        user_id=user_id,
        idempotency_key="subscription-accept-state-machine",
        command=WechatSubscriptionEventCommand.model_validate_json(
            json.dumps(
                {
                    "source": "request_prompt",
                    "templateCode": "coach_reminder",
                    "templateId": "wechat-template-real-contract-id",
                    "result": "accept",
                }
            )
        ),
    )
    delivery_id = _queue_delivery(db_session, user_id=user_id, now=NOW)
    db_session.commit()

    with pytest.raises(DBAPIError, match="invalid coach notification delivery transition"):
        db_session.execute(
            text(
                """
                update messaging.coach_notification_deliveries
                set state = 'platform_accepted', accepted_at = :now, terminal_at = :now
                where delivery_id = cast(:delivery_id as uuid)
                """
            ),
            {"delivery_id": delivery_id, "now": NOW},
        )
    db_session.rollback()


def _enabled_command(preference_version_id: UUID) -> UpdateCoachPreferenceCommand:
    return UpdateCoachPreferenceCommand.model_validate_json(
        json.dumps(
            {
                "basedOnPreferenceVersionId": str(preference_version_id),
                "enabled": True,
                "taskRemindersEnabled": True,
                "reflectionRemindersEnabled": True,
                "retestRemindersEnabled": True,
                "mockRemindersEnabled": True,
                "weeklySummaryEnabled": True,
                "receiveStart": "07:30:00",
                "receiveEnd": "22:30:00",
                "quietEnabled": True,
                "quietStart": "12:00:00",
                "quietEnd": "13:30:00",
                "dailyLimit": 2,
                "timezone": "Asia/Shanghai",
            }
        )
    )


def _queue_delivery(session: Session, *, user_id: str, now: datetime) -> str:
    repository = CoachNotificationRepository(session)
    preference = repository.current_preference(user_id=user_id)
    assert preference is not None
    template = CoachTemplateRelease.from_json(TEMPLATE_JSON)
    assert template is not None
    delivery = repository.create_queued_delivery(
        decision_key_seed={"test": str(uuid4())},
        user_id=user_id,
        preference_version_id=str(preference["preference_version_id"]),
        local_date=now.date(),
        category="task",
        fact_fingerprint="a" * 64,
        fact_refs=[{"entityType": "daily_plan_task", "entityId": "task-test"}],
        task_count=1,
        template_release_id=template.release_id,
        template_id=template.template_id,
        page_path_factory=lambda delivery_id: template.message(
            category="task",
            delivery_id=delivery_id,
            local_now=now.astimezone(ZoneInfo("Asia/Shanghai")),
        )[0],
        payload_factory=lambda delivery_id: template.message(
            category="task",
            delivery_id=delivery_id,
            local_now=now.astimezone(ZoneInfo("Asia/Shanghai")),
        )[1],
        now=now,
    )
    assert delivery is not None
    delivery_id = str(delivery["delivery_id"])
    BackgroundJobRepository().enqueue(
        session,
        job_key=f"coach-notification:send:{delivery_id}",
        job_type=COACH_SEND_JOB_TYPE,
        input_version=COACH_SEND_JOB_INPUT_VERSION,
        payload={"deliveryId": delivery_id},
        max_attempts=5,
        run_after=now,
    )
    return delivery_id


def _seed_user(session: Session) -> str:
    user_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learner.users (user_id, display_name, created_at, updated_at)
            values (cast(:user_id as uuid), '督学测试用户', :now, :now)
            """
        ),
        {"user_id": user_id, "now": NOW},
    )
    session.execute(
        text(
            """
            insert into learner.wechat_identities (
              identity_id, user_id, app_id, open_id, union_id,
              session_key_hash, last_login_at, created_at, updated_at
            ) values (
              cast(:identity_id as uuid), cast(:user_id as uuid), :app_id,
              :open_id, null, :session_key_hash, :now, :now, :now
            )
            """
        ),
        {
            "identity_id": str(uuid4()),
            "user_id": user_id,
            "app_id": "wx42824c7f7bfbf4b2",
            "open_id": f"openid-{user_id}",
            "session_key_hash": "b" * 64,
            "now": NOW,
        },
    )
    session.flush()
    return user_id


def _settings() -> Settings:
    return Settings(
        coach_notifications_enabled=True,
        wechat_mini_app_secret="test-secret",
        wechat_coach_template_release_json=TEMPLATE_JSON,
        wechat_coach_miniprogram_state="formal",
    )


def _seed_plan_task(session: Session, *, user_id: str) -> None:
    weekly_preference_id = str(uuid4())
    capacity_id = str(uuid4())
    plan_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learning.weekly_study_preference_versions (
              preference_version_id, command_id, user_id, version_no, timezone,
              weekday_minutes, weekend_minutes, long_block_minutes, fragment_minutes,
              regular_rest_weekday, regular_mock_weekday, effective_from,
              source_type, confirmed, input_fingerprint, created_at
            ) values (
              cast(:preference_id as uuid), :command_id, cast(:user_id as uuid),
              1, 'Asia/Shanghai', 120, 240, 60, 30, 7, 6, :local_date,
              'user_confirmed', true, :fingerprint, :now
            )
            """
        ),
        {
            "preference_id": weekly_preference_id,
            "command_id": f"weekly-pref-{user_id}",
            "user_id": user_id,
            "local_date": NOW.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            "fingerprint": "1" * 64,
            "now": NOW,
        },
    )
    session.execute(
        text(
            """
            insert into learning.daily_capacity_versions (
              capacity_version_id, command_id, user_id, local_date, version_no,
              available_minutes, energy_level, stress_level, confidence_level,
              confirmed, source_type, weekly_preference_version_id,
              input_fingerprint, recorded_at
            ) values (
              cast(:capacity_id as uuid), :command_id, cast(:user_id as uuid),
              :local_date, 1, 120, 4, 2, 4, true, 'user_override',
              cast(:preference_id as uuid), :fingerprint, :now
            )
            """
        ),
        {
            "capacity_id": capacity_id,
            "command_id": f"daily-capacity-{user_id}",
            "user_id": user_id,
            "local_date": NOW.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            "preference_id": weekly_preference_id,
            "fingerprint": "2" * 64,
            "now": NOW,
        },
    )
    session.execute(
        text(
            """
            insert into learning.daily_plan_versions (
              plan_version_id, command_id, user_id, local_date, version_no,
              capacity_version_id, weekly_preference_version_id, day_mode,
              headline, rationale, goal_gap, scheduled_minutes,
              due_reflection_count, due_verification_count, track_explanation,
              input_versions, evidence_refs, invalidation_conditions,
              replan_reason_codes, plan_basis, contract_version, algorithm_version,
              input_fingerprint, generated_at, valid_until
            ) values (
              cast(:plan_id as uuid), :command_id, cast(:user_id as uuid),
              :local_date, 1, cast(:capacity_id as uuid), cast(:preference_id as uuid),
              'normal', '完成今天的复盘', '已有确认复盘任务', '{}'::jsonb, 30,
              1, 0, '{}'::jsonb, '{}'::jsonb,
              '[{"owner":"planning","entityType":"source","entityId":"test"}]'::jsonb,
              '["task_completed"]'::jsonb, '[]'::jsonb, 'evidence_priority_v2',
              'daily-plan@2', 'evidence-priority@2', :fingerprint, :now, :valid_until
            )
            """
        ),
        {
            "plan_id": plan_id,
            "command_id": f"daily-plan-{user_id}",
            "user_id": user_id,
            "local_date": NOW.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            "capacity_id": capacity_id,
            "preference_id": weekly_preference_id,
            "fingerprint": "3" * 64,
            "now": NOW,
            "valid_until": NOW.replace(hour=15),
        },
    )
    session.execute(
        text(
            """
            insert into learning.daily_plan_tasks (
              task_id, plan_version_id, user_id, local_date, plan_version_no,
              candidate_key, work_item_key, task_order, track, stage, kind,
              title, reason, estimated_minutes, mandatory, start_url,
              priority_score, goal_gain, ability_gain, verification_urgency,
              time_fit, load_penalty, track_balance_bonus, evidence_refs,
              completion_contract, input_fingerprint
            ) values (
              cast(:task_id as uuid), cast(:plan_id as uuid), cast(:user_id as uuid),
              :local_date, 1, 'reflection-sensitive-test', :work_item_key, 1,
              'xingce', 'objective_reflection', 'reflection',
              '目标岗位 130分 · 私人笔记复盘', '用户已确认的复盘任务', 30, true,
              '/pages/reflection/index', 0.9, 0.8, 0.8, 0.8, 0.9, 0.1, 0.5,
              '[{"owner":"planning","entityType":"source","entityId":"test"}]'::jsonb,
              '{"objective":"完成复盘"}'::jsonb, :fingerprint
            )
            """
        ),
        {
            "task_id": str(uuid4()),
            "plan_id": plan_id,
            "user_id": user_id,
            "local_date": NOW.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            "work_item_key": "4" * 64,
            "fingerprint": "5" * 64,
        },
    )
    session.flush()
