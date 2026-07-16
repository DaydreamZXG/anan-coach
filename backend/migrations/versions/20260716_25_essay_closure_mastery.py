"""Add canonical essay closure writers and mastery state versions.

Revision ID: 20260716_25
Revises: 20260716_24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_25"
down_revision: str | Sequence[str] | None = "20260716_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_closure_records(bind):
        raise RuntimeError(
            "Essay closure or mastery records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.execute(
        "drop trigger if exists essay_mastery_state_versions_append_only "
        "on learning.essay_mastery_state_versions"
    )
    op.drop_table("essay_mastery_state_versions", schema="learning")
    for table, constraints, columns in (
        (
            "essay_transfer_verification_versions",
            (
                "essay_transfer_v2_shape_check",
                "essay_transfer_source_rewrite_fk",
                "essay_transfer_review_fk",
                "essay_transfer_decision_artifact_fk",
                "essay_transfer_target_decision_artifact_fk",
            ),
            (
                "writer_policy_version",
                "writer_contract_version",
                "finding_decision_artifact_version_id",
                "transfer_finding_decision_artifact_version_id",
                "transfer_review_version_id",
                "source_rewrite_version_id",
            ),
        ),
        (
            "essay_rewrite_versions",
            (
                "essay_rewrite_v2_shape_check",
                "essay_rewrite_decision_artifact_fk",
            ),
            (
                "writer_policy_version",
                "writer_contract_version",
                "finding_decision_artifact_version_id",
            ),
        ),
    ):
        for constraint in constraints:
            op.drop_constraint(constraint, table, schema="learning", type_="foreignkey" if constraint.endswith("_fk") else "check")
        for column in columns:
            op.drop_column(table, column, schema="learning")


def _has_closure_records(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                select exists (
                  select 1 from learning.essay_mastery_state_versions
                  union all
                  select 1 from learning.essay_rewrite_versions
                    where writer_contract_version = 'essay-rewrite-writer@v2'
                  union all
                  select 1 from learning.essay_transfer_verification_versions
                    where writer_contract_version = 'essay-transfer-writer@v2'
                )
                """
            )
        ).scalar_one()
    )


_STATEMENTS = (
    """
    alter table learning.essay_rewrite_versions
      add column finding_decision_artifact_version_id uuid,
      add column writer_contract_version text,
      add column writer_policy_version text
    """,
    """
    alter table learning.essay_rewrite_versions
      add constraint essay_rewrite_decision_artifact_fk
      foreign key (finding_decision_artifact_version_id)
      references learning.essay_learning_artifact_versions(artifact_version_id)
    """,
    """
    alter table learning.essay_rewrite_versions
      add constraint essay_rewrite_v2_shape_check check (
        (writer_contract_version is null
          and writer_policy_version is null
          and finding_decision_artifact_version_id is null)
        or (writer_contract_version = 'essay-rewrite-writer@v2'
          and writer_policy_version = 'essay-mastery@v1'
          and finding_decision_artifact_version_id is not null)
      )
    """,
    """
    alter table learning.essay_transfer_verification_versions
      add column source_rewrite_version_id uuid,
      add column transfer_review_version_id uuid,
      add column finding_decision_artifact_version_id uuid,
      add column transfer_finding_decision_artifact_version_id uuid,
      add column writer_contract_version text,
      add column writer_policy_version text
    """,
    """
    alter table learning.essay_transfer_verification_versions
      add constraint essay_transfer_source_rewrite_fk
      foreign key (source_rewrite_version_id)
      references learning.essay_rewrite_versions(rewrite_version_id)
    """,
    """
    alter table learning.essay_transfer_verification_versions
      add constraint essay_transfer_review_fk
      foreign key (transfer_review_version_id)
      references learning.essay_review_versions(review_version_id)
    """,
    """
    alter table learning.essay_transfer_verification_versions
      add constraint essay_transfer_decision_artifact_fk
      foreign key (finding_decision_artifact_version_id)
      references learning.essay_learning_artifact_versions(artifact_version_id)
    """,
    """
    alter table learning.essay_transfer_verification_versions
      add constraint essay_transfer_target_decision_artifact_fk
      foreign key (transfer_finding_decision_artifact_version_id)
      references learning.essay_learning_artifact_versions(artifact_version_id)
    """,
    """
    alter table learning.essay_transfer_verification_versions
      add constraint essay_transfer_v2_shape_check check (
        (writer_contract_version is null
          and writer_policy_version is null
          and source_rewrite_version_id is null
          and transfer_review_version_id is null
          and finding_decision_artifact_version_id is null
          and transfer_finding_decision_artifact_version_id is null)
        or (writer_contract_version = 'essay-transfer-writer@v2'
          and writer_policy_version = 'essay-mastery@v1'
          and source_rewrite_version_id is not null
          and transfer_review_version_id is not null
          and finding_decision_artifact_version_id is not null
          and transfer_finding_decision_artifact_version_id is not null)
      )
    """,
    """
    create table learning.essay_mastery_state_versions (
      mastery_version_id uuid primary key,
      mastery_id uuid not null,
      command_id text not null unique,
      user_id uuid not null references learner.users(user_id),
      finding_id text not null,
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in (
        'rewrite_due', 'transfer_due', 'stable_mastered', 'withdrawn'
      )),
      due_at timestamptz,
      source_task_version_id uuid not null,
      source_attempt_id uuid not null,
      source_answer_version_id uuid not null,
      source_review_version_id uuid not null,
      finding_decision_artifact_version_id uuid not null,
      rewrite_version_id uuid,
      transfer_verification_version_id uuid,
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) >= 4
      ),
      policy_version text not null check (policy_version = 'essay-mastery@v1'),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      supersedes_mastery_version_id uuid,
      created_at timestamptz not null,
      unique (mastery_id, version_no),
      unique (user_id, source_review_version_id, finding_id, version_no),
      unique (mastery_version_id, mastery_id, user_id),
      foreign key (source_attempt_id, user_id, source_task_version_id)
        references learning.essay_attempts(attempt_id, user_id, task_version_id),
      foreign key (
        source_answer_version_id, source_attempt_id, user_id, source_task_version_id
      ) references learning.essay_answer_versions(
        answer_version_id, attempt_id, user_id, task_version_id
      ),
      foreign key (
        source_review_version_id, source_attempt_id, user_id, source_task_version_id
      ) references learning.essay_review_versions(
        review_version_id, attempt_id, user_id, task_version_id
      ),
      foreign key (finding_decision_artifact_version_id)
        references learning.essay_learning_artifact_versions(artifact_version_id),
      foreign key (rewrite_version_id)
        references learning.essay_rewrite_versions(rewrite_version_id),
      foreign key (transfer_verification_version_id)
        references learning.essay_transfer_verification_versions(verification_version_id),
      foreign key (supersedes_mastery_version_id, mastery_id, user_id)
        references learning.essay_mastery_state_versions(
          mastery_version_id, mastery_id, user_id
        ),
      check (
        (version_no = 1 and supersedes_mastery_version_id is null)
        or (version_no > 1 and supersedes_mastery_version_id is not null)
      ),
      check (
        (state = 'rewrite_due' and due_at is not null)
        or (state = 'transfer_due' and due_at is not null and rewrite_version_id is not null)
        or (state = 'stable_mastered' and due_at is null
          and rewrite_version_id is not null
          and transfer_verification_version_id is not null)
        or (state = 'withdrawn' and due_at is null)
      )
    )
    """,
    """
    create unique index learning_essay_mastery_one_successor_idx
      on learning.essay_mastery_state_versions(supersedes_mastery_version_id)
      where supersedes_mastery_version_id is not null
    """,
    """
    create index learning_essay_mastery_owner_due_idx
      on learning.essay_mastery_state_versions(user_id, due_at)
      where due_at is not null
    """,
    """
    create index learning_essay_mastery_decision_idx
      on learning.essay_mastery_state_versions(
        user_id, finding_decision_artifact_version_id, finding_id, version_no
      )
    """,
    """
    create trigger essay_mastery_state_versions_append_only
      before update or delete on learning.essay_mastery_state_versions
      for each row execute function learning.reject_essay_intelligence_mutation()
    """,
)
