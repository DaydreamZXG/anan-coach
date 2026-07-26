"""Ability estimate v2 shadow: anchored difficulty inputs and shadow output table.

能力估计 v2 影子算法（ability-estimate-v2-shadow-1）落库：

1. product.question_occurrences 增加两列全国作答统计（可空、无回填）：
   - occurrence_correct_ratio：该次出现的全国正确率（0~1）；
   - occurrence_sample_size：该次出现的作答样本量，用于多次出现的样本加权。
   缺失 occurrence_correct_ratio 的题目不参与 v2 难度锚定（显式缺省，不硬给数）。

2. learning.ability_estimate_v2_shadow：影子估计结果表，append-only 风格 ——
   每次重算追加一行，绝不 update/delete 既有行；v1 用户可见输出一字不改，
   本表不接任何小程序 API（影子期只做对比留痕）。

只做加法：不改任何既有列、约束与 v1 行为。

Revision ID: 20260727_63
Revises: 20260727_62
Create Date: 2026-07-27
"""

from typing import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260727_63"
down_revision: str | Sequence[str] | None = "20260727_62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    alter table product.question_occurrences
      add column occurrence_correct_ratio numeric(6, 5)
    """,
    """
    alter table product.question_occurrences
      add column occurrence_sample_size integer
    """,
    """
    alter table product.question_occurrences
      add constraint question_occurrence_correct_ratio_check check (
        occurrence_correct_ratio is null
        or (occurrence_correct_ratio >= 0 and occurrence_correct_ratio <= 1)
      )
    """,
    """
    alter table product.question_occurrences
      add constraint question_occurrence_sample_size_check check (
        occurrence_sample_size is null or occurrence_sample_size > 0
      )
    """,
    # 影子估计表：样本不足时 theta/SE/置信区间整体缺省（null）而不是硬给数，
    # 用 check 把「缺省」和 insufficient_sample 标志锁死成同一件事。
    """
    create table learning.ability_estimate_v2_shadow (
      shadow_estimate_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      subject text not null check (subject = 'xingce'),
      theta numeric(8, 4),
      theta_se numeric(8, 4) check (theta_se is null or theta_se >= 0),
      ci_lower numeric(8, 4),
      ci_upper numeric(8, 4),
      sample_size integer not null check (sample_size >= 0),
      insufficient_sample boolean not null,
      strata jsonb not null check (jsonb_typeof(strata) = 'object'),
      mapped_v1_xingce_score numeric(6, 2) check (
        mapped_v1_xingce_score is null
        or (mapped_v1_xingce_score >= 0 and mapped_v1_xingce_score <= 100)
      ),
      v1_snapshot jsonb check (
        v1_snapshot is null or jsonb_typeof(v1_snapshot) = 'object'
      ),
      divergence jsonb check (
        divergence is null or jsonb_typeof(divergence) = 'object'
      ),
      algorithm_version text not null check (
        algorithm_version = 'ability-estimate-v2-shadow-1'
      ),
      computed_at timestamptz not null,
      created_at timestamptz not null default now(),
      check ((theta is null) = insufficient_sample),
      check ((theta is null) = (theta_se is null)),
      check ((theta is null) = (ci_lower is null)),
      check ((theta is null) = (ci_upper is null)),
      check (ci_lower is null or (ci_lower <= theta and theta <= ci_upper)),
      check ((theta is null) = (mapped_v1_xingce_score is null))
    )
    """,
    """
    create index learning_ability_v2_shadow_user_idx
      on learning.ability_estimate_v2_shadow(user_id, subject, computed_at desc)
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_shadow_data(bind):
        raise RuntimeError(
            "Ability estimate v2 shadow rows or occurrence statistics exist; "
            "destructive downgrade is forbidden. Repair with a forward migration."
        )
    op.execute("drop table if exists learning.ability_estimate_v2_shadow")
    op.execute(
        "alter table product.question_occurrences "
        "drop constraint if exists question_occurrence_correct_ratio_check"
    )
    op.execute(
        "alter table product.question_occurrences "
        "drop constraint if exists question_occurrence_sample_size_check"
    )
    op.drop_column("question_occurrences", "occurrence_correct_ratio", schema="product")
    op.drop_column("question_occurrences", "occurrence_sample_size", schema="product")


def _has_shadow_data(bind: Connection) -> bool:
    shadow_rows = bind.execute(
        text("select exists (select 1 from learning.ability_estimate_v2_shadow)")
    ).scalar()
    ratio_rows = bind.execute(
        text(
            "select exists (select 1 from product.question_occurrences "
            "where occurrence_correct_ratio is not null "
            "or occurrence_sample_size is not null)"
        )
    ).scalar()
    return bool(shadow_rows) or bool(ratio_rows)
