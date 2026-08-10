from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.data_rights.schemas import (
    AccountDeletionView,
    DataCategoryView,
    DataExportPayload,
    DataExportView,
    PrivacyOverview,
)
from app.platform.jobs import BackgroundJobRepository, job_registration


EXPORT_JOB_TYPE = "personal_data_export_v1"
DELETION_JOB_TYPE = "account_erasure_v1"
POLICY_VERSION = "personal-data-control@v1"
CONFIRMATION_PHRASE = "删除我的岸岸账户"

for _job_type in (EXPORT_JOB_TYPE, DELETION_JOB_TYPE):
    job_registration(
        job_type=_job_type,
        queue_name="platform.background_jobs",
    )


class DataRightsConflict(RuntimeError):
    pass


class DataRightsNotFound(LookupError):
    pass


@dataclass(frozen=True)
class DeletionWork:
    deletion_request_id: UUID
    user_id: UUID


class DataRightsRepository:
    def __init__(
        self,
        session: Session,
        *,
        jobs: BackgroundJobRepository | None = None,
    ) -> None:
        self._session = session
        self._jobs = jobs or BackgroundJobRepository()

    def overview(self, *, user_id: str, now: datetime) -> PrivacyOverview:
        policy = (
            self._session.execute(
                text(
                    "select title, content from privacy.policy_versions "
                    "where policy_version = :version and state = 'active'"
                ),
                {"version": POLICY_VERSION},
            )
            .mappings()
            .one()
        )
        counts = (
            self._session.execute(
                text(
                    """
                select
                  (select count(*) from learning.score_evidence where user_id = cast(:user_id as uuid)) as score_count,
                  (
                    select count(*)
                    from learning.xingce_question_favorite_states
                    where user_id = cast(:user_id as uuid)
                      and favorited
                  ) as favorite_count,
                  (select count(*) from learning.source_artifact_versions where owner_user_id = cast(:user_id as uuid)) as material_count,
                  (select count(*) from learning.report_projection_versions where user_id = cast(:user_id as uuid)) as report_count,
                  (select count(*) from ai_audit.model_call_ledger where user_id = cast(:user_id as uuid)) as ai_count,
                  (select count(*) from ai_audit.external_ai_operation_ledger where user_id = cast(:user_id as uuid)) as external_ai_count,
                  (select count(*) from commerce.wechat_orders where user_id = cast(:user_id as uuid)) as order_count,
                  (select count(*) from messaging.coach_notification_deliveries where user_id = cast(:user_id as uuid)) as notification_count,
                  (select count(*) from platform.companion_memory_items where user_id = cast(:user_id as uuid)) as companion_memory_count,
                  (select count(*) from platform.companion_short_term_memory_turns where user_id = cast(:user_id as uuid) and expires_at > :now) as companion_short_term_count,
                  (select count(*) from messaging.ilink_short_term_memory_turns where user_id = cast(:user_id as uuid) and expires_at > :now) as ilink_short_term_count,
                  (select count(*) from learning.task_edit_events where user_id = cast(:user_id as uuid)) as device_activity_count
                """
                ),
                {"user_id": user_id, "now": now},
            )
            .mappings()
            .one()
        )
        content = dict(policy["content"])
        return PrivacyOverview(
            policy_version=POLICY_VERSION,
            policy_title=str(policy["title"]),
            raw_material_retention=str(content["rawMaterialRetention"]),
            categories=[
                DataCategoryView(
                    category="identity_profile",
                    title="账户与备考档案",
                    item_count=1,
                    retention="账户存续期间",
                ),
                DataCategoryView(
                    category="learning_evidence",
                    title="成绩与学习证据",
                    item_count=(
                        int(counts["score_count"])
                        + int(counts["favorite_count"])
                    ),
                    retention="账户存续期间；注销后解除身份映射",
                ),
                DataCategoryView(
                    category="original_materials",
                    title="上传原件",
                    item_count=int(counts["material_count"]),
                    retention="账户存续期间；注销任务删除私有对象",
                ),
                DataCategoryView(
                    category="reports_and_ai",
                    title="报告与 AI 处理记录",
                    item_count=(
                        int(counts["report_count"])
                        + int(counts["ai_count"])
                        + int(counts["external_ai_count"])
                    ),
                    retention="账户存续期间；审计事实按合规周期留存",
                ),
                DataCategoryView(
                    category="communications",
                    title="岸岸记忆、AI 督学与消息",
                    item_count=(
                        int(counts["notification_count"])
                        + int(counts["companion_memory_count"])
                        + int(counts["companion_short_term_count"])
                        + int(counts["ilink_short_term_count"])
                    ),
                    retention="长期记忆由你主动管理；会话记忆 2 小时后自动过期；注销时删除",
                ),
                DataCategoryView(
                    category="device_activity",
                    title="作答设备与接管记录",
                    item_count=int(counts["device_activity_count"]),
                    retention="账户存续期间；注销时删除设备标识与接管事件",
                ),
                DataCategoryView(
                    category="commerce",
                    title="订单与权益",
                    item_count=int(counts["order_count"]),
                    retention="退款与法定账务周期内保留去标识化事实",
                ),
            ],
            account_deletion_effect=[
                "立即冻结登录并撤销全部设备会话",
                "删除微信/Apple 身份映射与私有上传对象",
                "删除岸岸长期记忆、会话记忆与 AI 督学消息记录",
                "删除作答设备标识、编辑租约与接管记录",
                "删除题目收藏状态与幂等命令记录",
                "清除显示名、邮箱和支付 OpenID",
                "仅保留无法用于恢复账户的审计与法定账务事实",
            ],
            server_time=now,
        )

    def create_export(
        self, *, user_id: str, command_key: str, now: datetime
    ) -> DataExportView:
        existing = self._export_by_command(user_id=user_id, command_key=command_key)
        if existing is not None:
            return existing
        export_id = uuid4()
        job = self._jobs.enqueue(
            self._session,
            job_key=f"privacy-export:{user_id}:{command_key}",
            job_type=EXPORT_JOB_TYPE,
            input_version="personal-data-export@v1",
            payload={"exportRequestId": str(export_id), "userId": user_id},
            max_attempts=3,
            run_after=now,
        )
        row = (
            self._session.execute(
                text(
                    """
                insert into privacy.data_export_requests (
                  export_request_id, command_key, user_id, policy_version,
                  state, job_id, requested_at
                ) values (
                  cast(:export_id as uuid), :command_key, cast(:user_id as uuid),
                  :policy_version, 'queued', cast(:job_id as uuid), :now
                ) returning *
                """
                ),
                {
                    "export_id": str(export_id),
                    "command_key": command_key,
                    "user_id": user_id,
                    "policy_version": POLICY_VERSION,
                    "job_id": str(job.job_id),
                    "now": now,
                },
            )
            .mappings()
            .one()
        )
        self._session.commit()
        return _export_view(row)

    def export_by_id(
        self, *, user_id: str, export_id: UUID, now: datetime
    ) -> DataExportView:
        self._session.execute(
            text(
                """
                update privacy.data_export_requests
                set state = 'expired'
                where export_request_id = cast(:export_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and state = 'completed' and expires_at <= :now
                """
            ),
            {"export_id": str(export_id), "user_id": user_id, "now": now},
        )
        self._session.execute(
            text(
                """
                delete from privacy.data_export_payloads payload
                using privacy.data_export_requests request
                where request.export_request_id = payload.export_request_id
                  and request.export_request_id = cast(:export_id as uuid)
                  and request.user_id = cast(:user_id as uuid)
                  and request.state = 'expired'
                """
            ),
            {"export_id": str(export_id), "user_id": user_id},
        )
        row = (
            self._session.execute(
                text(
                    "select * from privacy.data_export_requests "
                    "where export_request_id = cast(:export_id as uuid) "
                    "and user_id = cast(:user_id as uuid)"
                ),
                {"export_id": str(export_id), "user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        self._session.commit()
        if row is None:
            raise DataRightsNotFound("export")
        return _export_view(row)

    def export_payload(
        self, *, user_id: str, export_id: UUID, now: datetime
    ) -> DataExportPayload:
        view = self.export_by_id(user_id=user_id, export_id=export_id, now=now)
        if (
            view.state != "completed"
            or view.expires_at is None
            or view.expires_at <= now
        ):
            raise DataRightsConflict("export payload is not available")
        row = (
            self._session.execute(
                text(
                    """
                select payload, created_at from privacy.data_export_payloads
                where export_request_id = cast(:export_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
                ),
                {"export_id": str(export_id), "user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or view.payload_sha256 is None:
            raise DataRightsConflict("completed export payload is missing")
        return DataExportPayload(
            export_request_id=export_id,
            policy_version=POLICY_VERSION,
            generated_at=row["created_at"],
            payload_sha256=view.payload_sha256,
            payload=dict(row["payload"]),
        )

    def create_deletion(
        self,
        *,
        user_id: str,
        command_key: str,
        confirmation_phrase: str,
        now: datetime,
    ) -> AccountDeletionView:
        if confirmation_phrase != CONFIRMATION_PHRASE:
            raise DataRightsConflict(
                "account deletion confirmation phrase does not match"
            )
        existing = self._deletion_by_command(user_id=user_id, command_key=command_key)
        if existing is not None:
            return existing
        pending_financial = self._session.execute(
            text(
                """
                select exists (
                  select 1 from commerce.wechat_orders
                  where user_id = cast(:user_id as uuid)
                    and state in ('creating', 'awaiting_payment', 'payment_uncertain', 'refund_processing')
                )
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        if pending_financial:
            raise DataRightsConflict(
                "pending payment or refund must settle before deletion"
            )
        deletion_id = uuid4()
        job = self._jobs.enqueue(
            self._session,
            job_key=f"privacy-delete:{user_id}:{command_key}",
            job_type=DELETION_JOB_TYPE,
            input_version="account-erasure@v1",
            payload={"deletionRequestId": str(deletion_id), "userId": user_id},
            max_attempts=5,
            run_after=now,
        )
        phrase_hash = sha256(confirmation_phrase.encode("utf-8")).hexdigest()
        row = (
            self._session.execute(
                text(
                    """
                insert into privacy.account_deletion_requests (
                  deletion_request_id, command_key, user_id, policy_version,
                  confirmation_phrase_hash, state, job_id, requested_at
                ) values (
                  cast(:deletion_id as uuid), :command_key, cast(:user_id as uuid),
                  :policy_version, :phrase_hash, 'queued', cast(:job_id as uuid), :now
                ) returning *
                """
                ),
                {
                    "deletion_id": str(deletion_id),
                    "command_key": command_key,
                    "user_id": user_id,
                    "policy_version": POLICY_VERSION,
                    "phrase_hash": phrase_hash,
                    "job_id": str(job.job_id),
                    "now": now,
                },
            )
            .mappings()
            .one()
        )
        self._session.execute(
            text(
                """
                update learner.users set account_status = 'deletion_pending',
                  account_status_changed_at = :now, updated_at = :now
                where user_id = cast(:user_id as uuid) and account_status = 'active'
                """
            ),
            {"user_id": user_id, "now": now},
        )
        self._session.execute(
            text(
                """
                update learner.app_sessions set revoked_at = :now, revoked_reason = 'security'
                where user_id = cast(:user_id as uuid) and revoked_at is null
                """
            ),
            {"user_id": user_id, "now": now},
        )
        self._append_deletion_event(
            deletion_id=deletion_id,
            user_id=user_id,
            phase="requested",
            evidence={"policyVersion": POLICY_VERSION, "sessionsRevoked": True},
            now=now,
        )
        self._session.commit()
        return _deletion_view(row)

    def deletion_by_id(self, *, user_id: str, deletion_id: UUID) -> AccountDeletionView:
        row = (
            self._session.execute(
                text(
                    "select * from privacy.account_deletion_requests "
                    "where deletion_request_id = cast(:deletion_id as uuid) "
                    "and user_id = cast(:user_id as uuid)"
                ),
                {"deletion_id": str(deletion_id), "user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DataRightsNotFound("deletion")
        return _deletion_view(row)

    def build_export_payload(self, *, user_id: str, now: datetime) -> dict[str, object]:
        return {
            "contractVersion": "personal-data-export@v1",
            "policyVersion": POLICY_VERSION,
            "generatedAt": now.isoformat(),
            "subject": self._one_json(
                "select user_id, display_name, email, account_status, created_at from learner.users where user_id = cast(:user_id as uuid)",
                user_id,
            ),
            "profile": self._one_json(
                "select * from learner.profiles where user_id = cast(:user_id as uuid)",
                user_id,
            ),
            "preparationExperience": self._many_json(
                "select * from learner.preparation_experience_versions "
                "where user_id = cast(:user_id as uuid) order by version_no",
                user_id,
            ),
            "scoreEvidence": self._many_json(
                "select * from learning.score_evidence where user_id = cast(:user_id as uuid) order by occurred_at",
                user_id,
            ),
            "questionFavorites": self._many_json(
                "select question_id, saved_content_version_id, "
                "source_session_id, favorited, revision, favorited_at, "
                "created_at, updated_at "
                "from learning.xingce_question_favorite_states "
                "where user_id = cast(:user_id as uuid) "
                "order by updated_at, question_id",
                user_id,
            ),
            "questionFavoriteCommands": self._many_json(
                "select command_id, question_id, request_fingerprint, "
                "response_payload, created_at "
                "from learning.xingce_question_favorite_commands "
                "where user_id = cast(:user_id as uuid) "
                "order by created_at, command_id",
                user_id,
            ),
            "qualificationFacts": self._many_json(
                "select * from learner.qualification_facts where user_id = cast(:user_id as uuid) order by recorded_at",
                user_id,
            ),
            "positionPreferences": self._many_json(
                "select * from learner.position_preference_versions "
                "where user_id = cast(:user_id as uuid) order by version_no",
                user_id,
            ),
            "abilityBaselineDeclarations": self._many_json(
                "select * from learner.ability_baseline_declaration_versions "
                "where user_id = cast(:user_id as uuid) order by version_no",
                user_id,
            ),
            "sourceMaterials": self._many_json(
                "select artifact_version_id, source_kind, state, mime_type, content_sha256, byte_size, page_count, created_at from learning.source_artifact_versions where owner_user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
            "recognitionRevisions": self._many_json(
                "select * from learning.recognition_revisions where owner_user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
            "contentMatches": self._many_json(
                "select * from learning.content_match_versions where owner_user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
            "privateKnowledgeSources": self._many_json(
                "select source_version_id, source_id, version_no, title, document_kind, "
                "artifact_version_id, recognition_revision_id, canonical_markdown, "
                "content_sha256, supersedes_source_version_id, created_at "
                "from learning.private_knowledge_source_versions "
                "where owner_user_id = cast(:user_id as uuid) order by source_id, version_no",
                user_id,
            ),
            "mathInkRequests": self._many_json(
                "select request_id, session_id, question_id, input_fingerprint, ink_payload, "
                "provider_release, state, output_fingerprint, failure_code, latency_ms, "
                "created_at, completed_at from learning.math_ink_commands "
                "where owner_user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
            "mathFormulaVersions": self._many_json(
                "select formula_version_id, formula_id, request_id, session_id, question_id, "
                "version_no, state, latex, source, supersedes_formula_version_id, "
                "created_at, confirmed_at from learning.math_formula_versions "
                "where owner_user_id = cast(:user_id as uuid) order by formula_id, version_no",
                user_id,
            ),
            "essayAnswers": self._many_json(
                "select * from learning.essay_answer_versions where user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
            "essayReviews": self._many_json(
                "select * from learning.essay_review_versions where user_id = cast(:user_id as uuid) order by generated_at",
                user_id,
            ),
            "dailyPlans": self._many_json(
                "select * from learning.daily_plan_versions where user_id = cast(:user_id as uuid) order by local_date, version_no",
                user_id,
            ),
            "taskExecutions": self._many_json(
                "select * from learning.task_execution_versions where user_id = cast(:user_id as uuid) order by local_date, recorded_at",
                user_id,
            ),
            "reports": self._many_json(
                "select * from learning.report_projection_versions where user_id = cast(:user_id as uuid) order by generated_at",
                user_id,
            ),
            "aiProcessingLedger": self._many_json(
                "select call_id, feature, operation, provider, model, prompt_version, schema_version, status, input_tokens, cached_input_tokens, output_tokens, started_at, completed_at from ai_audit.model_call_ledger where user_id = cast(:user_id as uuid) order by started_at",
                user_id,
            ),
            "externalProcessingReceipts": self._many_json(
                """
                select receipt.receipt_id, receipt.scenario_key, receipt.purpose,
                       receipt.data_categories, receipt.declared_provider_release,
                       receipt.authorization_state, receipt.input_fingerprint,
                       receipt.dispatched_at, release.display_name,
                       release.operator_name, release.real_user_status,
                       release.model_training_use, release.retention_policy,
                       release.retention_seconds, release.deletion_support,
                       release.evidence
                from ai_audit.external_processing_receipts receipt
                left join ai_audit.processor_release_records release
                  on release.processor_release_record_id =
                     receipt.processor_release_record_id
                where receipt.user_id = cast(:user_id as uuid)
                order by receipt.dispatched_at
                """,
                user_id,
            ),
            "companionMemorySettings": self._many_json(
                "select long_term_enabled, updated_at "
                "from platform.companion_memory_settings "
                "where user_id = cast(:user_id as uuid)",
                user_id,
            ),
            "companionMemories": self._many_json(
                "select memory_id, category, content, source, revision, created_at, updated_at "
                "from platform.companion_memory_items "
                "where user_id = cast(:user_id as uuid) order by updated_at, memory_id",
                user_id,
            ),
            "companionShortTermConversation": self._many_json(
                "select turn_id, user_text, assistant_text, context_scope_fingerprint, "
                "created_at, expires_at "
                "from platform.companion_short_term_memory_turns "
                "where user_id = cast(:user_id as uuid) order by created_at, turn_id",
                user_id,
            ),
            "ilinkShortTermConversation": self._many_json(
                "select turn_id, user_text, assistant_text, created_at, expires_at "
                "from messaging.ilink_short_term_memory_turns "
                "where user_id = cast(:user_id as uuid) order by created_at, turn_id",
                user_id,
            ),
            "coachNotificationPreferences": self._many_json(
                "select * from messaging.coach_preference_versions "
                "where user_id = cast(:user_id as uuid) order by version_no",
                user_id,
            ),
            "wechatSubscriptionEvents": self._many_json(
                "select * from messaging.wechat_subscription_events "
                "where user_id = cast(:user_id as uuid) order by occurred_at",
                user_id,
            ),
            "wechatSubscriptionGrants": self._many_json(
                "select * from messaging.wechat_subscription_grants "
                "where user_id = cast(:user_id as uuid) order by accepted_at",
                user_id,
            ),
            "coachNotificationDecisions": self._many_json(
                "select * from messaging.coach_notification_decisions "
                "where user_id = cast(:user_id as uuid) order by decided_at",
                user_id,
            ),
            "coachNotificationDeliveries": self._many_json(
                "select delivery.*, opened.opened_at "
                "from messaging.coach_notification_deliveries delivery "
                "left join messaging.coach_notification_open_events opened "
                "on opened.delivery_id = delivery.delivery_id "
                "where delivery.user_id = cast(:user_id as uuid) "
                "order by delivery.created_at",
                user_id,
            ),
            "coachNotificationDeliveryEvents": self._many_json(
                "select * from messaging.coach_notification_delivery_events "
                "where user_id = cast(:user_id as uuid) order by occurred_at",
                user_id,
            ),
            "dailyOpeningPreferences": self._many_json(
                "select preference_version_id, version_no, enabled, weather_enabled, "
                "coffee_prompt_enabled, coffee_start, coffee_end, timezone, source_type, "
                "supersedes_preference_version_id, created_at "
                "from messaging.daily_opening_preference_versions "
                "where user_id = cast(:user_id as uuid) order by version_no",
                user_id,
            ),
            "dailyOpeningLocations": self._many_json(
                "select location_version_id, version_no, label, latitude, longitude, "
                "source_type, supersedes_location_version_id, created_at "
                "from messaging.daily_opening_location_versions "
                "where user_id = cast(:user_id as uuid) order by version_no",
                user_id,
            ),
            "dailyOpeningImpressions": self._many_json(
                "select impression_id, local_date, preference_version_id, "
                "location_version_id, plan_version_id, weather_fingerprint, "
                "coffee_state, displayed_at from messaging.daily_opening_impressions "
                "where user_id = cast(:user_id as uuid) order by local_date",
                user_id,
            ),
            "dailyOpeningCoffeeDecisions": self._many_json(
                "select decision_id, local_date, decision, decided_at "
                "from messaging.daily_opening_coffee_decisions "
                "where user_id = cast(:user_id as uuid) order by local_date",
                user_id,
            ),
            "taskEditLeases": self._many_json(
                "select resource_type, resource_id, lease_id, generation, device_id, "
                "device_label, reconciliation_state, local_base_revision, server_revision, "
                "acquired_at, last_seen_at, taken_over_at "
                "from learning.task_edit_leases where user_id = cast(:user_id as uuid) "
                "order by last_seen_at",
                user_id,
            ),
            "taskEditEvents": self._many_json(
                "select event_id, resource_type, resource_id, lease_id, generation, "
                "device_id, device_label, event_type, resource_revision, "
                "local_base_revision, resolution, metadata, created_at "
                "from learning.task_edit_events where user_id = cast(:user_id as uuid) "
                "order by created_at, event_id",
                user_id,
            ),
            "commerce": self._many_json(
                "select order_id, out_trade_no, currency, amount_minor, refunded_minor, state, paid_at, created_at from commerce.wechat_orders where user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
            "entitlementGrants": self._many_json(
                "select * from commerce.entitlement_grants where user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
            "retakePrograms": self._many_json(
                "select * from learning.retake_program_versions where user_id = cast(:user_id as uuid) order by created_at",
                user_id,
            ),
        }

    def complete_export(
        self,
        *,
        export_id: UUID,
        user_id: str,
        payload: dict[str, object],
        now: datetime,
        expires_after: timedelta,
    ) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        digest = sha256(encoded).hexdigest()
        self._session.execute(
            text(
                """
                insert into privacy.data_export_payloads (
                  export_request_id, user_id, payload, created_at
                ) values (
                  cast(:export_id as uuid), cast(:user_id as uuid),
                  cast(:payload as jsonb), :now
                ) on conflict (export_request_id) do nothing
                """
            ),
            {
                "export_id": str(export_id),
                "user_id": user_id,
                "payload": encoded.decode("utf-8"),
                "now": now,
            },
        )
        self._session.execute(
            text(
                """
                update privacy.data_export_requests set state = 'completed',
                  payload_sha256 = :digest, byte_size = :byte_size,
                  completed_at = :now, expires_at = :expires_at, failure_code = null
                where export_request_id = cast(:export_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and state in ('queued', 'running')
                """
            ),
            {
                "export_id": str(export_id),
                "user_id": user_id,
                "digest": digest,
                "byte_size": len(encoded),
                "now": now,
                "expires_at": now + expires_after,
            },
        )

    def mark_export_running(self, *, export_id: UUID, user_id: str) -> None:
        self._session.execute(
            text(
                "update privacy.data_export_requests set state = 'running' "
                "where export_request_id = cast(:export_id as uuid) "
                "and user_id = cast(:user_id as uuid) and state = 'queued'"
            ),
            {"export_id": str(export_id), "user_id": user_id},
        )

    def deletion_object_refs(self, *, user_id: str) -> list[str]:
        values = self._session.execute(
            text(
                """
                select object_ref from learning.source_artifact_versions
                where owner_user_id = cast(:user_id as uuid)
                union
                select object_ref from learning.upload_sessions
                where owner_user_id = cast(:user_id as uuid)
                union
                select object_ref from learning.learning_media_upload_sessions
                where user_id = cast(:user_id as uuid)
                union
                select object_ref from learning.learning_media_artifact_versions
                where user_id = cast(:user_id as uuid) and object_ref is not null
                """
            ),
            {"user_id": user_id},
        ).scalars()
        return sorted({str(value) for value in values if value})

    def expire_export_payloads(self, *, now: datetime) -> int:
        deleted = self._session.execute(
            text(
                """
                delete from privacy.data_export_payloads payload
                using privacy.data_export_requests request
                where request.export_request_id = payload.export_request_id
                  and request.state in ('completed', 'expired')
                  and request.expires_at <= :now
                """
            ),
            {"now": now},
        ).rowcount
        self._session.execute(
            text(
                """
                update privacy.data_export_requests set state = 'expired'
                where state = 'completed' and expires_at <= :now
                """
            ),
            {"now": now},
        )
        self._session.commit()
        return int(deleted or 0)

    def complete_deletion(
        self,
        *,
        deletion_id: UUID,
        user_id: str,
        deleted_object_count: int,
        now: datetime,
    ) -> dict[str, object]:
        token = sha256(f"deleted:{user_id}".encode("utf-8")).hexdigest()
        self._append_deletion_event(
            deletion_id=deletion_id,
            user_id=user_id,
            phase="objects_deleted",
            evidence={"deletedObjectCount": deleted_object_count},
            now=now,
        )
        erased_rows = self._erase_personal_content(
            deletion_id=deletion_id,
            user_id=user_id,
            now=now,
        )
        external_processing = self._record_external_processing_deletion(
            deletion_id=deletion_id,
            user_id=user_id,
            now=now,
        )
        self._session.execute(
            text(
                "delete from privacy.data_export_payloads where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        )
        self._session.execute(
            text(
                "delete from learner.wechat_identities where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        )
        self._session.execute(
            text(
                "delete from learner.apple_identities where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        )
        self._session.execute(
            text(
                "delete from learner.app_sessions where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        )
        self._session.execute(
            text(
                """
                update commerce.wechat_orders
                set payer_open_id = :payer, updated_at = :now
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id, "payer": f"deleted_{token[:32]}", "now": now},
        )
        self._session.execute(
            text(
                """
                update learner.users set display_name = null, email = null,
                  is_active = false, account_status = 'disabled',
                  account_status_changed_at = :now, updated_at = :now
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id, "now": now},
        )
        manifest: dict[str, object] = {
            "contractVersion": "account-erasure-result@v1",
            "identityMappingsDeleted": True,
            "sessionsDeleted": True,
            "privateObjectsDeleted": deleted_object_count,
            "profileIdentifiersCleared": True,
            "paymentOpenIdCleared": True,
            "personalContentErased": erased_rows,
            "externalProcessing": external_processing,
            "accountRecoverable": False,
            "retainedFacts": [
                "statutory_financial_ledger",
                "security_audit_without_user_content",
                "content_erased_learning_integrity",
            ],
        }
        self._append_deletion_event(
            deletion_id=deletion_id,
            user_id=user_id,
            phase="identity_mapping_deleted",
            evidence={"accountRecoverable": False},
            now=now,
        )
        self._session.execute(
            text(
                """
                update privacy.account_deletion_requests set state = 'completed',
                  deletion_manifest = cast(:manifest as jsonb), completed_at = :now,
                  failure_code = null
                where deletion_request_id = cast(:deletion_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {
                "deletion_id": str(deletion_id),
                "user_id": user_id,
                "manifest": json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                "now": now,
            },
        )
        self._append_deletion_event(
            deletion_id=deletion_id,
            user_id=user_id,
            phase="completed",
            evidence=manifest,
            now=now,
        )
        return manifest

    def _record_external_processing_deletion(
        self,
        *,
        deletion_id: UUID,
        user_id: str,
        now: datetime,
    ) -> dict[str, int]:
        self._session.execute(
            text(
                """
                insert into privacy.external_processing_deletion_events (
                  deletion_event_id, deletion_request_id, receipt_id,
                  processor_release_record_id, disposition, evidence, recorded_at
                )
                select
                  gen_random_uuid(), cast(:deletion_id as uuid), receipt.receipt_id,
                  receipt.processor_release_record_id,
                  case
                    when receipt.authorization_state = 'real_user_approved'
                      then 'not_required_zero_retention'
                    else 'not_dispatched_local_unreleased'
                  end,
                  jsonb_build_object(
                    'authorizationState', receipt.authorization_state,
                    'declaredProviderRelease', receipt.declared_provider_release,
                    'retentionPolicy', coalesce(release.retention_policy, 'local_unreleased'),
                    'receiptId', receipt.receipt_id::text
                  ),
                  :now
                from ai_audit.external_processing_receipts receipt
                left join ai_audit.processor_release_records release
                  on release.processor_release_record_id =
                     receipt.processor_release_record_id
                where receipt.user_id = cast(:user_id as uuid)
                on conflict (deletion_request_id, receipt_id) do nothing
                """
            ),
            {
                "deletion_id": str(deletion_id),
                "user_id": user_id,
                "now": now,
            },
        )
        counts = self._session.execute(
            text(
                """
                select disposition, count(*) as event_count
                from privacy.external_processing_deletion_events
                where deletion_request_id = cast(:deletion_id as uuid)
                group by disposition
                """
            ),
            {"deletion_id": str(deletion_id)},
        ).mappings()
        by_disposition = {
            str(row["disposition"]): int(row["event_count"]) for row in counts
        }
        return {
            "zeroRetentionReceipts": by_disposition.get(
                "not_required_zero_retention", 0
            ),
            "localUnreleasedReceipts": by_disposition.get(
                "not_dispatched_local_unreleased", 0
            ),
        }

    def _erase_personal_content(
        self,
        *,
        deletion_id: UUID,
        user_id: str,
        now: datetime,
    ) -> dict[str, int]:
        self._session.execute(
            text("select set_config('app.privacy_erasure_user_id', :user_id, true)"),
            {"user_id": user_id},
        )
        sending_count = int(
            self._session.execute(
                text(
                    "select count(*) from messaging.coach_notification_deliveries "
                    "where user_id = cast(:user_id as uuid) and state = 'sending'"
                ),
                {"user_id": user_id},
            ).scalar_one()
        )
        if sending_count:
            raise DataRightsConflict(
                "account erasure waits for in-flight coach notifications to settle"
            )
        statements: tuple[tuple[str, str], ...] = (
            (
                "companion_memory_items",
                "delete from platform.companion_memory_items "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "companion_memory_settings",
                "delete from platform.companion_memory_settings "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "companion_short_term_memory",
                "delete from platform.companion_short_term_memory_turns "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "ilink_short_term_memory",
                "delete from messaging.ilink_short_term_memory_turns "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "luckin_order_confirmations",
                "delete from messaging.luckin_order_confirmations "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "daily_opening_impressions",
                "delete from messaging.daily_opening_impressions "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "daily_opening_coffee_decisions",
                "delete from messaging.daily_opening_coffee_decisions "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "luckin_connection_versions",
                "delete from messaging.luckin_connection_versions "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "daily_opening_location_versions",
                "delete from messaging.daily_opening_location_versions "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "daily_opening_preference_versions",
                "delete from messaging.daily_opening_preference_versions "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "task_edit_events",
                "delete from learning.task_edit_events "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "task_edit_leases",
                "delete from learning.task_edit_leases "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "coach_notification_jobs",
                """
                delete from platform.background_jobs job
                using messaging.coach_notification_deliveries delivery
                where job.job_type = 'coach_notification_send_v1'
                  and job.payload->>'deliveryId' = delivery.delivery_id::text
                  and delivery.user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "coach_notification_open_events",
                "delete from messaging.coach_notification_open_events "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "coach_notification_delivery_events",
                "delete from messaging.coach_notification_delivery_events "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "coach_notification_deliveries",
                "delete from messaging.coach_notification_deliveries "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "coach_notification_decisions",
                "delete from messaging.coach_notification_decisions "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "wechat_subscription_grants",
                "delete from messaging.wechat_subscription_grants "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "wechat_subscription_events",
                "delete from messaging.wechat_subscription_events "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "coach_evaluation_cursors",
                "delete from messaging.coach_evaluation_cursors "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "coach_preferences",
                "delete from messaging.coach_preference_versions "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "profiles",
                "delete from learner.profiles where user_id = cast(:user_id as uuid)",
            ),
            (
                "preparation_experience",
                """
                update learner.preparation_experience_versions
                set declaration_status = 'legacy_not_collected',
                    first_systematic = false,
                    external_learning = false,
                    prior_guokao_exam = false,
                    retake = false,
                    prior_exam_years = '[]'::jsonb,
                    current_status = 'unspecified',
                    input_fingerprint = repeat('0', 64),
                    erased_at = :now
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "qualification_profiles",
                """
                update learner.qualification_profile_changes
                set command_payload = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "qualification_facts",
                """
                update learner.qualification_facts
                set value = jsonb_build_object('erased', true)
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "qualification_confirmations",
                """
                update learner.qualification_requirement_confirmations
                set statement = null
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "position_preferences",
                """
                update learner.position_preference_versions
                set preferred_regions = array['已删除']::text[],
                    accepts_relocation = false,
                    accepted_position_levels =
                      array['county_or_below']::text[],
                    input_fingerprint = repeat('0', 64),
                    erased_at = :now
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "score_evidence",
                """
                update learning.score_evidence
                set xingce_score = case when xingce_score is null then null else 0 end,
                    shenlun_score = case when shenlun_score is null then null else 0 end,
                    total_score = case when total_score is null then null else 0 end,
                    command_payload = '{}'::jsonb,
                    erased_at = :now
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "ability_baseline_declarations",
                """
                update learner.ability_baseline_declaration_versions
                set mode = 'unknown',
                    score_evidence_id = null,
                    input_fingerprint = repeat('0', 64),
                    erased_at = :now
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "ability_profiles",
                """
                update learning.ability_estimate_versions
                set xingce_lower = case when xingce_lower is null then null else 0 end,
                    xingce_recommended = case when xingce_recommended is null then null else 0 end,
                    xingce_upper = case when xingce_upper is null then null else 0 end,
                    shenlun_lower = case when shenlun_lower is null then null else 0 end,
                    shenlun_recommended = case when shenlun_recommended is null then null else 0 end,
                    shenlun_upper = case when shenlun_upper is null then null else 0 end,
                    total_lower = case when total_lower is null then null else 0 end,
                    total_recommended = case when total_recommended is null then null else 0 end,
                    total_upper = case when total_upper is null then null else 0 end,
                    source_evidence_ids = '[]'::jsonb,
                    evidence_summary = jsonb_build_object('erased', true)
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "learning_brain_profiles",
                """
                update learning.learner_brain_profile_versions
                set ability_estimate_version_id = null,
                    ability_snapshot = '{}'::jsonb,
                    evidence_summary = '{}'::jsonb,
                    candidate_summary = '{}'::jsonb,
                    module_abilities = '[]'::jsonb,
                    error_patterns = '[]'::jsonb,
                    decision_summary = '{}'::jsonb,
                    conflicts = '[]'::jsonb,
                    source_refs = '[]'::jsonb,
                    input_versions = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "report_projections",
                """
                update learning.report_projection_versions
                set payload = '{}'::jsonb,
                    evidence_refs = '[]'::jsonb,
                    next_plan_evidence_refs = '[]'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "external_mock_reports",
                """
                update learning.external_mock_report_versions
                set platform_name = '[deleted]', exam_name = '[deleted]',
                    total_score = case when total_score is null then null else 0 end,
                    xingce_score = case when xingce_score is null then null else 0 end,
                    shenlun_score = case when shenlun_score is null then null else 0 end,
                    module_scores = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "external_mock_commands",
                """
                update learning.external_mock_report_commands
                set response_payload = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "private_knowledge_sources",
                """
                delete from learning.private_knowledge_source_versions
                where owner_user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "math_ink_commands",
                """
                update learning.math_ink_commands
                set ink_payload = jsonb_build_object('strokes', '[]'::jsonb)
                where owner_user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "math_formula_versions",
                """
                update learning.math_formula_versions
                set latex = '[deleted]'
                where owner_user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "source_artifacts",
                """
                update learning.source_artifact_versions
                set state = 'deleted',
                    object_ref = 'deleted/' || artifact_version_id::text,
                    contract_payload = '{}'::jsonb
                where owner_user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "recognition_payloads",
                """
                update learning.recognition_revisions
                set candidate_payload = '{}'::jsonb
                where owner_user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "content_matches",
                """
                update learning.content_match_versions
                set candidates = '[]'::jsonb, contract_payload = '{}'::jsonb
                where owner_user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "learning_media_uploads",
                """
                update learning.learning_media_upload_sessions
                set original_filename = '[deleted]',
                    object_ref = 'tos://deleted/private/learning-media/' ||
                      upload_session_id::text,
                    scanner_receipt = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "learning_media_artifacts",
                """
                update learning.learning_media_artifact_versions
                set object_ref = case when object_ref is null then null
                      else 'tos://deleted/private/learning-media/' || artifact_version_id::text end,
                    scanner_receipt = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "xingce_question_favorite_commands",
                "delete from learning.xingce_question_favorite_commands "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "xingce_question_favorite_states",
                "delete from learning.xingce_question_favorite_states "
                "where user_id = cast(:user_id as uuid)",
            ),
            (
                "xingce_sessions",
                """
                update learning.xingce_task_sessions
                set safe_bundle = '{}'::jsonb,
                    scoring_bundle = '{}'::jsonb,
                    checkpoint = jsonb_build_object('answers', '[]'::jsonb)
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "xingce_commands",
                """
                update learning.xingce_commands set response_payload = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "xingce_answers",
                """
                update learning.xingce_answer_versions
                set payload = jsonb_build_object(
                      'questionId', question_id, 'erased', true
                    ),
                    payload_sha256 = repeat('0', 64)
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "xingce_reflections",
                """
                update learning.xingce_reflection_versions
                set payload = jsonb_build_object(
                      'rawText', '[deleted]', 'tags', jsonb_build_array('erased')
                    ),
                    payload_sha256 = repeat('0', 64)
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "essay_answers",
                """
                update learning.essay_answer_versions
                set answer_text = '[deleted]', answer_sha256 = repeat('0', 64),
                    thinking_payload = null, thinking_sha256 = null
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "essay_reviews",
                """
                update learning.essay_review_versions
                set status = 'failed',
                    readiness = jsonb_build_object('erased', true),
                    score_lower = null, score_recommended = null,
                    score_upper = null, score_maximum = null,
                    score_confidence = null, score_confidence_reasons = null,
                    grounding_contract_version = null, grounding_report = null,
                    dimensions = '[]'::jsonb, point_coverage = '[]'::jsonb,
                    claim_evidence = '[]'::jsonb, findings = '[]'::jsonb,
                    rewrite_plan = null, output_fingerprint = null
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "essay_rewrites",
                """
                update learning.essay_rewrite_versions
                set analysis_payload = '{}'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "essay_learning_artifacts",
                """
                update learning.essay_learning_artifact_versions
                set payload = jsonb_build_object(
                      'schemaType', artifact_type, 'erased', true
                    ),
                    payload_sha256 = repeat('0', 64)
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "companion_payloads",
                """
                update platform.companion_paid_commands
                set execution_payload = '{}'::jsonb,
                    response_payload = case when response_payload is null then null
                      else '{}'::jsonb end
                where user_id = cast(:user_id as uuid)
                """,
            ),
            (
                "ai_interaction_summaries",
                "delete from ai_audit.interaction_logs where learner_key = :user_id",
            ),
            (
                "retake_profiles",
                """
                update learning.retake_program_versions
                set loss_structure = jsonb_build_object('erased', true),
                    evidence_refs = '["erased"]'::jsonb
                where user_id = cast(:user_id as uuid)
                """,
            ),
        )
        counts: dict[str, int] = {}
        for key, statement in statements:
            result = self._session.execute(
                text(statement), {"user_id": user_id, "now": now}
            )
            counts[key] = max(int(result.rowcount or 0), 0)
        self._append_deletion_event(
            deletion_id=deletion_id,
            user_id=user_id,
            phase="personal_rows_erased",
            evidence={"erasedRows": counts},
            now=now,
        )
        return counts

    def mark_deletion_running(self, *, deletion_id: UUID, user_id: str) -> None:
        self._session.execute(
            text(
                "update privacy.account_deletion_requests set state = 'running' "
                "where deletion_request_id = cast(:deletion_id as uuid) "
                "and user_id = cast(:user_id as uuid) and state = 'queued'"
            ),
            {"deletion_id": str(deletion_id), "user_id": user_id},
        )

    def mark_export_failed(
        self, *, export_id: UUID, user_id: str, failure_code: str
    ) -> None:
        self._session.execute(
            text(
                """
                update privacy.data_export_requests
                set state = 'failed', failure_code = :failure_code
                where export_request_id = cast(:export_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and state in ('queued', 'running')
                """
            ),
            {
                "export_id": str(export_id),
                "user_id": user_id,
                "failure_code": failure_code,
            },
        )

    def mark_deletion_failed(
        self,
        *,
        deletion_id: UUID,
        user_id: str,
        failure_code: str,
        now: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                update privacy.account_deletion_requests
                set state = 'failed', failure_code = :failure_code
                where deletion_request_id = cast(:deletion_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and state in ('queued', 'running')
                """
            ),
            {
                "deletion_id": str(deletion_id),
                "user_id": user_id,
                "failure_code": failure_code,
            },
        )
        self._append_deletion_event(
            deletion_id=deletion_id,
            user_id=user_id,
            phase="failed",
            evidence={"failureCode": failure_code},
            now=now,
        )

    def _export_by_command(
        self, *, user_id: str, command_key: str
    ) -> DataExportView | None:
        row = (
            self._session.execute(
                text(
                    "select * from privacy.data_export_requests "
                    "where user_id = cast(:user_id as uuid) and command_key = :command_key"
                ),
                {"user_id": user_id, "command_key": command_key},
            )
            .mappings()
            .one_or_none()
        )
        return _export_view(row) if row is not None else None

    def _deletion_by_command(
        self, *, user_id: str, command_key: str
    ) -> AccountDeletionView | None:
        row = (
            self._session.execute(
                text(
                    "select * from privacy.account_deletion_requests "
                    "where user_id = cast(:user_id as uuid) and command_key = :command_key"
                ),
                {"user_id": user_id, "command_key": command_key},
            )
            .mappings()
            .one_or_none()
        )
        return _deletion_view(row) if row is not None else None

    def _append_deletion_event(
        self,
        *,
        deletion_id: UUID,
        user_id: str,
        phase: str,
        evidence: dict[str, object],
        now: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into privacy.deletion_events (
                  event_id, deletion_request_id, user_id, phase, evidence, created_at
                ) values (
                  gen_random_uuid(), cast(:deletion_id as uuid), cast(:user_id as uuid),
                  :phase, cast(:evidence as jsonb), :now
                )
                """
            ),
            {
                "deletion_id": str(deletion_id),
                "user_id": user_id,
                "phase": phase,
                "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                "now": now,
            },
        )

    def _one_json(self, query: str, user_id: str) -> dict[str, object] | None:
        row = (
            self._session.execute(text(query), {"user_id": user_id})
            .mappings()
            .one_or_none()
        )
        return _jsonable(dict(row)) if row is not None else None

    def _many_json(self, query: str, user_id: str) -> list[dict[str, object]]:
        rows = self._session.execute(text(query), {"user_id": user_id}).mappings().all()
        return [_jsonable(dict(row)) for row in rows]


def _jsonable(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _export_view(row: object) -> DataExportView:
    return DataExportView(
        export_request_id=row["export_request_id"],
        state=row["state"],
        requested_at=row["requested_at"],
        completed_at=row["completed_at"],
        expires_at=row["expires_at"],
        byte_size=row["byte_size"],
        payload_sha256=row["payload_sha256"],
        failure_code=row["failure_code"],
    )


def _deletion_view(row: object) -> AccountDeletionView:
    return AccountDeletionView(
        deletion_request_id=row["deletion_request_id"],
        state=row["state"],
        requested_at=row["requested_at"],
        completed_at=row["completed_at"],
        deletion_manifest=(
            dict(row["deletion_manifest"])
            if row["deletion_manifest"] is not None
            else None
        ),
        failure_code=row["failure_code"],
    )
