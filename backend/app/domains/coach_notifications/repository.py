from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.coach_notifications.schemas import (
    CoachCategory,
    UpdateCoachPreferenceCommand,
    WechatSubscriptionEventCommand,
)


class CoachNotificationConflict(RuntimeError):
    pass


class CoachNotificationNotFound(RuntimeError):
    pass


class CoachNotificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def current_or_create_preference(
        self, *, user_id: str, now: datetime
    ) -> RowMapping:
        current = self._current_preference(user_id=user_id)
        if current is not None:
            return current
        preference_id = str(uuid4())
        values = {
            "preference_version_id": preference_id,
            "command_id": f"coach-default:{user_id}",
            "request_fingerprint": _fingerprint({"enabled": False, "version": 1}),
            "user_id": user_id,
            "created_at": _utc(now),
        }
        row = self._session.execute(
            text(
                """
                insert into messaging.coach_preference_versions (
                  preference_version_id, command_id, request_fingerprint, user_id,
                  version_no, enabled, task_reminders_enabled,
                  reflection_reminders_enabled, retest_reminders_enabled,
                  mock_reminders_enabled, weekly_summary_enabled,
                  receive_start, receive_end, quiet_enabled, quiet_start, quiet_end,
                  daily_limit, timezone, source_type,
                  supersedes_preference_version_id, created_at
                ) values (
                  cast(:preference_version_id as uuid), :command_id,
                  :request_fingerprint, cast(:user_id as uuid), 1, false,
                  true, true, true, true, true, null, null, false, null, null,
                  1, 'Asia/Shanghai', 'system_default', null, :created_at
                )
                on conflict (user_id, version_no) do nothing
                returning *
                """
            ),
            values,
        ).mappings().one_or_none()
        if row is not None:
            return row
        current = self._current_preference(user_id=user_id)
        if current is None:
            raise CoachNotificationConflict("coach preference initialization raced")
        return current

    def update_preference(
        self,
        *,
        user_id: str,
        command_id: str,
        command: UpdateCoachPreferenceCommand,
        now: datetime,
        force_disabled: bool = False,
    ) -> RowMapping:
        self._lock_active_user(user_id=user_id)
        request = command.model_dump(by_alias=True, mode="json")
        if force_disabled:
            request = {"disable": True, "basedOn": str(command.based_on_preference_version_id)}
        fingerprint = _fingerprint(request)
        replay = self._session.execute(
            text(
                """
                select * from messaging.coach_preference_versions
                where user_id = cast(:user_id as uuid) and command_id = :command_id
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()
        if replay is not None:
            if replay["request_fingerprint"] != fingerprint:
                raise CoachNotificationConflict(
                    "preference idempotency key was reused with different input"
                )
            return replay

        current = self._session.execute(
            text(
                """
                select * from messaging.coach_preference_versions
                where user_id = cast(:user_id as uuid)
                order by version_no desc limit 1 for update
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        if current is None:
            raise CoachNotificationConflict("coach preference does not exist")
        if str(current["preference_version_id"]) != str(
            command.based_on_preference_version_id
        ):
            raise CoachNotificationConflict(
                "coach preference changed; reload before saving"
            )
        enabled = False if force_disabled else command.enabled
        preference_id = str(uuid4())
        row = self._session.execute(
            text(
                """
                insert into messaging.coach_preference_versions (
                  preference_version_id, command_id, request_fingerprint, user_id,
                  version_no, enabled, task_reminders_enabled,
                  reflection_reminders_enabled, retest_reminders_enabled,
                  mock_reminders_enabled, weekly_summary_enabled,
                  receive_start, receive_end, quiet_enabled, quiet_start, quiet_end,
                  daily_limit, timezone, source_type,
                  supersedes_preference_version_id, created_at
                ) values (
                  cast(:preference_version_id as uuid), :command_id,
                  :request_fingerprint, cast(:user_id as uuid), :version_no, :enabled,
                  :task_enabled, :reflection_enabled, :retest_enabled, :mock_enabled,
                  :weekly_enabled, :receive_start, :receive_end, :quiet_enabled,
                  :quiet_start, :quiet_end, :daily_limit, :timezone, :source_type,
                  cast(:supersedes as uuid), :created_at
                ) returning *
                """
            ),
            {
                "preference_version_id": preference_id,
                "command_id": command_id,
                "request_fingerprint": fingerprint,
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1,
                "enabled": enabled,
                "task_enabled": command.task_reminders_enabled,
                "reflection_enabled": command.reflection_reminders_enabled,
                "retest_enabled": command.retest_reminders_enabled,
                "mock_enabled": command.mock_reminders_enabled,
                "weekly_enabled": command.weekly_summary_enabled,
                "receive_start": command.receive_start,
                "receive_end": command.receive_end,
                "quiet_enabled": command.quiet_enabled,
                "quiet_start": command.quiet_start,
                "quiet_end": command.quiet_end,
                "daily_limit": command.daily_limit,
                "timezone": command.timezone,
                "source_type": "user_confirmed" if enabled else "user_disabled",
                "supersedes": str(current["preference_version_id"]),
                "created_at": _utc(now),
            },
        ).mappings().one()
        if enabled:
            self._session.execute(
                text(
                    """
                    insert into messaging.coach_evaluation_cursors (
                      user_id, next_evaluation_at, updated_at
                    ) values (cast(:user_id as uuid), :now, :now)
                    on conflict (user_id) do update set
                      next_evaluation_at = least(
                        messaging.coach_evaluation_cursors.next_evaluation_at,
                        excluded.next_evaluation_at
                      ),
                      updated_at = excluded.updated_at
                    """
                ),
                {"user_id": user_id, "now": _utc(now)},
            )
        else:
            self.invalidate_grants(user_id=user_id, now=now)
            self.cancel_outstanding(user_id=user_id, now=now, reason="user_disabled")
        return row

    def record_subscription_event(
        self,
        *,
        user_id: str,
        command_id: str,
        command: WechatSubscriptionEventCommand,
        now: datetime,
    ) -> tuple[RowMapping, bool]:
        self._lock_active_user(user_id=user_id)
        replay = self._session.execute(
            text(
                """
                select * from messaging.wechat_subscription_events
                where user_id = cast(:user_id as uuid)
                  and command_id = :command_id and template_code = :template_code
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "template_code": command.template_code,
            },
        ).mappings().one_or_none()
        if replay is not None:
            expected = (command.source, command.template_id, command.result)
            actual = (replay["source"], replay["template_id"], replay["result"])
            if actual != expected:
                raise CoachNotificationConflict(
                    "subscription idempotency key was reused with different input"
                )
            grant = self._session.execute(
                text(
                    "select grant_id from messaging.wechat_subscription_grants "
                    "where authorization_event_id = cast(:event_id as uuid)"
                ),
                {"event_id": str(replay["event_id"])},
            ).scalar_one_or_none()
            return replay, grant is not None

        event_id = str(uuid4())
        row = self._session.execute(
            text(
                """
                insert into messaging.wechat_subscription_events (
                  event_id, user_id, command_id, source, template_code,
                  template_id, result, occurred_at
                ) values (
                  cast(:event_id as uuid), cast(:user_id as uuid), :command_id,
                  :source, :template_code, :template_id, :result, :occurred_at
                ) on conflict do nothing returning *
                """
            ),
            {
                "event_id": event_id,
                "user_id": user_id,
                "command_id": command_id,
                "source": command.source,
                "template_code": command.template_code,
                "template_id": command.template_id,
                "result": command.result,
                "occurred_at": _utc(now),
            },
        ).mappings().one_or_none()
        if row is None:
            replay = self._session.execute(
                text(
                    """
                    select * from messaging.wechat_subscription_events
                    where user_id = cast(:user_id as uuid)
                      and command_id = :command_id
                      and template_code = :template_code
                    """
                ),
                {
                    "user_id": user_id,
                    "command_id": command_id,
                    "template_code": command.template_code,
                },
            ).mappings().one()
            expected = (command.source, command.template_id, command.result)
            actual = (replay["source"], replay["template_id"], replay["result"])
            if actual != expected:
                raise CoachNotificationConflict(
                    "subscription idempotency key was reused with different input"
                )
            grant = self._session.execute(
                text(
                    "select grant_id from messaging.wechat_subscription_grants "
                    "where authorization_event_id = cast(:event_id as uuid)"
                ),
                {"event_id": str(replay["event_id"])},
            ).scalar_one_or_none()
            return replay, grant is not None
        grant_created = command.source == "request_prompt" and command.result == "accept"
        if grant_created:
            self._session.execute(
                text(
                    """
                    insert into messaging.wechat_subscription_grants (
                      grant_id, user_id, authorization_event_id, template_code,
                      template_id, state, claimed_delivery_id, accepted_at,
                      consumed_at, invalidated_at
                    ) values (
                      cast(:grant_id as uuid), cast(:user_id as uuid),
                      cast(:event_id as uuid), :template_code, :template_id,
                      'available', null, :accepted_at, null, null
                    )
                    """
                ),
                {
                    "grant_id": str(uuid4()),
                    "user_id": user_id,
                    "event_id": event_id,
                    "template_code": command.template_code,
                    "template_id": command.template_id,
                    "accepted_at": _utc(now),
                },
            )
            self.wake_evaluation(user_id=user_id, now=now)
        elif command.source == "settings_snapshot" and command.result in {"reject", "ban"}:
            self.invalidate_grants(user_id=user_id, now=now)
            self.cancel_outstanding(
                user_id=user_id, now=now, reason=f"wechat_{command.result}"
            )
        return row, grant_created

    def available_grants(
        self, *, user_id: str, template_id: str | None = None
    ) -> int:
        return int(
            self._session.execute(
                text(
                    """
                    select count(*) from messaging.wechat_subscription_grants
                    where user_id = cast(:user_id as uuid) and state = 'available'
                      and (
                        cast(:template_id as text) is null
                        or template_id = cast(:template_id as text)
                      )
                    """
                ),
                {"user_id": user_id, "template_id": template_id},
            ).scalar_one()
        )

    def invalidate_retired_grants(self, *, template_id: str, now: datetime) -> int:
        result = self._session.execute(
            text(
                """
                update messaging.wechat_subscription_grants
                set state = 'invalidated', invalidated_at = :now
                where state = 'available' and template_id <> :template_id
                """
            ),
            {"template_id": template_id, "now": _utc(now)},
        )
        return int(result.rowcount or 0)

    def latest_subscription_event(self, *, user_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                """
                select * from messaging.wechat_subscription_events
                where user_id = cast(:user_id as uuid)
                order by occurred_at desc, event_id desc limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def recent_deliveries(self, *, user_id: str, limit: int = 10) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    select delivery.*, opened.opened_at
                    from messaging.coach_notification_deliveries delivery
                    left join messaging.coach_notification_open_events opened
                      on opened.delivery_id = delivery.delivery_id
                    where delivery.user_id = cast(:user_id as uuid)
                    order by delivery.created_at desc, delivery.delivery_id desc
                    limit :limit
                    """
                ),
                {"user_id": user_id, "limit": limit},
            ).mappings().all()
        )

    def record_open(
        self,
        *,
        user_id: str,
        delivery_id: str,
        command_id: str,
        opened_at: datetime,
    ) -> RowMapping:
        self._lock_active_user(user_id=user_id)
        replay = self._session.execute(
            text(
                """
                select * from messaging.coach_notification_open_events
                where user_id = cast(:user_id as uuid) and command_id = :command_id
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()
        if replay is not None:
            if str(replay["delivery_id"]) != delivery_id:
                raise CoachNotificationConflict(
                    "open idempotency key was reused for another delivery"
                )
            return replay
        delivery = self._session.execute(
            text(
                """
                select delivery_id from messaging.coach_notification_deliveries
                where delivery_id = cast(:delivery_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and state in ('platform_accepted', 'outcome_unknown')
                """
            ),
            {"delivery_id": delivery_id, "user_id": user_id},
        ).one_or_none()
        if delivery is None:
            raise CoachNotificationNotFound("notification delivery does not exist")
        row = self._session.execute(
            text(
                """
                insert into messaging.coach_notification_open_events (
                  open_event_id, delivery_id, user_id, command_id, opened_at
                ) values (
                  cast(:open_event_id as uuid), cast(:delivery_id as uuid),
                  cast(:user_id as uuid), :command_id, :opened_at
                ) on conflict do nothing returning *
                """
            ),
            {
                "open_event_id": str(uuid4()),
                "delivery_id": delivery_id,
                "user_id": user_id,
                "command_id": command_id,
                "opened_at": _utc(opened_at),
            },
        ).mappings().one_or_none()
        if row is not None:
            return row
        replay = self._session.execute(
            text(
                "select * from messaging.coach_notification_open_events "
                "where delivery_id = cast(:delivery_id as uuid) "
                "or (user_id = cast(:user_id as uuid) and command_id = :command_id)"
            ),
            {
                "delivery_id": delivery_id,
                "user_id": user_id,
                "command_id": command_id,
            },
        ).mappings().one()
        if str(replay["delivery_id"]) != delivery_id:
            raise CoachNotificationConflict(
                "open idempotency key was reused for another delivery"
            )
        return replay

    def claim_due_users(
        self,
        *,
        now: datetime,
        interval: timedelta,
        limit: int,
    ) -> list[str]:
        rows = self._session.execute(
            text(
                """
                with due as (
                  select cursor.user_id
                  from messaging.coach_evaluation_cursors cursor
                  join learner.users learner
                    on learner.user_id = cursor.user_id
                  join lateral (
                    select enabled from messaging.coach_preference_versions preference
                    where preference.user_id = cursor.user_id
                    order by preference.version_no desc limit 1
                  ) current on true
                  where cursor.next_evaluation_at <= :now
                    and learner.is_active = true
                    and learner.account_status = 'active'
                    and current.enabled = true
                  order by cursor.next_evaluation_at
                  for update of cursor skip locked
                  limit :limit
                )
                update messaging.coach_evaluation_cursors cursor
                set last_evaluated_at = :now,
                    next_evaluation_at = :next_evaluation_at,
                    updated_at = :now
                from due where cursor.user_id = due.user_id
                returning cursor.user_id::text
                """
            ),
            {
                "now": _utc(now),
                "next_evaluation_at": _utc(now) + interval,
                "limit": limit,
            },
        ).all()
        return [str(row[0]) for row in rows]

    def current_preference(self, *, user_id: str) -> RowMapping | None:
        return self._current_preference(user_id=user_id)

    def account_active(self, *, user_id: str) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from learner.users
                      where user_id = cast(:user_id as uuid)
                        and is_active = true and account_status = 'active'
                    )
                    """
                ),
                {"user_id": user_id},
            ).scalar_one()
        )

    def current_incomplete_tasks(self, *, user_id: str, local_date: date) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    select task.task_id::text, task.plan_version_id::text,
                           task.kind, task.stage, task.work_item_key,
                           execution.execution_version_id::text as execution_version_id,
                           execution.status as execution_status
                    from learning.daily_plan_versions plan
                    join learning.daily_plan_tasks task
                      on task.plan_version_id = plan.plan_version_id
                    left join lateral (
                      select item.execution_version_id, item.status
                      from learning.task_execution_versions item
                      where item.user_id = task.user_id
                        and item.work_item_key = task.work_item_key
                        and not exists (
                          select 1 from learning.task_execution_versions successor
                          where successor.supersedes_execution_version_id = item.execution_version_id
                        )
                      order by item.version_no desc limit 1
                    ) execution on true
                    where plan.user_id = cast(:user_id as uuid)
                      and plan.local_date = :local_date
                      and not exists (
                        select 1 from learning.daily_plan_versions successor
                        where successor.supersedes_plan_version_id = plan.plan_version_id
                      )
                      and coalesce(execution.status, 'ready') not in ('complete', 'abandoned')
                    order by task.task_order
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            ).mappings().all()
        )

    def weekly_plan_refs(
        self, *, user_id: str, week_start: date, week_end: date
    ) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    select plan.plan_version_id::text, plan.local_date,
                           plan.version_no
                    from learning.daily_plan_versions plan
                    where plan.user_id = cast(:user_id as uuid)
                      and plan.local_date between :week_start and :week_end
                      and not exists (
                        select 1 from learning.daily_plan_versions successor
                        where successor.supersedes_plan_version_id = plan.plan_version_id
                      )
                    order by plan.local_date
                    """
                ),
                {"user_id": user_id, "week_start": week_start, "week_end": week_end},
            ).mappings().all()
        )

    def settled_without_retry_for_fact(
        self,
        *,
        user_id: str,
        local_date: date,
        category: CoachCategory,
        fact_fingerprint: str,
    ) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from messaging.coach_notification_decisions decision
                      join messaging.coach_notification_deliveries delivery
                        on delivery.decision_id = decision.decision_id
                      where decision.user_id = cast(:user_id as uuid)
                        and decision.local_date = :local_date
                        and decision.category = :category
                        and decision.fact_fingerprint = :fact_fingerprint
                        and delivery.state in ('platform_accepted', 'outcome_unknown')
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "local_date": local_date,
                    "category": category,
                    "fact_fingerprint": fact_fingerprint,
                },
            ).scalar_one()
        )

    def pending_for_fact(
        self,
        *,
        user_id: str,
        local_date: date,
        category: CoachCategory,
        fact_fingerprint: str,
    ) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from messaging.coach_notification_decisions decision
                      join messaging.coach_notification_deliveries delivery
                        on delivery.decision_id = decision.decision_id
                      where decision.user_id = cast(:user_id as uuid)
                        and decision.local_date = :local_date
                        and decision.category = :category
                        and decision.fact_fingerprint = :fact_fingerprint
                        and delivery.state in ('queued', 'sending')
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "local_date": local_date,
                    "category": category,
                    "fact_fingerprint": fact_fingerprint,
                },
            ).scalar_one()
        )

    def accepted_count(self, *, user_id: str, local_date: date) -> int:
        return int(
            self._session.execute(
                text(
                    """
                    select count(*)
                    from messaging.coach_notification_decisions decision
                    join messaging.coach_notification_deliveries delivery
                      on delivery.decision_id = decision.decision_id
                    where decision.user_id = cast(:user_id as uuid)
                      and decision.local_date = :local_date
                      and delivery.state = 'platform_accepted'
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            ).scalar_one()
        )

    def create_suppressed_decision(
        self,
        *,
        decision_key: str,
        user_id: str,
        preference_version_id: str,
        local_date: date,
        category: CoachCategory,
        outcome: str,
        fact_fingerprint: str,
        fact_refs: list[dict[str, str | int]],
        task_count: int,
        now: datetime,
    ) -> bool:
        row = self._session.execute(
            text(
                """
                insert into messaging.coach_notification_decisions (
                  decision_id, decision_key, user_id, preference_version_id,
                  local_date, category, outcome, policy_version, fact_fingerprint,
                  fact_refs, task_count, grant_id, decided_at
                ) values (
                  cast(:decision_id as uuid), :decision_key, cast(:user_id as uuid),
                  cast(:preference_version_id as uuid), :local_date, :category,
                  :outcome, 'coach-supervision-policy@1', :fact_fingerprint,
                  cast(:fact_refs as jsonb), :task_count, null, :decided_at
                ) on conflict (decision_key) do nothing returning decision_id
                """
            ),
            {
                "decision_id": str(uuid4()),
                "decision_key": decision_key,
                "user_id": user_id,
                "preference_version_id": preference_version_id,
                "local_date": local_date,
                "category": category,
                "outcome": outcome,
                "fact_fingerprint": fact_fingerprint,
                "fact_refs": json.dumps(fact_refs, ensure_ascii=False, sort_keys=True),
                "task_count": task_count,
                "decided_at": _utc(now),
            },
        ).one_or_none()
        return row is not None

    def create_queued_delivery(
        self,
        *,
        decision_key_seed: dict[str, Any],
        user_id: str,
        preference_version_id: str,
        local_date: date,
        category: CoachCategory,
        fact_fingerprint: str,
        fact_refs: list[dict[str, str | int]],
        task_count: int,
        template_release_id: str,
        template_id: str,
        page_path_factory: Any,
        payload_factory: Any,
        now: datetime,
    ) -> RowMapping | None:
        grant = self._session.execute(
            text(
                """
                select * from messaging.wechat_subscription_grants
                where user_id = cast(:user_id as uuid)
                  and template_id = :template_id and state = 'available'
                order by accepted_at for update skip locked limit 1
                """
            ),
            {"user_id": user_id, "template_id": template_id},
        ).mappings().one_or_none()
        if grant is None:
            return None
        delivery_id = str(uuid4())
        decision_id = str(uuid4())
        decision_key = _fingerprint(
            {**decision_key_seed, "outcome": "queued", "grantId": str(grant["grant_id"])}
        )
        existing = self._session.execute(
            text(
                "select 1 from messaging.coach_notification_decisions "
                "where decision_key = :decision_key"
            ),
            {"decision_key": decision_key},
        ).one_or_none()
        if existing is not None:
            return None
        page_path = str(page_path_factory(delivery_id))
        payload = payload_factory(delivery_id)
        payload_fingerprint = _fingerprint(payload)
        self._session.execute(
            text(
                """
                insert into messaging.coach_notification_decisions (
                  decision_id, decision_key, user_id, preference_version_id,
                  local_date, category, outcome, policy_version, fact_fingerprint,
                  fact_refs, task_count, grant_id, decided_at
                ) values (
                  cast(:decision_id as uuid), :decision_key, cast(:user_id as uuid),
                  cast(:preference_version_id as uuid), :local_date, :category,
                  'queued', 'coach-supervision-policy@1', :fact_fingerprint,
                  cast(:fact_refs as jsonb), :task_count, cast(:grant_id as uuid),
                  :decided_at
                )
                """
            ),
            {
                "decision_id": decision_id,
                "decision_key": decision_key,
                "user_id": user_id,
                "preference_version_id": preference_version_id,
                "local_date": local_date,
                "category": category,
                "fact_fingerprint": fact_fingerprint,
                "fact_refs": json.dumps(fact_refs, ensure_ascii=False, sort_keys=True),
                "task_count": task_count,
                "grant_id": str(grant["grant_id"]),
                "decided_at": _utc(now),
            },
        )
        delivery = self._session.execute(
            text(
                """
                insert into messaging.coach_notification_deliveries (
                  delivery_id, decision_id, user_id, grant_id, template_release_id,
                  template_id, category, state, page_path, payload,
                  payload_fingerprint, created_at
                ) values (
                  cast(:delivery_id as uuid), cast(:decision_id as uuid),
                  cast(:user_id as uuid), cast(:grant_id as uuid),
                  :template_release_id, :template_id, :category, 'queued',
                  :page_path, cast(:payload as jsonb), :payload_fingerprint, :created_at
                ) returning *
                """
            ),
            {
                "delivery_id": delivery_id,
                "decision_id": decision_id,
                "user_id": user_id,
                "grant_id": str(grant["grant_id"]),
                "template_release_id": template_release_id,
                "template_id": template_id,
                "category": category,
                "page_path": page_path,
                "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "payload_fingerprint": payload_fingerprint,
                "created_at": _utc(now),
            },
        ).mappings().one()
        self._session.execute(
            text(
                """
                update messaging.wechat_subscription_grants
                set state = 'claimed', claimed_delivery_id = cast(:delivery_id as uuid)
                where grant_id = cast(:grant_id as uuid) and state = 'available'
                """
            ),
            {"delivery_id": delivery_id, "grant_id": str(grant["grant_id"])},
        )
        self._insert_delivery_event(
            delivery_id=delivery_id,
            user_id=user_id,
            event_type="queued",
            attempt_no=0,
            evidence={"policyVersion": "coach-supervision-policy@1"},
            now=now,
        )
        return delivery

    def delivery(self, *, delivery_id: str, for_update: bool = False) -> RowMapping | None:
        suffix = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from messaging.coach_notification_deliveries "
                "where delivery_id = cast(:delivery_id as uuid)" + suffix
            ),
            {"delivery_id": delivery_id},
        ).mappings().one_or_none()

    def current_enabled(self, *, user_id: str) -> bool:
        row = self._current_preference(user_id=user_id)
        return bool(
            row is not None
            and row["enabled"]
            and self.account_active(user_id=user_id)
        )

    def wechat_open_id(self, *, user_id: str, app_id: str) -> str | None:
        value = self._session.execute(
            text(
                """
                select open_id from learner.wechat_identities
                where user_id = cast(:user_id as uuid) and app_id = :app_id
                order by last_login_at desc limit 1
                """
            ),
            {"user_id": user_id, "app_id": app_id},
        ).scalar_one_or_none()
        return str(value) if value is not None else None

    def mark_sending(self, *, delivery: RowMapping, attempt_no: int, now: datetime) -> None:
        self._session.execute(
            text(
                """
                update messaging.coach_notification_deliveries set state = 'sending'
                where delivery_id = :delivery_id and state in ('queued', 'sending')
                """
            ),
            {"delivery_id": delivery["delivery_id"]},
        )
        self._insert_delivery_event(
            delivery_id=str(delivery["delivery_id"]),
            user_id=str(delivery["user_id"]),
            event_type="sending",
            attempt_no=attempt_no,
            evidence={},
            now=now,
        )

    def mark_retry(
        self,
        *,
        delivery: RowMapping,
        attempt_no: int,
        error_code: int | None,
        now: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                update messaging.coach_notification_deliveries
                set state = 'queued', platform_error_code = :error_code,
                    platform_error_message = 'transient_wechat_failure'
                where delivery_id = :delivery_id and state = 'sending'
                """
            ),
            {
                "delivery_id": delivery["delivery_id"],
                "error_code": error_code,
            },
        )
        self._insert_delivery_event(
            delivery_id=str(delivery["delivery_id"]),
            user_id=str(delivery["user_id"]),
            event_type="retry_scheduled",
            attempt_no=attempt_no,
            evidence={"errorCode": error_code},
            now=now,
        )

    def mark_accepted(
        self, *, delivery: RowMapping, attempt_no: int, now: datetime
    ) -> None:
        current = _utc(now)
        self._session.execute(
            text(
                """
                update messaging.coach_notification_deliveries
                set state = 'platform_accepted', accepted_at = :now, terminal_at = :now,
                    platform_error_code = null, platform_error_message = null
                where delivery_id = :delivery_id and state = 'sending'
                """
            ),
            {"delivery_id": delivery["delivery_id"], "now": current},
        )
        self._session.execute(
            text(
                """
                update messaging.wechat_subscription_grants
                set state = 'consumed', consumed_at = :now
                where grant_id = :grant_id and state = 'claimed'
                """
            ),
            {"grant_id": delivery["grant_id"], "now": current},
        )
        self._insert_delivery_event(
            delivery_id=str(delivery["delivery_id"]),
            user_id=str(delivery["user_id"]),
            event_type="platform_accepted",
            attempt_no=attempt_no,
            evidence={"platform": "wechat_subscribe_message"},
            now=now,
        )

    def mark_terminal(
        self,
        *,
        delivery: RowMapping,
        state: str,
        event_type: str,
        attempt_no: int,
        error_code: int | None,
        reason: str,
        now: datetime,
    ) -> None:
        current = _utc(now)
        self._session.execute(
            text(
                """
                update messaging.coach_notification_deliveries
                set state = :state, terminal_at = :now, platform_error_code = :error_code,
                    platform_error_message = :reason
                where delivery_id = :delivery_id
                  and state in ('queued', 'sending')
                """
            ),
            {
                "delivery_id": delivery["delivery_id"],
                "state": state,
                "now": current,
                "error_code": error_code,
                "reason": reason[:200],
            },
        )
        self._session.execute(
            text(
                """
                update messaging.wechat_subscription_grants
                set state = 'invalidated', invalidated_at = :now
                where grant_id = :grant_id and state in ('available', 'claimed')
                """
            ),
            {"grant_id": delivery["grant_id"], "now": current},
        )
        self._insert_delivery_event(
            delivery_id=str(delivery["delivery_id"]),
            user_id=str(delivery["user_id"]),
            event_type=event_type,
            attempt_no=attempt_no,
            evidence={"errorCode": error_code, "reason": reason[:120]},
            now=now,
        )

    def invalidate_grants(self, *, user_id: str, now: datetime) -> int:
        result = self._session.execute(
            text(
                """
                update messaging.wechat_subscription_grants
                set state = 'invalidated', invalidated_at = :now
                where user_id = cast(:user_id as uuid)
                  and state = 'available'
                """
            ),
            {"user_id": user_id, "now": _utc(now)},
        )
        return int(result.rowcount or 0)

    def cancel_outstanding(self, *, user_id: str, now: datetime, reason: str) -> int:
        rows = self._session.execute(
            text(
                """
                update messaging.coach_notification_deliveries
                set state = 'canceled', terminal_at = :now,
                    platform_error_message = :reason
                where user_id = cast(:user_id as uuid)
                  and state = 'queued'
                returning delivery_id::text, user_id::text, grant_id::text
                """
            ),
            {"user_id": user_id, "now": _utc(now), "reason": reason[:200]},
        ).mappings().all()
        for row in rows:
            self._session.execute(
                text(
                    """
                    update messaging.wechat_subscription_grants
                    set state = 'invalidated', invalidated_at = :now
                    where grant_id = cast(:grant_id as uuid) and state = 'claimed'
                    """
                ),
                {"grant_id": row["grant_id"], "now": _utc(now)},
            )
            self._insert_delivery_event(
                delivery_id=str(row["delivery_id"]),
                user_id=str(row["user_id"]),
                event_type="canceled",
                attempt_no=0,
                evidence={"reason": reason[:120]},
                now=now,
            )
        return len(rows)

    def wake_evaluation(self, *, user_id: str, now: datetime) -> None:
        self._session.execute(
            text(
                """
                insert into messaging.coach_evaluation_cursors (
                  user_id, next_evaluation_at, updated_at
                ) values (cast(:user_id as uuid), :now, :now)
                on conflict (user_id) do update set
                  next_evaluation_at = least(
                    messaging.coach_evaluation_cursors.next_evaluation_at,
                    excluded.next_evaluation_at
                  ), updated_at = excluded.updated_at
                """
            ),
            {"user_id": user_id, "now": _utc(now)},
        )

    def _current_preference(self, *, user_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                """
                select * from messaging.coach_preference_versions
                where user_id = cast(:user_id as uuid)
                order by version_no desc limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def _lock_active_user(self, *, user_id: str) -> None:
        row = self._session.execute(
            text(
                """
                select user_id from learner.users
                where user_id = cast(:user_id as uuid)
                  and is_active = true and account_status = 'active'
                for update
                """
            ),
            {"user_id": user_id},
        ).one_or_none()
        if row is None:
            raise CoachNotificationConflict("account is not active")

    def _insert_delivery_event(
        self,
        *,
        delivery_id: str,
        user_id: str,
        event_type: str,
        attempt_no: int,
        evidence: dict[str, Any],
        now: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into messaging.coach_notification_delivery_events (
                  delivery_event_id, delivery_id, user_id, event_type,
                  attempt_no, evidence, occurred_at
                ) values (
                  cast(:event_id as uuid), cast(:delivery_id as uuid),
                  cast(:user_id as uuid), :event_type, :attempt_no,
                  cast(:evidence as jsonb), :occurred_at
                )
                """
            ),
            {
                "event_id": str(uuid4()),
                "delivery_id": delivery_id,
                "user_id": user_id,
                "event_type": event_type,
                "attempt_no": attempt_no,
                "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                "occurred_at": _utc(now),
            },
        )


def _fingerprint(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def in_local_window(value: time, start: time, end: time) -> bool:
    if start < end:
        return start <= value < end
    return value >= start or value < end
