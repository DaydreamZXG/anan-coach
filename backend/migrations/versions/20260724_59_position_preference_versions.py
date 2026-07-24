"""Add versioned position preferences.

Revision ID: 20260724_59
Revises: 20260724_58
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260724_59"
down_revision = "20260724_58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_preference_versions",
        sa.Column("preference_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column(
            "preferred_regions",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column("accepts_relocation", sa.Boolean(), nullable=False),
        sa.Column(
            "accepted_position_levels",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_preference_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "version_no >= 1",
            name="position_preference_versions_version_check",
        ),
        sa.CheckConstraint(
            "cardinality(preferred_regions) between 1 and 5",
            name="position_preference_versions_regions_check",
        ),
        sa.CheckConstraint(
            "cardinality(accepted_position_levels) between 1 and 4 "
            "and accepted_position_levels <@ "
            "array['central','provincial','municipal','county_or_below']::text[]",
            name="position_preference_versions_levels_check",
        ),
        sa.CheckConstraint(
            "source_type in ('user_confirmed','user_revised')",
            name="position_preference_versions_source_check",
        ),
        sa.CheckConstraint(
            "char_length(input_fingerprint) = 64",
            name="position_preference_versions_fingerprint_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_preference_version_id"],
            ["learner.position_preference_versions.preference_version_id"],
        ),
        sa.PrimaryKeyConstraint("preference_version_id"),
        sa.UniqueConstraint(
            "command_id",
            name="position_preference_versions_command_id_key",
        ),
        sa.UniqueConstraint(
            "user_id",
            "version_no",
            name="position_preference_versions_user_version_key",
        ),
        sa.UniqueConstraint(
            "supersedes_preference_version_id",
            name="position_preference_versions_supersedes_key",
        ),
        schema="learner",
    )
    op.create_index(
        "learner_position_preference_current_idx",
        "position_preference_versions",
        ["user_id", "version_no"],
        schema="learner",
    )
    op.execute(
        """
        create function learner.enforce_position_preference_immutability()
        returns trigger language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            raise exception using errcode = '23514',
              message = 'position preference history cannot be deleted';
          end if;
          if privacy.erasure_authorized(to_jsonb(old)) then
            if row(
              new.preference_version_id, new.command_id, new.user_id,
              new.version_no, new.source_type,
              new.supersedes_preference_version_id, new.created_at
            ) is distinct from row(
              old.preference_version_id, old.command_id, old.user_id,
              old.version_no, old.source_type,
              old.supersedes_preference_version_id, old.created_at
            ) or new.preferred_regions <> array['已删除']::text[]
              or new.accepts_relocation
              or new.accepted_position_levels <>
                 array['county_or_below']::text[]
              or new.input_fingerprint <> repeat('0', 64)
              or new.erased_at is null then
              raise exception using errcode = '23514',
                message = 'invalid position preference erasure mutation';
            end if;
            return new;
          end if;
          raise exception using errcode = '23514',
            message = 'position preference history is append-only';
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger position_preference_versions_append_only
        before update or delete on learner.position_preference_versions
        for each row execute function
          learner.enforce_position_preference_immutability()
        """
    )


def downgrade() -> None:
    op.execute(
        "drop trigger if exists position_preference_versions_append_only "
        "on learner.position_preference_versions"
    )
    op.drop_index(
        "learner_position_preference_current_idx",
        table_name="position_preference_versions",
        schema="learner",
    )
    op.drop_table("position_preference_versions", schema="learner")
    op.execute(
        "drop function if exists "
        "learner.enforce_position_preference_immutability()"
    )
