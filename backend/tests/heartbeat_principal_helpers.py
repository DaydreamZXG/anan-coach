from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.job_consumer_principals import (
    heartbeat_database_principal,
)
from app.platform.jobs import (
    BackgroundJobRepository,
    JobConsumerHeartbeat,
)


def record_heartbeat_as_principal(
    session: Session,
    *,
    consumer_unit: str,
    consumer_entrypoint: str,
    process_instance_id: UUID,
) -> JobConsumerHeartbeat:
    principal = heartbeat_database_principal(
        consumer_unit=consumer_unit,
        consumer_entrypoint=consumer_entrypoint,
    )
    session.execute(text(f"set session authorization {principal}"))
    try:
        heartbeat = BackgroundJobRepository().record_consumer_heartbeat(
            session,
            consumer_unit=consumer_unit,
            consumer_entrypoint=consumer_entrypoint,
            process_instance_id=process_instance_id,
        )
        session.execute(text("reset session authorization"))
        return heartbeat
    except Exception:
        session.rollback()
        session.execute(text("reset session authorization"))
        session.commit()
        raise


def principal_session_factory(
    base_factory: sessionmaker[Session],
    *,
    consumer_unit: str,
    consumer_entrypoint: str,
) -> Callable[[], AbstractContextManager[Session]]:
    principal = heartbeat_database_principal(
        consumer_unit=consumer_unit,
        consumer_entrypoint=consumer_entrypoint,
    )

    @contextmanager
    def factory() -> Iterator[Session]:
        with base_factory() as session:
            session.execute(text(f"set session authorization {principal}"))
            try:
                yield session
            finally:
                session.rollback()
                session.execute(text("reset session authorization"))
                session.commit()

    return factory
