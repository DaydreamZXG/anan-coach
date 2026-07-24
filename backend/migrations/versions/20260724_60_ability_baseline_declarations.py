"""Add versioned onboarding ability baseline declarations.

Revision ID: 20260724_60
Revises: 20260724_59
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_60"
down_revision = "20260724_59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ability_baseline_declaration_versions",
        sa.Column("baseline_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("score_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_baseline_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "version_no >= 1",
            name="ability_baseline_declarations_version_check",
        ),
        sa.CheckConstraint(
            "mode in ('unknown','reported_score')",
            name="ability_baseline_declarations_mode_check",
        ),
        sa.CheckConstraint(
            "(mode = 'unknown' and score_evidence_id is null) or "
            "(mode = 'reported_score' and score_evidence_id is not null)",
            name="ability_baseline_declarations_evidence_check",
        ),
        sa.CheckConstraint(
            "source_type in ('user_confirmed','user_revised')",
            name="ability_baseline_declarations_source_check",
        ),
        sa.CheckConstraint(
            "char_length(input_fingerprint) = 64",
            name="ability_baseline_declarations_fingerprint_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["learner.users.user_id"],
            name="ability_baseline_declarations_user_fk",
        ),
        sa.ForeignKeyConstraint(
            ["score_evidence_id"],
            ["learning.score_evidence.evidence_id"],
            name="ability_baseline_declarations_evidence_fk",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_baseline_version_id"],
            ["learner.ability_baseline_declaration_versions.baseline_version_id"],
            name="ability_baseline_declarations_supersedes_fk",
        ),
        sa.PrimaryKeyConstraint(
            "baseline_version_id",
            name="ability_baseline_declarations_pkey",
        ),
        sa.UniqueConstraint(
            "command_id",
            name="ability_baseline_declarations_command_key",
        ),
        sa.UniqueConstraint(
            "user_id",
            "version_no",
            name="ability_baseline_declarations_user_version_key",
        ),
        sa.UniqueConstraint(
            "supersedes_baseline_version_id",
            name="ability_baseline_declarations_supersedes_key",
        ),
        schema="learner",
    )
    op.create_index(
        "learner_ability_baseline_current_idx",
        "ability_baseline_declaration_versions",
        ["user_id", "version_no"],
        schema="learner",
    )
    op.execute(
        """
        create function learner.enforce_ability_baseline_immutability()
        returns trigger language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            raise exception using errcode = '23514',
              message = 'ability baseline history cannot be deleted';
          end if;
          if privacy.erasure_authorized(to_jsonb(old)) then
            if row(
              new.baseline_version_id, new.command_id, new.user_id,
              new.version_no, new.source_type,
              new.supersedes_baseline_version_id, new.created_at
            ) is distinct from row(
              old.baseline_version_id, old.command_id, old.user_id,
              old.version_no, old.source_type,
              old.supersedes_baseline_version_id, old.created_at
            ) or new.mode <> 'unknown'
              or new.score_evidence_id is not null
              or new.input_fingerprint <> repeat('0', 64)
              or new.erased_at is null then
              raise exception using errcode = '23514',
                message = 'invalid ability baseline erasure mutation';
            end if;
            return new;
          end if;
          raise exception using errcode = '23514',
            message = 'ability baseline history is append-only';
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger ability_baseline_declarations_append_only
        before update or delete
        on learner.ability_baseline_declaration_versions
        for each row execute function
          learner.enforce_ability_baseline_immutability()
        """
    )


def downgrade() -> None:
    op.execute(
        "drop trigger if exists ability_baseline_declarations_append_only "
        "on learner.ability_baseline_declaration_versions"
    )
    op.drop_index(
        "learner_ability_baseline_current_idx",
        table_name="ability_baseline_declaration_versions",
        schema="learner",
    )
    op.drop_table("ability_baseline_declaration_versions", schema="learner")
    op.execute(
        "drop function if exists "
        "learner.enforce_ability_baseline_immutability()"
    )
