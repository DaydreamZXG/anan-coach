"""Persist gender as a versioned qualification fact.

Revision ID: 20260802_94
Revises: 20260802_93
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260802_94"
down_revision: str | Sequence[str] | None = "20260802_93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FACT_KEYS = (
    "'education', 'degree', 'major', 'graduation_year', 'fresh_graduate', "
    "'gender', 'political_status', 'work_years', 'grassroots_years', "
    "'service_project', 'hukou', 'age', 'certifications', 'other_conditions'"
)


def upgrade() -> None:
    for schema, table, constraint in (
        ("product", "position_requirements", "position_requirements_fact_key_check"),
        ("learner", "qualification_facts", "qualification_facts_fact_key_check"),
    ):
        op.drop_constraint(constraint, table, schema=schema, type_="check")
        op.create_check_constraint(
            constraint,
            table,
            f"fact_key in ({FACT_KEYS})",
            schema=schema,
        )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260802_94 is forward-only: stored gender facts and compiled "
        "position requirements must remain readable"
    )
