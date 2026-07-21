"""Grant the runtime role access to messaging-owned application data.

Revision ID: 20260721_48
Revises: 20260719_47
"""

from alembic import op
import sqlalchemy as sa


revision = "20260721_48"
down_revision = "20260719_47"
branch_labels = None
depends_on = None


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "select exists ("
                "select 1 from pg_roles where rolname = 'gongkao_api'"
                ")"
            )
        )
        .scalar_one()
    )


def upgrade() -> None:
    if not _runtime_role_exists():
        return
    bind = op.get_bind()
    bind.exec_driver_sql("grant usage on schema messaging to gongkao_api")
    bind.exec_driver_sql(
        "grant select, insert, update, delete on all tables "
        "in schema messaging to gongkao_api"
    )
    bind.exec_driver_sql(
        "grant usage, select, update on all sequences "
        "in schema messaging to gongkao_api"
    )
    bind.exec_driver_sql(
        "alter default privileges in schema messaging "
        "grant select, insert, update, delete on tables to gongkao_api"
    )
    bind.exec_driver_sql(
        "alter default privileges in schema messaging "
        "grant usage, select, update on sequences to gongkao_api"
    )


def downgrade() -> None:
    if not _runtime_role_exists():
        return
    bind = op.get_bind()
    bind.exec_driver_sql(
        "alter default privileges in schema messaging "
        "revoke select, insert, update, delete on tables from gongkao_api"
    )
    bind.exec_driver_sql(
        "alter default privileges in schema messaging "
        "revoke usage, select, update on sequences from gongkao_api"
    )
    bind.exec_driver_sql(
        "revoke select, insert, update, delete on all tables "
        "in schema messaging from gongkao_api"
    )
    bind.exec_driver_sql(
        "revoke usage, select, update on all sequences "
        "in schema messaging from gongkao_api"
    )
    bind.exec_driver_sql("revoke usage on schema messaging from gongkao_api")
