"""Add an optional structured replacement code to confirmed causes.

用户改写错因（source_kind = 'user_replacement'）此前只有自由文本，
learning_brain 聚合被迫落在单一 'user_replacement' 桶。新增可选列
replacement_code：用户在改写时可以额外选择 8 码错因分类之一，让改写
参与结构化归桶；未选码的旧行为完全不变（列保持 null）。

只做加法：新列可空、无默认值回填、不改既有约束语义。

Revision ID: 20260727_62
Revises: 20260724_61
Create Date: 2026-07-27
"""

from typing import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260727_62"
down_revision: str | Sequence[str] | None = "20260724_61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 与 app.domains.xingce_learning.cause_agent.tools.CAUSES 对齐的 8 码分类；
# 'user_replacement' 本身不是可选码。
_REPLACEMENT_CAUSE_CODES = (
    "stem_misread",
    "knowledge_gap",
    "method_selection",
    "calculation_error",
    "time_pressure",
    "guessing",
    "active_abandon",
    "other",
)

_STATEMENTS = (
    """
    alter table learning.xingce_confirmed_cause_versions
      add column replacement_code text
    """,
    """
    alter table learning.xingce_confirmed_cause_versions
      add constraint xingce_confirmed_cause_replacement_code_check check (
        replacement_code is null
        or (
          source_kind = 'user_replacement'
          and replacement_code in (
            'stem_misread', 'knowledge_gap', 'method_selection',
            'calculation_error', 'time_pressure', 'guessing',
            'active_abandon', 'other'
          )
        )
      )
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_replacement_codes(bind):
        raise RuntimeError(
            "Structured replacement codes exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.execute(
        "alter table learning.xingce_confirmed_cause_versions "
        "drop constraint if exists xingce_confirmed_cause_replacement_code_check"
    )
    op.drop_column(
        "xingce_confirmed_cause_versions",
        "replacement_code",
        schema="learning",
    )


def _has_replacement_codes(bind: Connection) -> bool:
    return bool(
        bind.execute(
            text(
                "select exists (select 1 from learning.xingce_confirmed_cause_versions "
                "where replacement_code is not null)"
            )
        ).scalar()
    )
