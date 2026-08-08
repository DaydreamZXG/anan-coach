"""Retire the direct iLink transport worker identity.

Revision ID: 20260808_102
Revises: 20260808_101

OpenILink Hub owns message transport.  The old direct long-poll worker also
owned private-object deletion, so transfer that one durable responsibility to
the dedicated deletion worker without changing the existing heartbeat login.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260808_102"
down_revision: str | Sequence[str] | None = "20260808_101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNIT = "gongkao-dazi-ilink-worker"
OLD_ENTRYPOINT = "app.workers.ilink_channel"
NEW_UNIT = "gongkao-dazi-ilink-object-deletion-worker"
NEW_ENTRYPOINT = "app.workers.ilink_object_deletions"
DATABASE_PRINCIPAL = "gongkao_worker_heartbeat_ilink_login"


def upgrade() -> None:
    bind = op.get_bind()
    old_identity = (
        bind.execute(
            text(
                """
            select consumer_unit, consumer_entrypoint, queue_name
            from platform.background_job_consumer_registry
            where consumer_unit = :consumer_unit
            """
            ),
            {"consumer_unit": OLD_UNIT},
        )
        .mappings()
        .one_or_none()
    )
    if old_identity is None or dict(old_identity) != {
        "consumer_unit": OLD_UNIT,
        "consumer_entrypoint": OLD_ENTRYPOINT,
        "queue_name": "messaging.ilink_object_deletion_queue",
    }:
        raise RuntimeError("legacy direct iLink worker registry identity is not exact")
    if (
        bind.execute(
            text(
                """
            select count(*)
            from platform.background_job_consumer_registry
            where consumer_unit = :consumer_unit
               or consumer_entrypoint = :consumer_entrypoint
            """
            ),
            {"consumer_unit": NEW_UNIT, "consumer_entrypoint": NEW_ENTRYPOINT},
        ).scalar_one()
        != 0
    ):
        raise RuntimeError("replacement iLink deletion worker identity already exists")

    bind.exec_driver_sql(
        "drop trigger background_job_consumer_registry_immutable_row "
        "on platform.background_job_consumer_registry"
    )
    bind.exec_driver_sql(
        "drop trigger background_job_consumer_principal_immutable_row "
        "on platform.background_job_consumer_principal_registry"
    )
    bind.execute(
        text(
            """
            insert into platform.background_job_consumer_registry (
              consumer_unit, consumer_entrypoint, queue_name
            ) values (
              :consumer_unit, :consumer_entrypoint,
              'messaging.ilink_object_deletion_queue'
            )
            """
        ),
        {"consumer_unit": NEW_UNIT, "consumer_entrypoint": NEW_ENTRYPOINT},
    )
    updated = bind.execute(
        text(
            """
            update platform.background_job_consumer_principal_registry
               set consumer_unit = :new_unit,
                   consumer_entrypoint = :new_entrypoint
             where database_principal = :database_principal
               and consumer_unit = :old_unit
               and consumer_entrypoint = :old_entrypoint
            """
        ),
        {
            "new_unit": NEW_UNIT,
            "new_entrypoint": NEW_ENTRYPOINT,
            "database_principal": DATABASE_PRINCIPAL,
            "old_unit": OLD_UNIT,
            "old_entrypoint": OLD_ENTRYPOINT,
        },
    )
    if updated.rowcount != 1:
        raise RuntimeError("iLink heartbeat principal transfer was not exact")
    bind.execute(
        text(
            """
            delete from platform.background_job_consumer_heartbeats
            where consumer_unit = :old_unit
               or consumer_entrypoint = :old_entrypoint
            """
        ),
        {"old_unit": OLD_UNIT, "old_entrypoint": OLD_ENTRYPOINT},
    )
    deleted = bind.execute(
        text(
            """
            delete from platform.background_job_consumer_registry
            where consumer_unit = :old_unit
              and consumer_entrypoint = :old_entrypoint
            """
        ),
        {"old_unit": OLD_UNIT, "old_entrypoint": OLD_ENTRYPOINT},
    )
    if deleted.rowcount != 1:
        raise RuntimeError("legacy direct iLink worker registry cleanup was not exact")
    bind.exec_driver_sql("drop table messaging.ilink_worker_cursors")
    bind.exec_driver_sql(
        """
        create trigger background_job_consumer_registry_immutable_row
        before update or delete
          on platform.background_job_consumer_registry
        for each row execute function
          platform.reject_background_job_registry_mutation()
        """
    )
    bind.exec_driver_sql(
        """
        create trigger background_job_consumer_principal_immutable_row
        before update or delete
          on platform.background_job_consumer_principal_registry
        for each row execute function
          platform.reject_background_job_registry_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("20260808_102 is forward-only")
