#!/usr/bin/env python3
"""Build and verify the encrypted database backup payload.

The shell entrypoints own encryption, signatures, and retention. This module owns
the PostgreSQL-consistent manifest and the restore evidence contract.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict


FORMAT_VERSION = "anan-postgres-backup/v1"
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


class BackupContractError(RuntimeError):
    """Raised when a backup or restore cannot satisfy the production contract."""


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
    database_revision: str,
    pg_dump_version: str,
    dump_path: Path,
    row_counts: dict[str, int],
) -> dict[str, Any]:
    if set(row_counts) != set(CRITICAL_TABLES):
        raise BackupContractError("critical table count set is incomplete")
    if any(not isinstance(value, int) or value < 0 for value in row_counts.values()):
        raise BackupContractError("critical table counts must be non-negative integers")
    return {
        "formatVersion": FORMAT_VERSION,
        "createdAt": created_at,
        "sourceEnvironmentId": source_environment_id,
        "backupTargetId": backup_target_id,
        "sourceDatabaseIdentitySha256": hashlib.sha256(
            source_identity.encode("utf-8")
        ).hexdigest(),
        "databaseRevision": database_revision,
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
        snapshot = str(connection.execute("select pg_export_snapshot()").fetchone()[0])
        database_name, server_address, server_port = connection.execute(
            "select current_database(), "
            "coalesce(inet_server_addr()::text, 'local-socket'), "
            "coalesce(inet_server_port()::text, 'local')"
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
            source_identity=f"{database_name}|{server_address}|{server_port}",
            database_revision=required_revision,
            pg_dump_version=pg_dump_version,
            dump_path=dump_path,
            row_counts=row_counts,
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
    return manifest


def restore_preflight(
    *,
    database_url: str,
    manifest_path: Path,
    dump_path: Path,
    target_environment_id: str,
) -> None:
    _validate_database_url(database_url)
    manifest = load_and_verify_manifest(
        manifest_path=manifest_path, dump_path=dump_path
    )
    if manifest.get("sourceEnvironmentId") == target_environment_id:
        raise BackupContractError("restore target environment is the backup source")
    with psycopg.connect(database_url) as connection:
        if _database_identity_sha256(connection) == manifest.get(
            "sourceDatabaseIdentitySha256"
        ):
            raise BackupContractError("restore target database is the backup source")
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
    artifact_sha256: str,
    report_path: Path,
) -> None:
    _validate_database_url(database_url)
    manifest = load_and_verify_manifest(
        manifest_path=manifest_path, dump_path=dump_path
    )
    if manifest.get("sourceEnvironmentId") == target_environment_id:
        raise BackupContractError("restore target environment is the backup source")
    expected_counts = manifest["criticalTableRowCounts"]
    with psycopg.connect(database_url) as connection:
        if _database_identity_sha256(connection) == manifest.get(
            "sourceDatabaseIdentitySha256"
        ):
            raise BackupContractError("restore target database is the backup source")
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
        "criticalTableRowCounts": actual_counts,
    }
    _write_json(report_path, report)


def _validate_database_url(database_url: str) -> None:
    if "+" in database_url.split(":", 1)[0]:
        raise BackupContractError("use a libpq URL, not a SQLAlchemy driver URL")
    parameters = conninfo_to_dict(database_url)
    if parameters.get("password"):
        raise BackupContractError(
            "database URL must not contain a password; use a mode-0600 PGPASSFILE"
        )


def _database_identity_sha256(connection: psycopg.Connection[Any]) -> str:
    database_name, server_address, server_port = connection.execute(
        "select current_database(), "
        "coalesce(inet_server_addr()::text, 'local-socket'), "
        "coalesce(inet_server_port()::text, 'local')"
    ).fetchone()
    identity = f"{database_name}|{server_address}|{server_port}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


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
    verify_parser = commands.add_parser("verify-restore")
    verify_parser.add_argument("--database-url", required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--dump", type=Path, required=True)
    verify_parser.add_argument("--target-environment-id", required=True)
    verify_parser.add_argument("--artifact-sha256", required=True)
    verify_parser.add_argument("--report", type=Path, required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command == "capture":
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
        )
    else:
        verify_restore(
            database_url=arguments.database_url,
            manifest_path=arguments.manifest,
            dump_path=arguments.dump,
            target_environment_id=arguments.target_environment_id,
            artifact_sha256=arguments.artifact_sha256,
            report_path=arguments.report,
        )


if __name__ == "__main__":
    main()
