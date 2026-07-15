"""Add the onboarding progress owner and versioned weekly study preferences.

Revision ID: 20260713_18
Revises: 20260713_17
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_18"
down_revision: str | Sequence[str] | None = "20260713_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_progress",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("progress_version", sa.BigInteger(), nullable=False),
        sa.Column("qualification_ready", sa.Boolean(), nullable=False),
        sa.Column("qualification_profile_version", sa.BigInteger(), nullable=True),
        sa.Column("study_preference_ready", sa.Boolean(), nullable=False),
        sa.Column("study_preference_version", sa.BigInteger(), nullable=True),
        sa.Column("result_ready", sa.Boolean(), nullable=False),
        sa.Column("result_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "progress_version >= 1",
            name="onboarding_progress_version_check",
        ),
        sa.CheckConstraint(
            "qualification_ready = (qualification_profile_version is not null) "
            "and (qualification_profile_version is null or qualification_profile_version >= 1)",
            name="onboarding_progress_qualification_check",
        ),
        sa.CheckConstraint(
            "study_preference_ready = (study_preference_version is not null) "
            "and (study_preference_version is null or study_preference_version >= 1)",
            name="onboarding_progress_preference_check",
        ),
        sa.CheckConstraint(
            "result_ready = (qualification_ready and study_preference_ready)",
            name="onboarding_progress_result_ready_check",
        ),
        sa.CheckConstraint(
            "result_acknowledged = completed",
            name="onboarding_progress_completion_check",
        ),
        sa.CheckConstraint(
            "not completed or (result_ready and completed_at is not null)",
            name="onboarding_progress_completed_at_check",
        ),
        sa.CheckConstraint(
            "completed or completed_at is null",
            name="onboarding_progress_open_completed_at_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["user_id", "study_preference_version"],
            [
                "learning.weekly_study_preference_versions.user_id",
                "learning.weekly_study_preference_versions.version_no",
            ],
            name="onboarding_progress_preference_owner_fk",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        schema="learner",
    )

    op.create_table(
        "onboarding_commands",
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "response_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "command_type in ('qualification_completed', 'qualification_invalidated', "
            "'study_preference_recorded', 'result_acknowledged')",
            name="onboarding_commands_type_check",
        ),
        sa.CheckConstraint(
            "char_length(command_id) between 8 and 260",
            name="onboarding_commands_id_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_payload) = 'object' "
            "and jsonb_typeof(response_payload) = 'object'",
            name="onboarding_commands_payload_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("command_id"),
        schema="learner",
    )
    op.create_index(
        "learner_onboarding_commands_user_created_idx",
        "onboarding_commands",
        ["user_id", "created_at"],
        schema="learner",
    )

    op.create_table(
        "onboarding_progress_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_progress_version", sa.BigInteger(), nullable=False),
        sa.Column("to_progress_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "state_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type in ('qualification_completed', 'qualification_invalidated', "
            "'study_preference_recorded', 'result_acknowledged')",
            name="onboarding_progress_events_type_check",
        ),
        sa.CheckConstraint(
            "from_progress_version >= 1 "
            "and to_progress_version = from_progress_version + 1",
            name="onboarding_progress_events_version_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(state_snapshot) = 'object'",
            name="onboarding_progress_events_snapshot_check",
        ),
        sa.ForeignKeyConstraint(
            ["command_id"],
            ["learner.onboarding_commands.command_id"],
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "user_id",
            "to_progress_version",
            name="onboarding_progress_events_user_version_key",
        ),
        sa.UniqueConstraint(
            "command_id",
            name="onboarding_progress_events_command_id_key",
        ),
        schema="learner",
    )

    op.execute(
        """
        create function learner.reject_onboarding_history_mutation()
        returns trigger language plpgsql as $$
        begin
          raise exception using
            errcode = '23514',
            message = 'onboarding history is append-only';
        end;
        $$
        """
    )
    for table in (
        "onboarding_commands",
        "onboarding_progress_events",
    ):
        op.execute(
            f"create trigger {table}_append_only "
            f"before update or delete on learner.{table} "
            "for each row execute function learner.reject_onboarding_history_mutation()"
        )

    # Migration 11 already established learning.weekly_study_preference_versions
    # as the planning owner. Seed only accounts that were already completed and
    # never acquired a planning preference; do not create a second schedule owner.
    op.execute(
        """
        insert into learning.weekly_study_preference_versions (
          preference_version_id, command_id, user_id, version_no,
          timezone, weekday_minutes, weekend_minutes, long_block_minutes,
          fragment_minutes, regular_rest_weekday, regular_mock_weekday,
          effective_from, source_type, confirmed, input_fingerprint,
          supersedes_preference_version_id, created_at
        )
        select
          gen_random_uuid(),
          'onboarding-migration-18:' || profile.user_id::text,
          profile.user_id,
          1,
          'Asia/Shanghai',
          profile.daily_minutes,
          profile.daily_minutes,
          least(profile.daily_minutes, 120),
          least(profile.daily_minutes, 25),
          7,
          6,
          profile.created_at::date,
          'user_confirmed',
          true,
          encode(digest(
            jsonb_build_object(
              'source', 'legacy_completed_profile',
              'dailyMinutes', profile.daily_minutes
            )::text,
            'sha256'
          ), 'hex'),
          null,
          profile.updated_at
        from learner.profiles profile
        where profile.onboarding_complete
          and profile.daily_minutes between 30 and 720
          and not exists (
            select 1
            from learning.weekly_study_preference_versions preference
            where preference.user_id = profile.user_id
          )
        """
    )

    # Existing completed accounts remain completed, but the legacy profile flag is
    # now only a projection of this owner. Incomplete accounts resume at A05/A06
    # only after explicit v2 commands establish the missing milestones.
    op.execute(
        """
        insert into learner.onboarding_progress (
          user_id, progress_version, qualification_ready,
          qualification_profile_version, study_preference_ready,
          study_preference_version, result_ready, result_acknowledged,
          completed, created_at, updated_at, completed_at
        )
        select
          profile.user_id,
          1,
          profile.onboarding_complete,
          case when profile.onboarding_complete then profile.profile_version else null end,
          (
            profile.onboarding_complete
            and exists (
              select 1
              from learning.weekly_study_preference_versions preference
              where preference.user_id = profile.user_id
                and preference.confirmed
                and not exists (
                  select 1
                  from learning.weekly_study_preference_versions successor
                  where successor.supersedes_preference_version_id =
                        preference.preference_version_id
                )
            )
          ),
          case when profile.onboarding_complete then (
            select max(preference.version_no)
            from learning.weekly_study_preference_versions preference
            where preference.user_id = profile.user_id
              and preference.confirmed
              and not exists (
                select 1
                from learning.weekly_study_preference_versions successor
                where successor.supersedes_preference_version_id =
                      preference.preference_version_id
              )
          ) else null end,
          (
            profile.onboarding_complete
            and exists (
              select 1
              from learning.weekly_study_preference_versions preference
              where preference.user_id = profile.user_id
                and preference.confirmed
                and not exists (
                  select 1
                  from learning.weekly_study_preference_versions successor
                  where successor.supersedes_preference_version_id =
                        preference.preference_version_id
                )
            )
          ),
          (
            profile.onboarding_complete
            and exists (
              select 1
              from learning.weekly_study_preference_versions preference
              where preference.user_id = profile.user_id
                and preference.confirmed
                and not exists (
                  select 1
                  from learning.weekly_study_preference_versions successor
                  where successor.supersedes_preference_version_id =
                        preference.preference_version_id
                )
            )
          ),
          (
            profile.onboarding_complete
            and exists (
              select 1
              from learning.weekly_study_preference_versions preference
              where preference.user_id = profile.user_id
                and preference.confirmed
                and not exists (
                  select 1
                  from learning.weekly_study_preference_versions successor
                  where successor.supersedes_preference_version_id =
                        preference.preference_version_id
                )
            )
          ),
          profile.created_at,
          profile.updated_at,
          case when profile.onboarding_complete and exists (
            select 1
            from learning.weekly_study_preference_versions preference
            where preference.user_id = profile.user_id
              and preference.confirmed
              and not exists (
                select 1
                from learning.weekly_study_preference_versions successor
                where successor.supersedes_preference_version_id =
                      preference.preference_version_id
              )
          ) then profile.updated_at else null end
        from learner.profiles profile
        """
    )
    op.execute(
        """
        update learner.profiles profile
        set onboarding_complete = progress.completed,
            updated_at = greatest(profile.updated_at, progress.updated_at)
        from learner.onboarding_progress progress
        where progress.user_id = profile.user_id
          and profile.onboarding_complete is distinct from progress.completed
        """
    )
    op.execute(
        """
        create function learner.enforce_onboarding_progress_transition()
        returns trigger language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            raise exception using
              errcode = '23514',
              message = 'onboarding progress cannot be deleted';
          end if;
          if row(new.user_id, new.created_at) is distinct from
             row(old.user_id, old.created_at) then
            raise exception using
              errcode = '23514',
              message = 'onboarding progress identity is immutable';
          end if;
          if old.completed and row(
               new.progress_version, new.qualification_ready,
               new.qualification_profile_version, new.study_preference_ready,
               new.study_preference_version, new.result_ready,
               new.result_acknowledged, new.completed, new.completed_at
             ) is distinct from row(
               old.progress_version, old.qualification_ready,
               old.qualification_profile_version, old.study_preference_ready,
               old.study_preference_version, old.result_ready,
               old.result_acknowledged, old.completed, old.completed_at
             ) then
            raise exception using
              errcode = '23514',
              message = 'completed onboarding progress is immutable';
          end if;
          if new.progress_version <> old.progress_version + 1 then
            raise exception using
              errcode = '23514',
              message = 'onboarding progress version must advance exactly once';
          end if;
          if old.study_preference_ready and not new.study_preference_ready then
            raise exception using
              errcode = '23514',
              message = 'study preference readiness cannot be revoked';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger onboarding_progress_enforce_transition "
        "before update or delete on learner.onboarding_progress "
        "for each row execute function "
        "learner.enforce_onboarding_progress_transition()"
    )
    op.execute(
        """
        create function learner.enforce_onboarding_profile_projection()
        returns trigger language plpgsql as $$
        declare
          owner_completed boolean;
        begin
          select progress.completed
            into owner_completed
            from learner.onboarding_progress progress
           where progress.user_id = new.user_id;
          if new.onboarding_complete is distinct from coalesce(owner_completed, false) then
            raise exception using
              errcode = '23514',
              message = 'profiles.onboarding_complete is an onboarding progress projection';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger onboarding_profile_enforce_projection "
        "before update of onboarding_complete on learner.profiles "
        "for each row execute function "
        "learner.enforce_onboarding_profile_projection()"
    )


def downgrade() -> None:
    op.execute(
        "drop trigger if exists onboarding_profile_enforce_projection "
        "on learner.profiles"
    )
    op.execute("drop function if exists learner.enforce_onboarding_profile_projection()")
    op.execute(
        "drop trigger if exists onboarding_progress_enforce_transition "
        "on learner.onboarding_progress"
    )
    op.execute("drop function if exists learner.enforce_onboarding_progress_transition()")
    for table in (
        "onboarding_progress_events",
        "onboarding_commands",
    ):
        op.execute(f"drop trigger if exists {table}_append_only on learner.{table}")
    op.execute("drop function if exists learner.reject_onboarding_history_mutation()")
    op.drop_table("onboarding_progress_events", schema="learner")
    op.drop_index(
        "learner_onboarding_commands_user_created_idx",
        table_name="onboarding_commands",
        schema="learner",
    )
    op.drop_table("onboarding_commands", schema="learner")
    op.drop_table("onboarding_progress", schema="learner")
