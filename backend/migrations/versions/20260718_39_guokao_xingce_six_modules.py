"""Publish the current six-module guokao xingce scoring policy.

Revision ID: 20260718_39
Revises: 20260718_38
"""

from typing import Sequence

from alembic import op


revision: str = "20260718_39"
down_revision: str | Sequence[str] | None = "20260718_38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TEST_STATEMENTS: tuple[str, ...] = (
    """
    insert into product.mock_exam_scoring_policy_versions (
      scoring_policy_version_id, state, metric_name, metric_unit, definition,
      module_weights, interval_method, confidence_level,
      calibration_status, calibration_sample_size, evidence_ref
    ) values (
      'mock-question-weighted-performance-index-v2',
      'published',
      'question_weighted_performance_index',
      'index_points',
      '2025 年起国考行测六模块按副省级标准卷题量占比加权；'
      || '仅表达本卷结构化表现，不是官方原始分或全国分位。',
      jsonb_build_object(
        '政治理论', 20.0 / 135.0,
        '常识判断', 15.0 / 135.0,
        '言语理解与表达', 30.0 / 135.0,
        '数量关系', 15.0 / 135.0,
        '判断推理', 35.0 / 135.0,
        '资料分析', 20.0 / 135.0
      ),
      'wilson_question_weighted_95',
      0.95,
      'structural_index',
      0,
      'module-catalog:guokao-xingce-modules-2025-v1'
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
          if exists (
            select 1 from product.mock_exam_paper_versions
            where scoring_policy_version_id =
              'mock-question-weighted-performance-index-v2'
          ) or exists (
            select 1 from learner.mock_exam_sessions
            where scoring_policy_version_id =
              'mock-question-weighted-performance-index-v2'
          ) then
            raise exception
              'cannot remove six-module scoring policy while evidence references it';
          end if;
        end;
        $$
        """
    )
    op.execute(
        "drop trigger mock_exam_scoring_policy_append_only "
        "on product.mock_exam_scoring_policy_versions"
    )
    op.execute(
        "delete from product.mock_exam_scoring_policy_versions "
        "where scoring_policy_version_id = "
        "'mock-question-weighted-performance-index-v2'"
    )
    op.execute(
        "create trigger mock_exam_scoring_policy_append_only "
        "before update or delete on product.mock_exam_scoring_policy_versions "
        "for each row execute function learning.reject_xingce_fact_mutation()"
    )
