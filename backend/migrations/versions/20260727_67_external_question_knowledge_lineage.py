"""Freeze external-question knowledge bindings to their resolved release.

Revision ID: 20260727_67
Revises: 20260727_66
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260727_67"
down_revision: str | Sequence[str] | None = "20260727_66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    alter table learner.external_question_intakes
      add column if not exists knowledge_release_id uuid
    """,
    """
    alter table learner.external_wrong_questions
      add column if not exists knowledge_release_id uuid
    """,
    """
    with fixed_legacy_binding as (
      select
        intake.intake_id,
        release.release_id as knowledge_release_id
      from learner.external_question_intakes intake
      join product.knowledge_map_nodes node
        on node.node_id = intake.selected_node_id
       and node.exam_scope = 'guokao'
       and node.track = 'xingce'
      join product.knowledge_map_releases release
        on release.release_id = node.release_id
       and release.release_id = md5(
            'knowledge-map-legacy|' || node.exam_scope || '|' || node.track
           )::uuid
       and release.release_version =
           'knowledge-map@legacy-pre-20260727-65:'
           || node.exam_scope || ':' || node.track
       and release.source_data_version = 'legacy:source-version-unknown'
       and release.vocab_version = 'legacy:vocab-version-unknown'
       and release.generator_version = 'legacy:generator-version-unknown'
       and release.status = 'retired'
       and release.activated_at is null
       and release.retired_at is not null
      where intake.selected_node_id is not null
        and intake.created_at <= release.retired_at
    )
    update learner.external_question_intakes intake
    set knowledge_release_id = binding.knowledge_release_id
    from fixed_legacy_binding binding
    where intake.intake_id = binding.intake_id
      and intake.knowledge_release_id is null
    """,
    """
    do $migration$
    begin
      if exists (
        select 1
        from learner.external_question_intakes intake
        where not (
          (
            intake.status = 'rejected'
            and intake.knowledge_release_id is null
            and intake.selected_node_id is null
          )
          or (
            intake.status in ('pending_confirmation', 'confirmed')
            and intake.knowledge_release_id is not null
            and intake.selected_node_id is not null
          )
        )
      ) then
        raise exception using
          errcode = '23514',
          message =
            'external-question legacy knowledge binding is missing or ambiguous';
      end if;
    end
    $migration$
    """,
    """
    update learner.external_wrong_questions wrong
    set knowledge_release_id = intake.knowledge_release_id
    from learner.external_question_intakes intake
    where intake.intake_id = wrong.intake_id
      and intake.user_id = wrong.user_id
      and intake.status = 'confirmed'
      and intake.selected_node_id = wrong.node_id
      and intake.knowledge_release_id is not null
      and wrong.knowledge_release_id is null
    """,
    """
    do $migration$
    begin
      if exists (
        select 1
        from learner.external_wrong_questions wrong
        left join learner.external_question_intakes intake
          on intake.intake_id = wrong.intake_id
         and intake.user_id = wrong.user_id
         and intake.status = 'confirmed'
         and intake.knowledge_release_id = wrong.knowledge_release_id
         and intake.selected_node_id = wrong.node_id
        where wrong.knowledge_release_id is null
           or intake.intake_id is null
      ) then
        raise exception using
          errcode = '23514',
          message =
            'external wrong-question row lacks an exact confirmed intake binding';
      end if;
    end
    $migration$
    """,
    """
    do $migration$
    begin
      if not exists (
        select 1
        from pg_constraint
        where conrelid = 'learner.external_question_intakes'::regclass
          and conname = 'learner_external_intake_release_status_check'
      ) then
        alter table learner.external_question_intakes
          add constraint learner_external_intake_release_status_check
          check (
            (
              status = 'rejected'
              and knowledge_release_id is null
              and selected_node_id is null
            )
            or (
              status in ('pending_confirmation', 'confirmed')
              and knowledge_release_id is not null
              and selected_node_id is not null
            )
          );
      end if;
    end
    $migration$
    """,
    """
    do $migration$
    begin
      if not exists (
        select 1
        from pg_constraint
        where conrelid = 'learner.external_question_intakes'::regclass
          and conname = 'learner_external_intake_release_node_fkey'
      ) then
        alter table learner.external_question_intakes
          add constraint learner_external_intake_release_node_fkey
          foreign key (knowledge_release_id, selected_node_id)
          references product.knowledge_map_nodes(release_id, node_id)
          on delete restrict;
      end if;
    end
    $migration$
    """,
    """
    do $migration$
    begin
      if not exists (
        select 1
        from pg_constraint
        where conrelid = 'learner.external_question_intakes'::regclass
          and conname = 'learner_external_intake_binding_key'
      ) then
        alter table learner.external_question_intakes
          add constraint learner_external_intake_binding_key
          unique (
            intake_id,
            user_id,
            knowledge_release_id,
            selected_node_id
          );
      end if;
    end
    $migration$
    """,
    """
    alter table learner.external_wrong_questions
      alter column knowledge_release_id set not null
    """,
    """
    do $migration$
    begin
      if not exists (
        select 1
        from pg_constraint
        where conrelid = 'learner.external_wrong_questions'::regclass
          and conname = 'learner_external_wrong_intake_binding_fkey'
      ) then
        alter table learner.external_wrong_questions
          add constraint learner_external_wrong_intake_binding_fkey
          foreign key (
            intake_id,
            user_id,
            knowledge_release_id,
            node_id
          )
          references learner.external_question_intakes(
            intake_id,
            user_id,
            knowledge_release_id,
            selected_node_id
          )
          on delete restrict;
      end if;
    end
    $migration$
    """,
    """
    do $migration$
    begin
      if not exists (
        select 1
        from pg_constraint
        where conrelid = 'learner.external_wrong_questions'::regclass
          and conname = 'learner_external_wrong_release_node_fkey'
      ) then
        alter table learner.external_wrong_questions
          add constraint learner_external_wrong_release_node_fkey
          foreign key (knowledge_release_id, node_id)
          references product.knowledge_map_nodes(release_id, node_id)
          on delete restrict;
      end if;
    end
    $migration$
    """,
    """
    create or replace function learner.guard_external_intake_knowledge_binding()
    returns trigger
    language plpgsql
    as $$
    begin
      if (new.knowledge_release_id, new.selected_node_id)
         is distinct from
         (old.knowledge_release_id, old.selected_node_id) then
        raise exception using
          errcode = '23514',
          message = 'external-question resolved knowledge binding is immutable';
      end if;
      return new;
    end
    $$
    """,
    """
    do $migration$
    begin
      if not exists (
        select 1
        from pg_trigger
        where tgrelid = 'learner.external_question_intakes'::regclass
          and tgname = 'external_question_intake_binding_immutable'
          and not tgisinternal
      ) then
        create trigger external_question_intake_binding_immutable
          before update of knowledge_release_id, selected_node_id
          on learner.external_question_intakes
          for each row
          execute function learner.guard_external_intake_knowledge_binding();
      end if;
    end
    $migration$
    """,
    """
    create or replace function learner.guard_external_wrong_knowledge_binding()
    returns trigger
    language plpgsql
    as $$
    begin
      if (
        new.intake_id,
        new.user_id,
        new.knowledge_release_id,
        new.node_id
      ) is distinct from (
        old.intake_id,
        old.user_id,
        old.knowledge_release_id,
        old.node_id
      ) then
        raise exception using
          errcode = '23514',
          message = 'external wrong-question knowledge lineage is immutable';
      end if;
      return new;
    end
    $$
    """,
    """
    do $migration$
    begin
      if not exists (
        select 1
        from pg_trigger
        where tgrelid = 'learner.external_wrong_questions'::regclass
          and tgname = 'external_wrong_question_binding_immutable'
          and not tgisinternal
      ) then
        create trigger external_wrong_question_binding_immutable
          before update of intake_id, user_id, knowledge_release_id, node_id
          on learner.external_wrong_questions
          for each row
          execute function learner.guard_external_wrong_knowledge_binding();
      end if;
    end
    $migration$
    """,
    """
    create index if not exists learner_external_intake_release_node_idx
      on learner.external_question_intakes(
        knowledge_release_id,
        selected_node_id
      )
    """,
    """
    create index if not exists learner_external_wrong_release_node_idx
      on learner.external_wrong_questions(
        knowledge_release_id,
        node_id,
        created_at
      )
    """,
)


_DOWN_STATEMENTS = (
    """
    drop index learner.learner_external_wrong_release_node_idx
    """,
    """
    drop index learner.learner_external_intake_release_node_idx
    """,
    """
    drop trigger external_wrong_question_binding_immutable
      on learner.external_wrong_questions
    """,
    """
    drop function learner.guard_external_wrong_knowledge_binding()
    """,
    """
    drop trigger external_question_intake_binding_immutable
      on learner.external_question_intakes
    """,
    """
    drop function learner.guard_external_intake_knowledge_binding()
    """,
    """
    alter table learner.external_wrong_questions
      drop constraint learner_external_wrong_release_node_fkey
    """,
    """
    alter table learner.external_wrong_questions
      drop constraint learner_external_wrong_intake_binding_fkey
    """,
    """
    alter table learner.external_question_intakes
      drop constraint learner_external_intake_binding_key
    """,
    """
    alter table learner.external_question_intakes
      drop constraint learner_external_intake_release_node_fkey
    """,
    """
    alter table learner.external_question_intakes
      drop constraint learner_external_intake_release_status_check
    """,
    """
    alter table learner.external_wrong_questions
      drop column knowledge_release_id
    """,
    """
    alter table learner.external_question_intakes
      drop column knowledge_release_id
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    lineage_exists = bool(
        bind.execute(
            text(
                """
                select exists (
                  select 1
                  from learner.external_question_intakes
                  where knowledge_release_id is not null
                  union all
                  select 1
                  from learner.external_wrong_questions
                  where knowledge_release_id is not null
                )
                """
            )
        ).scalar_one()
    )
    if lineage_exists:
        raise RuntimeError(
            "External-question release lineage exists; destructive downgrade "
            "is forbidden. Repair with a forward migration."
        )
    for statement in _DOWN_STATEMENTS:
        op.execute(statement)
