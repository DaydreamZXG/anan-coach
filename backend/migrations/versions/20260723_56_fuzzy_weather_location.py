"""Allow privacy-preserving fuzzy weather locations.

Revision ID: 20260723_56
Revises: 20260723_55
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "20260723_56"
down_revision = "20260723_55"
branch_labels = None
depends_on = None


_DROP_SOURCE_CHECK = """
alter table messaging.daily_opening_location_versions
  drop constraint daily_opening_location_versions_source_type_check
"""

_ADD_CURRENT_SOURCE_CHECK = """
alter table messaging.daily_opening_location_versions
  add constraint daily_opening_location_versions_source_type_check
  check (source_type in ('wechat_choose_location', 'wechat_fuzzy_location'))
"""

_ADD_LEGACY_SOURCE_CHECK = """
alter table messaging.daily_opening_location_versions
  add constraint daily_opening_location_versions_source_type_check
  check (source_type = 'wechat_choose_location')
"""

_TEST_STATEMENTS = (_DROP_SOURCE_CHECK, _ADD_CURRENT_SOURCE_CHECK)


def upgrade() -> None:
    for statement in _TEST_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_fuzzy_locations(bind):
        raise RuntimeError("Fuzzy weather locations exist; migrate those facts before downgrading.")
    op.execute(_DROP_SOURCE_CHECK)
    op.execute(_ADD_LEGACY_SOURCE_CHECK)


def _has_fuzzy_locations(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                select exists (
                  select 1
                  from messaging.daily_opening_location_versions
                  where source_type = 'wechat_fuzzy_location'
                )
                """
            )
        ).scalar_one()
    )
