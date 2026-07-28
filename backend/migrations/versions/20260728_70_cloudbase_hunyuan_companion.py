"""Approve the server-owned CloudBase Hunyuan Companion release.

Revision ID: 20260728_70
Revises: 20260727_69
"""

from typing import Sequence

from alembic import op


revision: str = "20260728_70"
down_revision: str | Sequence[str] | None = "20260727_69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RECORD_ID = "hunyuan-cloud-function-companion-20260728-1"
_PROVIDER_RELEASE = "hunyuan-cloud-function-companion-2026-07-28.1"
_ENDPOINT = (
    "https://zhus-d3gkwjeltb8e1ffd7.api.tcloudbasegateway.com/"
    "v1/functions/hunyuan-essay-grader"
)


def upgrade() -> None:
    op.execute(
        f"""
        with release_evidence as (
          select jsonb_build_object(
            'operatorIdentity', '腾讯云计算（北京）有限责任公司',
            'dataProcessingTerms', jsonb_build_array(
              'https://cloud.tencent.com/document/product/301/1967',
              'https://cloud.tencent.com/document/product/301/117329'
            ),
            'trainingUse',
              '腾讯云服务协议约定，除执行服务要求外不对用户业务数据作未授权使用',
            'retention',
              '本项目云函数为同步无存储处理；函数代码不记录输入或输出',
            'transportReview',
              '服务端经 TLS 调用固定 CloudBase 环境与固定云函数，模型和系统提示由服务端持有',
            'reviewedAt', '2026-07-28T00:00:00+08:00'
          ) as document
        )
        insert into ai_audit.processor_release_records (
          processor_release_record_id, provider_key, provider_release,
          display_name, operator_name, endpoint_fingerprint,
          transport_security, real_user_status, purposes, data_categories,
          data_region, model_training_use, retention_policy,
          retention_seconds, deletion_support, evidence,
          evidence_fingerprint, verified_at, valid_from, valid_until,
          published_at
        )
        select
          '{_RECORD_ID}',
          'wechat_hunyuan_cloud_function',
          '{_PROVIDER_RELEASE}',
          '微信云函数混元·岸岸问答',
          '腾讯云',
          encode(digest(convert_to('{_ENDPOINT}', 'UTF8'), 'sha256'), 'hex'),
          'cloud_function',
          'approved',
          '["companion_answer"]'::jsonb,
          '["learner_question","learning_evidence"]'::jsonb,
          'mainland_china',
          'contractually_prohibited',
          'zero_retention',
          0,
          'not_applicable_zero_retention',
          release_evidence.document,
          encode(
            digest(
              convert_to(release_evidence.document::text, 'UTF8'),
              'sha256'
            ),
            'hex'
          ),
          '2026-07-28 00:00:00+08'::timestamptz,
          '2026-07-28 00:00:00+08'::timestamptz,
          null,
          '2026-07-28 00:00:00+08'::timestamptz
        from release_evidence
        on conflict (processor_release_record_id) do nothing
        """
    )


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1
            from ai_audit.external_processing_receipts
            where processor_release_record_id =
              'hunyuan-cloud-function-companion-20260728-1'
          ) then
            raise exception
              'CloudBase Companion processing receipts exist; use a forward retirement migration';
          end if;
        end;
        $$
        """
    )
    op.execute(
        """
        alter table ai_audit.processor_release_records
          disable trigger processor_release_records_append_only
        """
    )
    op.execute(
        """
        delete from ai_audit.processor_release_records
        where processor_release_record_id =
          'hunyuan-cloud-function-companion-20260728-1'
        """
    )
    op.execute(
        """
        alter table ai_audit.processor_release_records
          enable trigger processor_release_records_append_only
        """
    )
