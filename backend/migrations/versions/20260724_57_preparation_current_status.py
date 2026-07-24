"""Persist the learner's current preparation status.

Revision ID: 20260724_57
Revises: 20260723_56
Create Date: 2026-07-24
"""

from alembic import op


revision = "20260724_57"
down_revision = "20260723_56"
branch_labels = None
depends_on = None


_TEST_STATEMENTS = (
    """
        alter table learner.preparation_experience_versions
          add column current_status text
    """,
    """
        alter table learner.preparation_experience_versions
          disable trigger preparation_experience_versions_append_only
    """,
    """
        update learner.preparation_experience_versions experience
        set current_status = case
          when profile.study_mode = 'fulltime' then 'fulltime'
          when profile.study_mode = 'parttime' then 'parttime'
          else 'unspecified'
        end
        from learner.profiles profile
        where profile.user_id = experience.user_id
    """,
    """
        update learner.preparation_experience_versions
        set current_status = 'unspecified'
        where current_status is null
    """,
    """
        alter table learner.preparation_experience_versions
          enable trigger preparation_experience_versions_append_only
    """,
    """
        alter table learner.preparation_experience_versions
          alter column current_status set not null,
          add constraint preparation_experience_current_status_check
            check (current_status in ('parttime', 'fulltime', 'student', 'unspecified'))
    """,
    """
        create or replace function learner.enforce_preparation_experience_immutability()
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
              or new.current_status <> 'unspecified'
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
            select 1
            from learner.preparation_experience_versions
            where current_status <> 'unspecified'
          ) then
            raise exception
              'preparation current-status facts exist; destructive downgrade is forbidden';
          end if;
        end;
        $$
        """
    )
    op.execute(
        """
        alter table learner.preparation_experience_versions
          drop constraint preparation_experience_current_status_check,
          drop column current_status
        """
    )
    op.execute(
        """
        create or replace function learner.enforce_preparation_experience_immutability()
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
        """
    )
