"""Add publishable processor releases and subject-linked external processing receipts.

Revision ID: 20260719_41
Revises: 20260718_40
"""

from typing import Sequence

from alembic import op


revision: str = "20260719_41"
down_revision: str | Sequence[str] | None = "20260718_40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TEST_STATEMENTS: tuple[str, ...] = (
    """
    create table ai_audit.external_processing_boundary_releases (
      boundary_version text primary key,
      activated_at timestamptz not null
    )
    """,
    """
    create trigger external_processing_boundary_releases_append_only
      before update or delete on ai_audit.external_processing_boundary_releases
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    insert into ai_audit.external_processing_boundary_releases (
      boundary_version, activated_at
    ) values ('external-ai-data-boundary@v1', now())
    """,
    """
    create table ai_audit.processor_release_records (
      processor_release_record_id text primary key,
      provider_key text not null,
      provider_release text not null,
      display_name text not null,
      operator_name text,
      endpoint_fingerprint text not null check (length(endpoint_fingerprint) = 64),
      transport_security text not null check (transport_security in (
        'tls', 'cloud_function', 'plaintext_http_blocked', 'not_integrated'
      )),
      real_user_status text not null check (real_user_status in (
        'approved', 'blocked', 'synthetic_only', 'retired'
      )),
      purposes jsonb not null check (
        jsonb_typeof(purposes) = 'array' and jsonb_array_length(purposes) > 0
      ),
      data_categories jsonb not null check (
        jsonb_typeof(data_categories) = 'array'
        and jsonb_array_length(data_categories) > 0
      ),
      data_region text not null,
      model_training_use text not null check (model_training_use in (
        'contractually_prohibited', 'vendor_improvement_possible',
        'unknown', 'not_applicable_synthetic'
      )),
      retention_policy text not null check (retention_policy in (
        'zero_retention', 'fixed_retention', 'unknown', 'not_applicable_synthetic'
      )),
      retention_seconds integer check (
        retention_seconds is null or retention_seconds >= 0
      ),
      deletion_support text not null check (deletion_support in (
        'verified', 'unverified', 'not_applicable_zero_retention',
        'not_applicable_synthetic'
      )),
      evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
      evidence_fingerprint text not null check (length(evidence_fingerprint) = 64),
      verified_at timestamptz not null,
      valid_from timestamptz not null,
      valid_until timestamptz,
      published_at timestamptz not null,
      unique (provider_key, provider_release, endpoint_fingerprint),
      check (valid_until is null or valid_until > valid_from),
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
    )
    """,
    """
    create trigger processor_release_records_append_only
      before update or delete on ai_audit.processor_release_records
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.external_processing_receipts (
      receipt_id uuid primary key,
      user_id uuid references learner.users(user_id),
      model_call_id uuid unique references ai_audit.model_call_ledger(call_id),
      external_operation_id uuid unique references
        ai_audit.external_ai_operation_ledger(operation_id),
      community_candidate_id uuid unique references
        product.community_method_card_candidates(candidate_id),
      processor_release_record_id text references
        ai_audit.processor_release_records(processor_release_record_id),
      declared_provider_release text not null,
      endpoint_fingerprint text not null check (length(endpoint_fingerprint) = 64),
      scenario_key text not null,
      purpose text not null,
      data_categories jsonb not null check (
        jsonb_typeof(data_categories) = 'array'
        and jsonb_array_length(data_categories) > 0
      ),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      authorization_state text not null check (authorization_state in (
        'real_user_approved', 'local_unreleased'
      )),
      dispatched_at timestamptz not null,
      check (num_nonnulls(
        model_call_id, external_operation_id, community_candidate_id
      ) = 1),
      check (
        (authorization_state = 'real_user_approved'
          and processor_release_record_id is not null)
        or (authorization_state = 'local_unreleased'
          and processor_release_record_id is null)
      )
    )
    """,
    """
    create index external_processing_receipts_user_idx
      on ai_audit.external_processing_receipts(user_id, dispatched_at desc)
    """,
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
    """,
    """
    create trigger external_processing_receipts_validate
      before insert on ai_audit.external_processing_receipts
      for each row execute function ai_audit.validate_external_processing_receipt()
    """,
    """
    create trigger external_processing_receipts_append_only
      before update or delete on ai_audit.external_processing_receipts
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table privacy.external_processing_deletion_events (
      deletion_event_id uuid primary key,
      deletion_request_id uuid not null references
        privacy.account_deletion_requests(deletion_request_id),
      receipt_id uuid not null references ai_audit.external_processing_receipts(receipt_id),
      processor_release_record_id text references
        ai_audit.processor_release_records(processor_release_record_id),
      disposition text not null check (disposition in (
        'not_required_zero_retention', 'not_dispatched_local_unreleased'
      )),
      evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
      recorded_at timestamptz not null,
      unique (deletion_request_id, receipt_id)
    )
    """,
    """
    create trigger external_processing_deletion_events_append_only
      before update or delete on privacy.external_processing_deletion_events
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    insert into ai_audit.processor_release_records (
      processor_release_record_id, provider_key, provider_release, display_name,
      operator_name, endpoint_fingerprint, transport_security, real_user_status,
      purposes, data_categories, data_region, model_training_use,
      retention_policy, retention_seconds, deletion_support, evidence,
      evidence_fingerprint, verified_at, valid_from, valid_until, published_at
    )
    select
      seed.record_id, seed.provider_key, seed.provider_release, seed.display_name,
      seed.operator_name,
      encode(digest(convert_to(seed.endpoint, 'UTF8'), 'sha256'), 'hex'),
      seed.transport_security, seed.real_user_status,
      seed.purposes::jsonb, seed.data_categories::jsonb, seed.data_region,
      seed.model_training_use, seed.retention_policy,
      seed.retention_seconds::integer,
      seed.deletion_support, seed.evidence::jsonb,
      encode(digest(convert_to(seed.evidence, 'UTF8'), 'sha256'), 'hex'),
      '2026-07-19 00:00:00+08'::timestamptz,
      '2026-07-19 00:00:00+08'::timestamptz,
      null, '2026-07-19 00:00:00+08'::timestamptz
    from (values
      (
        'gemini-web2api-companion-20260718-1', 'gemini_web2api_proxy',
        'gemini-web2api-2026-07-18.1', 'Gemini 兼容文本网关', null,
        'https://gemini-web2api.ergouapi.co/v1', 'tls', 'blocked',
        '["companion_answer", "external_question_answer_generate"]',
        '["learner_question", "learning_evidence", "recognized_question"]',
        'unverified', 'unknown', 'unknown', null, 'unverified',
        '{"status":"blocked","reason":"intermediary operator, training, retention and deletion terms are not independently verified"}'
      ),
      (
        'gemini-web2api-cause-20260718-1', 'gemini_web2api_proxy',
        'gemini-web2api-cause-2026-07-18.1', 'Gemini 兼容行测归因网关', null,
        'https://gemini-web2api.ergouapi.co/v1', 'tls', 'blocked',
        '["cause_candidate_generate"]',
        '["learning_evidence", "learner_answer"]', 'unverified', 'unknown',
        'unknown', null, 'unverified',
        '{"status":"blocked","reason":"intermediary operator, training, retention and deletion terms are not independently verified"}'
      ),
      (
        'gemini-web2api-community-20260718-1', 'gemini_web2api_proxy',
        'gemini-web2api-community-2026-07-18.1', 'Gemini 兼容方法卡网关', null,
        'https://gemini-web2api.ergouapi.co/v1', 'tls', 'blocked',
        '["community_candidate_generate"]', '["community_content"]',
        'unverified', 'unknown', 'unknown', null, 'unverified',
        '{"status":"blocked","reason":"intermediary operator, training, retention and deletion terms are not independently verified"}'
      ),
      (
        'gemini-web2api-rubric-20260718-1', 'gemini_web2api_proxy',
        'gemini-web2api-rubric-2026-07-18.1', 'Gemini 兼容申论规程网关', null,
        'https://gemini-web2api.ergouapi.co/v1', 'tls', 'blocked',
        '["rubric_generator", "rubric_critic"]', '["public_exam_material"]',
        'unverified', 'unknown', 'unknown', null, 'unverified',
        '{"status":"blocked","reason":"intermediary operator, training, retention and deletion terms are not independently verified"}'
      ),
      (
        'gemini-web2api-review-20260718-1', 'gemini_web2api_proxy',
        'gemini-web2api-review-2026-07-18.1', 'Gemini 兼容申论批改网关', null,
        'https://gemini-web2api.ergouapi.co/v1', 'tls', 'blocked',
        '["grounded_review_generator", "grounded_review_repair"]',
        '["essay_answer", "public_exam_material", "grading_rubric"]',
        'unverified', 'unknown', 'unknown', null, 'unverified',
        '{"status":"blocked","reason":"intermediary operator, training, retention and deletion terms are not independently verified"}'
      ),
      (
        'grok-vision-operator-proxy-v1', 'grok_operator_proxy',
        'grok-4.5-build-free@operator-proxy-v1', 'Grok 视觉网关', null,
        'http://34.96.236.81:8317/v1', 'plaintext_http_blocked', 'blocked',
        '["recognition_process"]', '["uploaded_image"]', 'unverified',
        'unknown', 'unknown', null, 'unverified',
        '{"status":"blocked","reason":"plaintext HTTP is forbidden for real learner content"}'
      ),
      (
        'hunyuan-cloud-function-connectivity-v1', 'wechat_hunyuan_cloud_function',
        'hunyuan-connectivity-probe@v1', '微信云函数混元连通性检查',
        '腾讯云', 'cloud-function://hunyuan-essay-grader/connectivity-probe',
        'cloud_function', 'synthetic_only', '["connectivity_probe"]',
        '["synthetic_probe"]', 'mainland_china', 'not_applicable_synthetic',
        'not_applicable_synthetic', null, 'not_applicable_synthetic',
        '{"status":"synthetic_only","terms":"https://cloud.tencent.com/document/product/301/97822"}'
      ),
      (
        'myscript-iink-cloud-v8-reviewed-20260719', 'myscript_iink_cloud',
        'myscript-iink-cloud-v8', 'MyScript iink Cloud', 'MyScript',
        'https://cloud.myscript.com', 'not_integrated', 'blocked',
        '["handwriting_math_recognition"]', '["handwriting_strokes"]',
        'external', 'unknown', 'unknown', null, 'unverified',
        '{"status":"blocked","terms":"https://developer.myscript.com/pdf/License-terms-of-use-and-sale.pdf","reason":"zero-retention, training prohibition and deletion evidence are not published for this product"}'
      )
    ) as seed(
      record_id, provider_key, provider_release, display_name, operator_name,
      endpoint, transport_security, real_user_status, purposes, data_categories,
      data_region, model_training_use, retention_policy, retention_seconds,
      deletion_support, evidence
    )
    """,
)


def upgrade() -> None:
    for statement in _TEST_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from ai_audit.external_processing_receipts)
            or exists (select 1 from privacy.external_processing_deletion_events) then
            raise exception 'external processing audit records exist; forward repair is required';
          end if;
        end;
        $$
        """
    )
    op.execute(
        "drop trigger external_processing_deletion_events_append_only "
        "on privacy.external_processing_deletion_events"
    )
    op.execute("drop table privacy.external_processing_deletion_events")
    op.execute(
        "drop trigger external_processing_receipts_append_only "
        "on ai_audit.external_processing_receipts"
    )
    op.execute(
        "drop trigger external_processing_receipts_validate "
        "on ai_audit.external_processing_receipts"
    )
    op.execute("drop table ai_audit.external_processing_receipts")
    op.execute("drop function ai_audit.validate_external_processing_receipt()")
    op.execute(
        "drop trigger processor_release_records_append_only "
        "on ai_audit.processor_release_records"
    )
    op.execute("drop table ai_audit.processor_release_records")
    op.execute(
        "drop trigger external_processing_boundary_releases_append_only "
        "on ai_audit.external_processing_boundary_releases"
    )
    op.execute("drop table ai_audit.external_processing_boundary_releases")
