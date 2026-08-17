"""Add versioned subject consent for external cause-analysis processors.

Revision ID: 20260817_116
Revises: 20260817_115
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260817_116"
down_revision: str | Sequence[str] | None = "20260817_115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POLICY_VERSION = "xingce-cause-external-ai@v1"
PROCESSOR_RECORD_IDS = (
    "tokenrhythm-cause-consent-20260817-v1",
    "deepseek-official-cause-consent-20260817-v1",
    "ergouapi-cause-consent-20260817-v1",
)


def upgrade() -> None:
    op.execute(
        """
        alter table ai_audit.processor_release_records
          add column authorization_mode text not null
            default 'contracted_data_protection',
          add column consent_policy_version text
            references privacy.policy_versions(policy_version),
          add constraint processor_release_authorization_mode_check
            check (authorization_mode in (
              'contracted_data_protection', 'user_consent'
            ))
        """
    )
    op.execute(
        """
        do $$
        declare candidate record;
        begin
          for candidate in
            select conname
            from pg_constraint
            where conrelid = 'ai_audit.processor_release_records'::regclass
              and contype = 'c'
              and pg_get_constraintdef(oid) like '%real_user_status%approved%'
              and pg_get_constraintdef(oid) like '%contractually_prohibited%'
              and pg_get_constraintdef(oid) like '%zero_retention%'
          loop
            execute format(
              'alter table ai_audit.processor_release_records drop constraint %I',
              candidate.conname
            );
          end loop;
        end;
        $$
        """
    )
    op.execute(
        """
        alter table ai_audit.processor_release_records
          add constraint processor_release_real_user_authorization_check
          check (
            real_user_status <> 'approved'
            or (
              transport_security in ('tls', 'cloud_function')
              and operator_name is not null
              and (
                (
                  authorization_mode = 'contracted_data_protection'
                  and consent_policy_version is null
                  and model_training_use = 'contractually_prohibited'
                  and retention_policy = 'zero_retention'
                  and retention_seconds = 0
                  and deletion_support = 'not_applicable_zero_retention'
                  and evidence ?& array[
                    'operatorIdentity', 'dataProcessingTerms', 'trainingUse',
                    'retention', 'transportReview'
                  ]
                )
                or (
                  authorization_mode = 'user_consent'
                  and consent_policy_version is not null
                  and model_training_use in (
                    'vendor_improvement_possible', 'unknown'
                  )
                  and retention_policy in ('fixed_retention', 'unknown')
                  and deletion_support in ('verified', 'unverified')
                  and evidence ?& array[
                    'plainLanguageDisclosure', 'trainingUse', 'retention',
                    'transportReview', 'remoteDeletion'
                  ]
                )
              )
            )
          )
        """
    )
    op.execute(
        """
        insert into privacy.policy_versions (
          policy_version, title, content, content_sha256,
          state, published_at, retired_at
        ) values (
          'xingce-cause-external-ai@v1',
          '岸岸外部 AI 错因分析说明',
          '{
            "policyKind":"xingce_cause_external_ai",
            "purpose":"形成可由用户否定的错因候选，用于后续复测和任务调整",
            "dataSent":["作答结果","用时","改选和排除动作","用户填写的复盘与草稿文字"],
            "dataNotSent":["账号标识","微信身份","对象存储地址","密钥"],
            "providerRisk":"处理方可能将内容用于服务改进，保留期可能不固定",
            "withdrawal":"可随时撤回；撤回后不再发送新内容，但无法承诺处理方删除已收到的副本",
            "consentExperience":"同一版本只确认一次，之后静默生效"
          }'::jsonb,
          encode(digest(convert_to('{
            "policyKind":"xingce_cause_external_ai",
            "purpose":"形成可由用户否定的错因候选，用于后续复测和任务调整",
            "dataSent":["作答结果","用时","改选和排除动作","用户填写的复盘与草稿文字"],
            "dataNotSent":["账号标识","微信身份","对象存储地址","密钥"],
            "providerRisk":"处理方可能将内容用于服务改进，保留期可能不固定",
            "withdrawal":"可随时撤回；撤回后不再发送新内容，但无法承诺处理方删除已收到的副本",
            "consentExperience":"同一版本只确认一次，之后静默生效"
          }', 'UTF8'), 'sha256'), 'hex'),
          'active', now(), null
        )
        """
    )
    op.execute(
        """
        insert into ai_audit.processor_release_records (
          processor_release_record_id, provider_key, provider_release,
          display_name, operator_name, endpoint_fingerprint,
          transport_security, real_user_status, purposes, data_categories,
          data_region, model_training_use, retention_policy,
          retention_seconds, deletion_support, evidence,
          evidence_fingerprint, verified_at, valid_from, valid_until,
          published_at, authorization_mode, consent_policy_version
        )
        select
          seed.record_id, seed.provider_key, seed.provider_release,
          seed.display_name, seed.operator_name,
          encode(digest(convert_to(seed.endpoint, 'UTF8'), 'sha256'), 'hex'),
          'tls', 'approved', '["cause_candidate_generate"]'::jsonb,
          '["learning_evidence","learner_answer"]'::jsonb,
          seed.data_region, seed.training_use, 'unknown', null, 'unverified',
          seed.evidence::jsonb,
          encode(digest(convert_to(seed.evidence, 'UTF8'), 'sha256'), 'hex'),
          now(), now(), null, now(), 'user_consent',
          'xingce-cause-external-ai@v1'
        from (values
          (
            'tokenrhythm-cause-consent-20260817-v1', 'tokenrhythm',
            'tokenrhythm-deepseek-v4-pro', 'TokenRhythm 错因分析',
            'TokenRhythm（法律主体未核验）',
            'https://tokenrhythm.studio/v1', 'unverified', 'unknown',
            '{"plainLanguageDisclosure":"xingce-cause-external-ai@v1","trainingUse":"unknown","retention":"unknown","transportReview":"https-only","remoteDeletion":"unverified","source":"https://tokenrhythm.studio/docs/overview"}'
          ),
          (
            'deepseek-official-cause-consent-20260817-v1', 'deepseek_official',
            'deepseek-official-v4-pro', 'DeepSeek 官方错因分析',
            'DeepSeek', 'https://api.deepseek.com', 'unverified',
            'vendor_improvement_possible',
            '{"plainLanguageDisclosure":"xingce-cause-external-ai@v1","trainingUse":"vendor_improvement_possible","retention":"varies_by_purpose","transportReview":"https-only","remoteDeletion":"unverified","source":"https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html"}'
          ),
          (
            'ergouapi-cause-consent-20260817-v1', 'ergouapi_relay',
            'ergouapi-deepseek-v4-flash-free', '二狗 API 错因分析备用线路',
            '二狗 API（法律主体未核验）',
            'https://ergouapi.co/v1', 'unverified', 'unknown',
            '{"plainLanguageDisclosure":"xingce-cause-external-ai@v1","trainingUse":"unknown","retention":"unknown","transportReview":"https-only","remoteDeletion":"unverified","source":"public-terms-not-verified"}'
          )
        ) as seed(
          record_id, provider_key, provider_release, display_name,
          operator_name, endpoint, data_region, training_use, evidence
        )
        """
    )
    op.execute(
        """
        alter table ai_audit.external_processing_receipts
          add column consent_event_id uuid
            references privacy.consent_events(consent_event_id)
        """
    )
    op.execute(
        "drop trigger external_processing_receipts_validate "
        "on ai_audit.external_processing_receipts"
    )
    op.execute("drop function ai_audit.validate_external_processing_receipt()")
    op.execute(
        """
        create function ai_audit.validate_external_processing_receipt()
        returns trigger language plpgsql as $$
        declare release ai_audit.processor_release_records%rowtype;
        declare ledger_user_id uuid;
        declare consent privacy.consent_events%rowtype;
        declare latest_consent_event_id uuid;
        begin
          if new.model_call_id is not null then
            select user_id into ledger_user_id from ai_audit.model_call_ledger
              where call_id = new.model_call_id;
          elsif new.external_operation_id is not null then
            select user_id into ledger_user_id from ai_audit.external_ai_operation_ledger
              where operation_id = new.external_operation_id;
          else
            select author_user_id into ledger_user_id
              from product.community_method_card_candidates
              where candidate_id = new.community_candidate_id;
          end if;
          if ledger_user_id is distinct from new.user_id then
            raise exception using errcode = '23514',
              message = 'external processing receipt subject does not match its ledger';
          end if;
          if new.authorization_state = 'real_user_approved' then
            select * into release from ai_audit.processor_release_records
              where processor_release_record_id = new.processor_release_record_id;
            if release.real_user_status is distinct from 'approved'
              or release.provider_release is distinct from new.declared_provider_release
              or release.endpoint_fingerprint is distinct from new.endpoint_fingerprint
              or new.dispatched_at < release.valid_from
              or (release.valid_until is not null and new.dispatched_at >= release.valid_until)
              or not (release.purposes ? new.purpose) then
              raise exception using errcode = '23514',
                message = 'external processing receipt lacks an active processor release';
            end if;
            if exists (
              select 1 from jsonb_array_elements_text(new.data_categories) category
              where not (release.data_categories ? category)
            ) then
              raise exception using errcode = '23514',
                message = 'external processing receipt exceeds approved data categories';
            end if;
            if release.authorization_mode = 'contracted_data_protection'
              and new.consent_event_id is not null then
              raise exception using errcode = '23514',
                message = 'contracted processor receipt must not carry subject consent';
            elsif release.authorization_mode = 'user_consent' then
              select * into consent from privacy.consent_events
                where consent_event_id = new.consent_event_id;
              select consent_event_id into latest_consent_event_id
                from privacy.consent_events
                where user_id = new.user_id
                  and policy_version = release.consent_policy_version
                  and created_at <= new.dispatched_at
                order by created_at desc, consent_event_id desc
                limit 1;
              if new.user_id is null
                or consent.user_id is distinct from new.user_id
                or consent.policy_version is distinct from release.consent_policy_version
                or consent.action is distinct from 'accepted'
                or consent.created_at > new.dispatched_at
                or latest_consent_event_id is distinct from new.consent_event_id then
                raise exception using errcode = '23514',
                  message = 'external processing receipt lacks current subject consent';
              end if;
            end if;
          elsif new.consent_event_id is not null then
            raise exception using errcode = '23514',
              message = 'local external processing receipt must not carry subject consent';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger external_processing_receipts_validate
          before insert on ai_audit.external_processing_receipts
          for each row execute function ai_audit.validate_external_processing_receipt()
        """
    )
    op.execute(
        """
        alter table privacy.external_processing_deletion_events
          drop constraint if exists external_processing_deletion_events_disposition_check
        """
    )
    op.execute(
        """
        do $$
        declare candidate record;
        begin
          for candidate in
            select conname
            from pg_constraint
            where conrelid = 'privacy.external_processing_deletion_events'::regclass
              and contype = 'c'
              and pg_get_constraintdef(oid) like '%not_required_zero_retention%'
          loop
            execute format(
              'alter table privacy.external_processing_deletion_events drop constraint %I',
              candidate.conname
            );
          end loop;
        end;
        $$
        """
    )
    op.execute(
        """
        alter table privacy.external_processing_deletion_events
          add constraint external_processing_deletion_events_disposition_check
          check (disposition in (
            'not_required_zero_retention', 'not_dispatched_local_unreleased',
            'remote_deletion_unavailable_disclosed'
          ))
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_facts = bind.execute(
        text(
            """
            select
              exists (
                select 1 from privacy.consent_events
                where policy_version = :policy_version
              )
              or exists (
                select 1 from ai_audit.external_processing_receipts
                where consent_event_id is not null
              )
              or exists (
                select 1 from privacy.external_processing_deletion_events
                where disposition = 'remote_deletion_unavailable_disclosed'
              )
            """
        ),
        {"policy_version": POLICY_VERSION},
    ).scalar_one()
    if has_facts:
        raise RuntimeError("external AI consent facts exist; migration 116 requires forward repair")
    op.execute(
        "drop trigger external_processing_receipts_validate "
        "on ai_audit.external_processing_receipts"
    )
    op.execute("drop function ai_audit.validate_external_processing_receipt()")
    op.execute(
        "drop trigger processor_release_records_append_only on ai_audit.processor_release_records"
    )
    op.execute(
        "delete from ai_audit.processor_release_records "
        "where processor_release_record_id in ("
        "'tokenrhythm-cause-consent-20260817-v1',"
        "'deepseek-official-cause-consent-20260817-v1',"
        "'ergouapi-cause-consent-20260817-v1')"
    )
    op.execute(
        """
        create trigger processor_release_records_append_only
          before update or delete on ai_audit.processor_release_records
          for each row execute function ai_audit.reject_append_only_mutation()
        """
    )
    op.execute(
        "delete from privacy.policy_versions where policy_version = 'xingce-cause-external-ai@v1'"
    )
    op.execute("alter table ai_audit.external_processing_receipts drop column consent_event_id")
    op.execute(
        "alter table ai_audit.processor_release_records "
        "drop constraint processor_release_real_user_authorization_check"
    )
    op.execute(
        "alter table ai_audit.processor_release_records "
        "drop constraint processor_release_authorization_mode_check"
    )
    op.execute(
        "alter table ai_audit.processor_release_records "
        "drop column consent_policy_version, drop column authorization_mode"
    )
    op.execute(
        """
        alter table ai_audit.processor_release_records
          add constraint processor_release_real_user_legacy_check
          check (
            real_user_status <> 'approved'
            or (
              transport_security in ('tls', 'cloud_function')
              and operator_name is not null
              and model_training_use = 'contractually_prohibited'
              and retention_policy = 'zero_retention'
              and retention_seconds = 0
              and deletion_support = 'not_applicable_zero_retention'
              and evidence ?& array[
                'operatorIdentity', 'dataProcessingTerms', 'trainingUse',
                'retention', 'transportReview'
              ]
            )
          )
        """
    )
    op.execute(
        """
        create function ai_audit.validate_external_processing_receipt()
        returns trigger language plpgsql as $$
        declare release ai_audit.processor_release_records%rowtype;
        declare ledger_user_id uuid;
        begin
          if new.model_call_id is not null then
            select user_id into ledger_user_id from ai_audit.model_call_ledger
              where call_id = new.model_call_id;
          elsif new.external_operation_id is not null then
            select user_id into ledger_user_id from ai_audit.external_ai_operation_ledger
              where operation_id = new.external_operation_id;
          else
            select author_user_id into ledger_user_id
              from product.community_method_card_candidates
              where candidate_id = new.community_candidate_id;
          end if;
          if ledger_user_id is distinct from new.user_id then
            raise exception using errcode = '23514',
              message = 'external processing receipt subject does not match its ledger';
          end if;
          if new.authorization_state = 'real_user_approved' then
            select * into release from ai_audit.processor_release_records
              where processor_release_record_id = new.processor_release_record_id;
            if release.real_user_status is distinct from 'approved'
              or release.provider_release is distinct from new.declared_provider_release
              or release.endpoint_fingerprint is distinct from new.endpoint_fingerprint
              or new.dispatched_at < release.valid_from
              or (release.valid_until is not null and new.dispatched_at >= release.valid_until)
              or not (release.purposes ? new.purpose) then
              raise exception using errcode = '23514',
                message = 'external processing receipt lacks an active processor release';
            end if;
            if exists (
              select 1 from jsonb_array_elements_text(new.data_categories) category
              where not (release.data_categories ? category)
            ) then
              raise exception using errcode = '23514',
                message = 'external processing receipt exceeds approved data categories';
            end if;
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger external_processing_receipts_validate
          before insert on ai_audit.external_processing_receipts
          for each row execute function ai_audit.validate_external_processing_receipt()
        """
    )
    op.execute(
        "alter table privacy.external_processing_deletion_events "
        "drop constraint external_processing_deletion_events_disposition_check"
    )
    op.execute(
        """
        alter table privacy.external_processing_deletion_events
          add constraint external_processing_deletion_events_disposition_legacy_check
          check (disposition in (
            'not_required_zero_retention', 'not_dispatched_local_unreleased'
          ))
        """
    )
