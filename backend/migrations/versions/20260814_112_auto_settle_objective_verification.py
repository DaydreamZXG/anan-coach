"""Allow an objectively correct verification to settle without a fake reflection.

Revision ID: 20260814_112
Revises: 20260814_111
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260814_112"
down_revision: str | Sequence[str] | None = "20260814_111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        alter table learning.xingce_attempt_review_versions
          drop constraint xingce_attempt_review_versions_stage3_ai_payload_check
        """
    )
    op.execute(
        """
        alter table learning.xingce_attempt_review_versions
          add constraint xingce_attempt_review_versions_stage3_ai_payload_check check (
            ((revision = 1 and supersedes_review_version_id is null)
              or (revision > 1 and supersedes_review_version_id is not null))
            and ((state = 'reflection_due'
              and reflection_version_id is null
              and cause_candidate_version_id is null
              and cause_decision_version_id is null)
            or (state = 'cause_candidate_pending'
              and reflection_version_id is not null
              and cause_decision_version_id is null)
            or (state in (
                  'cause_followup_due', 'cause_decision_due',
                  'cause_evidence_insufficient', 'cause_agent_terminated'
                )
              and reflection_version_id is not null
              and cause_candidate_version_id is not null
              and cause_decision_version_id is null)
            or (state in ('immediate_verification_due', 'mastery_recorded')
              and reflection_version_id is not null
              and cause_candidate_version_id is not null
              and cause_decision_version_id is not null)
            or (state = 'mastery_recorded'
              and revision = 1
              and supersedes_review_version_id is null
              and requires_cause_decision = false
              and reflection_version_id is null
              and cause_candidate_version_id is null
              and cause_decision_version_id is null))
          )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "objective verification settlements without reflections are forward-only"
    )
