"""Add preparation-experience onboarding and move retake to learning ownership.

Revision ID: 20260718_40
Revises: 20260718_39
"""

from typing import Sequence

from alembic import op


revision: str = "20260718_40"
down_revision: str | Sequence[str] | None = "20260718_39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TEST_STATEMENTS: tuple[str, ...] = (
    "drop trigger onboarding_progress_enforce_transition on learner.onboarding_progress",
    "drop function learner.enforce_onboarding_progress_transition()",
    """
    create table learner.preparation_experience_versions (
      experience_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null references learner.users(user_id),
      version_no integer not null check (version_no >= 1),
      declaration_status text not null check (
        declaration_status in ('declared', 'legacy_not_collected')
      ),
      first_systematic boolean not null,
      external_learning boolean not null,
      prior_guokao_exam boolean not null,
      retake boolean not null,
      prior_exam_years jsonb not null check (jsonb_typeof(prior_exam_years) = 'array'),
      source_type text not null check (source_type in ('user_confirmed', 'user_revised', 'migration')),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      supersedes_experience_version_id uuid,
      created_at timestamptz not null default now(),
      erased_at timestamptz,
      unique (user_id, command_id),
      unique (user_id, version_no),
      unique (experience_version_id, user_id),
      foreign key (supersedes_experience_version_id, user_id)
        references learner.preparation_experience_versions(experience_version_id, user_id),
      check (
        (version_no = 1 and supersedes_experience_version_id is null)
        or (version_no > 1 and supersedes_experience_version_id is not null)
      ),
      check (
        (
          declaration_status = 'legacy_not_collected'
          and not first_systematic and not external_learning
          and not prior_guokao_exam and not retake
          and prior_exam_years = '[]'::jsonb
        )
        or (
          declaration_status = 'declared'
          and (
            (
              first_systematic and not external_learning
              and not prior_guokao_exam and not retake
            )
            or (
              not first_systematic
              and (external_learning or prior_guokao_exam or retake)
              and (not retake or prior_guokao_exam)
            )
          )
        )
      )
    )
    """,
    """
    create unique index learner_preparation_experience_one_successor_idx
      on learner.preparation_experience_versions(supersedes_experience_version_id)
      where supersedes_experience_version_id is not null
    """,
    """
    create function learner.enforce_preparation_experience_immutability()
    returns trigger language plpgsql as $$
    begin
      if tg_op = 'DELETE' then
        raise exception using errcode = '23514',
          message = 'preparation experience history cannot be deleted';
      end if;
      if privacy.erasure_authorized(to_jsonb(old)) then
        if row(
          new.experience_version_id, new.command_id, new.user_id,
          new.version_no, new.source_type,
          new.supersedes_experience_version_id, new.created_at
        ) is distinct from row(
          old.experience_version_id, old.command_id, old.user_id,
          old.version_no, old.source_type,
          old.supersedes_experience_version_id, old.created_at
        ) or new.declaration_status <> 'legacy_not_collected'
          or new.first_systematic or new.external_learning
          or new.prior_guokao_exam or new.retake
          or new.prior_exam_years <> '[]'::jsonb
          or new.input_fingerprint <> repeat('0', 64)
          or new.erased_at is null then
          raise exception using errcode = '23514',
            message = 'invalid preparation experience erasure mutation';
        end if;
        return new;
      end if;
      raise exception using errcode = '23514',
        message = 'preparation experience history is append-only';
    end;
    $$
    """,
    """
    create trigger preparation_experience_versions_append_only
      before update or delete on learner.preparation_experience_versions
      for each row execute function learner.enforce_preparation_experience_immutability()
    """,
    """
    alter table learner.onboarding_progress
      add column experience_ready boolean not null default false,
      add column experience_profile_version integer
    """,
    """
    insert into learner.preparation_experience_versions (
      experience_version_id, command_id, user_id, version_no,
      declaration_status, first_systematic, external_learning,
      prior_guokao_exam, retake, prior_exam_years, source_type,
      input_fingerprint, supersedes_experience_version_id, created_at
    )
    select
      gen_random_uuid(),
      'preparation-experience-migration-40:' || progress.user_id::text,
      progress.user_id, 1, 'legacy_not_collected', false, false, false,
      false, '[]'::jsonb, 'migration',
      encode(digest(
        convert_to(
          jsonb_build_object(
            'declarationStatus', 'legacy_not_collected',
            'userId', progress.user_id::text
          )::text,
          'UTF8'
        ),
        'sha256'
      ), 'hex'),
      null, coalesce(progress.completed_at, progress.updated_at)
    from learner.onboarding_progress progress
    where progress.qualification_ready
       or progress.study_preference_ready
       or progress.result_ready
       or progress.completed
    """,
    """
    update learner.onboarding_progress progress
    set experience_ready = true,
        experience_profile_version = experience.version_no
    from learner.preparation_experience_versions experience
    where experience.user_id = progress.user_id
      and experience.declaration_status = 'legacy_not_collected'
      and (
        progress.qualification_ready
        or progress.study_preference_ready
        or progress.result_ready
        or progress.completed
      )
    """,
    """
    alter table learner.onboarding_progress
      add constraint onboarding_progress_experience_check check (
        experience_ready = (experience_profile_version is not null)
        and (
          experience_profile_version is null
          or experience_profile_version >= 1
        )
      ),
      add constraint onboarding_progress_experience_owner_fk foreign key (
        user_id, experience_profile_version
      ) references learner.preparation_experience_versions(user_id, version_no)
    """,
    "alter table learner.onboarding_progress drop constraint onboarding_progress_result_ready_check",
    """
    alter table learner.onboarding_progress
      add constraint onboarding_progress_result_ready_check check (
        result_ready = (
          experience_ready and qualification_ready and study_preference_ready
        )
      )
    """,
    "alter table learner.onboarding_commands drop constraint onboarding_commands_type_check",
    """
    alter table learner.onboarding_commands
      add constraint onboarding_commands_type_check check (
        command_type in (
          'preparation_experience_recorded',
          'qualification_completed', 'qualification_invalidated',
          'study_preference_recorded', 'result_acknowledged'
        )
      )
    """,
    "alter table learner.onboarding_progress_events drop constraint onboarding_progress_events_type_check",
    """
    alter table learner.onboarding_progress_events
      add constraint onboarding_progress_events_type_check check (
        event_type in (
          'preparation_experience_recorded',
          'qualification_completed', 'qualification_invalidated',
          'study_preference_recorded', 'result_acknowledged'
        )
      )
    """,
    """
    create function learner.enforce_onboarding_progress_transition()
    returns trigger language plpgsql as $$
    begin
      if tg_op = 'DELETE' then
        raise exception using
          errcode = '23514', message = 'onboarding progress cannot be deleted';
      end if;
      if row(new.user_id, new.created_at) is distinct from
         row(old.user_id, old.created_at) then
        raise exception using
          errcode = '23514', message = 'onboarding progress identity is immutable';
      end if;
      if old.completed and row(
           new.progress_version, new.experience_ready,
           new.experience_profile_version, new.qualification_ready,
           new.qualification_profile_version, new.study_preference_ready,
           new.study_preference_version, new.result_ready,
           new.result_acknowledged, new.completed, new.completed_at
         ) is distinct from row(
           old.progress_version, old.experience_ready,
           old.experience_profile_version, old.qualification_ready,
           old.qualification_profile_version, old.study_preference_ready,
           old.study_preference_version, old.result_ready,
           old.result_acknowledged, old.completed, old.completed_at
         ) then
        raise exception using
          errcode = '23514', message = 'completed onboarding progress is immutable';
      end if;
      if new.progress_version <> old.progress_version + 1 then
        raise exception using
          errcode = '23514',
          message = 'onboarding progress version must advance exactly once';
      end if;
      if old.experience_ready and not new.experience_ready then
        raise exception using
          errcode = '23514',
          message = 'preparation experience readiness cannot be revoked';
      end if;
      if old.study_preference_ready and not new.study_preference_ready then
        raise exception using
          errcode = '23514',
          message = 'study preference readiness cannot be revoked';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger onboarding_progress_enforce_transition
      before update or delete on learner.onboarding_progress
      for each row execute function learner.enforce_onboarding_progress_transition()
    """,
    """
    do $$
    declare constraint_name text;
    begin
      select pc.conname into constraint_name
      from pg_constraint pc
      where pc.conrelid = 'learning.retake_program_versions'::regclass
        and pc.contype = 'c'
        and pg_get_constraintdef(pc.oid) like '%target_exam_year%source_exam_year%+ 1%';
      if constraint_name is null then
        raise exception 'retake immediate-next-year constraint was not found';
      end if;
      execute 'alter table learning.retake_program_versions drop constraint '
        || quote_ident(constraint_name);
    end;
    $$
    """,
    """
    alter table learning.retake_program_versions
      add constraint retake_program_target_after_source_check
      check (target_exam_year > source_exam_year)
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
            select 1 from learner.preparation_experience_versions
            where declaration_status = 'declared'
          ) or exists (
            select 1 from learning.retake_program_versions
            where target_exam_year <> source_exam_year + 1
          ) then
            raise exception
              'preparation or multi-year retake facts exist; destructive downgrade is forbidden';
          end if;
        end;
        $$
        """
    )
    op.execute(
        "alter table learning.retake_program_versions "
        "drop constraint retake_program_target_after_source_check"
    )
    op.execute(
        "alter table learning.retake_program_versions add constraint "
        "retake_program_target_exam_year_check "
        "check (target_exam_year = source_exam_year + 1)"
    )
    op.execute(
        "drop trigger onboarding_progress_enforce_transition "
        "on learner.onboarding_progress"
    )
    op.execute("drop function learner.enforce_onboarding_progress_transition()")
    op.execute(
        "alter table learner.onboarding_progress drop constraint "
        "onboarding_progress_result_ready_check"
    )
    op.execute(
        "alter table learner.onboarding_progress add constraint "
        "onboarding_progress_result_ready_check check "
        "(result_ready = (qualification_ready and study_preference_ready))"
    )
    op.execute(
        "alter table learner.onboarding_progress drop constraint "
        "onboarding_progress_experience_owner_fk"
    )
    op.execute(
        "alter table learner.onboarding_progress drop constraint "
        "onboarding_progress_experience_check"
    )
    op.execute(
        "alter table learner.onboarding_progress "
        "drop column experience_profile_version, drop column experience_ready"
    )
    op.execute(
        "alter table learner.onboarding_commands drop constraint "
        "onboarding_commands_type_check"
    )
    op.execute(
        "alter table learner.onboarding_commands add constraint "
        "onboarding_commands_type_check check (command_type in "
        "('qualification_completed', 'qualification_invalidated', "
        "'study_preference_recorded', 'result_acknowledged'))"
    )
    op.execute(
        "alter table learner.onboarding_progress_events drop constraint "
        "onboarding_progress_events_type_check"
    )
    op.execute(
        "alter table learner.onboarding_progress_events add constraint "
        "onboarding_progress_events_type_check check (event_type in "
        "('qualification_completed', 'qualification_invalidated', "
        "'study_preference_recorded', 'result_acknowledged'))"
    )
    op.execute(
        "drop trigger preparation_experience_versions_append_only "
        "on learner.preparation_experience_versions"
    )
    op.execute("drop table learner.preparation_experience_versions")
    op.execute("drop function learner.enforce_preparation_experience_immutability()")
    op.execute(
        """
        create function learner.enforce_onboarding_progress_transition()
        returns trigger language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            raise exception using
              errcode = '23514', message = 'onboarding progress cannot be deleted';
          end if;
          if row(new.user_id, new.created_at) is distinct from
             row(old.user_id, old.created_at) then
            raise exception using
              errcode = '23514', message = 'onboarding progress identity is immutable';
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
              errcode = '23514', message = 'completed onboarding progress is immutable';
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
