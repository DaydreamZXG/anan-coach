#!/usr/bin/env python3
"""Build and verify the encrypted database backup payload.

The shell entrypoints own encryption, signatures, and retention. This module owns
the PostgreSQL-consistent manifest and the restore evidence contract.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import UTC, datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict


FORMAT_VERSION = "anan-postgres-backup/v3"
RESTORE_TARGET_FORMAT_VERSION = "anan-restore-target/v1"
RESTORE_TARGET_PURPOSE = "isolated-disposable-restore"
PUBLICATION_AUTHORITY_FORMAT_VERSION = (
    "anan-commercial-data-publication-backup-authority/v1"
)
# "GKDATA P1" encoded as one fixed positive int8. This is the same immutable
# protocol key used by the publisher and by application checkout guards.
COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY = 0x474B444154415001
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
SAFE_ENVIRONMENT = re.compile(r"^[A-Za-z0-9._-]+$")
CRITICAL_TABLES = (
    "learner.users",
    "learning.xingce_task_sessions",
    "learner.mock_exam_sessions",
    "learning.essay_answer_versions",
    "learning.private_knowledge_source_versions",
    "privacy.data_export_requests",
    "commerce.wechat_orders",
    "messaging.coach_notification_deliveries",
    "learning.task_edit_leases",
)
EXPECTED_BUNDLE_MEMBERS = frozenset({"manifest.json", "database.dump"})
BACKUP_RUN_ID = re.compile(
    r"^(?P<timestamp>[0-9]{8}T[0-9]{6}\.[0-9]{9}Z)-"
    r"(?P<random>[0-9a-f]{32})$"
)
BACKUP_ARTIFACT_NAME = re.compile(
    r"^anan-(?P<run_id>[0-9]{8}T[0-9]{6}\.[0-9]{9}Z-"
    r"[0-9a-f]{32})\.backup\.age$"
)
LEGACY_BACKUP_ARTIFACT_NAME = re.compile(
    r"^anan-(?P<run_id>[0-9]{8}T[0-9]{6}Z)\.backup\.age$"
)
RESTORE_REPORT_SET_NAME = re.compile(
    r"^anan-(?P<run_id>[0-9]{8}T[0-9]{6}\.[0-9]{9}Z-"
    r"[0-9a-f]{32})\.restore-report-set$"
)
RESTORE_WORKSPACE_FORMAT_VERSION = "anan-restore-workspace/v1"
RESTORE_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
RESTORE_WORKSPACE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class BackupContractError(RuntimeError):
    """Raised when a backup or restore cannot satisfy the production contract."""


def new_backup_run_id(
    *,
    timestamp_ns: int | None = None,
    random_token: str | None = None,
) -> str:
    """Return a sortable, cryptographically unique publication identity."""

    current_ns = time.time_ns() if timestamp_ns is None else timestamp_ns
    if current_ns < 0:
        raise BackupContractError("backup timestamp cannot be negative")
    seconds, nanoseconds = divmod(current_ns, 1_000_000_000)
    timestamp = datetime.fromtimestamp(seconds, UTC).strftime("%Y%m%dT%H%M%S")
    token = secrets.token_hex(16) if random_token is None else random_token
    run_id = f"{timestamp}.{nanoseconds:09d}Z-{token}"
    if BACKUP_RUN_ID.fullmatch(run_id) is None:
        raise BackupContractError("backup run identity is invalid")
    return run_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    *,
    created_at: str,
    source_environment_id: str,
    backup_target_id: str,
    source_identity: str,
    source_cluster_identity: str,
    database_revision: str,
    pg_dump_version: str,
    dump_path: Path,
    row_counts: dict[str, int],
    publication_authority: dict[str, Any],
) -> dict[str, Any]:
    if set(row_counts) != set(CRITICAL_TABLES):
        raise BackupContractError("critical table count set is incomplete")
    if any(not isinstance(value, int) or value < 0 for value in row_counts.values()):
        raise BackupContractError("critical table counts must be non-negative integers")
    _validate_publication_authority(
        publication_authority,
        expected_revision=database_revision,
        expected_environment=source_environment_id,
    )
    return {
        "formatVersion": FORMAT_VERSION,
        "createdAt": created_at,
        "sourceEnvironmentId": source_environment_id,
        "backupTargetId": backup_target_id,
        "sourceDatabaseIdentitySha256": hashlib.sha256(
            source_identity.encode("utf-8")
        ).hexdigest(),
        "sourceClusterIdentitySha256": hashlib.sha256(
            source_cluster_identity.encode("utf-8")
        ).hexdigest(),
        "databaseRevision": database_revision,
        "commercialDataPublication": publication_authority,
        "pgDumpVersion": pg_dump_version,
        "dump": {
            "filename": "database.dump",
            "sizeBytes": dump_path.stat().st_size,
            "sha256": sha256_file(dump_path),
        },
        "criticalTableRowCounts": dict(sorted(row_counts.items())),
    }


def capture(
    *,
    database_url: str,
    dump_path: Path,
    manifest_path: Path,
    required_revision: str,
    source_environment_id: str,
    backup_target_id: str,
) -> None:
    _validate_database_url(database_url)
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "begin transaction isolation level repeatable read read only"
        )
        connection.execute(
            "select pg_advisory_xact_lock_shared(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        publication_authority = _capture_active_publication_authority(
            connection=connection,
            required_revision=required_revision,
        )
        snapshot = str(connection.execute("select pg_export_snapshot()").fetchone()[0])
        database_name, database_oid, system_identifier = connection.execute(
            "select current_database(), "
            "(select oid::text from pg_database where datname = current_database()), "
            "(select system_identifier::text from pg_control_system())"
        ).fetchone()
        revisions = [
            str(row[0])
            for row in connection.execute(
                "select version_num from alembic_version order by version_num"
            ).fetchall()
        ]
        if revisions != [required_revision]:
            raise BackupContractError(
                f"database revision must be exactly {required_revision}; got {revisions}"
            )
        row_counts = {
            table: int(
                connection.execute(f"select count(*) from {table}").fetchone()[0]
            )
            for table in CRITICAL_TABLES
        }
        subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                f"--snapshot={snapshot}",
                f"--file={dump_path}",
                f"--dbname={database_url}",
            ],
            check=True,
        )
        subprocess.run(
            ["pg_restore", "--list", str(dump_path)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        pg_dump_version = subprocess.run(
            ["pg_dump", "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        manifest = build_manifest(
            created_at=datetime.now(UTC).isoformat(),
            source_environment_id=source_environment_id,
            backup_target_id=backup_target_id,
            source_identity=(f"{system_identifier}|{database_name}|{database_oid}"),
            source_cluster_identity=str(system_identifier),
            database_revision=required_revision,
            pg_dump_version=pg_dump_version,
            dump_path=dump_path,
            row_counts=row_counts,
            publication_authority=publication_authority,
        )
        _write_json(manifest_path, manifest)
        connection.rollback()


def extract_bundle(*, bundle_path: Path, output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "r:") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if names != EXPECTED_BUNDLE_MEMBERS or len(members) != len(names):
            raise BackupContractError("backup bundle member set is invalid")
        for member in members:
            if not member.isfile():
                raise BackupContractError(
                    "backup bundle may contain regular files only"
                )
            source = archive.extractfile(member)
            if source is None:
                raise BackupContractError(f"cannot read bundle member {member.name}")
            destination = output_directory / member.name
            with source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def load_and_verify_manifest(*, manifest_path: Path, dump_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("formatVersion") != FORMAT_VERSION
    ):
        raise BackupContractError("unsupported backup format")
    for label in (
        "sourceDatabaseIdentitySha256",
        "sourceClusterIdentitySha256",
    ):
        value = manifest.get(label)
        if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
            raise BackupContractError(f"backup manifest {label} is invalid")
    counts = manifest.get("criticalTableRowCounts")
    if not isinstance(counts, dict) or set(counts) != set(CRITICAL_TABLES):
        raise BackupContractError("backup manifest critical table set is invalid")
    dump = manifest.get("dump")
    if not isinstance(dump, dict) or dump.get("filename") != "database.dump":
        raise BackupContractError("backup dump metadata is invalid")
    if dump.get("sizeBytes") != dump_path.stat().st_size:
        raise BackupContractError("backup dump size does not match manifest")
    if dump.get("sha256") != sha256_file(dump_path):
        raise BackupContractError("backup dump digest does not match manifest")
    _validate_publication_authority(
        manifest.get("commercialDataPublication"),
        expected_revision=manifest.get("databaseRevision"),
        expected_environment=manifest.get("sourceEnvironmentId"),
    )
    return manifest


def restore_preflight(
    *,
    database_url: str,
    manifest_path: Path,
    dump_path: Path,
    target_environment_id: str,
    target_identity_file: Path,
) -> None:
    _validate_database_url(database_url)
    manifest = load_and_verify_manifest(
        manifest_path=manifest_path, dump_path=dump_path
    )
    target_authority = _load_restore_target_authority(
        path=target_identity_file,
        expected_environment_id=target_environment_id,
    )
    if manifest.get("sourceEnvironmentId") == target_environment_id:
        raise BackupContractError("restore target environment is the backup source")
    with psycopg.connect(database_url) as connection:
        _assert_restore_target_database(
            connection=connection,
            manifest=manifest,
            target_authority=target_authority,
        )
        relation_count = int(
            connection.execute(
                "select count(*) from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where c.relkind in ('r', 'p') "
                "and n.nspname not in ('pg_catalog', 'information_schema') "
                "and n.nspname not like 'pg_toast%'"
            ).fetchone()[0]
        )
    if relation_count != 0:
        raise BackupContractError("restore target must be a fresh empty database")


def verify_restore(
    *,
    database_url: str,
    manifest_path: Path,
    dump_path: Path,
    target_environment_id: str,
    target_identity_file: Path,
    artifact_sha256: str,
    report_path: Path,
) -> None:
    _validate_database_url(database_url)
    manifest = load_and_verify_manifest(
        manifest_path=manifest_path, dump_path=dump_path
    )
    target_authority = _load_restore_target_authority(
        path=target_identity_file,
        expected_environment_id=target_environment_id,
    )
    if manifest.get("sourceEnvironmentId") == target_environment_id:
        raise BackupContractError("restore target environment is the backup source")
    expected_counts = manifest["criticalTableRowCounts"]
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "select pg_advisory_xact_lock_shared(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        _assert_restore_target_database(
            connection=connection,
            manifest=manifest,
            target_authority=target_authority,
        )
        revisions = [
            str(row[0])
            for row in connection.execute(
                "select version_num from alembic_version order by version_num"
            ).fetchall()
        ]
        expected_revision = str(manifest["databaseRevision"])
        if revisions != [expected_revision]:
            raise BackupContractError(
                f"restored revision must be exactly {expected_revision}; got {revisions}"
            )
        restored_publication_authority = _capture_active_publication_authority(
            connection=connection,
            required_revision=expected_revision,
        )
        if restored_publication_authority != manifest["commercialDataPublication"]:
            raise BackupContractError(
                "restored commercial data publication authority does not "
                "match the backup snapshot"
            )
        actual_counts = {
            table: int(
                connection.execute(f"select count(*) from {table}").fetchone()[0]
            )
            for table in CRITICAL_TABLES
        }
    if actual_counts != expected_counts:
        raise BackupContractError(
            "restored critical table counts do not match backup snapshot"
        )
    report = {
        "contractVersion": "anan-restore-rehearsal/v1",
        "rehearsalId": str(uuid4()),
        "performedAt": datetime.now(UTC).isoformat(),
        "result": "passed",
        "artifactSha256": artifact_sha256,
        "sourceEnvironmentId": manifest["sourceEnvironmentId"],
        "targetEnvironmentId": target_environment_id,
        "backupTargetId": manifest["backupTargetId"],
        "databaseRevision": expected_revision,
        "commercialDataPublication": restored_publication_authority,
        "criticalTableRowCounts": actual_counts,
    }
    _write_json(report_path, report)


def _validate_database_url(database_url: str) -> None:
    overrides = sorted(
        name for name in os.environ if name.startswith("PG") and name != "PGPASSFILE"
    )
    if overrides:
        raise BackupContractError(
            "libpq environment may not override the database authority: "
            + ", ".join(overrides)
        )
    passfile = os.environ.get("PGPASSFILE")
    if passfile:
        passfile_path = Path(passfile)
        try:
            passfile_stat = passfile_path.stat()
        except OSError as exc:
            raise BackupContractError("PGPASSFILE is not readable") from exc
        if (
            not passfile_path.is_absolute()
            or not stat.S_ISREG(passfile_stat.st_mode)
            or stat.S_IMODE(passfile_stat.st_mode) != 0o600
            or passfile_stat.st_uid != os.geteuid()
        ):
            raise BackupContractError(
                "PGPASSFILE must be an absolute mode-0600 file owned by the "
                "backup process"
            )
    if "+" in database_url.split(":", 1)[0]:
        raise BackupContractError("use a libpq URL, not a SQLAlchemy driver URL")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise BackupContractError("database authority must be an explicit libpq URL")
    parameters = conninfo_to_dict(database_url)
    forbidden_parameters = sorted(
        parameter
        for parameter in ("password", "passfile", "hostaddr", "service")
        if parameters.get(parameter)
    )
    if forbidden_parameters:
        raise BackupContractError(
            "database URL contains an out-of-band authority parameter: "
            + ", ".join(forbidden_parameters)
        )
    for required in ("host", "port", "dbname", "user"):
        value = parameters.get(required)
        if not value or "," in value:
            raise BackupContractError(
                f"database URL must bind exactly one explicit {required}"
            )
    if not str(parameters["port"]).isdigit():
        raise BackupContractError("database URL port is invalid")


def _database_identities(
    connection: psycopg.Connection[Any],
) -> tuple[str, str, str]:
    database_name, database_oid, system_identifier = connection.execute(
        "select current_database(), "
        "(select oid::text from pg_database where datname = current_database()), "
        "(select system_identifier::text from pg_control_system())"
    ).fetchone()
    database_identity = f"{system_identifier}|{database_name}|{database_oid}"
    return (
        str(database_name),
        hashlib.sha256(database_identity.encode("utf-8")).hexdigest(),
        hashlib.sha256(str(system_identifier).encode("utf-8")).hexdigest(),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _publication_digest_for(
    *,
    request_digest: str,
    active_receipt_sha256: str,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "schemaVersion": "commercial-data-publication@v1",
                "requestDigest": request_digest,
                "activeReceiptSha256": active_receipt_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()


def _bounded_text(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value.strip()) <= 200


def _validate_publication_request(request: Any) -> None:
    if not isinstance(request, dict) or set(request) != {
        "schemaVersion",
        "candidateApplicationCommit",
        "rollbackApplicationCommit",
        "deploymentEnvironment",
        "expectedDatabaseRevision",
        "position",
        "knowledge",
    }:
        raise BackupContractError(
            "commercial data publication request schema is invalid"
        )
    candidate = request["candidateApplicationCommit"]
    rollback = request["rollbackApplicationCommit"]
    if (
        request["schemaVersion"] != "commercial-data-publication-request@v1"
        or not isinstance(candidate, str)
        or not COMMIT.fullmatch(candidate)
        or not isinstance(rollback, str)
        or not COMMIT.fullmatch(rollback)
        or candidate == rollback
        or not isinstance(request["deploymentEnvironment"], str)
        or not SAFE_ENVIRONMENT.fullmatch(request["deploymentEnvironment"])
        or not _bounded_text(request["expectedDatabaseRevision"])
    ):
        raise BackupContractError(
            "commercial data publication request identity is invalid"
        )
    position = request["position"]
    if (
        not isinstance(position, dict)
        or set(position)
        != {
            "examCode",
            "examYear",
            "ruleSetVersion",
            "positionDataVersion",
            "windowManifestSha256",
        }
        or not _bounded_text(position["examCode"])
        or not isinstance(position["examYear"], int)
        or isinstance(position["examYear"], bool)
        or not 2000 <= position["examYear"] <= 2100
        or not _bounded_text(position["ruleSetVersion"])
        or not _bounded_text(position["positionDataVersion"])
        or not isinstance(position["windowManifestSha256"], str)
        or not HEX_SHA256.fullmatch(position["windowManifestSha256"])
    ):
        raise BackupContractError(
            "commercial data publication position authority is invalid"
        )
    knowledge = request["knowledge"]
    if (
        not isinstance(knowledge, dict)
        or set(knowledge)
        != {
            "examScope",
            "track",
            "releaseVersion",
            "sourceDataVersion",
            "freshUntil",
        }
        or not all(_bounded_text(value) for value in knowledge.values())
    ):
        raise BackupContractError(
            "commercial data publication knowledge authority is invalid"
        )
    try:
        freshness = datetime.fromisoformat(knowledge["freshUntil"])
    except ValueError as exc:
        raise BackupContractError(
            "commercial data publication knowledge freshness is invalid"
        ) from exc
    if freshness.tzinfo is None:
        raise BackupContractError(
            "commercial data publication knowledge freshness has no timezone"
        )


def _validate_publication_authority(
    authority: Any,
    *,
    expected_revision: Any,
    expected_environment: Any,
) -> None:
    if not isinstance(authority, dict) or set(authority) != {
        "schemaVersion",
        "publicationId",
        "state",
        "requestDigest",
        "publicationDigest",
        "activeReceiptSha256",
        "deploymentEnvironment",
        "expectedDatabaseRevision",
        "compatibleApplicationCommits",
    }:
        raise BackupContractError(
            "backup commercial data publication authority is invalid"
        )
    if not _bounded_text(expected_revision):
        raise BackupContractError("backup database revision is invalid")
    if not isinstance(expected_environment, str) or not SAFE_ENVIRONMENT.fullmatch(
        expected_environment
    ):
        raise BackupContractError("backup source environment is invalid")
    request_digest = authority["requestDigest"]
    active_receipt = authority["activeReceiptSha256"]
    commits = authority["compatibleApplicationCommits"]
    try:
        UUID(str(authority["publicationId"]))
    except (TypeError, ValueError) as exc:
        raise BackupContractError(
            "backup commercial data publication id is invalid"
        ) from exc
    if (
        authority["schemaVersion"] != PUBLICATION_AUTHORITY_FORMAT_VERSION
        or authority["state"] != "active"
        or not isinstance(request_digest, str)
        or not HEX_SHA256.fullmatch(request_digest)
        or not isinstance(active_receipt, str)
        or not HEX_SHA256.fullmatch(active_receipt)
        or authority["deploymentEnvironment"] != expected_environment
        or authority["expectedDatabaseRevision"] != expected_revision
        or not isinstance(commits, list)
        or len(commits) != 2
        or not all(
            isinstance(commit, str) and COMMIT.fullmatch(commit) for commit in commits
        )
        or commits[0] == commits[1]
        or authority["publicationDigest"]
        != _publication_digest_for(
            request_digest=request_digest,
            active_receipt_sha256=active_receipt,
        )
    ):
        raise BackupContractError(
            "backup commercial data publication authority is not active and exact"
        )


def _capture_active_publication_authority(
    *,
    connection: psycopg.Connection[Any],
    required_revision: str,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        select
          singleton_key,
          publication_id::text,
          state,
          request_digest,
          request_payload::text,
          publication_digest,
          previous_receipt_sha256,
          active_receipt_sha256,
          expected_database_revision,
          compatible_application_commits::text,
          activated_at
        from platform.commercial_data_publication_state
        order by singleton_key
        """
    ).fetchall()
    revisions = [
        str(row[0])
        for row in connection.execute(
            "select version_num from public.alembic_version order by version_num"
        ).fetchall()
    ]
    if len(rows) != 1 or revisions != [required_revision]:
        raise BackupContractError(
            "commercial data backup requires one active authority at the exact revision"
        )
    (
        singleton_key,
        publication_id,
        state,
        stored_request_digest,
        encoded_request,
        publication_digest,
        previous_receipt,
        active_receipt,
        expected_database_revision,
        encoded_commits,
        activated_at,
    ) = rows[0]
    try:
        request = json.loads(encoded_request)
        commits = json.loads(encoded_commits)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BackupContractError(
            "commercial data publication authority JSON is invalid"
        ) from exc
    _validate_publication_request(request)
    actual_request_digest = hashlib.sha256(
        _canonical_json(request).encode("utf-8")
    ).hexdigest()
    expected_commits = [
        request["candidateApplicationCommit"],
        request["rollbackApplicationCommit"],
    ]
    if (
        singleton_key is not True
        or state != "active"
        or stored_request_digest != actual_request_digest
        or not isinstance(previous_receipt, str)
        or not HEX_SHA256.fullmatch(previous_receipt)
        or not isinstance(active_receipt, str)
        or not HEX_SHA256.fullmatch(active_receipt)
        or publication_digest
        != _publication_digest_for(
            request_digest=actual_request_digest,
            active_receipt_sha256=active_receipt,
        )
        or expected_database_revision != required_revision
        or request["expectedDatabaseRevision"] != required_revision
        or commits != expected_commits
        or activated_at is None
    ):
        raise BackupContractError(
            "commercial data publication authority is not active and exact"
        )
    authority = {
        "schemaVersion": PUBLICATION_AUTHORITY_FORMAT_VERSION,
        "publicationId": publication_id,
        "state": state,
        "requestDigest": actual_request_digest,
        "publicationDigest": publication_digest,
        "activeReceiptSha256": active_receipt,
        "deploymentEnvironment": request["deploymentEnvironment"],
        "expectedDatabaseRevision": expected_database_revision,
        "compatibleApplicationCommits": commits,
    }
    _validate_publication_authority(
        authority,
        expected_revision=required_revision,
        expected_environment=request["deploymentEnvironment"],
    )
    return authority


def _load_restore_target_authority(
    *,
    path: Path,
    expected_environment_id: str,
) -> dict[str, str]:
    try:
        metadata = path.stat()
        authority = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupContractError("restore target authority is unreadable") from exc
    if (
        metadata.st_uid != 0
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BackupContractError(
            "restore target authority must be root-owned and not group/world writable"
        )
    if not isinstance(authority, dict):
        raise BackupContractError("restore target authority payload is invalid")
    expected_keys = {
        "schemaVersion",
        "purpose",
        "environmentId",
        "executorIdentitySha256",
        "databaseName",
        "clusterSystemIdentifierSha256",
    }
    if set(authority) != expected_keys:
        raise BackupContractError("restore target authority fields are invalid")
    if (
        authority.get("schemaVersion") != RESTORE_TARGET_FORMAT_VERSION
        or authority.get("purpose") != RESTORE_TARGET_PURPOSE
        or authority.get("environmentId") != expected_environment_id
        or not isinstance(authority.get("databaseName"), str)
        or not authority["databaseName"]
        or not isinstance(authority.get("executorIdentitySha256"), str)
        or not HEX_SHA256.fullmatch(authority["executorIdentitySha256"])
        or not isinstance(
            authority.get("clusterSystemIdentifierSha256"),
            str,
        )
        or not HEX_SHA256.fullmatch(authority["clusterSystemIdentifierSha256"])
    ):
        raise BackupContractError("restore target authority does not match the request")
    if authority["executorIdentitySha256"] != _executor_identity_sha256():
        raise BackupContractError(
            "restore target authority belongs to another executor"
        )
    return authority


def _executor_identity_sha256() -> str:
    path = Path("/etc/machine-id")
    try:
        metadata = path.stat()
        identity = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise BackupContractError(
            "root-owned /etc/machine-id is required for restore rehearsal"
        ) from exc
    if (
        metadata.st_uid != 0
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not identity
    ):
        raise BackupContractError("restore executor identity is not trustworthy")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _assert_restore_target_database(
    *,
    connection: psycopg.Connection[Any],
    manifest: dict[str, Any],
    target_authority: dict[str, str],
) -> None:
    database_name, database_identity, cluster_identity = _database_identities(
        connection
    )
    if cluster_identity == manifest.get("sourceClusterIdentitySha256"):
        raise BackupContractError(
            "restore target is on the backup source PostgreSQL cluster"
        )
    if database_identity == manifest.get("sourceDatabaseIdentitySha256"):
        raise BackupContractError("restore target database is the backup source")
    if (
        database_name != target_authority["databaseName"]
        or cluster_identity != target_authority["clusterSystemIdentifierSha256"]
    ):
        raise BackupContractError(
            "restore connection does not match the root-owned target authority"
        )


def _verified_restore_target_identity(
    *,
    connection: psycopg.Connection[Any],
    target_authority: dict[str, str],
) -> str:
    database_name, database_identity, cluster_identity = _database_identities(
        connection
    )
    if (
        database_name != target_authority["databaseName"]
        or cluster_identity != target_authority["clusterSystemIdentifierSha256"]
    ):
        raise BackupContractError(
            "restore connection does not match the root-owned target authority"
        )
    return database_identity


def restore_target_identity(
    *,
    database_url: str,
    target_environment_id: str,
    target_identity_file: Path,
) -> str:
    """Return the live, authority-verified database identity used for locking."""

    _validate_database_url(database_url)
    target_authority = _load_restore_target_authority(
        path=target_identity_file,
        expected_environment_id=target_environment_id,
    )
    with psycopg.connect(database_url) as connection:
        return _verified_restore_target_identity(
            connection=connection,
            target_authority=target_authority,
        )


def _restore_advisory_lock_key(restore_identity: str) -> int:
    if RESTORE_IDENTITY.fullmatch(restore_identity) is None:
        raise BackupContractError("restore target identity is invalid")
    digest = hashlib.sha256(
        b"anan-restore-rehearsal-lock/v1\0" + bytes.fromhex(restore_identity)
    ).digest()
    unsigned = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return unsigned if unsigned < 2**63 else unsigned - 2**64


def hold_restore_target_lock(
    *,
    database_url: str,
    target_environment_id: str,
    target_identity_file: Path,
    expected_restore_identity: str,
    ready_file: Path,
) -> None:
    """Hold one non-blocking session lock until the invoking shell exits."""

    _validate_database_url(database_url)
    if RESTORE_IDENTITY.fullmatch(expected_restore_identity) is None:
        raise BackupContractError("expected restore target identity is invalid")
    target_authority = _load_restore_target_authority(
        path=target_identity_file,
        expected_environment_id=target_environment_id,
    )
    invoking_parent = os.getppid()
    with psycopg.connect(database_url, autocommit=True) as connection:
        actual_identity = _verified_restore_target_identity(
            connection=connection,
            target_authority=target_authority,
        )
        if actual_identity != expected_restore_identity:
            raise BackupContractError(
                "restore target identity changed before lock acquisition"
            )
        lock_key = _restore_advisory_lock_key(actual_identity)
        acquired = connection.execute(
            "select pg_try_advisory_lock(%s)",
            (lock_key,),
        ).fetchone()[0]
        if acquired is not True:
            raise BackupContractError(
                "another restore rehearsal already owns this target database"
            )
        if os.getppid() != invoking_parent:
            raise BackupContractError("restore invoker exited before lock readiness")
        _write_exclusive_json(
            ready_file,
            {
                "schemaVersion": "anan-restore-target-lock/v1",
                "restoreIdentity": actual_identity,
                "holderPid": os.getpid(),
            },
        )
        while os.getppid() == invoking_parent:
            time.sleep(0.1)


def verify_restore_target_lock_ready(
    *,
    ready_file: Path,
    expected_restore_identity: str,
    expected_holder_pid: int,
) -> None:
    """Bind the shell's live child PID to the holder's exclusive ready file."""

    _validate_restore_identity(expected_restore_identity)
    if (
        isinstance(expected_holder_pid, bool)
        or not isinstance(expected_holder_pid, int)
        or expected_holder_pid <= 1
    ):
        raise BackupContractError("restore lock holder PID is invalid")
    payload = _read_regular_json_nofollow(ready_file)
    if payload != {
        "schemaVersion": "anan-restore-target-lock/v1",
        "restoreIdentity": expected_restore_identity,
        "holderPid": expected_holder_pid,
    }:
        raise BackupContractError("restore target lock readiness is invalid")
    try:
        os.kill(expected_holder_pid, 0)
    except OSError as exc:
        raise BackupContractError("restore target lock holder is not live") from exc


def publish_backup_set(
    *,
    staging_directory: Path,
    destination_directory: Path,
    artifact_basename: str,
) -> Path:
    """Durably publish one complete set with a no-replace directory rename."""

    if not staging_directory.is_absolute() or not destination_directory.is_absolute():
        raise BackupContractError("backup publication paths must be absolute")
    artifact_match = BACKUP_ARTIFACT_NAME.fullmatch(artifact_basename)
    if artifact_match is None:
        raise BackupContractError("backup artifact filename is invalid")
    run_id = artifact_match.group("run_id")
    if destination_directory.name != f"anan-{run_id}.backup-set":
        raise BackupContractError(
            "backup set directory does not match its run identity"
        )
    expected_staging_prefix = f".{destination_directory.name}.staging."
    if not staging_directory.name.startswith(expected_staging_prefix):
        raise BackupContractError("backup staging directory name is invalid")
    try:
        staging_parent = staging_directory.parent.resolve(strict=True)
        destination_parent = destination_directory.parent.resolve(strict=True)
        staging_metadata = staging_directory.lstat()
    except OSError as exc:
        raise BackupContractError("backup staging directory is unavailable") from exc
    if (
        staging_parent != destination_parent
        or not stat.S_ISDIR(staging_metadata.st_mode)
        or stat.S_ISLNK(staging_metadata.st_mode)
    ):
        raise BackupContractError(
            "backup staging and destination must share one trusted directory"
        )
    try:
        destination_directory.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise BackupContractError("backup destination could not be inspected") from exc
    else:
        raise BackupContractError("backup destination already exists")

    hidden_artifact = staging_directory / f".{artifact_basename}.partial"
    hidden_signature = staging_directory / f".{artifact_basename}.sig.partial"
    hidden_checksum = staging_directory / f".{artifact_basename}.sha256.partial"
    expected_hidden_names = {
        hidden_artifact.name,
        hidden_signature.name,
        hidden_checksum.name,
    }
    if {entry.name for entry in staging_directory.iterdir()} != expected_hidden_names:
        raise BackupContractError(
            "backup staging directory is not one complete sealed set"
        )
    for path in (hidden_artifact, hidden_signature, hidden_checksum):
        _fsync_regular_file(path)
    if hidden_artifact.stat().st_size <= 0 or hidden_signature.stat().st_size <= 0:
        raise BackupContractError("backup artifact or signature is empty")
    expected_checksum = f"{sha256_file(hidden_artifact)}  {artifact_basename}\n"
    if hidden_checksum.read_text(encoding="utf-8") != expected_checksum:
        raise BackupContractError("backup checksum sidecar is invalid")

    published_artifact = staging_directory / artifact_basename
    published_signature = staging_directory / f"{artifact_basename}.sig"
    published_checksum = staging_directory / f"{artifact_basename}.sha256"
    hidden_artifact.rename(published_artifact)
    hidden_signature.rename(published_signature)
    hidden_checksum.rename(published_checksum)
    _fsync_directory(staging_directory)
    _rename_directory_noreplace(
        source=staging_directory,
        destination=destination_directory,
    )
    _fsync_directory(destination_parent)
    return destination_directory / artifact_basename


def publish_restore_report_set(
    *,
    staging_directory: Path,
    destination_directory: Path,
    report_basename: str,
) -> Path:
    """Durably publish one complete restore report set without replacement."""

    if not staging_directory.is_absolute() or not destination_directory.is_absolute():
        raise BackupContractError("restore report publication paths must be absolute")
    set_match = RESTORE_REPORT_SET_NAME.fullmatch(destination_directory.name)
    if set_match is None:
        raise BackupContractError("restore report set directory name is invalid")
    run_id = set_match.group("run_id")
    report_suffix = f"-{run_id}-restore-report.json"
    if not report_basename.endswith(report_suffix):
        raise BackupContractError(
            "restore report filename does not match its run identity"
        )
    backup_stem = report_basename[: -len(report_suffix)]
    backup_name = f"{backup_stem}.backup.age"
    if (
        BACKUP_ARTIFACT_NAME.fullmatch(backup_name) is None
        and LEGACY_BACKUP_ARTIFACT_NAME.fullmatch(backup_name) is None
    ):
        raise BackupContractError(
            "restore report filename does not identify a valid backup"
        )
    expected_staging_prefix = f".{destination_directory.name}.staging."
    if not staging_directory.name.startswith(expected_staging_prefix):
        raise BackupContractError("restore report staging directory name is invalid")
    try:
        staging_parent = staging_directory.parent.resolve(strict=True)
        destination_parent = destination_directory.parent.resolve(strict=True)
        staging_metadata = staging_directory.lstat()
    except OSError as exc:
        raise BackupContractError(
            "restore report staging directory is unavailable"
        ) from exc
    if (
        staging_parent != destination_parent
        or not stat.S_ISDIR(staging_metadata.st_mode)
        or stat.S_ISLNK(staging_metadata.st_mode)
    ):
        raise BackupContractError(
            "restore report staging and destination must share one trusted directory"
        )
    try:
        destination_directory.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise BackupContractError(
            "restore report destination could not be inspected"
        ) from exc
    else:
        raise BackupContractError("restore report destination already exists")

    hidden_report = staging_directory / f".{report_basename}.partial"
    hidden_signature = staging_directory / f".{report_basename}.sig.partial"
    hidden_checksum = staging_directory / f".{report_basename}.sha256.partial"
    expected_hidden_names = {
        hidden_report.name,
        hidden_signature.name,
        hidden_checksum.name,
    }
    if {entry.name for entry in staging_directory.iterdir()} != expected_hidden_names:
        raise BackupContractError(
            "restore report staging directory is not one complete sealed set"
        )
    for path in (hidden_report, hidden_signature, hidden_checksum):
        _fsync_regular_file(path)
    if hidden_report.stat().st_size <= 0 or hidden_signature.stat().st_size <= 0:
        raise BackupContractError("restore report or signature is empty")
    expected_checksum = f"{sha256_file(hidden_report)}  {report_basename}\n"
    if hidden_checksum.read_text(encoding="utf-8") != expected_checksum:
        raise BackupContractError("restore report checksum sidecar is invalid")

    published_report = staging_directory / report_basename
    published_signature = staging_directory / f"{report_basename}.sig"
    published_checksum = staging_directory / f"{report_basename}.sha256"
    hidden_report.rename(published_report)
    hidden_signature.rename(published_signature)
    hidden_checksum.rename(published_checksum)
    _fsync_directory(staging_directory)
    _rename_directory_noreplace(
        source=staging_directory,
        destination=destination_directory,
    )
    _fsync_directory(destination_parent)
    return destination_directory / report_basename


def verify_backup_file_layout(*, backup_file: Path) -> dict[str, str]:
    """Reject hidden/incomplete sets before restore reads any sidecar."""

    if not backup_file.is_absolute():
        raise BackupContractError("backup file must be an absolute path")
    artifact_match = BACKUP_ARTIFACT_NAME.fullmatch(backup_file.name)
    legacy_match = LEGACY_BACKUP_ARTIFACT_NAME.fullmatch(backup_file.name)
    if artifact_match is None and legacy_match is None:
        raise BackupContractError("backup artifact filename is invalid")
    parent = backup_file.parent
    if parent.name.startswith("."):
        raise BackupContractError(
            "backup artifact belongs to an unpublished staging directory"
        )
    try:
        parent_metadata = parent.lstat()
    except OSError as exc:
        raise BackupContractError("backup set directory is unavailable") from exc
    if not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(
        parent_metadata.st_mode
    ):
        raise BackupContractError("backup set directory must be a real directory")
    expected_paths = (
        backup_file,
        Path(f"{backup_file}.sig"),
        Path(f"{backup_file}.sha256"),
    )
    for path in expected_paths:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise BackupContractError("backup set is incomplete") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise BackupContractError(
                "backup set must contain regular non-symlink files"
            )

    identity_match = artifact_match or legacy_match
    assert identity_match is not None
    run_id = identity_match.group("run_id")
    set_directory_name = f"anan-{run_id}.backup-set"
    if artifact_match is not None:
        if parent.name != set_directory_name:
            raise BackupContractError(
                "new backup artifact is outside its atomic set directory"
            )
        if {entry.name for entry in parent.iterdir()} != {
            path.name for path in expected_paths
        }:
            raise BackupContractError(
                "published backup set contains unexpected entries"
            )
        layout = "atomic-set"
    else:
        if parent.name.endswith(".backup-set"):
            raise BackupContractError(
                "backup set directory and artifact identities differ"
            )
        # Preserve recovery of complete pre-contract backups, but hidden
        # staging paths are never accepted.
        layout = "legacy-complete-set"
    return {
        "artifact": str(backup_file),
        "layout": layout,
        "runId": run_id,
    }


def snapshot_restore_set(
    *,
    backup_file: Path,
    snapshot_directory: Path,
) -> dict[str, str]:
    """Pin and copy one coherent backup set before any verification or decrypt."""

    if not backup_file.is_absolute() or not snapshot_directory.is_absolute():
        raise BackupContractError("backup snapshot paths must be absolute")
    if not hasattr(os, "O_NOFOLLOW"):
        raise BackupContractError("O_NOFOLLOW is required to snapshot a backup set")
    artifact_match = BACKUP_ARTIFACT_NAME.fullmatch(backup_file.name)
    legacy_match = LEGACY_BACKUP_ARTIFACT_NAME.fullmatch(backup_file.name)
    identity_match = artifact_match or legacy_match
    if identity_match is None:
        raise BackupContractError("backup artifact filename is invalid")
    if backup_file.parent.name.startswith("."):
        raise BackupContractError(
            "backup artifact belongs to an unpublished staging directory"
        )
    run_id = identity_match.group("run_id")
    expected_names = (
        backup_file.name,
        f"{backup_file.name}.sig",
        f"{backup_file.name}.sha256",
    )
    source_directory_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        source_directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        source_directory_flags |= os.O_DIRECTORY
    try:
        source_directory_fd = os.open(
            backup_file.parent,
            source_directory_flags,
        )
    except OSError as exc:
        raise BackupContractError("backup set directory is unavailable") from exc
    source_descriptors: dict[str, int] = {}
    try:
        parent_metadata = os.fstat(source_directory_fd)
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise BackupContractError("backup set directory must be a real directory")
        source_entries = set(os.listdir(source_directory_fd))
        if artifact_match is not None:
            if backup_file.parent.name != f"anan-{run_id}.backup-set":
                raise BackupContractError(
                    "new backup artifact is outside its atomic set directory"
                )
            if source_entries != set(expected_names):
                raise BackupContractError(
                    "published backup set contains unexpected entries"
                )
            layout = "atomic-set"
        else:
            if backup_file.parent.name.endswith(".backup-set"):
                raise BackupContractError(
                    "backup set directory and artifact identities differ"
                )
            if not set(expected_names).issubset(source_entries):
                raise BackupContractError("backup set is incomplete")
            layout = "legacy-complete-set"

        source_file_flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            source_file_flags |= os.O_CLOEXEC
        source_metadata: dict[str, os.stat_result] = {}
        for name in expected_names:
            try:
                descriptor = os.open(
                    name,
                    source_file_flags,
                    dir_fd=source_directory_fd,
                )
            except OSError as exc:
                raise BackupContractError(
                    "backup set entry cannot be pinned without following links"
                ) from exc
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                os.close(descriptor)
                raise BackupContractError(
                    "backup set must contain single-link regular files"
                )
            source_descriptors[name] = descriptor
            source_metadata[name] = metadata
        if (
            source_metadata[backup_file.name].st_size <= 0
            or source_metadata[f"{backup_file.name}.sig"].st_size <= 0
        ):
            raise BackupContractError("backup artifact or signature is empty")

        try:
            os.mkdir(snapshot_directory, mode=0o700)
        except OSError as exc:
            raise BackupContractError(
                "private backup snapshot directory cannot be created"
            ) from exc
        destination_metadata = snapshot_directory.lstat()
        if (
            not stat.S_ISDIR(destination_metadata.st_mode)
            or stat.S_ISLNK(destination_metadata.st_mode)
            or destination_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(destination_metadata.st_mode) != 0o700
        ):
            raise BackupContractError(
                "backup snapshot directory must be private and process-owned"
            )
        destination_directory_fd = os.open(
            snapshot_directory,
            source_directory_flags,
        )
        artifact_digest = ""
        try:
            destination_file_flags = (
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            )
            if hasattr(os, "O_CLOEXEC"):
                destination_file_flags |= os.O_CLOEXEC
            for name in expected_names:
                destination_fd = os.open(
                    name,
                    destination_file_flags,
                    0o600,
                    dir_fd=destination_directory_fd,
                )
                digest = hashlib.sha256()
                try:
                    source_fd = source_descriptors[name]
                    while True:
                        chunk = os.read(source_fd, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        view = memoryview(chunk)
                        while view:
                            written = os.write(destination_fd, view)
                            view = view[written:]
                    os.fsync(destination_fd)
                finally:
                    os.close(destination_fd)
                if name == backup_file.name:
                    artifact_digest = digest.hexdigest()
            os.fsync(destination_directory_fd)
        finally:
            os.close(destination_directory_fd)

        for name, descriptor in source_descriptors.items():
            after_descriptor = os.fstat(descriptor)
            try:
                after_directory_entry = os.stat(
                    name,
                    dir_fd=source_directory_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise BackupContractError(
                    "backup set changed while its snapshot was copied"
                ) from exc
            expected_identity = _stable_file_identity(source_metadata[name])
            if (
                _stable_file_identity(after_descriptor) != expected_identity
                or _stable_file_identity(after_directory_entry) != expected_identity
            ):
                raise BackupContractError(
                    "backup set changed while its snapshot was copied"
                )
        if artifact_match is not None and set(os.listdir(source_directory_fd)) != set(
            expected_names
        ):
            raise BackupContractError(
                "published backup set changed while its snapshot was copied"
            )
        checksum_text = (snapshot_directory / f"{backup_file.name}.sha256").read_text(
            encoding="utf-8"
        )
        if checksum_text != f"{artifact_digest}  {backup_file.name}\n":
            raise BackupContractError(
                "backup checksum sidecar does not match the pinned artifact"
            )
    except (OSError, UnicodeDecodeError) as exc:
        raise BackupContractError("backup set snapshot failed") from exc
    finally:
        for descriptor in source_descriptors.values():
            os.close(descriptor)
        os.close(source_directory_fd)
    return {
        "artifact": str(snapshot_directory / backup_file.name),
        "artifactSha256": artifact_digest,
        "layout": layout,
        "runId": run_id,
    }


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def create_restore_workspace(
    *,
    state_directory: Path,
    restore_identity: str,
) -> Path:
    """Create a marker-bound private workspace for one restore target."""

    _validate_restore_identity(restore_identity)
    _prepare_private_state_directory(state_directory)
    workspace = Path(
        tempfile.mkdtemp(
            prefix=f".anan-restore-{restore_identity}.work.",
            dir=state_directory,
        )
    )
    workspace.chmod(0o700)
    _write_exclusive_json(
        workspace / ".anan-restore-owner.json",
        {
            "schemaVersion": RESTORE_WORKSPACE_FORMAT_VERSION,
            "restoreIdentity": restore_identity,
            "ownerUid": os.geteuid(),
            "creatorPid": os.getpid(),
        },
    )
    _fsync_directory(workspace)
    _fsync_directory(state_directory)
    return workspace


def reap_restore_residue(
    *,
    state_directory: Path,
    report_directory: Path,
    restore_identity: str,
    active_workspace: Path,
) -> dict[str, int]:
    """Remove only stale state carrying the current locked target identity."""

    _validate_restore_identity(restore_identity)
    _prepare_private_state_directory(state_directory)
    _validate_restore_workspace(
        active_workspace,
        state_directory=state_directory,
        restore_identity=restore_identity,
    )
    if not report_directory.is_absolute():
        raise BackupContractError("restore report directory must be absolute")
    try:
        report_metadata = report_directory.lstat()
    except OSError as exc:
        raise BackupContractError("restore report directory is unavailable") from exc
    if not stat.S_ISDIR(report_metadata.st_mode) or stat.S_ISLNK(
        report_metadata.st_mode
    ):
        raise BackupContractError("restore report directory must be a real directory")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise BackupContractError(
            "safe descriptor-relative residue removal is unavailable"
        )

    removed_workspaces = 0
    workspace_prefix = f".anan-restore-{restore_identity}.work."
    active_identity = _directory_identity(active_workspace)
    for candidate in state_directory.iterdir():
        if not candidate.name.startswith(workspace_prefix):
            continue
        if _directory_identity(candidate) == active_identity:
            continue
        _validate_restore_workspace(
            candidate,
            state_directory=state_directory,
            restore_identity=restore_identity,
        )
        shutil.rmtree(candidate)
        removed_workspaces += 1

    removed_report_stagings = 0
    staging_marker = f".restore-report-set.staging.{restore_identity}."
    for candidate in report_directory.iterdir():
        if (
            not candidate.name.startswith(".anan-")
            or staging_marker not in candidate.name
        ):
            continue
        _validate_restore_report_staging(
            candidate,
            report_directory=report_directory,
            restore_identity=restore_identity,
        )
        shutil.rmtree(candidate)
        removed_report_stagings += 1
    _fsync_directory(state_directory)
    _fsync_directory(report_directory)
    return {
        "removedReportStagings": removed_report_stagings,
        "removedWorkspaces": removed_workspaces,
    }


def _prepare_private_state_directory(path: Path) -> None:
    if not path.is_absolute():
        raise BackupContractError("restore state directory must be absolute")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise BackupContractError("restore state directory cannot be created") from exc
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BackupContractError("restore state directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise BackupContractError(
            "restore state directory must be a mode-0700 process-owned directory"
        )


def _validate_restore_identity(restore_identity: str) -> None:
    if RESTORE_IDENTITY.fullmatch(restore_identity) is None:
        raise BackupContractError("restore target identity is invalid")


def _directory_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BackupContractError("restore residue directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BackupContractError("restore residue must be a real directory")
    return (metadata.st_dev, metadata.st_ino)


def _validate_restore_workspace(
    workspace: Path,
    *,
    state_directory: Path,
    restore_identity: str,
) -> None:
    prefix = f".anan-restore-{restore_identity}.work."
    if (
        not workspace.is_absolute()
        or workspace.parent != state_directory
        or not workspace.name.startswith(prefix)
        or RESTORE_WORKSPACE_TOKEN.fullmatch(workspace.name[len(prefix) :]) is None
    ):
        raise BackupContractError("restore workspace identity is invalid")
    metadata = workspace.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise BackupContractError("restore workspace is not private and process-owned")
    marker_path = workspace / ".anan-restore-owner.json"
    marker = _read_regular_json_nofollow(marker_path)
    if (
        not isinstance(marker, dict)
        or set(marker)
        != {
            "schemaVersion",
            "restoreIdentity",
            "ownerUid",
            "creatorPid",
        }
        or marker.get("schemaVersion") != RESTORE_WORKSPACE_FORMAT_VERSION
        or marker.get("restoreIdentity") != restore_identity
        or marker.get("ownerUid") != os.geteuid()
        or not isinstance(marker.get("creatorPid"), int)
    ):
        raise BackupContractError("restore workspace ownership marker is invalid")
    workspace_parts = len(workspace.parts)
    for root, directories, files in os.walk(workspace, followlinks=False):
        root_path = Path(root)
        if len(root_path.parts) - workspace_parts > 3:
            raise BackupContractError("restore workspace nesting is invalid")
        for name in (*directories, *files):
            entry = root_path / name
            entry_metadata = entry.lstat()
            if (
                stat.S_ISLNK(entry_metadata.st_mode)
                or entry_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(entry_metadata.st_mode) & 0o077
                or not (
                    stat.S_ISDIR(entry_metadata.st_mode)
                    or stat.S_ISREG(entry_metadata.st_mode)
                )
            ):
                raise BackupContractError("restore workspace contains an unsafe entry")


def _validate_restore_report_staging(
    staging: Path,
    *,
    report_directory: Path,
    restore_identity: str,
) -> None:
    marker = f".staging.{restore_identity}."
    prefix, separator, token = staging.name.partition(marker)
    report_set_name = prefix.removeprefix(".")
    if (
        not separator
        or staging.parent != report_directory
        or RESTORE_REPORT_SET_NAME.fullmatch(report_set_name) is None
        or RESTORE_WORKSPACE_TOKEN.fullmatch(token) is None
    ):
        raise BackupContractError("restore report staging identity is invalid")
    metadata = staging.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise BackupContractError("restore report staging is not private")
    set_match = RESTORE_REPORT_SET_NAME.fullmatch(report_set_name)
    assert set_match is not None
    run_id = set_match.group("run_id")
    for entry in staging.iterdir():
        entry_metadata = entry.lstat()
        if (
            not stat.S_ISREG(entry_metadata.st_mode)
            or stat.S_ISLNK(entry_metadata.st_mode)
            or entry_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(entry_metadata.st_mode) & 0o077
            or not _is_restore_report_partial_name(entry.name, run_id=run_id)
        ):
            raise BackupContractError("restore report staging contains an unsafe entry")


def _is_restore_report_partial_name(name: str, *, run_id: str) -> bool:
    if not name.startswith("."):
        return False
    visible_name = name[1:]
    for suffix in (".sha256.partial", ".sig.partial", ".partial"):
        if visible_name.endswith(suffix):
            report_basename = visible_name[: -len(suffix)]
            break
    else:
        return False
    report_suffix = f"-{run_id}-restore-report.json"
    if not report_basename.endswith(report_suffix):
        return False
    backup_stem = report_basename[: -len(report_suffix)]
    backup_name = f"{backup_stem}.backup.age"
    return (
        BACKUP_ARTIFACT_NAME.fullmatch(backup_name) is not None
        or LEGACY_BACKUP_ARTIFACT_NAME.fullmatch(backup_name) is not None
    )


def _read_regular_json_nofollow(path: Path) -> Any:
    if not hasattr(os, "O_NOFOLLOW"):
        raise BackupContractError("O_NOFOLLOW is required for restore state")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupContractError("restore ownership marker is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise BackupContractError("restore ownership marker is unsafe")
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupContractError("restore ownership marker is invalid") from exc
    finally:
        os.close(descriptor)


def _write_exclusive_json(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        raise BackupContractError("exclusive restore state path is invalid")
    directory_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    try:
        directory_fd = os.open(path.parent, directory_flags)
    except OSError as exc:
        raise BackupContractError(
            "exclusive restore state directory is unavailable"
        ) from exc
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
        try:
            payload = (
                json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory_fd)
    except OSError as exc:
        raise BackupContractError("exclusive restore state cannot be written") from exc
    finally:
        os.close(directory_fd)


def _fsync_regular_file(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupContractError("backup publication entry is not a regular file")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_directory_noreplace(*, source: Path, destination: Path) -> None:
    parent_descriptor = os.open(source.parent, os.O_RDONLY)
    try:
        library = ctypes.CDLL(None, use_errno=True)
        encoded_source = os.fsencode(source.name)
        encoded_destination = os.fsencode(destination.name)
        if hasattr(library, "renameat2"):
            operation = library.renameat2
            operation.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            operation.restype = ctypes.c_int
            result = operation(
                parent_descriptor,
                encoded_source,
                parent_descriptor,
                encoded_destination,
                1,  # RENAME_NOREPLACE
            )
        elif hasattr(library, "renameatx_np"):
            operation = library.renameatx_np
            operation.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            operation.restype = ctypes.c_int
            result = operation(
                parent_descriptor,
                encoded_source,
                parent_descriptor,
                encoded_destination,
                0x00000004,  # RENAME_EXCL
            )
        else:
            raise BackupContractError(
                "filesystem lacks atomic no-replace directory publication"
            )
        if result != 0:
            error_number = ctypes.get_errno()
            if error_number in (errno.EEXIST, errno.ENOTEMPTY):
                raise BackupContractError("backup destination already exists")
            raise BackupContractError(
                "atomic backup directory publication failed: "
                f"{os.strerror(error_number)}"
            )
    finally:
        os.close(parent_descriptor)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new-run-id")
    publish_parser = commands.add_parser("publish-set")
    publish_parser.add_argument(
        "--staging-directory",
        type=Path,
        required=True,
    )
    publish_parser.add_argument(
        "--destination-directory",
        type=Path,
        required=True,
    )
    publish_parser.add_argument("--artifact-basename", required=True)
    report_publish_parser = commands.add_parser("publish-restore-report-set")
    report_publish_parser.add_argument(
        "--staging-directory",
        type=Path,
        required=True,
    )
    report_publish_parser.add_argument(
        "--destination-directory",
        type=Path,
        required=True,
    )
    report_publish_parser.add_argument("--report-basename", required=True)
    layout_parser = commands.add_parser("verify-set-layout")
    layout_parser.add_argument("--backup-file", type=Path, required=True)
    snapshot_parser = commands.add_parser("snapshot-restore-set")
    snapshot_parser.add_argument("--backup-file", type=Path, required=True)
    snapshot_parser.add_argument(
        "--snapshot-directory",
        type=Path,
        required=True,
    )
    target_identity_parser = commands.add_parser("restore-target-identity")
    target_identity_parser.add_argument("--database-url", required=True)
    target_identity_parser.add_argument("--target-environment-id", required=True)
    target_identity_parser.add_argument(
        "--target-identity-file",
        type=Path,
        required=True,
    )
    target_lock_parser = commands.add_parser("hold-restore-target-lock")
    target_lock_parser.add_argument("--database-url", required=True)
    target_lock_parser.add_argument("--target-environment-id", required=True)
    target_lock_parser.add_argument(
        "--target-identity-file",
        type=Path,
        required=True,
    )
    target_lock_parser.add_argument("--expected-restore-identity", required=True)
    target_lock_parser.add_argument("--ready-file", type=Path, required=True)
    lock_ready_parser = commands.add_parser("verify-restore-target-lock-ready")
    lock_ready_parser.add_argument("--ready-file", type=Path, required=True)
    lock_ready_parser.add_argument("--expected-restore-identity", required=True)
    lock_ready_parser.add_argument("--expected-holder-pid", type=int, required=True)
    workspace_parser = commands.add_parser("create-restore-workspace")
    workspace_parser.add_argument("--state-directory", type=Path, required=True)
    workspace_parser.add_argument("--restore-identity", required=True)
    residue_parser = commands.add_parser("reap-restore-residue")
    residue_parser.add_argument("--state-directory", type=Path, required=True)
    residue_parser.add_argument("--report-directory", type=Path, required=True)
    residue_parser.add_argument("--restore-identity", required=True)
    residue_parser.add_argument("--active-workspace", type=Path, required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--database-url", required=True)
    capture_parser.add_argument("--dump", type=Path, required=True)
    capture_parser.add_argument("--manifest", type=Path, required=True)
    capture_parser.add_argument("--required-revision", required=True)
    capture_parser.add_argument("--source-environment-id", required=True)
    capture_parser.add_argument("--backup-target-id", required=True)
    extract_parser = commands.add_parser("extract")
    extract_parser.add_argument("--bundle", type=Path, required=True)
    extract_parser.add_argument("--output-directory", type=Path, required=True)
    preflight_parser = commands.add_parser("restore-preflight")
    preflight_parser.add_argument("--database-url", required=True)
    preflight_parser.add_argument("--manifest", type=Path, required=True)
    preflight_parser.add_argument("--dump", type=Path, required=True)
    preflight_parser.add_argument("--target-environment-id", required=True)
    preflight_parser.add_argument("--target-identity-file", type=Path, required=True)
    verify_parser = commands.add_parser("verify-restore")
    verify_parser.add_argument("--database-url", required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--dump", type=Path, required=True)
    verify_parser.add_argument("--target-environment-id", required=True)
    verify_parser.add_argument("--target-identity-file", type=Path, required=True)
    verify_parser.add_argument("--artifact-sha256", required=True)
    verify_parser.add_argument("--report", type=Path, required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command == "new-run-id":
        print(new_backup_run_id())
    elif arguments.command == "publish-set":
        print(
            publish_backup_set(
                staging_directory=arguments.staging_directory,
                destination_directory=arguments.destination_directory,
                artifact_basename=arguments.artifact_basename,
            )
        )
    elif arguments.command == "publish-restore-report-set":
        print(
            publish_restore_report_set(
                staging_directory=arguments.staging_directory,
                destination_directory=arguments.destination_directory,
                report_basename=arguments.report_basename,
            )
        )
    elif arguments.command == "verify-set-layout":
        print(
            json.dumps(
                verify_backup_file_layout(backup_file=arguments.backup_file),
                sort_keys=True,
            )
        )
    elif arguments.command == "snapshot-restore-set":
        print(
            json.dumps(
                snapshot_restore_set(
                    backup_file=arguments.backup_file,
                    snapshot_directory=arguments.snapshot_directory,
                ),
                sort_keys=True,
            )
        )
    elif arguments.command == "restore-target-identity":
        print(
            restore_target_identity(
                database_url=arguments.database_url,
                target_environment_id=arguments.target_environment_id,
                target_identity_file=arguments.target_identity_file,
            )
        )
    elif arguments.command == "hold-restore-target-lock":
        hold_restore_target_lock(
            database_url=arguments.database_url,
            target_environment_id=arguments.target_environment_id,
            target_identity_file=arguments.target_identity_file,
            expected_restore_identity=arguments.expected_restore_identity,
            ready_file=arguments.ready_file,
        )
    elif arguments.command == "verify-restore-target-lock-ready":
        verify_restore_target_lock_ready(
            ready_file=arguments.ready_file,
            expected_restore_identity=arguments.expected_restore_identity,
            expected_holder_pid=arguments.expected_holder_pid,
        )
    elif arguments.command == "create-restore-workspace":
        print(
            create_restore_workspace(
                state_directory=arguments.state_directory,
                restore_identity=arguments.restore_identity,
            )
        )
    elif arguments.command == "reap-restore-residue":
        print(
            json.dumps(
                reap_restore_residue(
                    state_directory=arguments.state_directory,
                    report_directory=arguments.report_directory,
                    restore_identity=arguments.restore_identity,
                    active_workspace=arguments.active_workspace,
                ),
                sort_keys=True,
            )
        )
    elif arguments.command == "capture":
        capture(
            database_url=arguments.database_url,
            dump_path=arguments.dump,
            manifest_path=arguments.manifest,
            required_revision=arguments.required_revision,
            source_environment_id=arguments.source_environment_id,
            backup_target_id=arguments.backup_target_id,
        )
    elif arguments.command == "extract":
        extract_bundle(
            bundle_path=arguments.bundle,
            output_directory=arguments.output_directory,
        )
    elif arguments.command == "restore-preflight":
        restore_preflight(
            database_url=arguments.database_url,
            manifest_path=arguments.manifest,
            dump_path=arguments.dump,
            target_environment_id=arguments.target_environment_id,
            target_identity_file=arguments.target_identity_file,
        )
    elif arguments.command == "verify-restore":
        verify_restore(
            database_url=arguments.database_url,
            manifest_path=arguments.manifest,
            dump_path=arguments.dump,
            target_environment_id=arguments.target_environment_id,
            target_identity_file=arguments.target_identity_file,
            artifact_sha256=arguments.artifact_sha256,
            report_path=arguments.report,
        )
    else:
        raise BackupContractError("unsupported backup artifact command")


if __name__ == "__main__":
    main()
