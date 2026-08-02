from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
from uuid import uuid4

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "ops" / "scripts" / "release_manifest.py"
STAGING_SCRIPT = REPOSITORY_ROOT / "ops" / "scripts" / "release_staging.sh"
CANDIDATE_COMMIT = "1" * 40
ROLLBACK_COMMIT = "7" * 40
SPEC = importlib.util.spec_from_file_location("release_manifest", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
release_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_manifest)


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _artifact(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    for root in ("backend", "apps", "ops", "scripts", ".github", "audit"):
        file = source / root / "authority.txt"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(root, encoding="utf-8")
    (source / "backend" / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (source / "backend" / "pyproject.toml").write_text(
        '[project]\nname = "test"\nversion = "0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (source / "ops" / "release-db-compatibility.env").write_text(
        "\n".join(
            (
                "ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT=artifact-db-compatibility@v1",
                "ARTIFACT_DATABASE_MIGRATION_HEAD=20260730_79",
                "ARTIFACT_DATABASE_COMPATIBLE_REVISIONS=20260730_79",
                "",
            )
        ),
        encoding="utf-8",
    )
    (source / "ops" / "commercial-data-publication-guard.json").write_text(
        json.dumps(
            {
                "schemaVersion": "anan-release-capability/v1",
                "capability": "commercial-data-publication-guard",
                "contractVersion": "commercial-data-publication-guard@v1",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    project_config = source / "apps" / "wechat" / "project.config.json"
    project_config.parent.mkdir(parents=True, exist_ok=True)
    project_config.write_text(
        json.dumps(
            {
                "appid": "wx42824c7f7bfbf4b2",
                "compileType": "miniprogram",
                "miniprogramRoot": "miniprogram/",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    generated_types = (
        source / "apps" / "wechat" / "miniprogram" / "api" / "generated" / "types.gen.ts"
    )
    generated_types.parent.mkdir(parents=True, exist_ok=True)
    generated_types.write_text("export type Fixture = string;\n", encoding="utf-8")
    canonical_openapi = source / "audit" / "api-contract" / "openapi.json"
    canonical_openapi.parent.mkdir(parents=True, exist_ok=True)
    canonical_openapi.write_text('{"openapi":"3.1.0"}\n', encoding="utf-8")
    artifact = tmp_path / "anan-candidate.tar.gz"
    with tarfile.open(
        artifact,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "1" * 40},
    ) as archive:
        for root in ("backend", "apps", "ops", "scripts", ".github", "audit"):
            archive.add(source / root, arcname=root)
    return artifact


def _archive_member_sha256(artifact: Path, name: str) -> str:
    with tarfile.open(artifact, "r:gz") as archive:
        handle = archive.extractfile(name)
        assert handle is not None
        return sha256(handle.read()).hexdigest()


def _rebuild_artifact(artifact: Path) -> None:
    source = artifact.parent / "source"
    with tarfile.open(
        artifact,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "1" * 40},
    ) as archive:
        for root in ("backend", "apps", "ops", "scripts", ".github", "audit"):
            archive.add(source / root, arcname=root)


def _refresh_artifact_identity(
    payload: dict[str, object],
    artifact: Path,
) -> None:
    authority = payload["backendArtifact"]
    assert isinstance(authority, dict)
    authority["sha256"] = _sha256(artifact)
    authority["bytes"] = artifact.stat().st_size


def _wechat_key_paths(tmp_path: Path) -> tuple[Path, Path]:
    private_key = tmp_path / "wechat-uploader-private.pem"
    public_key = tmp_path / "wechat-uploader-public.pem"
    if not private_key.exists():
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
                "-out",
                str(private_key),
            ],
            check=True,
            capture_output=True,
        )
        private_key.chmod(0o600)
        subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                str(private_key),
                "-pubout",
                "-out",
                str(public_key),
            ],
            check=True,
            capture_output=True,
        )
        public_key.chmod(0o644)
    return private_key, public_key


def _public_key_id(public_key: Path) -> str:
    completed = subprocess.run(
        [
            "openssl",
            "pkey",
            "-pubin",
            "-in",
            str(public_key),
            "-outform",
            "DER",
        ],
        check=True,
        capture_output=True,
    )
    return f"sha256:{sha256(completed.stdout).hexdigest()}"


def _sign_receipt(receipt: Path, signature: Path, private_key: Path) -> None:
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(signature),
            str(receipt),
        ],
        check=True,
        capture_output=True,
    )


def _manifest(artifact: Path, *, deployable: bool = True) -> dict[str, object]:
    missing = (
        []
        if deployable
        else [
            "isolated_api_contract_check",
            "isolated_database_graph_check",
            "wechat_upload_version",
            "wechat_upload_receipt",
            "data_release_receipt",
        ]
    )
    raw_log = artifact.parent / "wechat-upload.log"
    receipt_file = artifact.parent / "wechat-upload.json"
    receipt_signature = artifact.parent / "wechat-upload.json.sig"
    info_file = artifact.parent / "wechat-upload-info.json"
    data_receipt_file = artifact.parent / "data-release.json"
    authority: dict[str, object] = {}
    if deployable:
        raw_log.write_bytes(b"wechat devtools upload completed\n")
        info_authority = {
            "size": {
                "packages": [
                    {"name": "__FULL__", "size": 1024},
                ]
            }
        }
        info_file.write_text(
            json.dumps(info_authority, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        private_key, public_key = _wechat_key_paths(artifact.parent)
        authority = {
            "schemaVersion": "anan-wechat-upload-receipt/v2",
            "receiptId": str(uuid4()),
            "operation": "devtools_upload",
            "result": "success",
            "startedAt": "2026-07-30T00:00:00+00:00",
            "completedAt": "2026-07-30T00:01:00+00:00",
            "sourceGitCommit": CANDIDATE_COMMIT,
            "sourceGitTree": "2" * 40,
            "wechatTreeObjectId": "b" * 40,
            "sourceTreeListingSha256": "6" * 64,
            "projectRelativePath": "apps/wechat",
            "projectConfigSha256": _archive_member_sha256(
                artifact,
                "apps/wechat/project.config.json",
            ),
            "appId": "wx42824c7f7bfbf4b2",
            "compileType": "miniprogram",
            "miniprogramRoot": "miniprogram/",
            "uploadVersion": "2.0.0",
            "uploadDescription": "candidate fixture",
            "devtoolsCliExitCode": 0,
            "rawCliLogSha256": _sha256(raw_log),
            "rawCliLogBytes": raw_log.stat().st_size,
            "infoOutputSha256": _sha256(info_file),
            "infoOutputBytes": info_file.stat().st_size,
            "infoOutput": info_authority,
            "tool": {
                "identityMode": "macOS-codesign",
                "policySha256": "c" * 64,
                "bundleIdentifier": "com.tencent.webplusdevtools",
                "shortVersion": "2.01.test",
                "bundleVersion": "test",
                "teamIdentifier": "FN2V63AD2J",
                "cliLauncherSha256": "d" * 64,
                "cliJsSha256": "e" * 64,
                "cliModuleSha256": "f" * 64,
            },
            "uploaderKeyId": _public_key_id(public_key),
            "signatureAlgorithm": "RSA-SHA256",
        }
        receipt_file.write_text(
            json.dumps(authority, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _sign_receipt(receipt_file, receipt_signature, private_key)
        data_receipt_file.write_text(
            json.dumps(
                {
                    "schemaVersion": "anan-data-release-receipt/v1",
                    "observedAt": "2026-07-30T00:00:00+00:00",
                    "environment": "preproduction",
                    "database": {
                        "databaseName": "gongkao",
                        "clusterSystemIdentifierSha256": "8" * 64,
                        "alembicRevision": "20260730_79",
                    },
                    "position": {
                        "releaseId": "00000000-0000-4000-8000-000000000001",
                        "examCode": "guokao",
                        "examYear": 2026,
                        "ruleSetVersion": "position-rules@test",
                        "positionDataVersion": "positions@test",
                        "sourceSnapshotSha256": "9" * 64,
                        "releaseContentSha256": "a" * 64,
                        "lifecycleState": "open",
                        "currentBusinessWindow": {
                            "windowId": ("00000000-0000-4000-8000-000000000003"),
                            "kind": "primary",
                            "sequenceNo": 1,
                            "opensAt": "2026-07-01T00:00:00+00:00",
                            "closesAt": "2026-08-01T00:00:00+00:00",
                            "officialSourceSha256": "d" * 64,
                        },
                    },
                    "knowledge": {
                        "releaseId": "00000000-0000-4000-8000-000000000002",
                        "releaseVersion": "knowledge@test",
                        "sourceDataVersion": "questions@test",
                        "sourceFingerprint": "b" * 64,
                        "releaseContentSha256": "c" * 64,
                        "freshUntil": "2026-08-01T00:00:00+00:00",
                    },
                    "compatibleApplicationCommits": [
                        CANDIDATE_COMMIT,
                        ROLLBACK_COMMIT,
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    data_authority = (
        json.loads(data_receipt_file.read_text(encoding="utf-8")) if deployable else None
    )
    return {
        "schemaVersion": 4,
        "releaseId": "candidate",
        "status": "deployable_candidate" if deployable else "candidate_incomplete",
        "builtAt": "2026-07-30T00:00:00+00:00",
        "source": {
            "gitCommit": "1" * 40,
            "gitTree": "2" * 40,
            "branch": "codex/commercial-selection-repair",
            "dirty": False,
        },
        "backendArtifact": {
            "fileName": artifact.name,
            "sha256": _sha256(artifact),
            "bytes": artifact.stat().st_size,
            "archiveRoots": [
                ".github",
                "apps",
                "audit",
                "backend",
                "ops",
                "scripts",
            ],
        },
        "database": {
            "alembicHead": "20260730_79",
            "compatibleRevisions": ["20260730_79"],
            "contractPath": "ops/release-db-compatibility.env",
            "contractSha256": _archive_member_sha256(
                artifact,
                "ops/release-db-compatibility.env",
            ),
            "alembicGraphCheck": ("passed" if deployable else "not_verified_isolated"),
            "verifiedHeads": ["20260730_79"] if deployable else None,
        },
        "runtimeDependencies": {
            "resolver": "uv",
            "installMode": "sync-frozen-no-dev-no-editable",
            "lockPath": "backend/uv.lock",
            "lockSha256": _archive_member_sha256(
                artifact,
                "backend/uv.lock",
            ),
            "lockFormatVersion": 1,
            "lockRevision": 3,
            "pyprojectPath": "backend/pyproject.toml",
            "pyprojectSha256": _archive_member_sha256(
                artifact,
                "backend/pyproject.toml",
            ),
            "requiresPython": ">=3.11",
        },
        "guardCapabilities": {
            "commercialDataPublication": {
                "capability": "commercial-data-publication-guard",
                "contractVersion": "commercial-data-publication-guard@v1",
                "markerPath": "ops/commercial-data-publication-guard.json",
                "markerSha256": _archive_member_sha256(
                    artifact,
                    "ops/commercial-data-publication-guard.json",
                ),
            }
        },
        "apiContract": {
            "openapiSha256": _archive_member_sha256(
                artifact,
                "audit/api-contract/openapi.json",
            ),
            "generatedTypesSha256": _archive_member_sha256(
                artifact,
                "apps/wechat/miniprogram/api/generated/types.gen.ts",
            ),
            "generatedTypesCheck": ("passed" if deployable else "not_verified_isolated"),
            "verificationEnvironment": ("isolated-artifact-lockfiles" if deployable else None),
            "toolchain": (
                {
                    name: {
                        "version": version,
                        "executableSha256": digest * 64,
                    }
                    for name, version, digest in (
                        ("uv", "uv 0.11.32", "1"),
                        ("node", "v24.0.0", "2"),
                        ("npm", "11.0.0", "3"),
                    )
                }
                if deployable
                else None
            ),
        },
        "wechat": {
            "sourceTreeListingSha256": "6" * 64,
            "projectConfigPath": "apps/wechat/project.config.json",
            "projectConfigSha256": _archive_member_sha256(
                artifact,
                "apps/wechat/project.config.json",
            ),
            "appId": "wx42824c7f7bfbf4b2",
            "uploadVersion": "2.0.0" if deployable else None,
            "uploadReceipt": (
                {
                    "fileName": "wechat-upload.json",
                    "sha256": _sha256(receipt_file),
                    "bytes": receipt_file.stat().st_size,
                    "authority": authority,
                    "signature": {
                        "fileName": receipt_signature.name,
                        "sha256": _sha256(receipt_signature),
                        "bytes": receipt_signature.stat().st_size,
                        "algorithm": "RSA-SHA256",
                        "uploaderKeyId": authority["uploaderKeyId"],
                    },
                    "infoOutput": {
                        "fileName": info_file.name,
                        "sha256": _sha256(info_file),
                        "bytes": info_file.stat().st_size,
                        "authority": json.loads(info_file.read_text(encoding="utf-8")),
                    },
                    "rawCliLog": {
                        "fileName": raw_log.name,
                        "sha256": _sha256(raw_log),
                        "bytes": raw_log.stat().st_size,
                    },
                }
                if deployable
                else None
            ),
        },
        "dataAuthorities": {
            "receipt": (
                {
                    "fileName": data_receipt_file.name,
                    "sha256": _sha256(data_receipt_file),
                    "bytes": data_receipt_file.stat().st_size,
                    "authority": data_authority,
                }
                if deployable
                else None
            ),
        },
        "deployment": {
            "environment": "preproduction",
            "rollbackReleaseId": "previous",
        },
        "missingExternalEvidence": missing,
    }


def _write_manifest(tmp_path: Path, artifact: Path, *, deployable: bool = True) -> Path:
    path = tmp_path / "candidate.release.json"
    path.write_text(
        json.dumps(_manifest(artifact, deployable=deployable)),
        encoding="utf-8",
    )
    return path


def _wechat_evidence(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    return (
        tmp_path / "wechat-upload.json",
        tmp_path / "wechat-upload.json.sig",
        tmp_path / "wechat-upload-info.json",
        tmp_path / "wechat-upload.log",
        tmp_path / "wechat-uploader-public.pem",
    )


def _wechat_verify_arguments(tmp_path: Path) -> dict[str, Path]:
    receipt, signature, info, raw_log, public_key = _wechat_evidence(tmp_path)
    return {
        "wechat_upload_receipt_path": receipt,
        "wechat_upload_receipt_signature_path": signature,
        "wechat_upload_info_output_path": info,
        "wechat_upload_raw_log_path": raw_log,
        "wechat_uploader_trust_public_key_path": public_key,
    }


def _resign_manifest_wechat_receipt(
    payload: dict[str, object],
    tmp_path: Path,
) -> None:
    wechat = payload["wechat"]
    assert isinstance(wechat, dict)
    receipt = wechat["uploadReceipt"]
    assert isinstance(receipt, dict)
    authority = receipt["authority"]
    assert isinstance(authority, dict)
    receipt_path, signature_path, _, _, _ = _wechat_evidence(tmp_path)
    receipt_path.write_text(
        json.dumps(authority, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    private_key, _ = _wechat_key_paths(tmp_path)
    _sign_receipt(receipt_path, signature_path, private_key)
    receipt["sha256"] = _sha256(receipt_path)
    receipt["bytes"] = receipt_path.stat().st_size
    signature = receipt["signature"]
    assert isinstance(signature, dict)
    signature["sha256"] = _sha256(signature_path)
    signature["bytes"] = signature_path.stat().st_size


def _data_evidence(tmp_path: Path) -> Path:
    return tmp_path / "data-release.json"


def test_release_manifest_binds_artifact_and_all_release_authorities(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact)

    result = release_manifest.verify_manifest(
        manifest_path=manifest,
        artifact_path=artifact,
        expected_commit="1" * 40,
        require_deployable=True,
        expected_release_id="candidate",
        expected_environment="preproduction",
        expected_rollback_release_id="previous",
        expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
        data_release_receipt_path=_data_evidence(tmp_path),
        **_wechat_verify_arguments(tmp_path),
    )

    assert result["releaseId"] == "candidate"
    assert result["database"]["alembicHead"] == "20260730_79"
    assert result["apiContract"]["generatedTypesCheck"] == "passed"
    assert result["wechat"]["uploadReceipt"]["sha256"] == _sha256(_wechat_evidence(tmp_path)[0])
    assert result["deployment"]["rollbackReleaseId"] == "previous"
    assert (
        result["guardCapabilities"]["commercialDataPublication"]["contractVersion"]
        == "commercial-data-publication-guard@v1"
    )


def test_release_manifest_rejects_missing_guard_capability_authority(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    payload.pop("guardCapabilities")
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="authority sections are missing",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_release_manifest_rejects_legacy_schema_even_with_copied_guard_fields(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    payload["schemaVersion"] = 3
    manifest = tmp_path / "legacy.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="schema version is invalid",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_release_manifest_rejects_guard_marker_digest_tampering(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    capability = payload["guardCapabilities"]["commercialDataPublication"]
    capability["markerSha256"] = "0" * 64
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="does not match the artifact",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_data_release_receipt_accepts_a_proven_closed_recruitment_cycle(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    data = payload["dataAuthorities"]
    assert isinstance(data, dict)
    receipt = data["receipt"]
    assert isinstance(receipt, dict)
    authority = receipt["authority"]
    assert isinstance(authority, dict)
    position = authority["position"]
    assert isinstance(position, dict)
    position["lifecycleState"] = "closed"
    position["currentBusinessWindow"] = None
    receipt_path = _data_evidence(tmp_path)
    receipt_path.write_text(json.dumps(authority), encoding="utf-8")
    receipt["sha256"] = _sha256(receipt_path)
    receipt["bytes"] = receipt_path.stat().st_size
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = release_manifest.verify_manifest(
        manifest_path=manifest,
        artifact_path=artifact,
        expected_commit=CANDIDATE_COMMIT,
        require_deployable=True,
        expected_release_id="candidate",
        expected_environment="preproduction",
        expected_rollback_release_id="previous",
        expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
        data_release_receipt_path=receipt_path,
        **_wechat_verify_arguments(tmp_path),
    )

    assert (
        result["dataAuthorities"]["receipt"]["authority"]["position"]["lifecycleState"] == "closed"
    )


def test_release_manifest_rejects_artifact_changed_after_manifest(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact)
    with artifact.open("ab") as handle:
        handle.write(b"changed-after-signing")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="digest does not match",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit="1" * 40,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_release_manifest_rejects_archive_from_another_git_commit(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    source = payload["source"]
    assert isinstance(source, dict)
    source["gitCommit"] = "9" * 40
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="Git commit does not match manifest",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=None,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
        )


def test_release_manifest_rejects_wechat_receipt_for_another_source_tree(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    wechat = payload["wechat"]
    assert isinstance(wechat, dict)
    receipt = wechat["uploadReceipt"]
    assert isinstance(receipt, dict)
    authority = receipt["authority"]
    assert isinstance(authority, dict)
    authority["sourceTreeListingSha256"] = "0" * 64
    _resign_manifest_wechat_receipt(payload, tmp_path)
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="source tree does not match",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit="1" * 40,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_release_manifest_rejects_raw_wechat_log_changed_after_receipt(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact)
    _, _, _, raw_log_path, _ = _wechat_evidence(tmp_path)
    raw_log_path.write_bytes(b"forged after receipt\n")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="raw CLI log evidence is invalid",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit="1" * 40,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


@pytest.mark.parametrize(
    "evidence_name",
    ("receipt", "signature", "info"),
)
def test_release_manifest_rejects_each_tampered_wechat_evidence_file(
    tmp_path: Path,
    evidence_name: str,
) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact)
    receipt, signature, info, _, _ = _wechat_evidence(tmp_path)
    evidence = {
        "receipt": receipt,
        "signature": signature,
        "info": info,
    }[evidence_name]
    evidence.write_bytes(evidence.read_bytes() + b"\n")

    with pytest.raises(release_manifest.ReleaseManifestError, match="WeChat"):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_release_manifest_rejects_signed_v1_wechat_receipt(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    wechat = payload["wechat"]
    assert isinstance(wechat, dict)
    receipt = wechat["uploadReceipt"]
    assert isinstance(receipt, dict)
    authority = receipt["authority"]
    assert isinstance(authority, dict)
    authority["schemaVersion"] = "anan-wechat-upload-receipt/v1"
    _resign_manifest_wechat_receipt(payload, tmp_path)
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="receipt schema is invalid",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_release_manifest_rejects_invalid_pinned_tool_policy_digest(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    wechat = payload["wechat"]
    assert isinstance(wechat, dict)
    receipt = wechat["uploadReceipt"]
    assert isinstance(receipt, dict)
    authority = receipt["authority"]
    assert isinstance(authority, dict)
    tool = authority["tool"]
    assert isinstance(tool, dict)
    tool["policySha256"] = "not-a-sha256"
    _resign_manifest_wechat_receipt(payload, tmp_path)
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="policySha256",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_release_manifest_rejects_missing_wechat_signature(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact)
    _wechat_evidence(tmp_path)[1].unlink()

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="signature",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_release_manifest_rejects_attacker_self_signed_wechat_receipt(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    receipt_path, signature_path, _, _, _ = _wechat_evidence(tmp_path)
    attacker_root = tmp_path / "attacker"
    attacker_root.mkdir()
    attacker_private_key, _ = _wechat_key_paths(attacker_root)
    _sign_receipt(receipt_path, signature_path, attacker_private_key)
    wechat = payload["wechat"]
    assert isinstance(wechat, dict)
    receipt = wechat["uploadReceipt"]
    assert isinstance(receipt, dict)
    signature = receipt["signature"]
    assert isinstance(signature, dict)
    signature["sha256"] = _sha256(signature_path)
    signature["bytes"] = signature_path.stat().st_size
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="signature is not trusted",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_candidate_rejects_partial_wechat_evidence_even_without_deployable_gate(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact, deployable=False)
    wechat = payload["wechat"]
    assert isinstance(wechat, dict)
    wechat["uploadVersion"] = "2.0.0"
    missing = payload["missingExternalEvidence"]
    assert isinstance(missing, list)
    missing.remove("wechat_upload_version")
    manifest = tmp_path / "partial.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="partial WeChat upload evidence",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_incomplete_candidate_cannot_enter_release_staging(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact, deployable=False)

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="lacks WeChat, deployment, or rollback evidence",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit="1" * 40,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_repository_database_contract_names_its_head_as_compatible() -> None:
    head, compatible = release_manifest._parse_database_contract(
        REPOSITORY_ROOT / "ops" / "release-db-compatibility.env"
    )

    assert head in compatible


def test_manifest_rejects_artifact_missing_database_contract(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact, deployable=False)
    (tmp_path / "source" / "ops" / "release-db-compatibility.env").unlink()
    _rebuild_artifact(artifact)
    _refresh_artifact_identity(payload, artifact)
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="missing required member: ops/release-db-compatibility.env",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_manifest_rejects_database_contract_digest_not_in_artifact(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact, deployable=False)
    database = payload["database"]
    assert isinstance(database, dict)
    database["contractSha256"] = "0" * 64
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="database contract authority does not match the artifact",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_manifest_rejects_database_authority_different_from_artifact_contract(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact, deployable=False)
    contract = tmp_path / "source" / "ops" / "release-db-compatibility.env"
    contract.write_text(
        "\n".join(
            (
                "ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT=artifact-db-compatibility@v1",
                "ARTIFACT_DATABASE_MIGRATION_HEAD=20260730_80",
                "ARTIFACT_DATABASE_COMPATIBLE_REVISIONS=20260730_80",
                "",
            )
        ),
        encoding="utf-8",
    )
    _rebuild_artifact(artifact)
    _refresh_artifact_identity(payload, artifact)
    database = payload["database"]
    assert isinstance(database, dict)
    database["contractSha256"] = _archive_member_sha256(
        artifact,
        "ops/release-db-compatibility.env",
    )
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="database authority does not match the artifact contract",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_isolated_gate_rejects_contract_head_different_from_alembic_graph() -> None:
    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="Alembic graph head does not match",
    ):
        release_manifest._validate_isolated_alembic_heads(
            json.dumps(["20260730_80"]),
            expected_head="20260730_79",
        )


@pytest.mark.parametrize(
    ("field", "expected", "message"),
    (
        ("release", "other", "activation target"),
        ("environment", "production", "trusted target"),
        ("rollback", "definitely-missing", "current release"),
    ),
)
def test_deployable_manifest_requires_exact_target_identity(
    tmp_path: Path,
    field: str,
    expected: str,
    message: str,
) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact)
    identities = {
        "expected_release_id": "candidate",
        "expected_environment": "preproduction",
        "expected_rollback_release_id": "previous",
    }
    identities[
        {
            "release": "expected_release_id",
            "environment": "expected_environment",
            "rollback": "expected_rollback_release_id",
        }[field]
    ] = expected

    with pytest.raises(release_manifest.ReleaseManifestError, match=message):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit="1" * 40,
            require_deployable=True,
            expected_compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
            **identities,
        )


def test_deployable_manifest_rejects_data_incompatible_with_rollback_commit(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    manifest = _write_manifest(tmp_path, artifact)

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="incompatible with an expected application commit",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=True,
            expected_release_id="candidate",
            expected_environment="preproduction",
            expected_rollback_release_id="previous",
            expected_compatible_commits=(
                CANDIDATE_COMMIT,
                "d" * 40,
            ),
            data_release_receipt_path=_data_evidence(tmp_path),
            **_wechat_verify_arguments(tmp_path),
        )


def test_manifest_rejects_runtime_lock_authority_not_in_artifact(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    runtime = payload["runtimeDependencies"]
    assert isinstance(runtime, dict)
    runtime["lockSha256"] = "0" * 64
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="uv lock authority",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit="1" * 40,
            require_deployable=False,
        )


def test_manifest_rejects_generated_types_digest_not_in_artifact(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    payload = _manifest(artifact)
    api_contract = payload["apiContract"]
    assert isinstance(api_contract, dict)
    api_contract["generatedTypesSha256"] = "0" * 64
    manifest = tmp_path / "candidate.release.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="API contract authority does not match artifact members",
    ):
        release_manifest.verify_manifest(
            manifest_path=manifest,
            artifact_path=artifact,
            expected_commit=CANDIDATE_COMMIT,
            require_deployable=False,
        )


def test_staging_release_is_lightweight_but_stays_inside_staging() -> None:
    script = STAGING_SCRIPT.read_text(encoding="utf-8")

    assert "rsync -az --delete" in script
    assert "/srv/gongkao-dazi/staging" in script
    assert "gongkao-dazi-staging-" in script
    assert "systemctl restart" in script
    assert "curl --fail" in script
    assert ".staging-release-id" in script
    assert "STAGING_RELEASE_TRUST_PUBLIC_KEY" not in script
    assert "RELEASE_SIGNING_PRIVATE_KEY" not in script
    assert "WECHAT_UPLOAD_RECEIPT" not in script
    assert "DATA_RELEASE_RECEIPT" not in script
    assert "gongkao-dazi-production" not in script


def _builder_harness(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, str]]:
    harness_root = tmp_path / "builder-repository"
    scripts = harness_root / "ops" / "scripts"
    scripts.mkdir(parents=True)
    builder = scripts / "build_immutable_artifact.sh"
    builder.write_text(
        (REPOSITORY_ROOT / "ops" / "scripts" / "build_immutable_artifact.sh").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    builder.chmod(0o755)
    verifier = scripts / "release_manifest.py"
    verifier.write_text(SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    verifier.chmod(0o755)

    shim_root = tmp_path / "builder-shims"
    shim_root.mkdir()
    git_shim = shim_root / "git"
    git_shim.write_text(
        """#!/bin/sh
set -eu
case " $* " in
  *" status "*) exit 0 ;;
  *" archive "*)
    output=""
    for argument in "$@"; do
      case "${argument}" in
        --output=*) output="${argument#--output=}" ;;
      esac
    done
    test -n "${output}"
    printf 'builder:%s\\n' "$$" > "${output}"
    if [ -n "${BUILDER_BARRIER_DIR:-}" ]; then
      : > "${BUILDER_BARRIER_DIR}/$$"
      while [ "$(find "${BUILDER_BARRIER_DIR}" -type f | wc -l | tr -d ' ')" -lt 2 ]; do
        sleep 0.01
      done
    fi
    if [ -n "${BUILDER_BLOCK_FILE:-}" ]; then
      : > "${BUILDER_BLOCK_FILE}.ready"
      while [ -e "${BUILDER_BLOCK_FILE}" ]; do
        sleep 0.01
      done
    fi
    exit 0
    ;;
esac
printf 'unexpected git invocation: %s\\n' "$*" >&2
exit 91
""",
        encoding="utf-8",
    )
    git_shim.chmod(0o755)
    python_shim = shim_root / "python3"
    python_shim.write_text(
        """#!/bin/sh
set -eu
if [ "$#" -ge 2 ] && [ "$2" = "build" ]; then
  artifact=""
  output=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --artifact) shift; artifact="$1" ;;
      --output) shift; output="$1" ;;
    esac
    shift
  done
  test -n "${artifact}"
  test -n "${output}"
  cp "${artifact}" "${output}"
  (
    cd "$(dirname "${output}")"
    shasum -a 256 "$(basename "${output}")" > "$(basename "${output}").sha256"
  )
  exit 0
fi
exec "${REAL_PYTHON}" "$@"
""",
        encoding="utf-8",
    )
    python_shim.chmod(0o755)
    output_dir = tmp_path / "builder-output"
    environment = {
        **os.environ,
        "PATH": f"{shim_root}:{os.environ['PATH']}",
        "REAL_PYTHON": sys.executable,
        "ARTIFACT_OUTPUT_DIR": str(output_dir),
    }
    return builder, output_dir, environment


def _assert_complete_release_set(release_set: Path, release_id: str) -> None:
    artifact = release_set / f"anan-{release_id}.tar.gz"
    manifest = release_set / f"anan-{release_id}.release.json"
    assert {entry.name for entry in release_set.iterdir()} == {
        artifact.name,
        f"{artifact.name}.sha256",
        manifest.name,
        f"{manifest.name}.sha256",
    }
    assert artifact.read_bytes() == manifest.read_bytes()
    for path in (artifact, manifest):
        subprocess.run(
            ["shasum", "-a", "256", "-c", f"{path.name}.sha256"],
            cwd=release_set,
            check=True,
            capture_output=True,
            text=True,
        )


def test_artifact_builder_concurrency_publishes_once_without_overwrite(
    tmp_path: Path,
) -> None:
    builder, output_dir, environment = _builder_harness(tmp_path)
    barrier = tmp_path / "builder-barrier"
    barrier.mkdir()
    environment["BUILDER_BARRIER_DIR"] = str(barrier)
    environment["RELEASE_ID"] = "race"
    processes = [
        subprocess.Popen(
            [str(builder)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=20) for process in processes]

    assert sorted(process.returncode for process in processes) == [0, 1]
    assert any("refusing to overwrite existing release output" in stderr for _, stderr in results)
    release_set = output_dir / "anan-race.release-set"
    _assert_complete_release_set(release_set, "race")
    assert not tuple(output_dir.glob(".anan-race.release-set.*"))


def test_artifact_builder_sigkill_leaves_no_visible_partial_set_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    builder, output_dir, environment = _builder_harness(tmp_path)
    block = tmp_path / "hold-builder"
    block.touch()
    environment["BUILDER_BLOCK_FILE"] = str(block)
    environment["RELEASE_ID"] = "kill-window"
    process = subprocess.Popen(
        [str(builder)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    ready = Path(f"{block}.ready")
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    assert tuple(output_dir.glob(".anan-kill-window.release-set.*"))

    os.killpg(process.pid, signal.SIGKILL)
    process.communicate(timeout=10)
    assert not (output_dir / "anan-kill-window.release-set").exists()

    block.unlink()
    retry_environment = {
        key: value for key, value in environment.items() if key != "BUILDER_BLOCK_FILE"
    }
    completed = subprocess.run(
        [str(builder)],
        env=retry_environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    published_artifact = Path(completed.stdout.strip())
    assert published_artifact.parent == output_dir / "anan-kill-window.release-set"
    _assert_complete_release_set(published_artifact.parent, "kill-window")


def test_artifact_builder_never_overwrites_or_leaves_a_partial_release_set() -> None:
    script = (REPOSITORY_ROOT / "ops" / "scripts" / "build_immutable_artifact.sh").read_text(
        encoding="utf-8"
    )

    assert "refusing to overwrite existing release output" in script
    assert "cleanup_failed_build()" in script
    assert "mktemp -d" in script
    assert "publish-release-set" in script
    assert '"${output_dir}/.anan-${release_id}.release-set.' in script
    assert '"${release_set}"' in script
    assert "audit/api-contract/openapi.json" in script
    assert "backend/.venv/bin/python" not in script
    assert "--wechat-upload-receipt-signature" in script
    assert "--wechat-upload-info-output" in script
    assert "--wechat-uploader-trust-public-key" in script


def test_stage8_ci_uploads_the_complete_atomic_release_set() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "stage8-release.yml").read_text(
        encoding="utf-8"
    )
    upload = workflow.split("uses: actions/upload-artifact@v4", maxsplit=1)[1]

    assert "path: dist/anan-${{ inputs.release_id }}.release-set/" in upload
    assert "release-set/anan-${{ inputs.release_id }}.tar.gz" not in upload
    assert "if-no-files-found: error" in upload


def test_release_signing_key_validation_is_cross_platform_and_fail_closed(
    tmp_path: Path,
) -> None:
    key = tmp_path / "release-signing.pem"
    key.write_text("test private key\n", encoding="utf-8")
    key.chmod(0o600)

    release_manifest.validate_signing_private_key(key)

    key.chmod(0o644)
    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="mode-0600",
    ):
        release_manifest.validate_signing_private_key(key)

    key.chmod(0o600)
    link = tmp_path / "release-signing-link.pem"
    link.symlink_to(key)
    with pytest.raises(
        release_manifest.ReleaseManifestError,
        match="non-symlink",
    ):
        release_manifest.validate_signing_private_key(link)
