#!/usr/bin/env python3
"""Own the fail-closed commercial data publication database protocol.

This helper is the only supported writer for the singleton publication state.
For a publication it acquires the PostgreSQL exclusive advisory lock, duplicates
that live database session onto a fixed inheritable file descriptor, and then
``exec`` replaces the lock acquirer with the mutation controller.  The kernel
capability therefore crosses every controlled subprocess without a check/use
window: killing the controller cannot leave a separate parent free to start the
next mutation, and an already-running child keeps the lock until that mutation
ends.  The durable state remains ``publishing`` until an exact signed receipt is
recorded and explicitly activated.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
from typing import Any, Callable, Iterator, Sequence
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from data_release_authority import (
    DataReleaseAuthorityError,
    _validate_database_url,
    verify_bootstrap_authority_payload,
)


REQUEST_SCHEMA_VERSION = "commercial-data-publication-request@v1"
# "GKDATA P1" encoded as one fixed positive int8. This exact value is mirrored
# by app.db.session and is a release protocol constant, not a per-environment
# configuration knob.
COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY = 0x474B444154415001
# fd 9 is the root-owned filesystem coordination lock.  fd 8 is reserved for
# the PostgreSQL session that owns the publication advisory lock; sudo is
# invoked with ``-C 10`` so both capabilities survive every publication child.
COMMERCIAL_DATA_PUBLICATION_FENCE_FD = 8

SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
SAFE_ENVIRONMENT = re.compile(r"^[A-Za-z0-9._-]+$")
STATE_TABLE = "platform.commercial_data_publication_state"
TRANSITION_TABLE = "platform.commercial_data_publication_transitions"


class CommercialDataPublicationError(RuntimeError):
    """Raised when publication cannot proceed without changing authority."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def request_digest(request: dict[str, Any]) -> str:
    validate_request(request)
    return sha256(canonical_json(request).encode("utf-8")).hexdigest()


def publication_digest_for(
    *,
    request_digest_value: str,
    receipt_sha256: str,
) -> str:
    _require_sha256(request_digest_value, "request")
    _require_sha256(receipt_sha256, "publication receipt")
    return sha256(
        canonical_json(
            {
                "schemaVersion": "commercial-data-publication@v1",
                "requestDigest": request_digest_value,
                "activeReceiptSha256": receipt_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()


def build_request(
    *,
    candidate_commit: str,
    rollback_commit: str,
    environment: str,
    expected_revision: str,
    position_exam_code: str,
    position_exam_year: int,
    position_rule_set_version: str,
    position_data_version: str,
    position_window_manifest_sha256: str,
    knowledge_exam_scope: str,
    knowledge_track: str,
    knowledge_release_version: str,
    knowledge_source_data_version: str,
    knowledge_fresh_until: str,
) -> dict[str, Any]:
    request = {
        "schemaVersion": REQUEST_SCHEMA_VERSION,
        "candidateApplicationCommit": candidate_commit,
        "rollbackApplicationCommit": rollback_commit,
        "deploymentEnvironment": environment,
        "expectedDatabaseRevision": expected_revision,
        "position": {
            "examCode": position_exam_code,
            "examYear": position_exam_year,
            "ruleSetVersion": position_rule_set_version,
            "positionDataVersion": position_data_version,
            "windowManifestSha256": position_window_manifest_sha256,
        },
        "knowledge": {
            "examScope": knowledge_exam_scope,
            "track": knowledge_track,
            "releaseVersion": knowledge_release_version,
            "sourceDataVersion": knowledge_source_data_version,
            "freshUntil": knowledge_fresh_until,
        },
    }
    validate_request(request)
    return request


def validate_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict) or set(request) != {
        "schemaVersion",
        "candidateApplicationCommit",
        "rollbackApplicationCommit",
        "deploymentEnvironment",
        "expectedDatabaseRevision",
        "position",
        "knowledge",
    }:
        raise CommercialDataPublicationError(
            "commercial data publication request has an invalid schema"
        )
    candidate = request["candidateApplicationCommit"]
    rollback = request["rollbackApplicationCommit"]
    if (
        request["schemaVersion"] != REQUEST_SCHEMA_VERSION
        or not isinstance(candidate, str)
        or not COMMIT.fullmatch(candidate)
        or not isinstance(rollback, str)
        or not COMMIT.fullmatch(rollback)
        or candidate == rollback
    ):
        raise CommercialDataPublicationError(
            "commercial data publication commits are invalid"
        )
    environment = request["deploymentEnvironment"]
    revision = request["expectedDatabaseRevision"]
    if (
        not isinstance(environment, str)
        or not SAFE_ENVIRONMENT.fullmatch(environment)
        or not _bounded_text(revision)
    ):
        raise CommercialDataPublicationError(
            "commercial data publication environment or revision is invalid"
        )

    position = request["position"]
    if not isinstance(position, dict) or set(position) != {
        "examCode",
        "examYear",
        "ruleSetVersion",
        "positionDataVersion",
        "windowManifestSha256",
    }:
        raise CommercialDataPublicationError(
            "commercial data publication position request is invalid"
        )
    if (
        not _bounded_text(position["examCode"])
        or not isinstance(position["examYear"], int)
        or isinstance(position["examYear"], bool)
        or not 2000 <= position["examYear"] <= 2100
        or not _bounded_text(position["ruleSetVersion"])
        or not _bounded_text(position["positionDataVersion"])
        or not isinstance(position["windowManifestSha256"], str)
        or not SHA256.fullmatch(position["windowManifestSha256"])
    ):
        raise CommercialDataPublicationError(
            "commercial data publication position inputs are invalid"
        )

    knowledge = request["knowledge"]
    if not isinstance(knowledge, dict) or set(knowledge) != {
        "examScope",
        "track",
        "releaseVersion",
        "sourceDataVersion",
        "freshUntil",
    }:
        raise CommercialDataPublicationError(
            "commercial data publication knowledge request is invalid"
        )
    if not all(
        _bounded_text(knowledge[field])
        for field in (
            "examScope",
            "track",
            "releaseVersion",
            "sourceDataVersion",
            "freshUntil",
        )
    ):
        raise CommercialDataPublicationError(
            "commercial data publication knowledge inputs are invalid"
        )
    try:
        fresh_until = datetime.fromisoformat(knowledge["freshUntil"])
    except ValueError as exc:
        raise CommercialDataPublicationError(
            "knowledge freshness must be an ISO-8601 timestamp"
        ) from exc
    if fresh_until.tzinfo is None:
        raise CommercialDataPublicationError(
            "knowledge freshness timestamp must include a timezone"
        )


def _bounded_text(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value.strip()) <= 200


def load_request(path: Path) -> dict[str, Any]:
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommercialDataPublicationError(
            "commercial data publication request is unreadable"
        ) from exc
    validate_request(request)
    return request


def normalize_database_url(database_url: str) -> str:
    value = database_url.strip()
    if value.startswith("postgresql+psycopg:"):
        value = value.replace("postgresql+psycopg:", "postgresql:", 1)
    _validate_database_url(value)
    return value


def database_url_from_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise CommercialDataPublicationError(
            f"{name} must contain the publication database authority"
        )
    return normalize_database_url(value)


def bootstrap_upgrade_and_begin(
    *,
    database_url: str,
    request: dict[str, Any],
    bootstrap_receipt_path: Path,
    bootstrap_receipt_sha256: str,
    backend_root: Path,
    after_upgrade_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Atomically verify the stopped legacy DB, migrate, and persist publishing.

    A process death at any point before commit rolls back both Alembic DDL and
    the publication row. The optional hook exists solely so the real
    PostgreSQL SIGKILL regression can stop a subprocess in the historical
    upgrade-before-begin window.
    """

    database_url = normalize_database_url(database_url)
    validate_request(request)
    _require_sha256(bootstrap_receipt_sha256, "bootstrap receipt")
    resolved_receipt = bootstrap_receipt_path.resolve()
    try:
        encoded_receipt = resolved_receipt.read_bytes()
        bootstrap_receipt = json.loads(encoded_receipt)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommercialDataPublicationError(
            "bootstrap baseline receipt is unreadable"
        ) from exc
    actual_receipt_sha256 = sha256(encoded_receipt).hexdigest()
    if actual_receipt_sha256 != bootstrap_receipt_sha256:
        raise CommercialDataPublicationError(
            "bootstrap baseline receipt digest changed"
        )
    if not isinstance(bootstrap_receipt, dict):
        raise CommercialDataPublicationError(
            "bootstrap baseline receipt authority is invalid"
        )
    baseline_revision = str(
        bootstrap_receipt.get("database", {}).get("alembicRevision", "")
    )
    baseline_commit = str(bootstrap_receipt.get("applicationCommit", ""))
    if baseline_commit != request["rollbackApplicationCommit"]:
        raise CommercialDataPublicationError(
            "bootstrap baseline is not bound to the current application commit"
        )
    if bootstrap_receipt.get("environment") != request["deploymentEnvironment"]:
        raise CommercialDataPublicationError(
            "bootstrap baseline is not bound to the deployment environment"
        )
    alembic_config_path = backend_root.resolve() / "alembic.ini"
    if not alembic_config_path.is_file():
        raise CommercialDataPublicationError(
            "bootstrap Alembic configuration is unavailable"
        )

    sqlalchemy_url = database_url.replace(
        "postgresql:",
        "postgresql+psycopg:",
        1,
    )
    engine = create_engine(
        sqlalchemy_url,
        poolclass=NullPool,
        future=True,
    )
    request_digest_value = request_digest(request)
    encoded_request = canonical_json(request)
    encoded_commits = canonical_json(
        [
            request["candidateApplicationCommit"],
            request["rollbackApplicationCommit"],
        ]
    )
    publication_id = uuid4()
    try:
        with engine.connect() as raw_connection:
            connection = raw_connection.execution_options(
                isolation_level="REPEATABLE READ"
            )
            with connection.begin():
                connection.exec_driver_sql(
                    "select pg_advisory_xact_lock(%s)",
                    (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
                )
                verify_bootstrap_authority_payload(
                    connection,
                    expected=bootstrap_receipt,
                    environment=request["deploymentEnvironment"],
                    expected_revision=baseline_revision,
                    application_commit=baseline_commit,
                )

                config = Config(str(alembic_config_path))
                config.attributes["connection"] = connection
                command.upgrade(
                    config,
                    request["expectedDatabaseRevision"],
                )
                revisions = [
                    str(row[0])
                    for row in connection.exec_driver_sql(
                        "select version_num from public.alembic_version "
                        "order by version_num"
                    ).all()
                ]
                if revisions != [request["expectedDatabaseRevision"]]:
                    raise CommercialDataPublicationError(
                        "bootstrap migration did not reach the requested revision"
                    )
                if after_upgrade_hook is not None:
                    after_upgrade_hook()

                connection.exec_driver_sql(
                    "select set_config(%s, %s, true)",
                    (
                        "gongkao.commercial_data_publication_writer",
                        "publisher-v1",
                    ),
                )
                existing = connection.exec_driver_sql(
                    f"select count(*) from {STATE_TABLE}"
                ).scalar_one()
                if existing != 0:
                    raise CommercialDataPublicationError(
                        "bootstrap publication state is no longer empty"
                    )
                row = (
                    connection.exec_driver_sql(
                        f"""
                    insert into {STATE_TABLE} (
                      singleton_key,
                      publication_id,
                      state,
                      request_digest,
                      request_payload,
                      publication_digest,
                      previous_receipt_sha256,
                      active_receipt_sha256,
                      expected_database_revision,
                      compatible_application_commits,
                      publication_started_at,
                      state_changed_at,
                      activated_at
                    ) values (
                      true,
                      %(publication_id)s,
                      'publishing',
                      %(request_digest)s,
                      %(request_payload)s::jsonb,
                      null,
                      %(previous_receipt)s,
                      %(previous_receipt)s,
                      %(expected_revision)s,
                      %(compatible_commits)s::jsonb,
                      transaction_timestamp(),
                      transaction_timestamp(),
                      null
                    )
                    returning *
                    """,
                        {
                            "publication_id": publication_id,
                            "request_digest": request_digest_value,
                            "request_payload": encoded_request,
                            "previous_receipt": bootstrap_receipt_sha256,
                            "expected_revision": request["expectedDatabaseRevision"],
                            "compatible_commits": encoded_commits,
                        },
                    )
                    .mappings()
                    .one()
                )
                connection.exec_driver_sql(
                    f"""
                    insert into {TRANSITION_TABLE} (
                      publication_id,
                      event_kind,
                      from_state,
                      to_state,
                      request_digest,
                      request_payload,
                      publication_digest,
                      previous_receipt_sha256,
                      active_receipt_sha256,
                      expected_database_revision,
                      compatible_application_commits,
                      occurred_at
                    ) values (
                      %(publication_id)s,
                      'publication_started',
                      null,
                      'publishing',
                      %(request_digest)s,
                      %(request_payload)s::jsonb,
                      null,
                      %(previous_receipt)s,
                      %(previous_receipt)s,
                      %(expected_revision)s,
                      %(compatible_commits)s::jsonb,
                      transaction_timestamp()
                    )
                    """,
                    {
                        "publication_id": publication_id,
                        "request_digest": request_digest_value,
                        "request_payload": encoded_request,
                        "previous_receipt": bootstrap_receipt_sha256,
                        "expected_revision": request["expectedDatabaseRevision"],
                        "compatible_commits": encoded_commits,
                    },
                )
                result = _validate_state_row(dict(row))
                result["action"] = "bootstrap_started"
        return result
    except (
        DataReleaseAuthorityError,
        SQLAlchemyError,
        CommandError,
    ) as exc:
        raise CommercialDataPublicationError(str(exc)) from exc
    finally:
        engine.dispose()


def inspect_publication_state(*, database_url: str) -> dict[str, Any]:
    database_url = normalize_database_url(database_url)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        revisions = _database_revisions(connection)
        relation = connection.execute(
            "select to_regclass(%s)::text as relation",
            (STATE_TABLE,),
        ).fetchone()
        if relation is None or relation["relation"] is None:
            return {
                "authorityStatus": "uninitialized",
                "databaseRevisions": revisions,
            }
        rows = list(
            connection.execute(
                f"select * from {STATE_TABLE} order by singleton_key"
            ).fetchall()
        )
    if not rows:
        return {
            "authorityStatus": "uninitialized",
            "databaseRevisions": revisions,
        }
    if len(rows) != 1:
        raise CommercialDataPublicationError(
            "commercial data publication authority is not a singleton"
        )
    state = _validate_state_row(rows[0])
    state["authorityStatus"] = "initialized"
    state["databaseRevisions"] = revisions
    return state


def begin_publication(
    *,
    database_url: str,
    request: dict[str, Any],
    previous_receipt_sha256: str,
) -> dict[str, Any]:
    database_url = normalize_database_url(database_url)
    digest = request_digest(request)
    _require_sha256(previous_receipt_sha256, "previous receipt")
    commits = [
        request["candidateApplicationCommit"],
        request["rollbackApplicationCommit"],
    ]
    encoded_request = canonical_json(request)
    encoded_commits = canonical_json(commits)
    with _writer_connection(database_url) as connection:
        rows = list(
            connection.execute(
                f"select * from {STATE_TABLE} where singleton_key = true for update"
            ).fetchall()
        )
        if len(rows) > 1:
            raise CommercialDataPublicationError(
                "commercial data publication authority is not a singleton"
            )
        if not rows:
            publication_id = uuid4()
            row = connection.execute(
                f"""
                insert into {STATE_TABLE} (
                  singleton_key,
                  publication_id,
                  state,
                  request_digest,
                  request_payload,
                  publication_digest,
                  previous_receipt_sha256,
                  active_receipt_sha256,
                  expected_database_revision,
                  compatible_application_commits,
                  publication_started_at,
                  state_changed_at,
                  activated_at
                ) values (
                  true,
                  %s,
                  'publishing',
                  %s,
                  %s::jsonb,
                  null,
                  %s,
                  %s,
                  %s,
                  %s::jsonb,
                  transaction_timestamp(),
                  transaction_timestamp(),
                  null
                )
                returning *
                """,
                (
                    publication_id,
                    digest,
                    encoded_request,
                    previous_receipt_sha256,
                    previous_receipt_sha256,
                    request["expectedDatabaseRevision"],
                    encoded_commits,
                ),
            ).fetchone()
            if row is None:
                raise CommercialDataPublicationError(
                    "commercial data publication could not be started"
                )
            _append_transition(
                connection,
                row=row,
                event_kind="publication_started",
                from_state=None,
            )
            result = _validate_state_row(row)
            result["action"] = "started"
            return result

        current = rows[0]
        state = _validate_state_row(current)
        _require_exact_request(
            state,
            request=request,
            digest=digest,
        )
        if state["state"] == "publishing":
            if state["previousReceiptSha256"] != previous_receipt_sha256:
                raise CommercialDataPublicationError(
                    "publishing request does not match its previous receipt"
                )
            _append_transition(
                connection,
                row=current,
                event_kind="publication_resumed",
                from_state="publishing",
            )
            state["action"] = "resume"
            return state

        if state["activeReceiptSha256"] != previous_receipt_sha256:
            raise CommercialDataPublicationError(
                "active publication does not match the current signed receipt"
            )
        state["action"] = "already_active"
        return state


def start_next_publication(
    *,
    database_url: str,
    request: dict[str, Any],
    previous_receipt_sha256: str,
) -> dict[str, Any]:
    """Start or resume, allowing a different request only from active.

    ``begin_publication`` intentionally rejects a different request before it
    decides state. This wrapper performs the active-to-publishing transition
    atomically and keeps the stricter helper available to tests and callers.
    """

    database_url = normalize_database_url(database_url)
    digest = request_digest(request)
    _require_sha256(previous_receipt_sha256, "previous receipt")
    commits = [
        request["candidateApplicationCommit"],
        request["rollbackApplicationCommit"],
    ]
    encoded_request = canonical_json(request)
    encoded_commits = canonical_json(commits)
    with _writer_connection(database_url) as connection:
        rows = list(
            connection.execute(
                f"select * from {STATE_TABLE} where singleton_key = true for update"
            ).fetchall()
        )
        if len(rows) > 1:
            raise CommercialDataPublicationError(
                "commercial data publication authority is not a singleton"
            )
        if not rows:
            publication_id = uuid4()
            row = connection.execute(
                f"""
                insert into {STATE_TABLE} (
                  singleton_key, publication_id, state, request_digest,
                  request_payload, publication_digest,
                  previous_receipt_sha256, active_receipt_sha256,
                  expected_database_revision,
                  compatible_application_commits,
                  publication_started_at, state_changed_at, activated_at
                ) values (
                  true, %s, 'publishing', %s, %s::jsonb, null,
                  %s, %s, %s, %s::jsonb,
                  transaction_timestamp(), transaction_timestamp(), null
                )
                returning *
                """,
                (
                    publication_id,
                    digest,
                    encoded_request,
                    previous_receipt_sha256,
                    previous_receipt_sha256,
                    request["expectedDatabaseRevision"],
                    encoded_commits,
                ),
            ).fetchone()
            if row is None:
                raise CommercialDataPublicationError(
                    "commercial data publication could not be started"
                )
            _append_transition(
                connection,
                row=row,
                event_kind="publication_started",
                from_state=None,
            )
            result = _validate_state_row(row)
            result["action"] = "started"
            return result

        current = rows[0]
        state = _validate_state_row(current)
        if state["state"] == "publishing":
            _require_exact_request(state, request=request, digest=digest)
            if state["previousReceiptSha256"] != previous_receipt_sha256:
                raise CommercialDataPublicationError(
                    "publishing request does not match its previous receipt"
                )
            _append_transition(
                connection,
                row=current,
                event_kind="publication_resumed",
                from_state="publishing",
            )
            state["action"] = "resume"
            return state

        if state["requestDigest"] == digest:
            _require_exact_request(state, request=request, digest=digest)
            if state["activeReceiptSha256"] != previous_receipt_sha256:
                raise CommercialDataPublicationError(
                    "active publication does not match the current signed receipt"
                )
            state["action"] = "already_active"
            return state

        if state["activeReceiptSha256"] != previous_receipt_sha256:
            raise CommercialDataPublicationError(
                "new publication does not start from the active signed receipt"
            )
        row = connection.execute(
            f"""
            update {STATE_TABLE}
               set publication_id = %s,
                   state = 'publishing',
                   request_digest = %s,
                   request_payload = %s::jsonb,
                   publication_digest = null,
                   previous_receipt_sha256 = %s,
                   active_receipt_sha256 = %s,
                   expected_database_revision = %s,
                   compatible_application_commits = %s::jsonb,
                   publication_started_at = transaction_timestamp(),
                   state_changed_at = transaction_timestamp(),
                   activated_at = null
             where singleton_key = true
            returning *
            """,
            (
                uuid4(),
                digest,
                encoded_request,
                previous_receipt_sha256,
                previous_receipt_sha256,
                request["expectedDatabaseRevision"],
                encoded_commits,
            ),
        ).fetchone()
        if row is None:
            raise CommercialDataPublicationError(
                "commercial data publication could not be advanced"
            )
        _append_transition(
            connection,
            row=row,
            event_kind="publication_started",
            from_state="active",
        )
        result = _validate_state_row(row)
        result["action"] = "started"
        return result


def record_publication_receipt(
    *,
    database_url: str,
    expected_request_digest: str,
    receipt_sha256: str,
) -> dict[str, Any]:
    database_url = normalize_database_url(database_url)
    _require_sha256(expected_request_digest, "request")
    _require_sha256(receipt_sha256, "publication receipt")
    with _writer_connection(database_url) as connection:
        _require_exclusive_publication_lock(connection)
        row = connection.execute(
            f"select * from {STATE_TABLE} where singleton_key = true for update"
        ).fetchone()
        if row is None:
            raise CommercialDataPublicationError(
                "commercial data publication authority is empty"
            )
        state = _validate_state_row(row)
        if (
            state["state"] != "publishing"
            or state["requestDigest"] != expected_request_digest
        ):
            raise CommercialDataPublicationError(
                "only the exact publishing request can record a receipt"
            )
        old_digest = state["publicationDigest"]
        new_digest = publication_digest_for(
            request_digest_value=expected_request_digest,
            receipt_sha256=receipt_sha256,
        )
        if old_digest == new_digest and state["activeReceiptSha256"] == receipt_sha256:
            state["action"] = "already_recorded"
            return state
        updated = connection.execute(
            f"""
            update {STATE_TABLE}
               set publication_digest = %s,
                   active_receipt_sha256 = %s,
                   state_changed_at = transaction_timestamp()
             where singleton_key = true
            returning *
            """,
            (new_digest, receipt_sha256),
        ).fetchone()
        if updated is None:
            raise CommercialDataPublicationError(
                "commercial data publication receipt was not recorded"
            )
        _append_transition(
            connection,
            row=updated,
            event_kind=(
                "receipt_recorded" if old_digest is None else "receipt_replaced"
            ),
            from_state="publishing",
        )
        result = _validate_state_row(updated)
        result["action"] = "recorded"
        return result


def finish_publication(
    *,
    database_url: str,
    expected_request_digest: str,
    receipt_sha256: str,
) -> dict[str, Any]:
    database_url = normalize_database_url(database_url)
    _require_sha256(expected_request_digest, "request")
    _require_sha256(receipt_sha256, "publication receipt")
    with _writer_connection(database_url) as connection:
        _require_exclusive_publication_lock(connection)
        row = connection.execute(
            f"select * from {STATE_TABLE} where singleton_key = true for update"
        ).fetchone()
        if row is None:
            raise CommercialDataPublicationError(
                "commercial data publication authority is empty"
            )
        state = _validate_state_row(row)
        if state["state"] == "active":
            expected_publication_digest = publication_digest_for(
                request_digest_value=expected_request_digest,
                receipt_sha256=receipt_sha256,
            )
            if (
                state["requestDigest"] == expected_request_digest
                and state["publicationDigest"] == expected_publication_digest
                and state["activeReceiptSha256"] == receipt_sha256
            ):
                state["action"] = "already_active"
                return state
            raise CommercialDataPublicationError(
                "active commercial data publication belongs to another receipt"
            )
        if (
            state["requestDigest"] != expected_request_digest
            or state["activeReceiptSha256"] != receipt_sha256
            or state["publicationDigest"]
            != publication_digest_for(
                request_digest_value=expected_request_digest,
                receipt_sha256=receipt_sha256,
            )
        ):
            raise CommercialDataPublicationError(
                "publishing state does not authorize this receipt"
            )
        updated = connection.execute(
            f"""
            update {STATE_TABLE}
               set state = 'active',
                   state_changed_at = transaction_timestamp(),
                   activated_at = transaction_timestamp()
             where singleton_key = true
            returning *
            """,
        ).fetchone()
        if updated is None:
            raise CommercialDataPublicationError(
                "commercial data publication was not activated"
            )
        _append_transition(
            connection,
            row=updated,
            event_kind="publication_activated",
            from_state="publishing",
        )
        result = _validate_state_row(updated)
        result["action"] = "activated"
        return result


def classify_pointer(
    *,
    state: dict[str, Any],
    pointer_receipt_sha256: str,
) -> str:
    _require_sha256(pointer_receipt_sha256, "authority pointer receipt")
    authority_status = state.get("authorityStatus")
    if authority_status != "initialized":
        return "bootstrap"
    current_state = state.get("state")
    active_receipt = state.get("activeReceiptSha256")
    previous_receipt = state.get("previousReceiptSha256")
    publication_digest = state.get("publicationDigest")
    if current_state == "active":
        if (
            pointer_receipt_sha256 == active_receipt
            and publication_digest
            == publication_digest_for(
                request_digest_value=state["requestDigest"],
                receipt_sha256=active_receipt,
            )
        ):
            return "verify-active-noop-or-start"
        raise CommercialDataPublicationError(
            "active database authority does not match the signed pointer"
        )
    if current_state == "publishing":
        if pointer_receipt_sha256 == previous_receipt:
            return "redo-publication"
        if (
            isinstance(publication_digest, str)
            and pointer_receipt_sha256 == active_receipt
        ):
            return "verify-pointer-and-finish"
        raise CommercialDataPublicationError(
            "publishing database authority does not recognize the signed pointer"
        )
    raise CommercialDataPublicationError("commercial data publication state is unknown")


def assert_active_publication_pairing(
    *,
    database_url: str,
    receipt_sha256: str,
    environment: str,
    expected_revision: str,
    expected_compatible_commits: Sequence[str],
    allow_recorded_publishing: bool = False,
) -> dict[str, Any]:
    """Bind one filesystem receipt and application pair to the DB singleton."""

    _require_sha256(receipt_sha256, "authority pointer receipt")
    if SAFE_ENVIRONMENT.fullmatch(environment) is None:
        raise CommercialDataPublicationError("deployment environment is invalid")
    commits = list(expected_compatible_commits)
    if (
        len(commits) != 2
        or len(set(commits)) != 2
        or not all(COMMIT.fullmatch(commit) for commit in commits)
    ):
        raise CommercialDataPublicationError(
            "exactly two unique compatible commits are required"
        )
    state = inspect_publication_state(database_url=database_url)
    if state.get("authorityStatus") != "initialized":
        raise CommercialDataPublicationError(
            "commercial data publication authority is uninitialized"
        )
    request = state["request"]
    request_commits = [
        request.get("candidateApplicationCommit"),
        request.get("rollbackApplicationCommit"),
    ]
    if (
        request.get("deploymentEnvironment") != environment
        or state["expectedDatabaseRevision"] != expected_revision
        or request.get("expectedDatabaseRevision") != expected_revision
        or set(state["compatibleApplicationCommits"]) != set(commits)
        or set(request_commits) != set(commits)
    ):
        raise CommercialDataPublicationError(
            "database publication request does not match the application pair"
        )
    expected_digest = publication_digest_for(
        request_digest_value=state["requestDigest"],
        receipt_sha256=receipt_sha256,
    )
    if state["activeReceiptSha256"] != receipt_sha256:
        raise CommercialDataPublicationError(
            "database publication receipt does not match the signed pointer"
        )
    if state["publicationDigest"] != expected_digest:
        raise CommercialDataPublicationError(
            "database publication digest does not match the signed pointer"
        )
    if state["state"] == "active":
        return state
    if allow_recorded_publishing and state["state"] == "publishing":
        return state
    raise CommercialDataPublicationError("commercial data publication is not active")


def execute_with_exclusive_publication_lock(
    *,
    database_url: str,
    expected_request_digest: str,
    ready_file: Path,
    command: Sequence[str],
) -> None:
    database_url = normalize_database_url(database_url)
    _require_sha256(expected_request_digest, "request")
    if ready_file.exists() or not ready_file.is_absolute():
        raise CommercialDataPublicationError(
            "exclusive-lock ready file must be a new absolute path"
        )
    executable = Path(command[0]) if command else None
    if executable is None or not executable.is_absolute() or not executable.is_file():
        raise CommercialDataPublicationError(
            "exclusive publication continuation must be an absolute file"
        )

    connection = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    locked = False
    duplicated_fence = False
    try:
        # Block in PostgreSQL instead of using a userspace readiness poll.  The
        # server timeout bounds checkout draining without creating a post-ready
        # check/use race.
        connection.execute("set statement_timeout = '60s'")
        connection.execute(
            "select pg_advisory_lock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        locked = True
        connection.execute("set statement_timeout = 0")
        row = connection.execute(
            f"select * from {STATE_TABLE} where singleton_key = true"
        ).fetchone()
        if row is None:
            raise CommercialDataPublicationError(
                "commercial data publication authority is empty"
            )
        state = _validate_state_row(row)
        if (
            state["state"] != "publishing"
            or state["requestDigest"] != expected_request_digest
        ):
            raise CommercialDataPublicationError(
                "exclusive lock belongs to a stale publication request"
            )

        backend_identity = connection.execute(
            """
            select
              pg_backend_pid() as backend_pid,
              host(inet_client_addr()) as client_address,
              inet_client_port() as client_port
            """
        ).fetchone()
        backend_pid = backend_identity["backend_pid"]
        client_address = backend_identity["client_address"]
        client_port = backend_identity["client_port"]
        if not isinstance(client_address, str) or not isinstance(client_port, int):
            raise CommercialDataPublicationError(
                "exclusive publication fence requires an explicit TCP connection"
            )
        source_fd = connection.fileno()
        if source_fd == COMMERCIAL_DATA_PUBLICATION_FENCE_FD:
            os.set_inheritable(source_fd, True)
        else:
            os.dup2(
                source_fd,
                COMMERCIAL_DATA_PUBLICATION_FENCE_FD,
                inheritable=True,
            )
            duplicated_fence = True

        continuation_environment = dict(os.environ)
        continuation_environment.update(
            {
                "COMMERCIAL_DATA_PUBLICATION_FENCE_FD": str(
                    COMMERCIAL_DATA_PUBLICATION_FENCE_FD
                ),
                "COMMERCIAL_DATA_PUBLICATION_FENCE_BACKEND_PID": str(backend_pid),
                "COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_ADDRESS": (client_address),
                "COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_PORT": str(client_port),
                "COMMERCIAL_DATA_PUBLICATION_FENCE_REQUEST_DIGEST": (
                    expected_request_digest
                ),
            }
        )
        _write_ready_file(
            ready_file,
            {
                "schemaVersion": "commercial-data-publication-fence@v2",
                "controllerPid": os.getpid(),
                "databaseBackendPid": backend_pid,
                "databaseClientAddress": client_address,
                "databaseClientPort": client_port,
                "fenceFd": COMMERCIAL_DATA_PUBLICATION_FENCE_FD,
                "requestDigest": expected_request_digest,
            },
        )
        # On success this never returns.  The same PID now runs the mutation
        # controller and fd 8 keeps this exact PostgreSQL session (and its
        # session-level advisory lock) alive.
        os.execve(
            str(executable),
            list(command),
            continuation_environment,
        )
    except OSError as exc:
        raise CommercialDataPublicationError(
            "exclusive publication continuation could not be executed"
        ) from exc
    finally:
        if duplicated_fence:
            try:
                os.close(COMMERCIAL_DATA_PUBLICATION_FENCE_FD)
            except OSError:
                pass
        if locked:
            try:
                connection.execute(
                    "select pg_advisory_unlock(%s)",
                    (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
                )
            except psycopg.Error:
                pass
        connection.close()


def assert_inherited_publication_fence(
    *,
    database_url: str,
    expected_request_digest: str,
    expected_backend_pid: int,
    expected_client_address: str,
    expected_client_port: int,
    fence_fd: int,
) -> dict[str, Any]:
    """Validate the kernel capability before the locked controller can mutate."""

    database_url = normalize_database_url(database_url)
    _require_sha256(expected_request_digest, "request")
    if (
        expected_backend_pid <= 0
        or expected_client_port <= 0
        or expected_client_port > 65535
        or fence_fd != COMMERCIAL_DATA_PUBLICATION_FENCE_FD
    ):
        raise CommercialDataPublicationError(
            "commercial data publication fence identity is invalid"
        )
    try:
        normalized_expected_address = str(ip_address(expected_client_address))
    except ValueError as exc:
        raise CommercialDataPublicationError(
            "commercial data publication fence client address is invalid"
        ) from exc
    try:
        descriptor = os.fstat(fence_fd)
    except OSError as exc:
        raise CommercialDataPublicationError(
            "commercial data publication fence descriptor is absent"
        ) from exc
    if not stat.S_ISSOCK(descriptor.st_mode) or not os.get_inheritable(fence_fd):
        raise CommercialDataPublicationError(
            "commercial data publication fence descriptor is not an inheritable socket"
        )
    duplicate = os.dup(fence_fd)
    try:
        inherited_socket = socket.socket(fileno=duplicate)
        try:
            socket_name = inherited_socket.getsockname()
        finally:
            inherited_socket.close()
    except OSError as exc:
        try:
            os.close(duplicate)
        except OSError:
            pass
        raise CommercialDataPublicationError(
            "commercial data publication fence socket is invalid"
        ) from exc
    if (
        not isinstance(socket_name, tuple)
        or len(socket_name) < 2
        or not isinstance(socket_name[0], str)
        or not isinstance(socket_name[1], int)
    ):
        raise CommercialDataPublicationError(
            "commercial data publication fence must use TCP"
        )
    try:
        inherited_address = str(ip_address(socket_name[0].split("%", 1)[0]))
    except ValueError as exc:
        raise CommercialDataPublicationError(
            "commercial data publication fence socket address is invalid"
        ) from exc
    if (
        inherited_address != normalized_expected_address
        or socket_name[1] != expected_client_port
    ):
        raise CommercialDataPublicationError(
            "commercial data publication fence socket is not the lock session"
        )

    with psycopg.connect(
        database_url,
        row_factory=dict_row,
        autocommit=True,
    ) as connection:
        key = COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY
        upper = (key >> 32) & 0xFFFFFFFF
        lower = key & 0xFFFFFFFF
        holder = connection.execute(
            """
            select
              host(activity.client_addr) as client_address,
              activity.client_port
              from pg_catalog.pg_locks publication_lock
              join pg_catalog.pg_stat_activity activity
                on activity.pid = publication_lock.pid
             where publication_lock.locktype = 'advisory'
               and publication_lock.database = (
                 select oid
                   from pg_catalog.pg_database
                  where datname = current_database()
               )
               and publication_lock.classid = %s::oid
               and publication_lock.objid = %s::oid
               and publication_lock.objsubid = 1
               and publication_lock.mode = 'ExclusiveLock'
               and publication_lock.granted
               and publication_lock.pid = %s
               and activity.datname = current_database()
            """,
            (upper, lower, expected_backend_pid),
        ).fetchone()
        if holder is None:
            raise CommercialDataPublicationError(
                "commercial data publication fence database session is absent"
            )
        try:
            holder_address = str(ip_address(holder["client_address"]))
        except (TypeError, ValueError) as exc:
            raise CommercialDataPublicationError(
                "commercial data publication fence database endpoint is invalid"
            ) from exc
        if (
            holder_address != normalized_expected_address
            or holder["client_port"] != expected_client_port
        ):
            raise CommercialDataPublicationError(
                "commercial data publication fence descriptor is not owned "
                "by the exclusive database session"
            )
        row = connection.execute(
            f"select * from {STATE_TABLE} where singleton_key = true"
        ).fetchone()
        if row is None:
            raise CommercialDataPublicationError(
                "commercial data publication authority is empty"
            )
        state = _validate_state_row(row)
        if (
            state["state"] != "publishing"
            or state["requestDigest"] != expected_request_digest
        ):
            raise CommercialDataPublicationError(
                "commercial data publication fence belongs to a stale request"
            )
    return {
        "schemaVersion": "commercial-data-publication-fence@v2",
        "databaseBackendPid": expected_backend_pid,
        "databaseClientAddress": normalized_expected_address,
        "databaseClientPort": expected_client_port,
        "fenceFd": fence_fd,
        "requestDigest": expected_request_digest,
    }


def _write_ready_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise CommercialDataPublicationError(
                "exclusive-lock ready file already exists"
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _writer_connection(
    database_url: str,
) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            connection.execute(
                "set local gongkao.commercial_data_publication_writer = 'publisher-v1'"
            )
            relation = connection.execute(
                "select to_regclass(%s)::text as relation",
                (STATE_TABLE,),
            ).fetchone()
            if relation is None or relation["relation"] is None:
                raise CommercialDataPublicationError(
                    "commercial data publication migration is missing"
                )
            connection.execute(
                sql.SQL("lock table {} in share row exclusive mode").format(
                    sql.Identifier("platform", "commercial_data_publication_state")
                )
            )
            yield connection


def _database_revisions(
    connection: psycopg.Connection[dict[str, Any]],
) -> list[str]:
    relation = connection.execute(
        "select to_regclass('public.alembic_version')::text as relation"
    ).fetchone()
    if relation is None or relation["relation"] is None:
        raise CommercialDataPublicationError("database revision authority is missing")
    revisions = [
        str(row["version_num"])
        for row in connection.execute(
            "select version_num from public.alembic_version order by version_num"
        ).fetchall()
    ]
    if len(revisions) != 1:
        raise CommercialDataPublicationError(
            "database must report exactly one migration revision"
        )
    return revisions


def _validate_state_row(row: dict[str, Any]) -> dict[str, Any]:
    request = row["request_payload"]
    if not isinstance(request, dict):
        request = json.loads(request)
    digest = request_digest(request)
    compatible_commits = row["compatible_application_commits"]
    if not isinstance(compatible_commits, list):
        compatible_commits = json.loads(compatible_commits)
    state = str(row["state"])
    publication_digest = row["publication_digest"]
    active_receipt = str(row["active_receipt_sha256"])
    expected_publication_digest = publication_digest_for(
        request_digest_value=digest,
        receipt_sha256=active_receipt,
    )
    if (
        row["singleton_key"] is not True
        or digest != row["request_digest"]
        or row["expected_database_revision"] != request["expectedDatabaseRevision"]
        or compatible_commits
        != [
            request["candidateApplicationCommit"],
            request["rollbackApplicationCommit"],
        ]
        or state not in {"publishing", "active"}
        or not SHA256.fullmatch(active_receipt)
        or not SHA256.fullmatch(str(row["previous_receipt_sha256"]))
        or (
            publication_digest is not None
            and not SHA256.fullmatch(str(publication_digest))
        )
        or (
            state == "active"
            and (
                publication_digest is None
                or str(publication_digest) != expected_publication_digest
                or row["activated_at"] is None
            )
        )
        or (
            state == "publishing"
            and (
                row["activated_at"] is not None
                or (
                    publication_digest is None
                    and active_receipt != str(row["previous_receipt_sha256"])
                )
                or (
                    publication_digest is not None
                    and str(publication_digest) != expected_publication_digest
                )
            )
        )
    ):
        raise CommercialDataPublicationError(
            "commercial data publication state is internally inconsistent"
        )
    return {
        "publicationId": str(row["publication_id"]),
        "state": state,
        "requestDigest": str(row["request_digest"]),
        "request": request,
        "publicationDigest": (
            str(publication_digest) if publication_digest is not None else None
        ),
        "previousReceiptSha256": str(row["previous_receipt_sha256"]),
        "activeReceiptSha256": active_receipt,
        "expectedDatabaseRevision": str(row["expected_database_revision"]),
        "compatibleApplicationCommits": list(compatible_commits),
        "publicationStartedAt": row["publication_started_at"].isoformat(),
        "stateChangedAt": row["state_changed_at"].isoformat(),
        "activatedAt": (
            row["activated_at"].isoformat() if row["activated_at"] is not None else None
        ),
    }


def _require_exact_request(
    state: dict[str, Any],
    *,
    request: dict[str, Any],
    digest: str,
) -> None:
    if state["requestDigest"] != digest or state["request"] != request:
        raise CommercialDataPublicationError(
            "another commercial data publication request is already durable"
        )


def _append_transition(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    row: dict[str, Any],
    event_kind: str,
    from_state: str | None,
) -> None:
    connection.execute(
        f"""
        insert into {TRANSITION_TABLE} (
          publication_id,
          event_kind,
          from_state,
          to_state,
          request_digest,
          request_payload,
          publication_digest,
          previous_receipt_sha256,
          active_receipt_sha256,
          expected_database_revision,
          compatible_application_commits,
          occurred_at
        ) values (
          %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb,
          transaction_timestamp()
        )
        """,
        (
            row["publication_id"],
            event_kind,
            from_state,
            row["state"],
            row["request_digest"],
            canonical_json(row["request_payload"]),
            row["publication_digest"],
            row["previous_receipt_sha256"],
            row["active_receipt_sha256"],
            row["expected_database_revision"],
            canonical_json(row["compatible_application_commits"]),
        ),
    )


def _require_exclusive_publication_lock(
    connection: psycopg.Connection[dict[str, Any]],
) -> None:
    key = COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY
    upper = (key >> 32) & 0xFFFFFFFF
    lower = key & 0xFFFFFFFF
    held = connection.execute(
        """
        select exists (
          select 1
            from pg_catalog.pg_locks
           where locktype = 'advisory'
             and database = (
               select oid from pg_catalog.pg_database
                where datname = current_database()
             )
             and classid = %s::oid
             and objid = %s::oid
             and objsubid = 1
             and mode = 'ExclusiveLock'
             and granted
             and pid <> pg_backend_pid()
        ) as held
        """,
        (upper, lower),
    ).fetchone()
    if held is None or not held["held"]:
        raise CommercialDataPublicationError(
            "commercial data publication exclusive database lock is absent"
        )


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise CommercialDataPublicationError(f"{label} SHA-256 is invalid")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise CommercialDataPublicationError("output path must be absolute")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="MIGRATION_DATABASE_URL",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-request")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--candidate-commit", required=True)
    build.add_argument("--rollback-commit", required=True)
    build.add_argument("--environment", required=True)
    build.add_argument("--expected-revision", required=True)
    build.add_argument("--position-exam-code", default="guokao")
    build.add_argument("--position-exam-year", type=int, required=True)
    build.add_argument("--position-rule-set-version", required=True)
    build.add_argument("--position-data-version", required=True)
    build.add_argument("--position-window-manifest-sha256", required=True)
    build.add_argument("--knowledge-exam-scope", default="guokao")
    build.add_argument("--knowledge-track", default="xingce")
    build.add_argument("--knowledge-release-version", required=True)
    build.add_argument("--knowledge-source-data-version", required=True)
    build.add_argument("--knowledge-fresh-until", required=True)

    subparsers.add_parser("inspect")

    begin = subparsers.add_parser("begin")
    begin.add_argument("--request", type=Path, required=True)
    begin.add_argument("--previous-receipt-sha256", required=True)

    bootstrap = subparsers.add_parser("bootstrap-upgrade-and-begin")
    bootstrap.add_argument("--request", type=Path, required=True)
    bootstrap.add_argument("--bootstrap-receipt", type=Path, required=True)
    bootstrap.add_argument("--bootstrap-receipt-sha256", required=True)
    bootstrap.add_argument("--backend-root", type=Path, required=True)

    plan = subparsers.add_parser("classify-pointer")
    plan.add_argument("--pointer-receipt-sha256", required=True)

    pairing = subparsers.add_parser("assert-pairing")
    pairing.add_argument("--receipt-sha256", required=True)
    pairing.add_argument("--environment", required=True)
    pairing.add_argument("--expected-revision", required=True)
    pairing.add_argument(
        "--compatible-commit",
        action="append",
        required=True,
    )
    pairing.add_argument(
        "--allow-recorded-publishing",
        action="store_true",
    )

    record = subparsers.add_parser("record-receipt")
    record.add_argument("--request-digest", required=True)
    record.add_argument("--receipt-sha256", required=True)

    finish = subparsers.add_parser("finish")
    finish.add_argument("--request-digest", required=True)
    finish.add_argument("--receipt-sha256", required=True)

    execute = subparsers.add_parser("exec-exclusive")
    execute.add_argument("--request-digest", required=True)
    execute.add_argument("--ready-file", type=Path, required=True)
    execute.add_argument("continuation", nargs=argparse.REMAINDER)

    inherited = subparsers.add_parser("assert-inherited-fence")
    inherited.add_argument("--request-digest", required=True)
    inherited.add_argument("--backend-pid", type=int, required=True)
    inherited.add_argument("--client-address", required=True)
    inherited.add_argument("--client-port", type=int, required=True)
    inherited.add_argument("--fence-fd", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "build-request":
            request = build_request(
                candidate_commit=arguments.candidate_commit,
                rollback_commit=arguments.rollback_commit,
                environment=arguments.environment,
                expected_revision=arguments.expected_revision,
                position_exam_code=arguments.position_exam_code,
                position_exam_year=arguments.position_exam_year,
                position_rule_set_version=arguments.position_rule_set_version,
                position_data_version=arguments.position_data_version,
                position_window_manifest_sha256=(
                    arguments.position_window_manifest_sha256
                ),
                knowledge_exam_scope=arguments.knowledge_exam_scope,
                knowledge_track=arguments.knowledge_track,
                knowledge_release_version=arguments.knowledge_release_version,
                knowledge_source_data_version=(arguments.knowledge_source_data_version),
                knowledge_fresh_until=arguments.knowledge_fresh_until,
            )
            _write_json(arguments.output, request)
            print(
                json.dumps(
                    {
                        "schemaVersion": REQUEST_SCHEMA_VERSION,
                        "requestDigest": request_digest(request),
                        "output": str(arguments.output),
                    },
                    sort_keys=True,
                )
            )
            return 0

        database_url = database_url_from_environment(arguments.database_url_env)
        if arguments.command == "inspect":
            result = inspect_publication_state(database_url=database_url)
        elif arguments.command == "bootstrap-upgrade-and-begin":
            result = bootstrap_upgrade_and_begin(
                database_url=database_url,
                request=load_request(arguments.request),
                bootstrap_receipt_path=arguments.bootstrap_receipt,
                bootstrap_receipt_sha256=(arguments.bootstrap_receipt_sha256),
                backend_root=arguments.backend_root,
            )
        elif arguments.command == "begin":
            result = start_next_publication(
                database_url=database_url,
                request=load_request(arguments.request),
                previous_receipt_sha256=arguments.previous_receipt_sha256,
            )
        elif arguments.command == "classify-pointer":
            state = inspect_publication_state(database_url=database_url)
            result = {
                "action": classify_pointer(
                    state=state,
                    pointer_receipt_sha256=arguments.pointer_receipt_sha256,
                ),
                "state": state,
            }
        elif arguments.command == "assert-pairing":
            result = assert_active_publication_pairing(
                database_url=database_url,
                receipt_sha256=arguments.receipt_sha256,
                environment=arguments.environment,
                expected_revision=arguments.expected_revision,
                expected_compatible_commits=(arguments.compatible_commit),
                allow_recorded_publishing=(arguments.allow_recorded_publishing),
            )
        elif arguments.command == "record-receipt":
            result = record_publication_receipt(
                database_url=database_url,
                expected_request_digest=arguments.request_digest,
                receipt_sha256=arguments.receipt_sha256,
            )
        elif arguments.command == "finish":
            result = finish_publication(
                database_url=database_url,
                expected_request_digest=arguments.request_digest,
                receipt_sha256=arguments.receipt_sha256,
            )
        elif arguments.command == "exec-exclusive":
            continuation = list(arguments.continuation)
            if continuation[:1] == ["--"]:
                continuation = continuation[1:]
            execute_with_exclusive_publication_lock(
                database_url=database_url,
                expected_request_digest=arguments.request_digest,
                ready_file=arguments.ready_file,
                command=continuation,
            )
            return 0
        elif arguments.command == "assert-inherited-fence":
            result = assert_inherited_publication_fence(
                database_url=database_url,
                expected_request_digest=arguments.request_digest,
                expected_backend_pid=arguments.backend_pid,
                expected_client_address=arguments.client_address,
                expected_client_port=arguments.client_port,
                fence_fd=arguments.fence_fd,
            )
        else:
            raise CommercialDataPublicationError("unsupported publication command")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (CommercialDataPublicationError, psycopg.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
