"""Add WeChat mini-program identities.

Revision ID: 20260712_01
Revises: 20260712_00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_01"
down_revision: str | Sequence[str] | None = "20260712_00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wechat_identities",
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("open_id", sa.Text(), nullable=False),
        sa.Column("union_id", sa.Text(), nullable=True),
        sa.Column("session_key_hash", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "length(session_key_hash) = 64",
            name="wechat_identities_session_key_hash_length",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("identity_id"),
        sa.UniqueConstraint("app_id", "open_id", name="wechat_identities_app_open_key"),
        schema="learner",
    )
    op.create_index(
        "learner_wechat_identities_user_idx",
        "wechat_identities",
        ["user_id"],
        schema="learner",
    )
    op.create_index(
        "learner_wechat_identities_union_idx",
        "wechat_identities",
        ["union_id"],
        unique=False,
        postgresql_where=sa.text("union_id is not null"),
        schema="learner",
    )


def downgrade() -> None:
    op.drop_index(
        "learner_wechat_identities_union_idx",
        table_name="wechat_identities",
        schema="learner",
    )
    op.drop_index(
        "learner_wechat_identities_user_idx",
        table_name="wechat_identities",
        schema="learner",
    )
    op.drop_table("wechat_identities", schema="learner")
