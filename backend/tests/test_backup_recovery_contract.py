from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "scripts" / "backup_artifact.py"
SPEC = importlib.util.spec_from_file_location("backup_artifact", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
backup_artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_artifact)


def test_backup_manifest_binds_snapshot_revision_dump_and_critical_tables(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"immutable custom-format fixture")
    counts = {
        table: index for index, table in enumerate(backup_artifact.CRITICAL_TABLES)
    }

    manifest = backup_artifact.build_manifest(
        created_at="2026-07-19T01:02:03+00:00",
        source_environment_id="production-cn-1",
        backup_target_id="cross-domain-object-store-1",
        source_identity="gongkao|10.0.0.8|5432",
        database_revision="20260719_46",
        pg_dump_version="pg_dump (PostgreSQL) 17.5",
        dump_path=dump,
        row_counts=counts,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = backup_artifact.load_and_verify_manifest(
        manifest_path=manifest_path, dump_path=dump
    )
    assert loaded["databaseRevision"] == "20260719_46"
    assert loaded["criticalTableRowCounts"] == counts
    assert "gongkao|10.0.0.8|5432" not in json.dumps(loaded)

    dump.write_bytes(b"tampered")
    with pytest.raises(backup_artifact.BackupContractError, match="dump size"):
        backup_artifact.load_and_verify_manifest(
            manifest_path=manifest_path, dump_path=dump
        )


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
        backup_artifact.extract_bundle(
            bundle_path=bundle, output_directory=tmp_path / "output"
        )
    assert not (tmp_path / "outside").exists()


def test_backup_entrypoints_fail_closed_and_restore_requires_evidence() -> None:
    backup = (ROOT / "ops" / "scripts" / "backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "ops" / "scripts" / "restore_rehearsal.sh").read_text(
        encoding="utf-8"
    )

    assert '[[ "${BACKUP_ENABLED}" != "true" ]]' in backup
    assert "--source-environment-id" in backup
    assert "openssl dgst -sha256 -sign" in backup
    assert "flock --nonblock" in backup
    assert "isolated-disposable-database" in restore
    assert "restore-preflight" in restore
    assert "openssl dgst -sha256 -verify" in restore
    assert "verify-restore" in restore
    assert '"${report}.sig"' in restore
