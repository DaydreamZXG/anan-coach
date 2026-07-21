"""Separate ordinary work years from grassroots work years.

Revision ID: 20260721_53
Revises: 20260721_52
Create Date: 2026-07-21
"""

from alembic import op


revision = "20260721_53"
down_revision = "20260721_52"
branch_labels = None
depends_on = None


FACT_KEYS = (
    "'education', 'degree', 'major', 'fresh_graduate', 'political_status', "
    "'work_years', 'grassroots_years', 'service_project', 'hukou', 'age', "
    "'certifications', 'other_conditions'"
)

PREVIOUS_FACT_KEYS = (
    "'education', 'degree', 'major', 'fresh_graduate', 'political_status', "
    "'grassroots_years', 'service_project', 'hukou', 'age', 'certifications', "
    "'other_conditions'"
)


def upgrade() -> None:
    _replace_fact_key_constraint(FACT_KEYS)


def downgrade() -> None:
    _replace_fact_key_constraint(PREVIOUS_FACT_KEYS)


def _replace_fact_key_constraint(fact_keys: str) -> None:
    op.drop_constraint(
        "position_requirements_fact_key_check",
        "position_requirements",
        schema="product",
        type_="check",
    )
    op.create_check_constraint(
        "position_requirements_fact_key_check",
        "position_requirements",
        f"fact_key in ({fact_keys})",
        schema="product",
    )
    op.drop_constraint(
        "qualification_facts_fact_key_check",
        "qualification_facts",
        schema="learner",
        type_="check",
    )
    op.create_check_constraint(
        "qualification_facts_fact_key_check",
        "qualification_facts",
        f"fact_key in ({fact_keys})",
        schema="learner",
    )
