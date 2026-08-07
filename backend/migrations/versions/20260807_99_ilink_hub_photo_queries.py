"""Allow photo queries to originate from an account-bound OpeniLink Hub bot.

Revision ID: 20260807_99
Revises: 20260806_98
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260807_99"
down_revision: str | Sequence[str] | None = "20260806_98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table messaging.ilink_photo_queries alter column link_id drop not null"
    )
    op.execute(
        """
        alter table messaging.ilink_photo_queries
          add column hub_authorization_id uuid references messaging.ilink_hub_bot_links(
            authorization_id
          ) on update cascade on delete cascade,
          add constraint ilink_photo_queries_single_channel_ck check (
            num_nonnulls(link_id, hub_authorization_id) = 1
          )
        """
    )


def downgrade() -> None:
    op.execute(
        "delete from messaging.ilink_photo_queries where hub_authorization_id is not null"
    )
    op.execute(
        """
        alter table messaging.ilink_photo_queries
          drop constraint ilink_photo_queries_single_channel_ck,
          drop column hub_authorization_id,
          alter column link_id set not null
        """
    )
