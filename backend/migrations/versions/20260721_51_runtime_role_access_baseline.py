"""Repair runtime role privileges for production application schemas.

Revision ID: 20260721_51
Revises: 20260721_50
"""

from __future__ import annotations

from alembic import op


revision = "20260721_51"
down_revision = "20260721_50"
branch_labels = None
depends_on = None


APPLICATION_SCHEMAS = (
    "learner",
    "learning",
    "ai_audit",
    "product",
    "commerce",
    "messaging",
    "privacy",
    "platform",
)


def _role_exists(role_name: str) -> bool:
    return bool(
        op.get_bind()
        .exec_driver_sql(
            "select exists (select 1 from pg_roles where rolname = %(role_name)s)",
            {"role_name": role_name},
        )
        .scalar_one()
    )


def upgrade() -> None:
    if not _role_exists("gongkao_api"):
        return

    bind = op.get_bind()
    schemas = ", ".join(APPLICATION_SCHEMAS)
    bind.exec_driver_sql(f"grant usage on schema {schemas} to gongkao_api")
    for schema in APPLICATION_SCHEMAS:
        bind.exec_driver_sql(
            f"grant select, insert, update, delete on all tables in schema {schema} "
            "to gongkao_api"
        )
        bind.exec_driver_sql(
            f"grant usage, select, update on all sequences in schema {schema} "
            "to gongkao_api"
        )
        bind.exec_driver_sql(
            f"alter default privileges in schema {schema} "
            "grant select, insert, update, delete on tables to gongkao_api"
        )
        bind.exec_driver_sql(
            f"alter default privileges in schema {schema} "
            "grant usage, select, update on sequences to gongkao_api"
        )
        if _role_exists("gongkao_owner"):
            bind.exec_driver_sql(
                f"alter default privileges for role gongkao_owner in schema {schema} "
                "grant select, insert, update, delete on tables to gongkao_api"
            )
            bind.exec_driver_sql(
                f"alter default privileges for role gongkao_owner in schema {schema} "
                "grant usage, select, update on sequences to gongkao_api"
            )


def downgrade() -> None:
    # Runtime grants are a forward repair for production availability. Revoking
    # them during a downgrade would make older releases fail on the same tables.
    return
