from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.database_compatibility import current_database_revisions


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "scripts" / "backup_artifact.py"
SPEC = importlib.util.spec_from_file_location("backup_artifact", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
backup_artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_artifact)
sys.path.insert(0, str(ROOT / "ops" / "scripts"))
try:
    PUBLICATION_SPEC = importlib.util.spec_from_file_location(
        "backup_test_commercial_data_publication",
        ROOT / "ops" / "scripts" / "commercial_data_publication.py",
    )
    assert PUBLICATION_SPEC is not None and PUBLICATION_SPEC.loader is not None
    publication = importlib.util.module_from_spec(PUBLICATION_SPEC)
    PUBLICATION_SPEC.loader.exec_module(publication)
finally:
    sys.path.remove(str(ROOT / "ops" / "scripts"))


CANDIDATE_COMMIT = "1" * 40
ROLLBACK_COMMIT = "7" * 40
BASELINE_RECEIPT = "a" * 64
ACTIVE_RECEIPT = "b" * 64
CURRENT_DATABASE_REVISION = "20260802_94"


def _publication_authority(
    *,
    request_digest: str = "c" * 64,
    active_receipt: str = ACTIVE_RECEIPT,
    revision: str = "20260719_46",
    deployment_environment: str = "production-cn-1",
) -> dict[str, object]:
    return {
        "schemaVersion": backup_artifact.PUBLICATION_AUTHORITY_FORMAT_VERSION,
        "publicationId": "00000000-0000-0000-0000-000000000001",
        "state": "active",
        "requestDigest": request_digest,
        "publicationDigest": backup_artifact._publication_digest_for(
            request_digest=request_digest,
            active_receipt_sha256=active_receipt,
        ),
        "activeReceiptSha256": active_receipt,
        "deploymentEnvironment": deployment_environment,
        "expectedDatabaseRevision": revision,
        "compatibleApplicationCommits": [
            CANDIDATE_COMMIT,
            ROLLBACK_COMMIT,
        ],
    }


def _backup_target_authority(
    *,
    source_environment_id: str = "production-cn-1",
) -> dict[str, str]:
    return {
        "schemaVersion": backup_artifact.BACKUP_TARGET_FORMAT_VERSION,
        "purpose": backup_artifact.BACKUP_TARGET_PURPOSE,
        "targetId": "cross-domain-object-store-1",
        "sourceEnvironmentId": source_environment_id,
        "sourceMachineIdentitySha256": "1" * 64,
        "backupDirectory": "/mnt/anan-backup/encrypted",
        "targetMountPoint": "/mnt/anan-backup",
        "targetDeviceMajorMinor": "8:17",
        "targetFilesystemType": "ext4",
        "targetMountRootSha256": "3" * 64,
        "targetMountSourceSha256": "2" * 64,
    }


def _database_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _libpq_url(engine: Engine) -> str:
    return _database_url(engine).replace(
        "postgresql+psycopg:",
        "postgresql:",
        1,
    )


def _request() -> dict[str, object]:
    return publication.build_request(
        candidate_commit=CANDIDATE_COMMIT,
        rollback_commit=ROLLBACK_COMMIT,
        environment="preproduction",
        expected_revision=CURRENT_DATABASE_REVISION,
        position_exam_code="guokao",
        position_exam_year=2026,
        position_rule_set_version="rules-v1",
        position_data_version="positions-v1",
        position_window_manifest_sha256="d" * 64,
        knowledge_exam_scope="guokao",
        knowledge_track="xingce",
        knowledge_release_version="knowledge-v1",
        knowledge_source_data_version="knowledge-source-v1",
        knowledge_fresh_until="2026-12-31T00:00:00+00:00",
    )


def _activate_publication(engine: Engine) -> None:
    request = _request()
    started = publication.start_next_publication(
        database_url=_database_url(engine),
        request=request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    with psycopg.connect(_libpq_url(engine), autocommit=True) as holder:
        holder.execute(
            "select pg_advisory_lock(%s)",
            (backup_artifact.COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        try:
            publication.record_publication_receipt(
                database_url=_database_url(engine),
                expected_request_digest=started["requestDigest"],
                receipt_sha256=ACTIVE_RECEIPT,
            )
            publication.finish_publication(
                database_url=_database_url(engine),
                expected_request_digest=started["requestDigest"],
                receipt_sha256=ACTIVE_RECEIPT,
            )
        finally:
            holder.execute(
                "select pg_advisory_unlock(%s)",
                (backup_artifact.COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
            )


def test_backup_manifest_binds_snapshot_revision_dump_and_critical_tables(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"immutable custom-format fixture")
    counts = {table: index for index, table in enumerate(backup_artifact.CRITICAL_TABLES)}

    manifest = backup_artifact.build_manifest(
        created_at="2026-07-19T01:02:03+00:00",
        source_environment_id="production-cn-1",
        backup_target_authority=_backup_target_authority(),
        source_identity="cluster-1|gongkao|16384",
        source_cluster_identity="cluster-1",
        database_revision="20260719_46",
        pg_dump_version="pg_dump (PostgreSQL) 17.5",
        dump_path=dump,
        row_counts=counts,
        publication_authority=_publication_authority(),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = backup_artifact.load_and_verify_manifest(manifest_path=manifest_path, dump_path=dump)
    assert loaded["databaseRevision"] == "20260719_46"
    assert loaded["criticalTableRowCounts"] == counts
    assert "cluster-1|gongkao|16384" not in json.dumps(loaded)
    assert loaded["sourceClusterIdentitySha256"]
    assert loaded["commercialDataPublication"]["activeReceiptSha256"] == ACTIVE_RECEIPT
    assert loaded["backupTargetAuthority"]["targetDeviceMajorMinor"] == "8:17"
    assert loaded["backupTargetAuthoritySha256"] == backup_artifact.hashlib.sha256(
        backup_artifact._canonical_json(loaded["backupTargetAuthority"]).encode("utf-8")
    ).hexdigest()

    legacy_manifest = dict(manifest)
    legacy_manifest["formatVersion"] = backup_artifact.LEGACY_FORMAT_VERSION
    legacy_manifest.pop("backupTargetAuthority")
    legacy_manifest.pop("backupTargetAuthoritySha256")
    manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")
    legacy_loaded = backup_artifact.load_and_verify_manifest(
        manifest_path=manifest_path,
        dump_path=dump,
    )
    assert backup_artifact._backup_target_report_evidence(legacy_loaded) == {
        "backupTargetAuthorityStatus": "legacy-unbound",
        "backupTargetAuthoritySha256": None,
    }

    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dump.write_bytes(b"tampered")
    with pytest.raises(backup_artifact.BackupContractError, match="dump size"):
        backup_artifact.load_and_verify_manifest(manifest_path=manifest_path, dump_path=dump)


def test_frozen_v3_manifest_remains_restorable_but_reports_legacy_unbound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"frozen v3 custom-format fixture")
    publication_authority = _publication_authority()
    manifest = backup_artifact.build_manifest(
        created_at="2026-07-19T01:02:03+00:00",
        source_environment_id="production-cn-1",
        backup_target_authority=_backup_target_authority(),
        source_identity="cluster-1|gongkao|16384",
        source_cluster_identity="cluster-1",
        database_revision="20260719_46",
        pg_dump_version="pg_dump (PostgreSQL) 17.5",
        dump_path=dump,
        row_counts={table: 0 for table in backup_artifact.CRITICAL_TABLES},
        publication_authority=publication_authority,
    )
    manifest["formatVersion"] = backup_artifact.LEGACY_FORMAT_VERSION
    manifest.pop("backupTargetAuthority")
    manifest.pop("backupTargetAuthoritySha256")
    manifest_path = tmp_path / "manifest-v3.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    class Result:
        def __init__(self, *, one=None, all_rows=None) -> None:
            self.one = one
            self.all_rows = all_rows

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.all_rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query: str, *_args):
            if "select version_num from public.alembic_version" in query:
                return Result(all_rows=[("20260719_46",)])
            if "select count(*)" in query:
                return Result(one=(0,))
            return Result(one=(True,))

    monkeypatch.setattr(backup_artifact.psycopg, "connect", lambda *_args, **_kwargs: Connection())
    monkeypatch.setattr(
        backup_artifact,
        "_load_restore_target_authority",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        backup_artifact,
        "_assert_restore_target_database",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        backup_artifact,
        "_capture_active_publication_authority",
        lambda **_kwargs: publication_authority,
    )

    backup_artifact.restore_preflight(
        database_url="postgresql://restore@127.0.0.1:5432/disposable",
        manifest_path=manifest_path,
        dump_path=dump,
        target_environment_id="isolated-restore-1",
        target_identity_file=tmp_path / "authority.json",
    )
    report_path = tmp_path / "restore-report.json"
    backup_artifact.verify_restore(
        database_url="postgresql://restore@127.0.0.1:5432/disposable",
        manifest_path=manifest_path,
        dump_path=dump,
        target_environment_id="isolated-restore-1",
        target_identity_file=tmp_path / "authority.json",
        artifact_sha256="f" * 64,
        report_path=report_path,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["backupTargetAuthorityStatus"] == "legacy-unbound"
    assert report["backupTargetAuthoritySha256"] is None


def test_same_nanosecond_backup_run_identities_are_unique() -> None:
    timestamp_ns = 1_785_366_123_456_789_012

    with ThreadPoolExecutor(max_workers=16) as executor:
        run_ids = list(
            executor.map(
                lambda _index: backup_artifact.new_backup_run_id(timestamp_ns=timestamp_ns),
                range(128),
            )
        )

    assert len(set(run_ids)) == len(run_ids)
    assert all(backup_artifact.BACKUP_RUN_ID.fullmatch(run_id) for run_id in run_ids)
    assert len({run_id.rsplit("-", 1)[0] for run_id in run_ids}) == 1


def test_backup_set_publication_is_atomic_durable_and_never_overwrites(
    tmp_path: Path,
) -> None:
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    run_id = backup_artifact.new_backup_run_id(
        timestamp_ns=1_785_366_123_456_789_012,
        random_token="1" * 32,
    )
    artifact_basename = f"anan-{run_id}.backup.age"
    destination = backup_directory / f"anan-{run_id}.backup-set"

    def stage(payload: bytes) -> Path:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging.",
                dir=backup_directory,
            )
        )
        artifact = staging / f".{artifact_basename}.partial"
        signature = staging / f".{artifact_basename}.sig.partial"
        checksum = staging / f".{artifact_basename}.sha256.partial"
        artifact.write_bytes(payload)
        signature.write_bytes(b"detached-signature")
        checksum.write_text(
            f"{backup_artifact.sha256_file(artifact)}  {artifact_basename}\n",
            encoding="utf-8",
        )
        return staging

    first_staging = stage(b"first encrypted backup")
    published = backup_artifact.publish_backup_set(
        staging_directory=first_staging,
        destination_directory=destination,
        artifact_basename=artifact_basename,
    )

    assert published == destination / artifact_basename
    assert published.read_bytes() == b"first encrypted backup"
    assert not first_staging.exists()
    assert (
        backup_artifact.verify_backup_file_layout(backup_file=published)["layout"] == "atomic-set"
    )

    second_staging = stage(b"must not replace the first backup")
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="already exists",
    ):
        backup_artifact.publish_backup_set(
            staging_directory=second_staging,
            destination_directory=destination,
            artifact_basename=artifact_basename,
        )
    assert published.read_bytes() == b"first encrypted backup"

    loose_directory = tmp_path / "loose"
    loose_directory.mkdir()
    loose = loose_directory / artifact_basename
    loose.write_bytes(published.read_bytes())
    Path(f"{loose}.sig").write_bytes(Path(f"{published}.sig").read_bytes())
    Path(f"{loose}.sha256").write_bytes(Path(f"{published}.sha256").read_bytes())
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="outside its atomic set",
    ):
        backup_artifact.verify_backup_file_layout(backup_file=loose)

    legacy = loose_directory / "anan-20260730T010203Z.backup.age"
    legacy.write_bytes(b"complete legacy backup")
    Path(f"{legacy}.sig").write_bytes(b"legacy signature")
    Path(f"{legacy}.sha256").write_text(
        f"{backup_artifact.sha256_file(legacy)}  {legacy.name}\n",
        encoding="utf-8",
    )
    assert (
        backup_artifact.verify_backup_file_layout(backup_file=legacy)["layout"]
        == "legacy-complete-set"
    )


def test_restore_snapshot_uses_nofollow_regular_single_link_files(
    tmp_path: Path,
) -> None:
    run_id = backup_artifact.new_backup_run_id(
        timestamp_ns=1_785_366_123_456_789_012,
        random_token="2" * 32,
    )
    artifact_basename = f"anan-{run_id}.backup.age"
    backup_set = tmp_path / f"anan-{run_id}.backup-set"
    backup_set.mkdir()
    artifact = backup_set / artifact_basename
    artifact.write_bytes(b"encrypted snapshot")
    Path(f"{artifact}.sig").write_bytes(b"detached signature")
    Path(f"{artifact}.sha256").write_text(
        f"{backup_artifact.sha256_file(artifact)}  {artifact_basename}\n",
        encoding="utf-8",
    )

    snapshot = tmp_path / "snapshot"
    result = backup_artifact.snapshot_restore_set(
        backup_file=artifact,
        snapshot_directory=snapshot,
    )

    assert Path(result["artifact"]).read_bytes() == b"encrypted snapshot"
    assert result["artifactSha256"] == backup_artifact.sha256_file(artifact)
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in snapshot.iterdir())

    signature = Path(f"{artifact}.sig")
    signature.unlink()
    signature.symlink_to(tmp_path / "attacker-controlled-signature")
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="without following links",
    ):
        backup_artifact.snapshot_restore_set(
            backup_file=artifact,
            snapshot_directory=tmp_path / "symlink-snapshot",
        )


def test_restore_snapshot_rejects_source_identity_change_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = backup_artifact.new_backup_run_id(
        timestamp_ns=1_785_366_123_456_789_012,
        random_token="8" * 32,
    )
    artifact_basename = f"anan-{run_id}.backup.age"
    backup_set = tmp_path / f"anan-{run_id}.backup-set"
    backup_set.mkdir()
    artifact = backup_set / artifact_basename
    artifact.write_bytes(b"A" * (1024 * 1024 + 1))
    Path(f"{artifact}.sig").write_bytes(b"signature A")
    Path(f"{artifact}.sha256").write_text(
        f"{backup_artifact.sha256_file(artifact)}  {artifact_basename}\n",
        encoding="utf-8",
    )
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"unsigned replacement B")
    real_read = backup_artifact.os.read
    replaced = False

    def coordinated_read(descriptor: int, length: int) -> bytes:
        nonlocal replaced
        value = real_read(descriptor, length)
        if value and not replaced:
            replaced = True
            replacement.replace(artifact)
        return value

    monkeypatch.setattr(backup_artifact.os, "read", coordinated_read)

    with pytest.raises(
        backup_artifact.BackupContractError,
        match="changed while its snapshot was copied",
    ):
        backup_artifact.snapshot_restore_set(
            backup_file=artifact,
            snapshot_directory=tmp_path / "raced-snapshot",
        )
    assert replaced is True


def test_restore_lock_ready_file_binds_identity_and_live_holder_pid(
    tmp_path: Path,
) -> None:
    restore_identity = "9" * 64
    workspace = backup_artifact.create_restore_workspace(
        state_directory=tmp_path / "restore-state",
        restore_identity=restore_identity,
    )
    ready_file = workspace / "restore-lock.ready"
    backup_artifact._write_exclusive_json(
        ready_file,
        {
            "schemaVersion": "anan-restore-target-lock/v1",
            "restoreIdentity": restore_identity,
            "holderPid": os.getpid(),
        },
    )

    backup_artifact.verify_restore_target_lock_ready(
        ready_file=ready_file,
        expected_restore_identity=restore_identity,
        expected_holder_pid=os.getpid(),
    )
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="readiness is invalid",
    ):
        backup_artifact.verify_restore_target_lock_ready(
            ready_file=ready_file,
            expected_restore_identity="a" * 64,
            expected_holder_pid=os.getpid(),
        )


def test_concurrent_same_restore_report_identity_never_overwrites(
    tmp_path: Path,
) -> None:
    report_directory = tmp_path / "restore-reports"
    report_directory.mkdir()
    run_id = backup_artifact.new_backup_run_id(
        timestamp_ns=1_785_366_123_456_789_012,
        random_token="3" * 32,
    )
    backup_run_id = backup_artifact.new_backup_run_id(
        timestamp_ns=1_785_366_000_000_000_000,
        random_token="4" * 32,
    )
    report_basename = f"anan-{backup_run_id}-{run_id}-restore-report.json"
    destination = report_directory / f"anan-{run_id}.restore-report-set"

    def stage(payload: bytes) -> Path:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging.",
                dir=report_directory,
            )
        )
        report = staging / f".{report_basename}.partial"
        signature = staging / f".{report_basename}.sig.partial"
        checksum = staging / f".{report_basename}.sha256.partial"
        report.write_bytes(payload)
        signature.write_bytes(b"signature:" + payload)
        checksum.write_text(
            f"{backup_artifact.sha256_file(report)}  {report_basename}\n",
            encoding="utf-8",
        )
        return staging

    stagings = (stage(b"first report"), stage(b"second report"))

    def publish(staging: Path) -> tuple[str, str]:
        try:
            published = backup_artifact.publish_restore_report_set(
                staging_directory=staging,
                destination_directory=destination,
                report_basename=report_basename,
            )
        except backup_artifact.BackupContractError as exc:
            return ("error", str(exc))
        return ("published", str(published))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, stagings))

    assert [result[0] for result in results].count("published") == 1
    assert [result[0] for result in results].count("error") == 1
    error = next(result[1] for result in results if result[0] == "error")
    assert "already exists" in error
    published_report = destination / report_basename
    assert published_report.read_bytes() in {b"first report", b"second report"}
    assert {path.name for path in destination.iterdir()} == {
        report_basename,
        f"{report_basename}.sha256",
        f"{report_basename}.sig",
    }


def test_bundle_extraction_rejects_path_traversal_and_unexpected_members(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "malicious.tar"
    with tarfile.open(bundle, "w") as archive:
        payload = b"do not extract"
        member = tarfile.TarInfo("../outside")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(backup_artifact.BackupContractError, match="member set"):
        backup_artifact.extract_bundle(bundle_path=bundle, output_directory=tmp_path / "output")
    assert not (tmp_path / "outside").exists()


def test_backup_rejects_publishing_authority(db_engine: Engine) -> None:
    publication.start_next_publication(
        database_url=_database_url(db_engine),
        request=_request(),
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    with psycopg.connect(_libpq_url(db_engine)) as connection:
        connection.execute("begin transaction isolation level repeatable read read only")
        connection.execute(
            "select pg_advisory_xact_lock_shared(%s)",
            (backup_artifact.COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        with pytest.raises(
            backup_artifact.BackupContractError,
            match="not active and exact",
        ):
            backup_artifact._capture_active_publication_authority(
                connection=connection,
                required_revision=CURRENT_DATABASE_REVISION,
            )


def test_backup_capture_locks_before_reading_or_exporting_snapshot() -> None:
    source = inspect.getsource(backup_artifact.capture)

    assert (
        backup_artifact.COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY
        == publication.COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY
    )
    begin = source.index("begin transaction isolation level repeatable read read only")
    shared_lock = source.index("pg_advisory_xact_lock_shared")
    authority = source.index("_capture_active_publication_authority")
    snapshot = source.index("pg_export_snapshot")
    assert begin < shared_lock < authority < snapshot


def test_backup_target_authority_binds_source_and_live_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backup"
    backup_directory.mkdir(mode=0o700)
    machine_identity = "1" * 64
    mount_identity = {
        "targetMountPoint": str(tmp_path),
        "targetDeviceMajorMinor": "8:17",
        "targetFilesystemType": "ext4",
        "targetMountRootSha256": "3" * 64,
        "targetMountSourceSha256": "2" * 64,
    }
    authority = {
        "schemaVersion": backup_artifact.BACKUP_TARGET_FORMAT_VERSION,
        "purpose": backup_artifact.BACKUP_TARGET_PURPOSE,
        "targetId": "backup-failure-domain-b",
        "sourceEnvironmentId": "production-cn-1",
        "sourceMachineIdentitySha256": machine_identity,
        "backupDirectory": str(backup_directory),
        **mount_identity,
    }
    monkeypatch.setattr(
        backup_artifact,
        "_read_root_owned_authority",
        lambda *_args, **_kwargs: authority,
    )
    monkeypatch.setattr(
        backup_artifact,
        "_executor_identity_sha256",
        lambda: machine_identity,
    )
    monkeypatch.setattr(
        backup_artifact,
        "_live_mount_identity",
        lambda _path: mount_identity,
    )

    loaded = backup_artifact._load_backup_target_authority(
        path=tmp_path / "root-authority.json",
        expected_source_environment_id="production-cn-1",
        backup_directory=backup_directory,
    )

    assert loaded["targetId"] == "backup-failure-domain-b"
    authority["targetDeviceMajorMinor"] = "8:18"
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="live target mount",
    ):
        backup_artifact._load_backup_target_authority(
            path=tmp_path / "root-authority.json",
            expected_source_environment_id="production-cn-1",
            backup_directory=backup_directory,
        )


def test_live_mount_binding_selects_top_mount_and_cross_checks_directory_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "mounted-backup"
    backup_directory.mkdir()
    mount_point = str(tmp_path)
    old_source = "/dev/old"
    live_source = "/dev/live"
    mountinfo = "\n".join(
        (
            f"100 1 8:16 /old-root {mount_point} rw - ext4 {old_source} rw",
            f"101 1 8:17 /live-root {mount_point} rw - ext4 {live_source} rw",
        )
    )
    real_read_text = Path.read_text

    def mountinfo_read_text(path: Path, *args, **kwargs):
        if path == Path("/proc/self/mountinfo"):
            return mountinfo
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", mountinfo_read_text)
    monkeypatch.setattr(
        os,
        "stat",
        lambda _path: SimpleNamespace(st_dev=os.makedev(8, 17)),
    )

    identity = backup_artifact._live_mount_identity(backup_directory)

    assert identity["targetDeviceMajorMinor"] == "8:17"
    assert identity["targetMountSourceSha256"] == backup_artifact.hashlib.sha256(
        live_source.encode("utf-8")
    ).hexdigest()
    assert identity["targetMountRootSha256"] == backup_artifact.hashlib.sha256(
        b"/live-root"
    ).hexdigest()


def test_backup_authority_requires_dedicated_executor_and_private_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backup"
    backup_directory.mkdir(mode=0o700)
    signing_key = tmp_path / "backup-signing.pem"
    passfile = tmp_path / "backup.pgpass"
    for path in (signing_key, passfile):
        path.write_text("fixture\n", encoding="utf-8")
        path.chmod(0o600)
    monkeypatch.setattr(
        backup_artifact.pwd,
        "getpwuid",
        lambda _uid: SimpleNamespace(pw_name=backup_artifact.BACKUP_EXECUTOR_USER),
    )
    monkeypatch.setattr(
        backup_artifact,
        "_load_backup_target_authority",
        lambda **_kwargs: {"targetId": "backup-failure-domain-b"},
    )

    verified = backup_artifact.verify_backup_authority(
        target_authority_file=tmp_path / "root-authority.json",
        source_environment_id="production-cn-1",
        backup_directory=backup_directory,
        signing_private_key_file=signing_key,
        passfile=passfile,
    )
    assert verified["targetId"] == "backup-failure-domain-b"

    signing_key.chmod(0o640)
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="mode-0600",
    ):
        backup_artifact.verify_backup_authority(
            target_authority_file=tmp_path / "root-authority.json",
            source_environment_id="production-cn-1",
            backup_directory=backup_directory,
            signing_private_key_file=signing_key,
            passfile=passfile,
        )

    monkeypatch.setattr(
        backup_artifact.pwd,
        "getpwuid",
        lambda _uid: SimpleNamespace(pw_name="deploy"),
    )
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="dedicated gongkao-backup",
    ):
        backup_artifact.verify_backup_authority(
            target_authority_file=tmp_path / "root-authority.json",
            source_environment_id="production-cn-1",
            backup_directory=backup_directory,
            signing_private_key_file=signing_key,
            passfile=passfile,
        )


def test_backup_target_authority_file_must_be_root_owned_and_nofollow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_path = tmp_path / "backup-target-authority.json"
    authority_path.write_text('{"schemaVersion":"fixture"}\n', encoding="utf-8")
    authority_path.chmod(0o444)
    real_fstat = os.fstat

    def root_owned_fstat(descriptor: int):
        metadata = real_fstat(descriptor)
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_nlink=metadata.st_nlink,
            st_uid=0,
        )

    monkeypatch.setattr(os, "fstat", root_owned_fstat)
    loaded = backup_artifact._read_root_owned_authority(
        authority_path,
        label="backup target authority",
    )
    assert loaded == {"schemaVersion": "fixture"}

    link = tmp_path / "authority-link.json"
    link.symlink_to(authority_path)
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="unreadable",
    ):
        backup_artifact._read_root_owned_authority(
            link,
            label="backup target authority",
        )


def test_backup_shared_and_publication_exclusive_locks_are_mutually_exclusive(
    db_engine: Engine,
) -> None:
    key = backup_artifact.COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY
    with (
        psycopg.connect(_libpq_url(db_engine), autocommit=True) as publisher,
        psycopg.connect(_libpq_url(db_engine)) as backup,
    ):
        publisher.execute("select pg_advisory_lock(%s)", (key,))
        backup.execute("begin transaction isolation level repeatable read read only")
        backup.execute("set local lock_timeout = '200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            backup.execute("select pg_advisory_xact_lock_shared(%s)", (key,))
        backup.rollback()
        publisher.execute("select pg_advisory_unlock(%s)", (key,))

        backup.execute("begin transaction isolation level repeatable read read only")
        backup.execute("select pg_advisory_xact_lock_shared(%s)", (key,))
        assert (
            publisher.execute(
                "select pg_try_advisory_lock(%s)",
                (key,),
            ).fetchone()[0]
            is False
        )
        backup.rollback()
        assert (
            publisher.execute(
                "select pg_try_advisory_lock(%s)",
                (key,),
            ).fetchone()[0]
            is True
        )
        publisher.execute("select pg_advisory_unlock(%s)", (key,))


def test_restore_rejects_self_consistent_tampered_publication_authority(
    db_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_publication(db_engine)
    with psycopg.connect(_libpq_url(db_engine)) as connection:
        connection.execute(
            "select pg_advisory_xact_lock_shared(%s)",
            (backup_artifact.COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        authority = backup_artifact._capture_active_publication_authority(
            connection=connection,
            required_revision=CURRENT_DATABASE_REVISION,
        )
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"immutable custom-format fixture")
    manifest = backup_artifact.build_manifest(
        created_at="2026-07-30T01:02:03+00:00",
        source_environment_id="preproduction",
        backup_target_authority=_backup_target_authority(
            source_environment_id="preproduction"
        ),
        source_identity="cluster-1|gongkao|16384",
        source_cluster_identity="cluster-1",
        database_revision=CURRENT_DATABASE_REVISION,
        pg_dump_version="pg_dump (PostgreSQL) 17.5",
        dump_path=dump,
        row_counts={table: 0 for table in backup_artifact.CRITICAL_TABLES},
        publication_authority=authority,
    )
    tampered_receipt = "e" * 64
    manifest["commercialDataPublication"]["activeReceiptSha256"] = tampered_receipt
    manifest["commercialDataPublication"]["publicationDigest"] = (
        backup_artifact._publication_digest_for(
            request_digest=authority["requestDigest"],
            active_receipt_sha256=tampered_receipt,
        )
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        backup_artifact,
        "_load_restore_target_authority",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        backup_artifact,
        "_assert_restore_target_database",
        lambda **_kwargs: None,
    )

    with pytest.raises(
        backup_artifact.BackupContractError,
        match="does not match the backup snapshot",
    ):
        backup_artifact.verify_restore(
            database_url=_libpq_url(db_engine),
            manifest_path=manifest_path,
            dump_path=dump,
            target_environment_id="isolated-restore-1",
            target_identity_file=tmp_path / "unused-target.json",
            artifact_sha256="f" * 64,
            report_path=tmp_path / "restore-report.json",
        )


def test_backup_entrypoints_fail_closed_and_restore_requires_evidence() -> None:
    backup = (ROOT / "ops" / "scripts" / "backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "ops" / "scripts" / "restore_rehearsal.sh").read_text(encoding="utf-8")

    assert '[[ "${BACKUP_ENABLED}" != "true" ]]' in backup
    assert "--source-environment-id" in backup
    assert "REQUIRED_DATABASE_REVISION is forbidden for backup" in backup
    assert "from backup_artifact import _validate_database_url" in backup
    assert "-m app.core.database_compatibility assert-current" in backup
    assert '--required-revision "${required_database_revision}"' in backup
    assert "openssl dgst -sha256 -sign" in backup
    assert "flock --nonblock" in backup
    assert 'mktemp -d "${BACKUP_DIRECTORY}/.${set_basename}.staging.XXXXXX"' in backup
    assert "publish-set" in backup
    retention_observation = backup.split("retention_candidate_sets=", maxsplit=1)[1]
    assert "-exec rm -rf" not in retention_observation
    assert "-delete" not in retention_observation
    assert "anan_backup_retention_candidate_sets" in retention_observation
    assert "anan_backup_legacy_retention_candidate_files" in retention_observation
    assert "snapshot-restore-set" in restore
    assert "hold-restore-target-lock" in restore
    assert "isolated-disposable-database" in restore
    assert "restore-preflight" in restore
    assert "--target-identity-file" in restore
    assert "openssl dgst -sha256 -verify" in restore
    assert "verify-restore" in restore
    assert "publish-restore-report-set" in restore
    assert (
        '"${RESTORE_REPORT_DIRECTORY}/.${report_set_basename}.staging.${restore_identity}.XXXXXX"'
    ) in restore
    assert '"${signature_partial}" "${report_partial}"' in restore


def test_backup_rejects_copied_database_revision_before_any_runtime_work() -> None:
    result = subprocess.run(
        [str(ROOT / "ops" / "scripts" / "backup.sh")],
        check=False,
        env={**os.environ, "REQUIRED_DATABASE_REVISION": "stale-copy"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 64
    assert "REQUIRED_DATABASE_REVISION is forbidden for backup" in result.stderr


def test_sigkill_before_atomic_publish_leaves_no_restore_selectable_backup(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir(mode=0o700)
    metrics_directory = tmp_path / "metrics"
    metrics_directory.mkdir()
    publish_ready = tmp_path / "publish-ready"
    run_id = "20260730T010203.000000000Z-" + ("2" * 32)
    release_root = tmp_path / "release"
    script_directory = release_root / "ops" / "scripts"
    script_directory.mkdir(parents=True)
    backup_script = script_directory / "backup.sh"
    backup_script_source = (ROOT / "ops" / "scripts" / "backup.sh").read_text(
        encoding="utf-8"
    )
    for production_path, fixture_path in {
        "/etc/gongkao-dazi/backup-target-authority.json": str(
            tmp_path / "backup-target-authority.json"
        ),
        "/etc/gongkao-dazi/secrets/backup.pgpass": str(tmp_path / "backup.pgpass"),
        "/etc/gongkao-dazi/secrets/backup-signing.pem": str(
            tmp_path / "signing.pem"
        ),
        "/var/backups/gongkao-dazi/encrypted": str(backup_directory),
        "/var/lib/gongkao-backup/metrics/anan-backup.prom": str(
            metrics_directory / "backup.prom"
        ),
    }.items():
        backup_script_source = backup_script_source.replace(
            production_path,
            fixture_path,
        )
    backup_script.write_text(backup_script_source, encoding="utf-8")
    backup_script.chmod(0o755)
    (script_directory / "backup_artifact.py").write_text(
        "# fixture path checked by backup.sh\n",
        encoding="utf-8",
    )
    artifact_database_contract = release_root / "ops" / "release-db-compatibility.env"
    artifact_database_contract.write_text(
        "ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT=artifact-db-compatibility@v1\n"
        f"ARTIFACT_DATABASE_MIGRATION_HEAD={CURRENT_DATABASE_REVISION}\n"
        f"ARTIFACT_DATABASE_COMPATIBLE_REVISIONS={CURRENT_DATABASE_REVISION}\n",
        encoding="utf-8",
    )
    backup_python = release_root / "backend" / ".venv" / "bin" / "python"
    backup_python.parent.mkdir(parents=True)

    _write_executable(
        backup_python,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-c" ]]; then
  exit 0
fi
if [[ "${1:-}" == "-m" ]]; then
  if [[ "${2:-}" != "app.core.database_compatibility" \
    || "${3:-}" != "assert-current" ]]; then
    printf '%s\\n' "unexpected compatibility command: $*" >&2
    exit 96
  fi
  shift 3
  contract=""
  while (( $# > 0 )); do
    case "$1" in
      --backend-root) shift 2 ;;
      --contract) contract="$2"; shift 2 ;;
      *) printf '%s\\n' "unexpected compatibility argument: $1" >&2; exit 96 ;;
    esac
  done
  [[ "${contract}" == "${BACKUP_TEST_ARTIFACT_CONTRACT}" ]]
  [[ "${DATABASE_URL}" == "${BACKUP_TEST_COMPATIBILITY_DATABASE_URL}" ]]
  printf '%s\\n' "${BACKUP_TEST_ARTIFACT_REVISION}"
  exit 0
fi
shift
command="$1"
shift
case "${command}" in
  verify-backup-authority)
    ;;
  new-run-id)
    printf '%s\\n' "${BACKUP_TEST_RUN_ID}"
    ;;
  capture)
    dump=""
    manifest=""
    required_revision=""
    while (( $# > 0 )); do
      case "$1" in
        --dump) dump="$2"; shift 2 ;;
        --manifest) manifest="$2"; shift 2 ;;
        --required-revision) required_revision="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    [[ "${required_revision}" == "${BACKUP_TEST_ARTIFACT_REVISION}" ]]
    printf '%s\\n' "fake custom dump" >"${dump}"
    printf '%s\\n' '{"formatVersion":"test"}' >"${manifest}"
    ;;
  publish-set)
    staging=""
    artifact=""
    while (( $# > 0 )); do
      case "$1" in
        --staging-directory) staging="$2"; shift 2 ;;
        --artifact-basename) artifact="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    mv "${staging}/.${artifact}.partial" "${staging}/${artifact}"
    mv "${staging}/.${artifact}.sig.partial" "${staging}/${artifact}.sig"
    mv "${staging}/.${artifact}.sha256.partial" \
      "${staging}/${artifact}.sha256"
    : >"${BACKUP_TEST_PUBLISH_READY}"
    while :; do sleep 1; done
    ;;
  *)
    printf '%s\\n' "unexpected fake python command: ${command}" >&2
    exit 97
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "age",
        """#!/usr/bin/env bash
set -euo pipefail
output=""
input=""
while (( $# > 0 )); do
  case "$1" in
    --recipient) shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) input="$1"; shift ;;
  esac
done
cp "${input}" "${output}"
""",
    )
    _write_executable(
        fake_bin / "openssl",
        """#!/usr/bin/env bash
set -euo pipefail
output=""
while (( $# > 0 )); do
  if [[ "$1" == "-out" ]]; then
    output="$2"
    shift 2
  else
    shift
  fi
done
printf '%s\\n' "fake detached signature" >"${output}"
""",
    )
    for command in ("flock", "pg_dump", "pg_restore"):
        _write_executable(
            fake_bin / command,
            "#!/usr/bin/env bash\nexit 0\n",
        )
    signing_key = tmp_path / "signing.pem"
    signing_key.write_text("test key\n", encoding="utf-8")
    passfile = tmp_path / "backup.pgpass"
    passfile.write_text("test password fixture\n", encoding="utf-8")
    target_authority = tmp_path / "backup-target-authority.json"
    target_authority.write_text("{}\n", encoding="utf-8")
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BACKUP_ENABLED": "true",
        "BACKUP_DATABASE_URL": ("postgresql://backup@127.0.0.1:5432/gongkao"),
        "BACKUP_DIRECTORY": str(backup_directory),
        "AGE_RECIPIENT": "age1test",
        "BACKUP_SIGNING_PRIVATE_KEY_FILE": str(signing_key),
        "BACKUP_SOURCE_ENVIRONMENT_ID": "preproduction",
        "BACKUP_TARGET_AUTHORITY_FILE": str(target_authority),
        "PGPASSFILE": str(passfile),
        "BACKUP_RETENTION_DAYS": "30",
        "BACKUP_METRICS_FILE": str(metrics_directory / "backup.prom"),
        "BACKUP_TEST_ARTIFACT_CONTRACT": str(artifact_database_contract),
        "BACKUP_TEST_ARTIFACT_REVISION": CURRENT_DATABASE_REVISION,
        "BACKUP_TEST_COMPATIBILITY_DATABASE_URL": (
            "postgresql+psycopg://backup@127.0.0.1:5432/gongkao"
        ),
        "BACKUP_TEST_RUN_ID": run_id,
        "BACKUP_TEST_PUBLISH_READY": str(publish_ready),
    }
    process = subprocess.Popen(
        [str(backup_script)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not publish_ready.is_file() and process.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("backup did not reach the pre-publication kill window")
            time.sleep(0.01)
        assert process.poll() is None
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    assert list(backup_directory.glob("anan-*.backup-set/anan-*.backup.age")) == []
    stranded = list(backup_directory.rglob("anan-*.backup.age"))
    assert len(stranded) == 1
    with pytest.raises(
        backup_artifact.BackupContractError,
        match="unpublished staging directory",
    ):
        backup_artifact.verify_backup_file_layout(backup_file=stranded[0])
    assert not (metrics_directory / "backup.prom").exists()


def test_same_instant_restore_rehearsals_publish_distinct_complete_sets(
    tmp_path: Path,
) -> None:
    environment, report_directory, metrics_file, _sign_ready = _restore_rehearsal_test_environment(
        tmp_path
    )

    def run_restore(index: int) -> subprocess.CompletedProcess[str]:
        run_environment = {
            **environment,
            "RESTORE_DATABASE_URL": (
                f"postgresql://restore@127.0.0.1:5432/disposable_report_writer_{index}"
            ),
        }
        return subprocess.run(
            [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
            cwd=ROOT,
            env=run_environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run_restore, range(2)))

    assert [result.returncode for result in results] == [0, 0], [
        (result.stdout, result.stderr) for result in results
    ]
    report_paths = [Path(result.stdout.strip().splitlines()[-1]) for result in results]
    assert len(set(report_paths)) == 2
    assert all(path.is_file() for path in report_paths)
    assert len({path.parent for path in report_paths}) == 2
    fixed_timestamp = environment["RESTORE_TEST_FIXED_TIMESTAMP"]
    assert all(path.parent.name.startswith(f"anan-{fixed_timestamp}-") for path in report_paths)
    for report in report_paths:
        assert {path.name for path in report.parent.iterdir()} == {
            report.name,
            f"{report.name}.sha256",
            f"{report.name}.sig",
        }
        assert Path(f"{report}.sha256").read_text(encoding="utf-8") == (
            f"{backup_artifact.sha256_file(report)}  {report.name}\n"
        )
        assert Path(f"{report}.sig").read_text(encoding="utf-8") == (
            f"signature for .{report.name}.partial\n"
        )
    assert len(list(report_directory.glob("anan-*.restore-report-set"))) == 2
    assert not list(report_directory.glob(".*.restore-report-set.staging.*"))
    assert metrics_file.is_file()


def test_same_name_restore_rehearsal_processes_never_overwrite(
    tmp_path: Path,
) -> None:
    environment, report_directory, metrics_file, _sign_ready = _restore_rehearsal_test_environment(
        tmp_path
    )
    barrier_directory = tmp_path / "verify-barrier"
    barrier_directory.mkdir()
    environment["RESTORE_TEST_FIXED_TOKEN"] = "6" * 32
    environment["RESTORE_TEST_VERIFY_BARRIER"] = str(barrier_directory)

    def run_restore(index: int) -> subprocess.CompletedProcess[str]:
        run_environment = {
            **environment,
            "RESTORE_DATABASE_URL": (
                f"postgresql://restore@127.0.0.1:5432/disposable_report_collision_{index}"
            ),
        }
        return subprocess.run(
            [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
            cwd=ROOT,
            env=run_environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run_restore, range(2)))

    assert sorted(result.returncode for result in results) == [0, 1], [
        (result.stdout, result.stderr) for result in results
    ]
    failed = next(result for result in results if result.returncode != 0)
    assert "already exists" in failed.stderr
    report_sets = list(report_directory.glob("anan-*.restore-report-set"))
    assert len(report_sets) == 1
    report_paths = list(report_sets[0].glob("*-restore-report.json"))
    assert len(report_paths) == 1
    report = report_paths[0]
    assert {path.name for path in report.parent.iterdir()} == {
        report.name,
        f"{report.name}.sha256",
        f"{report.name}.sig",
    }
    assert Path(f"{report}.sha256").read_text(encoding="utf-8") == (
        f"{backup_artifact.sha256_file(report)}  {report.name}\n"
    )
    assert not list(report_directory.glob(".*.restore-report-set.staging.*"))
    assert metrics_file.is_file()


@pytest.mark.parametrize("replacement_window", ("after-verify", "after-decrypt"))
def test_restore_source_replacement_cannot_change_snapshot_or_report_digest(
    tmp_path: Path,
    replacement_window: str,
) -> None:
    (
        environment,
        _report_directory,
        _metrics_file,
        _sign_ready,
    ) = _restore_rehearsal_test_environment(tmp_path)
    backup_file = Path(environment["BACKUP_FILE"])
    original_payload = backup_file.read_bytes()
    original_digest = backup_artifact.sha256_file(backup_file)
    observed_content = tmp_path / f"{replacement_window}-observed"
    ready = tmp_path / f"{replacement_window}-ready"
    release = tmp_path / f"{replacement_window}-release"
    if replacement_window == "after-verify":
        environment.update(
            {
                "RESTORE_TEST_VERIFY_CONTENT_FILE": str(observed_content),
                "RESTORE_TEST_VERIFY_READY": str(ready),
                "RESTORE_TEST_VERIFY_RELEASE": str(release),
            }
        )
    else:
        environment.update(
            {
                "RESTORE_TEST_AGE_CONTENT_FILE": str(observed_content),
                "RESTORE_TEST_AGE_READY": str(ready),
                "RESTORE_TEST_AGE_RELEASE": str(release),
            }
        )

    process = subprocess.Popen(
        [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.is_file() and process.poll() is None:
            if time.monotonic() >= deadline:
                stdout, stderr = process.communicate(timeout=1)
                pytest.fail(
                    "restore did not reach the coordinated replacement window: "
                    f"stdout={stdout!r}, stderr={stderr!r}"
                )
            time.sleep(0.01)
        assert process.poll() is None
        replacement = tmp_path / "unsigned-replacement"
        replacement.write_bytes(b"unsigned encrypted snapshot B")
        replacement.replace(backup_file)
        Path(f"{backup_file}.sig").write_bytes(b"signature B")
        Path(f"{backup_file}.sha256").write_text(
            f"{backup_artifact.sha256_file(backup_file)}  {backup_file.name}\n",
            encoding="utf-8",
        )
        release.touch()
        stdout, stderr = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, (stdout, stderr)
    report = Path(stdout.strip().splitlines()[-1])
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    assert observed_content.read_bytes() == original_payload
    assert report_payload["artifactSha256"] == original_digest
    assert backup_artifact.sha256_file(backup_file) != original_digest


def test_same_target_restore_lock_fails_second_writer_before_pg_restore(
    tmp_path: Path,
) -> None:
    (
        environment,
        _report_directory,
        _metrics_file,
        _sign_ready,
    ) = _restore_rehearsal_test_environment(tmp_path)
    lock_directory = tmp_path / "target-locks"
    lock_directory.mkdir()
    restore_barrier = tmp_path / "pg-restore-barrier"
    restore_barrier.mkdir()
    restore_release = tmp_path / "pg-restore-release"
    environment.update(
        {
            "RESTORE_TEST_LOCK_DIRECTORY": str(lock_directory),
            "RESTORE_TEST_PG_RESTORE_BARRIER": str(restore_barrier),
            "RESTORE_TEST_PG_RESTORE_RELEASE": str(restore_release),
        }
    )

    first = subprocess.Popen(
        [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not list(restore_barrier.iterdir()) and first.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("first restore did not enter pg_restore")
            time.sleep(0.01)
        assert first.poll() is None
        second = subprocess.run(
            [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert second.returncode == 75, (second.stdout, second.stderr)
        assert "already locked" in second.stderr
        assert len(list(restore_barrier.iterdir())) == 1
        restore_release.touch()
        first_stdout, first_stderr = first.communicate(timeout=15)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=5)

    assert first.returncode == 0, (first_stdout, first_stderr)


def test_restore_advisory_lock_is_scoped_to_verified_database_identity(
    db_engine: Engine,
) -> None:
    with (
        psycopg.connect(_libpq_url(db_engine), autocommit=True) as holder,
        psycopg.connect(_libpq_url(db_engine), autocommit=True) as contender,
    ):
        _, restore_identity, _ = backup_artifact._database_identities(holder)
        other_identity = backup_artifact.hashlib.sha256(
            f"{restore_identity}|different-database".encode("utf-8")
        ).hexdigest()
        lock_key = backup_artifact._restore_advisory_lock_key(restore_identity)
        other_lock_key = backup_artifact._restore_advisory_lock_key(other_identity)
        assert lock_key != other_lock_key

        holder.execute("select pg_advisory_lock(%s)", (lock_key,))
        try:
            assert (
                contender.execute(
                    "select pg_try_advisory_lock(%s)",
                    (lock_key,),
                ).fetchone()[0]
                is False
            )
            assert (
                contender.execute(
                    "select pg_try_advisory_lock(%s)",
                    (other_lock_key,),
                ).fetchone()[0]
                is True
            )
            contender.execute(
                "select pg_advisory_unlock(%s)",
                (other_lock_key,),
            )
        finally:
            holder.execute("select pg_advisory_unlock(%s)", (lock_key,))


def test_different_target_restore_identities_do_not_share_a_lock(
    tmp_path: Path,
) -> None:
    (
        environment,
        _report_directory,
        _metrics_file,
        _sign_ready,
    ) = _restore_rehearsal_test_environment(tmp_path)
    lock_directory = tmp_path / "target-locks"
    lock_directory.mkdir()
    restore_barrier = tmp_path / "pg-restore-barrier"
    restore_barrier.mkdir()
    restore_release = tmp_path / "pg-restore-release"
    common = {
        **environment,
        "RESTORE_TEST_LOCK_DIRECTORY": str(lock_directory),
        "RESTORE_TEST_PG_RESTORE_BARRIER": str(restore_barrier),
        "RESTORE_TEST_PG_RESTORE_RELEASE": str(restore_release),
    }
    environments = [
        {
            **common,
            "RESTORE_DATABASE_URL": (
                f"postgresql://restore@127.0.0.1:5432/isolated_target_{index}"
            ),
        }
        for index in range(2)
    ]
    processes = [
        subprocess.Popen(
            [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
            cwd=ROOT,
            env=run_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for run_environment in environments
    ]
    try:
        deadline = time.monotonic() + 10
        while len(list(restore_barrier.iterdir())) < 2 and all(
            process.poll() is None for process in processes
        ):
            if time.monotonic() >= deadline:
                pytest.fail("different restore targets did not both enter pg_restore")
            time.sleep(0.01)
        assert len(list(restore_barrier.iterdir())) == 2
        restore_release.touch()
        results = [process.communicate(timeout=15) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    assert [process.returncode for process in processes] == [0, 0], results


def test_sigkill_residue_is_private_and_reaped_by_next_same_target_run(
    tmp_path: Path,
) -> None:
    environment, report_directory, metrics_file, sign_ready = _restore_rehearsal_test_environment(
        tmp_path
    )
    environment["RESTORE_TEST_BLOCK_SIGNING"] = "true"
    process = subprocess.Popen(
        [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not sign_ready.is_file() and process.poll() is None:
            if time.monotonic() >= deadline:
                stdout, stderr = process.communicate(timeout=1)
                pytest.fail(
                    "restore did not reach the report-signing kill window: "
                    f"stdout={stdout!r}, stderr={stderr!r}"
                )
            time.sleep(0.01)
        assert process.poll() is None
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    assert not list(report_directory.glob("anan-*.restore-report-set"))
    staging_directories = list(report_directory.glob(".*.restore-report-set.staging.*"))
    assert len(staging_directories) == 1
    staged_names = {path.name for path in staging_directories[0].iterdir()}
    assert len(staged_names) == 2
    assert all(name.startswith(".anan-") for name in staged_names)
    assert any(name.endswith("-restore-report.json.partial") for name in staged_names)
    assert any(name.endswith("-restore-report.json.sha256.partial") for name in staged_names)
    assert not metrics_file.exists()
    restore_identity = backup_artifact.hashlib.sha256(
        environment["RESTORE_DATABASE_URL"].encode("utf-8")
    ).hexdigest()
    state_directory = Path(environment["RESTORE_REHEARSAL_STATE_DIRECTORY"])
    workspaces = list(state_directory.glob(f".anan-restore-{restore_identity}.work.*"))
    assert len(workspaces) == 1
    stranded_workspace = workspaces[0]
    assert stat.S_IMODE(stranded_workspace.stat().st_mode) == 0o700
    assert (stranded_workspace / "backup.tar").is_file()
    assert (stranded_workspace / "payload" / "database.dump").is_file()
    stranded_staging = staging_directories[0]

    environment["RESTORE_TEST_BLOCK_SIGNING"] = "false"
    sign_ready.unlink(missing_ok=True)
    rerun = subprocess.run(
        [str(ROOT / "ops" / "scripts" / "restore_rehearsal.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert rerun.returncode == 0, (rerun.stdout, rerun.stderr)
    assert not stranded_workspace.exists()
    assert not stranded_staging.exists()
    assert not list(state_directory.glob(f".anan-restore-{restore_identity}.work.*"))
    assert not list(report_directory.glob(".*.restore-report-set.staging.*"))
    assert len(list(report_directory.glob("anan-*.restore-report-set"))) == 1
    assert metrics_file.is_file()


@pytest.mark.parametrize(
    "variable",
    (
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGSSLMODE",
        "PGOPTIONS",
    ),
)
def test_database_authority_rejects_libpq_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    monkeypatch.setenv(variable, "attacker-controlled")

    with pytest.raises(
        backup_artifact.BackupContractError,
        match="libpq environment",
    ):
        backup_artifact._validate_database_url("postgresql://backup@127.0.0.1:5432/gongkao")


@pytest.mark.parametrize(
    "database_url",
    (
        "postgresql:///gongkao",
        "postgresql://backup@db.example.invalid/gongkao",
        "postgresql://backup@db.example.invalid:5432/",
        "postgresql://backup@db.example.invalid:5432/gongkao?hostaddr=127.0.0.1",
        (
            "postgresql://backup@db.example.invalid:5432/gongkao"
            "?options=-csearch_path%3Dshadow"
        ),
        (
            "postgresql://backup@db.example.invalid:5432/gongkao"
            "?host=other.example.invalid&port=6543"
        ),
        "postgresql://backup@db.example.invalid:5432/gongkao?dbname=other",
        "postgresql://backup@db.example.invalid:5432/gongkao?user=other",
        (
            "postgresql://backup@db.example.invalid:5432/gongkao"
            "?host=first.example.invalid&host=second.example.invalid"
        ),
        (
            "postgresql://backup@db.example.invalid:5432/gongkao"
            "?sslmode=require&sslmode=disable"
        ),
        "postgresql://back%75p@db.example.invalid:5432/gongkao",
        "postgresql://backup@%64b.example.invalid:5432/gongkao",
        "postgresql://backup@db.example.invalid:%35%34%33%32/gongkao",
        "postgresql://backup@db.example.invalid:5432/gong%6Bao",
        "postgresql://backup@db.example.invalid:5432/gongkao%2Fshadow",
        "postgresql://backup@%2Fvar%2Frun%2Fpostgresql:5432/gongkao",
        "postgresql://backup@db1.example.invalid,db2.example.invalid:5432/gongkao",
        "service=production",
    ),
)
def test_database_authority_requires_one_explicit_target(
    database_url: str,
) -> None:
    with pytest.raises(backup_artifact.BackupContractError):
        backup_artifact._validate_database_url(database_url)


def test_database_authority_allows_one_transport_policy() -> None:
    backup_artifact._validate_database_url(
        "postgresql://backup@db.example.invalid:5432/gongkao"
        "?sslmode=verify%2Dfull&sslrootcert=%2Fetc%2Fssl%2Fpostgres-ca.pem"
    )


def test_validated_url_targets_same_database_for_compatibility_and_capture(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = str(time.time_ns())
    decoded_database = f"backup_parser_target_{suffix}"
    encoded_database = decoded_database.replace("_", "%5F", 1)
    literal_database = encoded_database
    decoded_revision = f"decoded-{suffix}"
    literal_revision = f"literal-{suffix}"
    admin_database_url = postgres_url.replace(
        "postgresql+psycopg:",
        "postgresql:",
        1,
    )
    admin_parameters = backup_artifact.conninfo_to_dict(admin_database_url)
    user = str(admin_parameters["user"])
    host = str(admin_parameters["host"])
    port = str(admin_parameters["port"])
    assert all("%" not in value for value in (user, host, port))
    rendered_host = f"[{host}]" if ":" in host else host
    database_authority = f"postgresql://{user}@{rendered_host}:{port}"
    canonical_url = f"{database_authority}/{decoded_database}"
    differential_url = f"{database_authority}/{encoded_database}"
    literal_seed_url = (
        f"{database_authority}/{encoded_database.replace('%', '%25')}"
    )

    with psycopg.connect(admin_database_url, autocommit=True) as admin_connection:
        for database_name in (decoded_database, literal_database):
            admin_connection.execute(
                sql.SQL("drop database if exists {} with (force)").format(
                    sql.Identifier(database_name)
                )
            )
            admin_connection.execute(
                sql.SQL("create database {}").format(sql.Identifier(database_name))
            )
    try:
        for database_url, revision in (
            (canonical_url, decoded_revision),
            (literal_seed_url, literal_revision),
        ):
            with psycopg.connect(database_url) as connection:
                connection.execute(
                    """
                    create table public.alembic_version (
                      version_num varchar(255) not null
                    )
                    """
                )
                connection.execute(
                    "insert into public.alembic_version (version_num) values (%s)",
                    (revision,),
                )

        with pytest.raises(
            backup_artifact.BackupContractError,
            match="must not use percent-encoding",
        ):
            backup_artifact._validate_database_url(differential_url)

        with psycopg.connect(differential_url) as libpq_connection:
            assert (
                libpq_connection.execute("select current_database()").fetchone()[0]
                == decoded_database
            )
        differential_engine = create_engine(
            differential_url.replace(
                "postgresql:",
                "postgresql+psycopg:",
                1,
            ),
            future=True,
        )
        try:
            with differential_engine.connect() as sqlalchemy_connection:
                assert (
                    sqlalchemy_connection.execute(
                        text("select current_database()")
                    ).scalar_one()
                    == literal_database
                )
        finally:
            differential_engine.dispose()

        backup_artifact._validate_database_url(canonical_url)
        compatibility_url = canonical_url.replace(
            "postgresql:",
            "postgresql+psycopg:",
            1,
        )
        assert current_database_revisions(compatibility_url) == (
            decoded_revision,
        )

        capture_databases: list[str] = []

        def capture_publication_authority(*, connection, required_revision):
            assert required_revision == decoded_revision
            capture_databases.append(
                str(connection.execute("select current_database()").fetchone()[0])
            )
            return _publication_authority(
                revision=decoded_revision,
                deployment_environment="production-cn-1",
            )

        def fake_backup_command(command, **_kwargs):
            if command[:2] == ["pg_dump", "--version"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="pg_dump (PostgreSQL) 16.0\n",
                )
            if command[0] == "pg_dump":
                dump_argument = next(
                    argument for argument in command if argument.startswith("--file=")
                )
                Path(dump_argument.split("=", 1)[1]).write_bytes(b"fixture dump")
            return subprocess.CompletedProcess(command, 0, stdout="")

        monkeypatch.setattr(
            backup_artifact,
            "_load_backup_target_authority",
            lambda **_kwargs: _backup_target_authority(),
        )
        monkeypatch.setattr(
            backup_artifact,
            "_capture_active_publication_authority",
            capture_publication_authority,
        )
        monkeypatch.setattr(backup_artifact, "CRITICAL_TABLES", ())
        monkeypatch.setattr(backup_artifact.subprocess, "run", fake_backup_command)
        backup_artifact.capture(
            database_url=canonical_url,
            dump_path=tmp_path / "database.dump",
            manifest_path=tmp_path / "manifest.json",
            required_revision=decoded_revision,
            source_environment_id="production-cn-1",
            backup_target_authority_file=tmp_path / "unused-authority.json",
            backup_directory=tmp_path,
        )
        assert capture_databases == [decoded_database]
    finally:
        with psycopg.connect(admin_database_url, autocommit=True) as admin_connection:
            for database_name in (decoded_database, literal_database):
                admin_connection.execute(
                    sql.SQL("drop database if exists {} with (force)").format(
                        sql.Identifier(database_name)
                    )
                )


def test_restore_rejects_another_database_on_the_source_cluster() -> None:
    source_cluster = backup_artifact.hashlib.sha256(b"cluster-1").hexdigest()
    manifest = {
        "sourceClusterIdentitySha256": source_cluster,
        "sourceDatabaseIdentitySha256": "0" * 64,
    }
    authority = {
        "databaseName": "disposable",
        "clusterSystemIdentifierSha256": source_cluster,
    }
    connection = _IdentityConnection(
        database_name="disposable",
        database_oid="20000",
        system_identifier="cluster-1",
    )

    with pytest.raises(
        backup_artifact.BackupContractError,
        match="source PostgreSQL cluster",
    ):
        backup_artifact._assert_restore_target_database(
            connection=connection,
            manifest=manifest,
            target_authority=authority,
        )


def test_restore_target_authority_is_root_owned_and_executor_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_path = tmp_path / "restore-target.json"
    executor_hash = "1" * 64
    authority_path.write_text(
        json.dumps(
            {
                "schemaVersion": backup_artifact.RESTORE_TARGET_FORMAT_VERSION,
                "purpose": backup_artifact.RESTORE_TARGET_PURPOSE,
                "environmentId": "isolated-restore-1",
                "executorIdentitySha256": executor_hash,
                "databaseName": "disposable",
                "clusterSystemIdentifierSha256": "2" * 64,
            }
        ),
        encoding="utf-8",
    )
    real_stat = Path.stat

    def trusted_stat(path: Path, *args, **kwargs):
        if path == authority_path:
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o400)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", trusted_stat)
    monkeypatch.setattr(
        backup_artifact,
        "_executor_identity_sha256",
        lambda: executor_hash,
    )

    loaded = backup_artifact._load_restore_target_authority(
        path=authority_path,
        expected_environment_id="isolated-restore-1",
    )

    assert loaded["databaseName"] == "disposable"


class _IdentityResult:
    def __init__(self, row: tuple[str, str, str]) -> None:
        self._row = row

    def fetchone(self) -> tuple[str, str, str]:
        return self._row


class _IdentityConnection:
    def __init__(
        self,
        *,
        database_name: str,
        database_oid: str,
        system_identifier: str,
    ) -> None:
        self._row = (database_name, database_oid, system_identifier)

    def execute(self, _query: str) -> _IdentityResult:
        return _IdentityResult(self._row)


def _restore_rehearsal_test_environment(
    tmp_path: Path,
) -> tuple[dict[str, str], Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    report_directory = tmp_path / "restore-reports"
    report_directory.mkdir()
    metrics_directory = tmp_path / "metrics"
    metrics_directory.mkdir()
    metrics_file = metrics_directory / "restore.prom"
    sign_ready = tmp_path / "report-sign-ready"

    backup_run_id = "20260729T235959.123456789Z-" + ("5" * 32)
    artifact_basename = f"anan-{backup_run_id}.backup.age"
    backup_set = tmp_path / f"anan-{backup_run_id}.backup-set"
    backup_set.mkdir()
    backup_file = backup_set / artifact_basename
    backup_file.write_bytes(b"encrypted backup fixture")
    Path(f"{backup_file}.sig").write_bytes(b"backup signature fixture")
    Path(f"{backup_file}.sha256").write_text(
        f"{backup_artifact.sha256_file(backup_file)}  {artifact_basename}\n",
        encoding="utf-8",
    )

    _write_executable(
        fake_bin / "python3",
        """#!/usr/bin/env bash
set -euo pipefail
helper="$1"
shift
command="$1"
shift
case "${command}" in
  new-run-id)
    if [[ -n "${RESTORE_TEST_FIXED_TIMESTAMP:-}" ]]; then
      token="${RESTORE_TEST_FIXED_TOKEN:-}"
      if [[ -z "${token}" ]]; then
        token="$(
          "${RESTORE_TEST_REAL_PYTHON}" \
            -c 'import secrets; print(secrets.token_hex(16))'
        )"
      fi
      printf '%s-%s\\n' "${RESTORE_TEST_FIXED_TIMESTAMP}" "${token}"
    else
      exec "${RESTORE_TEST_REAL_PYTHON}" "${helper}" new-run-id "$@"
    fi
    ;;
  verify-set-layout|snapshot-restore-set|verify-restore-target-lock-ready|create-restore-workspace|reap-restore-residue|publish-restore-report-set)
    exec "${RESTORE_TEST_REAL_PYTHON}" "${helper}" "${command}" "$@"
    ;;
  restore-target-identity)
    database_url=""
    while (( $# > 0 )); do
      case "$1" in
        --database-url) database_url="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ -n "${RESTORE_TEST_TARGET_IDENTITY:-}" ]]; then
      printf '%s\n' "${RESTORE_TEST_TARGET_IDENTITY}"
    else
      printf '%s' "${database_url}" | shasum -a 256 | awk '{print $1}'
    fi
    ;;
  hold-restore-target-lock)
    expected_identity=""
    ready_file=""
    while (( $# > 0 )); do
      case "$1" in
        --expected-restore-identity) expected_identity="$2"; shift 2 ;;
        --ready-file) ready_file="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    lock_path=""
    cleanup_lock() {
      if [[ -n "${lock_path}" ]]; then
        rmdir "${lock_path}" 2>/dev/null || true
      fi
    }
    trap cleanup_lock EXIT
    trap 'exit 0' TERM INT
    if [[ -n "${RESTORE_TEST_LOCK_DIRECTORY:-}" ]]; then
      lock_path="${RESTORE_TEST_LOCK_DIRECTORY}/${expected_identity}.lock"
      if ! mkdir "${lock_path}" 2>/dev/null; then
        printf '%s\n' "test restore target is already locked" >&2
        exit 75
      fi
    fi
    printf '{"schemaVersion":"anan-restore-target-lock/v1","restoreIdentity":"%s","holderPid":%s}\n' \
      "${expected_identity}" "$$" >"${ready_file}"
    parent_pid="${PPID}"
    while kill -0 "${parent_pid}" 2>/dev/null; do
      sleep 0.02
    done
    ;;
  extract)
    output=""
    while (( $# > 0 )); do
      case "$1" in
        --output-directory) output="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    mkdir -p "${output}"
    printf '%s\\n' "fake custom dump" >"${output}/database.dump"
    printf '%s\\n' '{"formatVersion":"test"}' >"${output}/manifest.json"
    ;;
  restore-preflight)
    ;;
  verify-restore)
    report=""
    artifact_sha256=""
    while (( $# > 0 )); do
      case "$1" in
        --report) report="$2"; shift 2 ;;
        --artifact-sha256) artifact_sha256="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    printf '{"result":"passed","writerPid":%s,"artifactSha256":"%s"}\\n' \
      "$$" "${artifact_sha256}" >"${report}"
    if [[ -n "${RESTORE_TEST_VERIFY_BARRIER:-}" ]]; then
      : >"${RESTORE_TEST_VERIFY_BARRIER}/$$"
      while [[ "$(
        find "${RESTORE_TEST_VERIFY_BARRIER}" -type f | wc -l | tr -d ' '
      )" -lt 2 ]]; do
        sleep 0.01
      done
    fi
    ;;
  *)
    printf '%s\\n' "unexpected fake python command: ${command}" >&2
    exit 97
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "age",
        """#!/usr/bin/env bash
set -euo pipefail
output=""
input=""
while (( $# > 0 )); do
  case "$1" in
    --identity) shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) input="$1"; shift ;;
  esac
done
cp "${input}" "${output}"
if [[ -n "${RESTORE_TEST_AGE_CONTENT_FILE:-}" ]]; then
  cp "${input}" "${RESTORE_TEST_AGE_CONTENT_FILE}"
fi
if [[ -n "${RESTORE_TEST_AGE_READY:-}" ]]; then
  : >"${RESTORE_TEST_AGE_READY}"
  while [[ ! -f "${RESTORE_TEST_AGE_RELEASE}" ]]; do
    sleep 0.01
  done
fi
""",
    )
    _write_executable(
        fake_bin / "openssl",
        """#!/usr/bin/env bash
set -euo pipefail
is_sign="false"
output=""
input=""
while (( $# > 0 )); do
  case "$1" in
    -sign)
      is_sign="true"
      shift 2
      ;;
    -out)
      output="$2"
      shift 2
      ;;
    *)
      input="$1"
      shift
      ;;
  esac
done
if [[ "${is_sign}" == "true" ]]; then
  if [[ "${RESTORE_TEST_BLOCK_SIGNING:-false}" == "true" ]]; then
    : >"${RESTORE_TEST_SIGN_READY}"
    while :; do sleep 1; done
  fi
  printf 'signature for %s\\n' "$(basename "${input}")" >"${output}"
elif [[ -n "${RESTORE_TEST_VERIFY_CONTENT_FILE:-}" ]]; then
  cp "${input}" "${RESTORE_TEST_VERIFY_CONTENT_FILE}"
  : >"${RESTORE_TEST_VERIFY_READY}"
  while [[ ! -f "${RESTORE_TEST_VERIFY_RELEASE}" ]]; do
    sleep 0.01
  done
fi
""",
    )
    _write_executable(
        fake_bin / "pg_restore",
        """#!/usr/bin/env bash
set -euo pipefail
for argument in "$@"; do
  if [[ "${argument}" == "--list" ]]; then
    exit 0
  fi
done
if [[ -n "${RESTORE_TEST_PG_RESTORE_BARRIER:-}" ]]; then
  mkdir "${RESTORE_TEST_PG_RESTORE_BARRIER}/$$"
  while [[ ! -f "${RESTORE_TEST_PG_RESTORE_RELEASE}" ]]; do
    sleep 0.01
  done
fi
""",
    )

    age_identity = tmp_path / "age-identity.txt"
    backup_public_key = tmp_path / "backup-public.pem"
    report_private_key = tmp_path / "report-private.pem"
    target_identity = tmp_path / "restore-target.json"
    for path in (
        age_identity,
        backup_public_key,
        report_private_key,
        target_identity,
    ):
        path.write_text("test fixture\n", encoding="utf-8")

    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RESTORE_DATABASE_URL": ("postgresql://restore@127.0.0.1:5432/disposable"),
        "BACKUP_FILE": str(backup_file),
        "AGE_IDENTITY_FILE": str(age_identity),
        "BACKUP_SIGNING_PUBLIC_KEY_FILE": str(backup_public_key),
        "RESTORE_REPORT_SIGNING_PRIVATE_KEY_FILE": str(report_private_key),
        "RESTORE_REHEARSAL_ACK": "isolated-disposable-database",
        "RESTORE_TARGET_ENVIRONMENT_ID": "isolated-restore-1",
        "RESTORE_TARGET_IDENTITY_FILE": str(target_identity),
        "RESTORE_REPORT_DIRECTORY": str(report_directory),
        "RESTORE_METRICS_FILE": str(metrics_file),
        "RESTORE_REHEARSAL_STATE_DIRECTORY": str(tmp_path / "restore-state"),
        "RESTORE_TEST_REAL_PYTHON": sys.executable,
        "RESTORE_TEST_FIXED_TIMESTAMP": "20260730T010203.123456789Z",
        "RESTORE_TEST_FIXED_TOKEN": "",
        "RESTORE_TEST_VERIFY_BARRIER": "",
        "RESTORE_TEST_BLOCK_SIGNING": "false",
        "RESTORE_TEST_SIGN_READY": str(sign_ready),
        "RESTORE_TEST_TARGET_IDENTITY": "",
        "RESTORE_TEST_LOCK_DIRECTORY": "",
        "RESTORE_TEST_VERIFY_CONTENT_FILE": "",
        "RESTORE_TEST_VERIFY_READY": "",
        "RESTORE_TEST_VERIFY_RELEASE": "",
        "RESTORE_TEST_AGE_CONTENT_FILE": "",
        "RESTORE_TEST_AGE_READY": "",
        "RESTORE_TEST_AGE_RELEASE": "",
        "RESTORE_TEST_PG_RESTORE_BARRIER": "",
        "RESTORE_TEST_PG_RESTORE_RELEASE": "",
    }
    return environment, report_directory, metrics_file, sign_ready


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
