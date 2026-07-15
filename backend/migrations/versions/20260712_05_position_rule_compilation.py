"""Add auditable rule compilation and generic manual-review facts.

Revision ID: 20260712_05
Revises: 20260712_04
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_05"
down_revision: str | Sequence[str] | None = "20260712_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FACT_KEYS = (
    "'education', 'degree', 'major', 'fresh_graduate', 'political_status', "
    "'grassroots_years', 'service_project', 'hukou', 'age', 'certifications', "
    "'other_conditions'"
)


def upgrade() -> None:
    op.drop_constraint(
        "position_requirements_fact_key_check",
        "position_requirements",
        schema="product",
        type_="check",
    )
    op.create_check_constraint(
        "position_requirements_fact_key_check",
        "position_requirements",
        f"fact_key in ({FACT_KEYS})",
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
        f"fact_key in ({FACT_KEYS})",
        schema="learner",
    )

    op.create_table(
        "position_rule_compilations",
        sa.Column("compilation_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("position_data_version", sa.Text(), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requirements_count", sa.Integer(), nullable=False),
        sa.Column("manual_review_count", sa.Integer(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('deterministic', 'needs_confirmation', 'no_conditions')",
            name="position_rule_compilations_status_check",
        ),
        sa.CheckConstraint(
            "requirements_count >= 0 and manual_review_count >= 0 "
            "and manual_review_count <= requirements_count",
            name="position_rule_compilations_counts_check",
        ),
        sa.CheckConstraint(
            "char_length(source_fingerprint) = 64",
            name="position_rule_compilations_fingerprint_check",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["product.positions.position_id"]),
        sa.PrimaryKeyConstraint("compilation_id"),
        sa.UniqueConstraint(
            "position_id",
            "rule_set_version",
            "position_data_version",
            name="position_rule_compilations_input_version_key",
        ),
        schema="product",
    )
    op.create_index(
        "product_position_rule_compilations_release_idx",
        "position_rule_compilations",
        ["rule_set_version", "position_data_version", "status"],
        schema="product",
    )


def downgrade() -> None:
    op.drop_index(
        "product_position_rule_compilations_release_idx",
        table_name="position_rule_compilations",
        schema="product",
    )
    op.drop_table("position_rule_compilations", schema="product")
    op.drop_constraint(
        "qualification_facts_fact_key_check",
        "qualification_facts",
        schema="learner",
        type_="check",
    )
    op.create_check_constraint(
        "qualification_facts_fact_key_check",
        "qualification_facts",
        "fact_key in ('education', 'degree', 'major', 'fresh_graduate', "
        "'political_status', 'grassroots_years', 'service_project', 'hukou', "
        "'age', 'certifications')",
        schema="learner",
    )
    op.drop_constraint(
        "position_requirements_fact_key_check",
        "position_requirements",
        schema="product",
        type_="check",
    )
    op.create_check_constraint(
        "position_requirements_fact_key_check",
        "position_requirements",
        "fact_key in ('education', 'degree', 'major', 'fresh_graduate', "
        "'political_status', 'grassroots_years', 'service_project', 'hukou', "
        "'age', 'certifications')",
        schema="product",
    )
