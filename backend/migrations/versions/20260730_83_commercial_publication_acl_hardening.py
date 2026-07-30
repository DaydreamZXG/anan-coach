"""Harden runtime access to commercial publication authority.

Revision ID: 20260730_83
Revises: 20260730_82
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260730_83"
down_revision: str | Sequence[str] | None = "20260730_82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PUBLICATION_TABLES = (
    "platform.commercial_data_publication_state",
    "platform.commercial_data_publication_transitions",
)
PUBLICATION_TRANSITION_SEQUENCE = (
    "platform.commercial_data_publication_transitions_transition_id_seq"
)


def _runtime_role_exists(bind: Connection) -> bool:
    return bool(
        bind.execute(
            text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
        ).scalar_one()
    )


def harden_runtime_publication_authority(
    bind: Connection | None = None,
) -> None:
    """Make the runtime role read-only even under legacy default grants.

    The writer GUC remains a trigger-side protocol assertion for the trusted
    migration credential. PostgreSQL custom GUCs are user-settable and are
    therefore never treated as an identity or authorization boundary.
    """

    connection = bind or op.get_bind()
    if not _runtime_role_exists(connection):
        return
    tables = ", ".join(PUBLICATION_TABLES)
    connection.exec_driver_sql(
        f"revoke insert, update, delete, truncate, references, trigger on {tables} from gongkao_api"
    )
    connection.exec_driver_sql(f"grant select on {tables} to gongkao_api")
    connection.exec_driver_sql(
        f"revoke all privileges on sequence {PUBLICATION_TRANSITION_SEQUENCE} from gongkao_api"
    )


def upgrade() -> None:
    harden_runtime_publication_authority()


def downgrade() -> None:
    raise RuntimeError(
        "commercial publication ACL hardening is forward-only; publish a forward migration"
    )
