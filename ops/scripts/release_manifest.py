#!/usr/bin/env python3
"""Build and verify the detached immutable release authority manifest."""

from __future__ import annotations

import argparse
import ctypes
from datetime import UTC, datetime
import errno
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from typing import Any
from uuid import UUID


FORMAT_VERSION = 4
WECHAT_RECEIPT_VERSION = "anan-wechat-upload-receipt/v2"
DATA_RELEASE_RECEIPT_VERSION = "anan-data-release-receipt/v1"
GUARD_CAPABILITY_SCHEMA_VERSION = "anan-release-capability/v1"
GUARD_CAPABILITY_NAME = "commercial-data-publication-guard"
GUARD_CAPABILITY_CONTRACT_VERSION = "commercial-data-publication-guard@v1"
GUARD_CAPABILITY_PATH = "ops/commercial-data-publication-guard.json"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
ENVIRONMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUIRED_ARCHIVE_ROOTS = frozenset(
    {"backend", "apps", "ops", "scripts", ".github", "audit"}
)
CANONICAL_OPENAPI_PATH = "audit/api-contract/openapi.json"
GENERATED_TYPES_PATH = "apps/wechat/miniprogram/api/generated/types.gen.ts"
WECHAT_PROJECT_CONFIG_PATH = "apps/wechat/project.config.json"
DATABASE_CONTRACT_PATH = "ops/release-db-compatibility.env"
DATABASE_CONTRACT_VERSION = "artifact-db-compatibility@v1"
MAX_WECHAT_RECEIPT_BYTES = 256 * 1024
MAX_WECHAT_SIGNATURE_BYTES = 16 * 1024
MAX_WECHAT_INFO_BYTES = 4 * 1024 * 1024
MAX_WECHAT_LOG_BYTES = 16 * 1024 * 1024


class ReleaseManifestError(RuntimeError):
    """Raised when a release cannot be tied to one immutable authority."""


def validate_signing_private_key(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseManifestError("release signing private key is unreadable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ReleaseManifestError(
            "release signing private key must be a non-symlink mode-0600 "
            "file owned by the signing process"
        )


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise ReleaseManifestError(
            f"command failed: {' '.join(arguments)}\n{stderr.strip()}"
        ) from exc
    return completed.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bounded(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ReleaseManifestError(f"{label} is unreadable") from exc
    if not path.is_file() or metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
        raise ReleaseManifestError(
            f"{label} must be a non-empty file no larger than {maximum_bytes} bytes"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseManifestError(f"{label} is unreadable") from exc


def _json_object_without_duplicates(encoded: bytes, *, label: str) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseManifestError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ReleaseManifestError(f"{label} contains non-finite JSON value {value}")

    try:
        parsed = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"{label} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ReleaseManifestError(f"{label} must be a JSON object")
    return parsed


def _validate_trust_public_key(path: Path) -> Path:
    resolved = path.resolve()
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseManifestError(
            "WeChat uploader trust public key is unreadable"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size <= 0
    ):
        raise ReleaseManifestError(
            "WeChat uploader trust public key must be a non-symlink regular "
            "file owned by root or the verifier and not group/world writable"
        )
    return resolved


def _public_key_id(path: Path) -> str:
    trusted = _validate_trust_public_key(path)
    try:
        completed = subprocess.run(
            [
                "openssl",
                "pkey",
                "-pubin",
                "-in",
                str(trusted),
                "-outform",
                "DER",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseManifestError(
            "WeChat uploader trust public key is invalid"
        ) from exc
    if not completed.stdout:
        raise ReleaseManifestError("WeChat uploader trust public key is invalid")
    return f"sha256:{sha256(completed.stdout).hexdigest()}"


def _verify_detached_signature(
    *,
    payload: Path,
    signature: Path,
    public_key: Path,
) -> str:
    _read_bounded(
        signature,
        label="WeChat upload receipt signature",
        maximum_bytes=MAX_WECHAT_SIGNATURE_BYTES,
    )
    trusted = _validate_trust_public_key(public_key)
    try:
        subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-verify",
                str(trusted),
                "-signature",
                str(signature.resolve()),
                str(payload.resolve()),
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseManifestError(
            "WeChat upload receipt signature is not trusted"
        ) from exc
    return _public_key_id(trusted)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_checksum(path: Path) -> Path:
    checksum_path = path.with_name(f"{path.name}.sha256")
    checksum_path.write_text(
        f"{_sha256_file(path)}  {path.name}\n",
        encoding="utf-8",
    )
    return checksum_path


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
            raise ReleaseManifestError(
                "release publication entry is not a regular file"
            )
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
            raise ReleaseManifestError(
                "filesystem lacks atomic no-replace release publication"
            )
        if result != 0:
            error_number = ctypes.get_errno()
            if error_number in (errno.EEXIST, errno.ENOTEMPTY):
                raise ReleaseManifestError(
                    "refusing to overwrite existing release output"
                )
            raise ReleaseManifestError(
                "atomic release directory publication failed: "
                f"{os.strerror(error_number)}"
            )
    finally:
        os.close(parent_descriptor)


def publish_release_set(
    *,
    staging_directory: Path,
    destination_directory: Path,
    release_id: str,
) -> Path:
    if not RELEASE_ID.fullmatch(release_id):
        raise ReleaseManifestError("release id contains unsupported characters")
    try:
        staging_metadata = staging_directory.lstat()
    except OSError as exc:
        raise ReleaseManifestError("release staging directory is unavailable") from exc
    if not stat.S_ISDIR(staging_metadata.st_mode) or stat.S_ISLNK(
        staging_metadata.st_mode
    ):
        raise ReleaseManifestError("release staging directory must be a real directory")
    staging_directory = staging_directory.resolve()
    destination_parent = destination_directory.parent.resolve()
    destination_directory = destination_parent / destination_directory.name
    if (
        staging_directory.parent != destination_parent
        or not staging_directory.name.startswith(f".anan-{release_id}.release-set.")
        or destination_directory.name != f"anan-{release_id}.release-set"
    ):
        raise ReleaseManifestError("release staging directory identity is invalid")
    artifact_name = f"anan-{release_id}.tar.gz"
    manifest_name = f"anan-{release_id}.release.json"
    expected_names = {
        artifact_name,
        f"{artifact_name}.sha256",
        manifest_name,
        f"{manifest_name}.sha256",
    }
    entries = tuple(staging_directory.iterdir())
    if {entry.name for entry in entries} != expected_names:
        raise ReleaseManifestError(
            "release staging directory is not one complete sealed set"
        )
    for entry in entries:
        try:
            metadata = entry.lstat()
        except OSError as exc:
            raise ReleaseManifestError("release staging entry is unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ReleaseManifestError(
                "release staging entries must be regular non-symlink files"
            )
        _fsync_regular_file(entry)
    _fsync_directory(staging_directory)
    _rename_directory_noreplace(
        source=staging_directory,
        destination=destination_directory,
    )
    _fsync_directory(destination_parent)
    return destination_directory / artifact_name


def _git(project_root: Path, *arguments: str) -> str:
    return _run(["git", *arguments], cwd=project_root)


def _require_clean_source(project_root: Path) -> tuple[str, str, str]:
    commit = _git(project_root, "rev-parse", "HEAD")
    tree = _git(project_root, "rev-parse", "HEAD^{tree}")
    branch = _git(project_root, "branch", "--show-current")
    dirty = _git(project_root, "status", "--porcelain", "--untracked-files=normal")
    if dirty:
        preview = "\n".join(dirty.splitlines()[:20])
        raise ReleaseManifestError(
            "release artifacts require a clean source tree; current changes:\n"
            f"{preview}"
        )
    return commit, tree, branch


def _assert_safe_archive(artifact: Path) -> tuple[tuple[str, ...], str]:
    roots: set[str] = set()
    try:
        with tarfile.open(artifact, mode="r:*") as archive:
            archive_commit = archive.pax_headers.get("comment", "")
            if not re.fullmatch(r"[0-9a-f]{40,64}", archive_commit):
                raise ReleaseManifestError(
                    "release artifact lacks a valid Git archive commit"
                )
            members = archive.getmembers()
            if not members:
                raise ReleaseManifestError("release artifact is empty")
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ReleaseManifestError(
                        f"release artifact contains unsafe path: {member.name}"
                    )
                if member.isdev() or member.isfifo():
                    raise ReleaseManifestError(
                        f"release artifact contains unsafe member: {member.name}"
                    )
                if member.issym():
                    resolved_link = posixpath.normpath(
                        posixpath.join(
                            posixpath.dirname(member.name),
                            member.linkname,
                        )
                    )
                    if posixpath.isabs(member.linkname) or (
                        resolved_link == ".." or resolved_link.startswith("../")
                    ):
                        raise ReleaseManifestError(
                            "release artifact contains an escaping symbolic link: "
                            f"{member.name}"
                        )
                if member.islnk():
                    resolved_link = posixpath.normpath(member.linkname)
                    if posixpath.isabs(member.linkname) or (
                        resolved_link == ".." or resolved_link.startswith("../")
                    ):
                        raise ReleaseManifestError(
                            "release artifact contains an escaping hard link: "
                            f"{member.name}"
                        )
                if path.parts:
                    roots.add(path.parts[0])
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseManifestError(
            "release artifact is not a readable tar archive"
        ) from exc
    missing = REQUIRED_ARCHIVE_ROOTS - roots
    if missing:
        raise ReleaseManifestError(
            f"release artifact is missing required roots: {sorted(missing)}"
        )
    return tuple(sorted(roots)), archive_commit


def _archive_member_sha256(artifact: Path, member_name: str) -> str:
    try:
        with tarfile.open(artifact, mode="r:*") as archive:
            member = archive.getmember(member_name)
            if not member.isfile():
                raise ReleaseManifestError(
                    f"release artifact member is not a regular file: {member_name}"
                )
            handle = archive.extractfile(member)
            if handle is None:
                raise ReleaseManifestError(
                    f"release artifact member is unreadable: {member_name}"
                )
            digest = sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except KeyError as exc:
        raise ReleaseManifestError(
            f"release artifact is missing required member: {member_name}"
        ) from exc
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseManifestError(
            "release artifact is not a readable tar archive"
        ) from exc


def _archive_member_bytes(artifact: Path, member_name: str) -> bytes:
    try:
        with tarfile.open(artifact, mode="r:*") as archive:
            member = archive.getmember(member_name)
            if not member.isfile():
                raise ReleaseManifestError(
                    f"release artifact member is not a regular file: {member_name}"
                )
            handle = archive.extractfile(member)
            if handle is None:
                raise ReleaseManifestError(
                    f"release artifact member is unreadable: {member_name}"
                )
            return handle.read()
    except KeyError as exc:
        raise ReleaseManifestError(
            f"release artifact is missing required member: {member_name}"
        ) from exc
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseManifestError(
            "release artifact is not a readable tar archive"
        ) from exc


def _archive_json_object(
    artifact: Path,
    member_name: str,
    *,
    label: str,
) -> dict[str, Any]:
    return _json_object_without_duplicates(
        _archive_member_bytes(artifact, member_name),
        label=label,
    )


def _guard_capability_authority(
    project_root: Path,
    artifact: Path,
) -> dict[str, str]:
    marker_path = project_root / GUARD_CAPABILITY_PATH
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(
            "commercial data publication guard marker is unreadable"
        ) from exc
    expected = {
        "schemaVersion": GUARD_CAPABILITY_SCHEMA_VERSION,
        "capability": GUARD_CAPABILITY_NAME,
        "contractVersion": GUARD_CAPABILITY_CONTRACT_VERSION,
    }
    if marker != expected:
        raise ReleaseManifestError(
            "commercial data publication guard marker is invalid"
        )
    marker_sha256 = _sha256_file(marker_path)
    if _archive_member_sha256(artifact, GUARD_CAPABILITY_PATH) != marker_sha256:
        raise ReleaseManifestError(
            "release artifact guard capability differs from source"
        )
    return {
        "capability": GUARD_CAPABILITY_NAME,
        "contractVersion": GUARD_CAPABILITY_CONTRACT_VERSION,
        "markerPath": GUARD_CAPABILITY_PATH,
        "markerSha256": marker_sha256,
    }


def _verify_guard_capability(
    authority: Any,
    artifact: Path,
) -> None:
    if not isinstance(authority, dict) or authority != {
        "capability": GUARD_CAPABILITY_NAME,
        "contractVersion": GUARD_CAPABILITY_CONTRACT_VERSION,
        "markerPath": GUARD_CAPABILITY_PATH,
        "markerSha256": authority.get("markerSha256"),
    }:
        raise ReleaseManifestError("release guard capability authority is invalid")
    marker_sha256 = authority.get("markerSha256")
    if (
        not isinstance(marker_sha256, str)
        or not HEX_SHA256.fullmatch(marker_sha256)
        or _archive_member_sha256(artifact, GUARD_CAPABILITY_PATH) != marker_sha256
    ):
        raise ReleaseManifestError(
            "release guard capability does not match the artifact"
        )
    try:
        with tarfile.open(artifact, mode="r:*") as archive:
            handle = archive.extractfile(GUARD_CAPABILITY_PATH)
            if handle is None:
                raise ReleaseManifestError(
                    "release guard capability marker is unreadable"
                )
            marker = json.loads(handle.read().decode("utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as exc:
        raise ReleaseManifestError(
            "release guard capability marker is unreadable"
        ) from exc
    if marker != {
        "schemaVersion": GUARD_CAPABILITY_SCHEMA_VERSION,
        "capability": GUARD_CAPABILITY_NAME,
        "contractVersion": GUARD_CAPABILITY_CONTRACT_VERSION,
    }:
        raise ReleaseManifestError("release guard capability marker is invalid")


def _runtime_dependency_authority(project_root: Path) -> dict[str, Any]:
    lock_path = project_root / "backend" / "uv.lock"
    pyproject_path = project_root / "backend" / "pyproject.toml"
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseManifestError(
            "backend runtime dependency authority is unreadable"
        ) from exc
    requires_python = pyproject.get("project", {}).get("requires-python")
    if (
        not isinstance(lock.get("version"), int)
        or not isinstance(lock.get("revision"), int)
        or not isinstance(requires_python, str)
        or not requires_python
        or lock.get("requires-python") != requires_python
    ):
        raise ReleaseManifestError(
            "backend uv.lock does not match the project Python authority"
        )
    return {
        "resolver": "uv",
        "installMode": "sync-frozen-no-dev-no-editable",
        "lockPath": "backend/uv.lock",
        "lockSha256": _sha256_file(lock_path),
        "lockFormatVersion": lock["version"],
        "lockRevision": lock["revision"],
        "pyprojectPath": "backend/pyproject.toml",
        "pyprojectSha256": _sha256_file(pyproject_path),
        "requiresPython": requires_python,
    }


def _parse_database_contract_bytes(
    encoded: bytes,
    *,
    label: str,
) -> tuple[str, tuple[str, ...]]:
    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ReleaseManifestError(f"{label} must be valid UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ReleaseManifestError(f"{label} contains an invalid declaration")
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in values:
            raise ReleaseManifestError(
                f"{label} contains duplicate declaration {normalized_key}"
            )
        values[normalized_key] = value.strip().strip("\"'")
    expected_keys = {
        "ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT",
        "ARTIFACT_DATABASE_MIGRATION_HEAD",
        "ARTIFACT_DATABASE_COMPATIBLE_REVISIONS",
    }
    if set(values) != expected_keys:
        raise ReleaseManifestError(f"{label} declarations are invalid")
    if values["ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT"] != DATABASE_CONTRACT_VERSION:
        raise ReleaseManifestError(f"{label} version is invalid")
    head = values.get("ARTIFACT_DATABASE_MIGRATION_HEAD", "")
    compatible = tuple(
        item.strip()
        for item in values.get("ARTIFACT_DATABASE_COMPATIBLE_REVISIONS", "").split(",")
        if item.strip()
    )
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+", head)
        or len(set(compatible)) != len(compatible)
        or any(
            re.fullmatch(r"[A-Za-z0-9_.-]+", revision) is None
            for revision in compatible
        )
        or head not in compatible
    ):
        raise ReleaseManifestError(
            "database compatibility contract must include its artifact head"
        )
    return head, compatible


def _parse_database_contract(path: Path) -> tuple[str, tuple[str, ...]]:
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise ReleaseManifestError(
            "database compatibility contract is unreadable"
        ) from exc
    return _parse_database_contract_bytes(
        encoded,
        label="database compatibility contract",
    )


def _artifact_database_contract(
    artifact: Path,
) -> tuple[str, tuple[str, ...], str]:
    encoded = _archive_member_bytes(artifact, DATABASE_CONTRACT_PATH)
    head, compatible = _parse_database_contract_bytes(
        encoded,
        label="artifact database compatibility contract",
    )
    return head, compatible, sha256(encoded).hexdigest()


def _validate_isolated_alembic_heads(
    encoded: str,
    *,
    expected_head: str,
) -> tuple[str, ...]:
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ReleaseManifestError(
            "isolated artifact Alembic graph output is invalid"
        ) from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(head, str) or not head for head in parsed)
    ):
        raise ReleaseManifestError("isolated artifact Alembic graph output is invalid")
    heads = tuple(parsed)
    if heads != (expected_head,):
        raise ReleaseManifestError(
            "artifact Alembic graph head does not match its database contract"
        )
    return heads


def _isolated_gate_environment(temporary_root: Path) -> dict[str, str]:
    environment = {
        **os.environ,
        "HOME": str(temporary_root / "home"),
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
        "UV_CACHE_DIR": str(temporary_root / "uv-cache"),
        "npm_config_cache": str(temporary_root / "npm-cache"),
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_ignore_scripts": "true",
    }
    for name in (
        "BASH_ENV",
        "ENV",
        "NODE_OPTIONS",
        "NPM_CONFIG_USERCONFIG",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_ACTIVE",
        "UV_NO_DEV",
        "UV_NO_PROJECT",
        "UV_NO_SYNC",
        "UV_PROJECT_ENVIRONMENT",
    ):
        environment.pop(name, None)
    Path(environment["HOME"]).mkdir(mode=0o700)
    return environment


def _trusted_tool_authority(
    name: str,
    *,
    cwd: Path,
    environment: dict[str, str],
) -> dict[str, str]:
    executable = shutil.which(name)
    if executable is None:
        raise ReleaseManifestError(
            f"{name} is required for the isolated API contract gate"
        )
    resolved = Path(executable).resolve()
    if not resolved.is_file():
        raise ReleaseManifestError(
            f"{name} executable is invalid for the isolated API contract gate"
        )
    return {
        "version": _run([name, "--version"], cwd=cwd, environment=environment),
        "executableSha256": _sha256_file(resolved),
    }


def _isolated_api_contract_authority(
    artifact: Path,
    *,
    expected_database_head: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    with tempfile.TemporaryDirectory(
        prefix="anan-release-api-contract-"
    ) as temporary_name:
        temporary_root = Path(temporary_name)
        extracted_root = temporary_root / "source"
        extracted_root.mkdir(mode=0o700)
        try:
            with tarfile.open(artifact, mode="r:*") as archive:
                archive.extractall(extracted_root, filter="data")
        except (OSError, tarfile.TarError) as exc:
            raise ReleaseManifestError(
                "release artifact cannot be isolated for API contract verification"
            ) from exc

        backend_root = extracted_root / "backend"
        wechat_root = extracted_root / "apps" / "wechat"
        environment = _isolated_gate_environment(temporary_root)
        toolchain = {
            name: _trusted_tool_authority(
                name,
                cwd=extracted_root,
                environment=environment,
            )
            for name in ("uv", "node", "npm")
        }
        if not toolchain["node"]["version"].startswith("v24."):
            raise ReleaseManifestError(
                "isolated API contract gate requires the locked Node 24 toolchain"
            )
        _run(
            [
                "npm",
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            cwd=wechat_root,
            environment=environment,
        )
        openapi_path = temporary_root / "openapi.json"
        _run(
            [
                "uv",
                "run",
                "--project",
                str(backend_root),
                "--locked",
                "--isolated",
                "--no-dev",
                "--no-env-file",
                "python",
                str(backend_root / "scripts" / "export_openapi.py"),
                "--output",
                str(openapi_path),
            ],
            cwd=backend_root,
            environment=environment,
        )
        _run(
            [
                "node",
                str(wechat_root / "scripts" / "generate-api.mjs"),
                "--check",
            ],
            cwd=wechat_root,
            environment=environment,
        )
        alembic_heads = _validate_isolated_alembic_heads(
            _run(
                [
                    "uv",
                    "run",
                    "--project",
                    str(backend_root),
                    "--locked",
                    "--isolated",
                    "--no-dev",
                    "--no-env-file",
                    "python",
                    "-c",
                    (
                        "import json;"
                        "from alembic.config import Config;"
                        "from alembic.script import ScriptDirectory;"
                        "config=Config('alembic.ini');"
                        "print(json.dumps(ScriptDirectory.from_config(config).get_heads()))"
                    ),
                ],
                cwd=backend_root,
                environment=environment,
            ),
            expected_head=expected_database_head,
        )
        canonical_openapi_sha256 = _archive_member_sha256(
            artifact,
            CANONICAL_OPENAPI_PATH,
        )
        if _sha256_file(openapi_path) != canonical_openapi_sha256:
            raise ReleaseManifestError(
                "isolated OpenAPI export differs from the canonical artifact member"
            )
        return (
            {
                "openapiSha256": canonical_openapi_sha256,
                "generatedTypesSha256": _archive_member_sha256(
                    artifact,
                    GENERATED_TYPES_PATH,
                ),
                "generatedTypesCheck": "passed",
                "verificationEnvironment": "isolated-artifact-lockfiles",
                "toolchain": toolchain,
            },
            alembic_heads,
        )


def _unverified_api_contract_authority(artifact: Path) -> dict[str, Any]:
    return {
        "openapiSha256": _archive_member_sha256(
            artifact,
            CANONICAL_OPENAPI_PATH,
        ),
        "generatedTypesSha256": _archive_member_sha256(
            artifact,
            GENERATED_TYPES_PATH,
        ),
        "generatedTypesCheck": "not_verified_isolated",
        "verificationEnvironment": None,
        "toolchain": None,
    }


def _wechat_tree_listing_sha256(project_root: Path, commit: str) -> str:
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "-z", commit, "--", "apps/wechat"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    if not completed.stdout:
        raise ReleaseManifestError("Git commit contains no WeChat source tree")
    return sha256(completed.stdout).hexdigest()


def _validate_wechat_info_output(authority: Any) -> None:
    if not isinstance(authority, dict):
        raise ReleaseManifestError("WeChat CLI info output must be a JSON object")
    size = authority.get("size")
    packages = size.get("packages") if isinstance(size, dict) else None
    if (
        not isinstance(packages, list)
        or not packages
        or any(
            not isinstance(package, dict)
            or not isinstance(package.get("name"), str)
            or not package["name"].strip()
            or isinstance(package.get("size"), bool)
            or not isinstance(package.get("size"), int)
            or package["size"] < 0
            for package in packages
        )
    ):
        raise ReleaseManifestError(
            "WeChat CLI info output lacks valid package-size evidence"
        )


def _receipt(
    receipt_path: Path | None,
    signature_path: Path | None,
    info_output_path: Path | None,
    raw_log_path: Path | None,
    uploader_trust_public_key_path: Path | None,
    *,
    upload_version: str,
    source_commit: str,
    source_git_tree: str,
    source_tree_sha256: str,
    project_config_sha256: str,
    expected_app_id: str,
) -> dict[str, Any] | None:
    evidence = (
        receipt_path,
        signature_path,
        info_output_path,
        raw_log_path,
        uploader_trust_public_key_path,
    )
    if all(path is None for path in evidence):
        return None
    if any(path is None for path in evidence):
        raise ReleaseManifestError(
            "WeChat upload receipt, signature, info output, raw log, and "
            "trusted uploader public key must be supplied together"
        )
    assert receipt_path is not None
    assert signature_path is not None
    assert info_output_path is not None
    assert raw_log_path is not None
    assert uploader_trust_public_key_path is not None
    resolved = receipt_path.resolve()
    resolved_signature = signature_path.resolve()
    resolved_info = info_output_path.resolve()
    resolved_raw_log = raw_log_path.resolve()
    receipt_bytes = _read_bounded(
        resolved,
        label="WeChat upload receipt",
        maximum_bytes=MAX_WECHAT_RECEIPT_BYTES,
    )
    info_bytes = _read_bounded(
        resolved_info,
        label="WeChat CLI info output",
        maximum_bytes=MAX_WECHAT_INFO_BYTES,
    )
    _read_bounded(
        resolved_raw_log,
        label="WeChat raw CLI log",
        maximum_bytes=MAX_WECHAT_LOG_BYTES,
    )
    uploader_key_id = _verify_detached_signature(
        payload=resolved,
        signature=resolved_signature,
        public_key=uploader_trust_public_key_path,
    )
    authority = _json_object_without_duplicates(
        receipt_bytes,
        label="WeChat upload receipt",
    )
    info_authority = _json_object_without_duplicates(
        info_bytes,
        label="WeChat CLI info output",
    )
    _validate_wechat_info_output(info_authority)
    _validate_wechat_receipt(
        authority,
        upload_version=upload_version,
        source_commit=source_commit,
        source_git_tree=source_git_tree,
        source_tree_sha256=source_tree_sha256,
        project_config_sha256=project_config_sha256,
        expected_app_id=expected_app_id,
        uploader_key_id=uploader_key_id,
    )
    raw_log_sha256 = _sha256_file(resolved_raw_log)
    info_sha256 = _sha256_file(resolved_info)
    if (
        authority.get("rawCliLogSha256") != raw_log_sha256
        or authority.get("rawCliLogBytes") != resolved_raw_log.stat().st_size
    ):
        raise ReleaseManifestError(
            "WeChat upload receipt raw log evidence does not match the original log"
        )
    if (
        authority.get("infoOutputSha256") != info_sha256
        or authority.get("infoOutputBytes") != resolved_info.stat().st_size
        or authority.get("infoOutput") != info_authority
    ):
        raise ReleaseManifestError(
            "WeChat upload receipt info output does not match the detached file"
        )
    return {
        "fileName": resolved.name,
        "sha256": _sha256_file(resolved),
        "bytes": resolved.stat().st_size,
        "authority": authority,
        "signature": {
            "fileName": resolved_signature.name,
            "sha256": _sha256_file(resolved_signature),
            "bytes": resolved_signature.stat().st_size,
            "algorithm": authority["signatureAlgorithm"],
            "uploaderKeyId": uploader_key_id,
        },
        "infoOutput": {
            "fileName": resolved_info.name,
            "sha256": info_sha256,
            "bytes": resolved_info.stat().st_size,
            "authority": info_authority,
        },
        "rawCliLog": {
            "fileName": resolved_raw_log.name,
            "sha256": raw_log_sha256,
            "bytes": resolved_raw_log.stat().st_size,
        },
    }


def _data_release_receipt(
    receipt_path: Path | None,
    *,
    source_commit: str,
    deployment_environment: str,
) -> dict[str, Any] | None:
    if receipt_path is None:
        return None
    resolved = receipt_path.resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ReleaseManifestError("data release receipt must be a non-empty file")
    try:
        authority = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError("data release receipt must be valid JSON") from exc
    _validate_data_release_receipt(
        authority,
        source_commit=source_commit,
        deployment_environment=deployment_environment,
    )
    return {
        "fileName": resolved.name,
        "sha256": _sha256_file(resolved),
        "bytes": resolved.stat().st_size,
        "authority": authority,
    }


def _validate_data_release_receipt(
    authority: Any,
    *,
    source_commit: str,
    deployment_environment: str,
) -> None:
    if not isinstance(authority, dict) or set(authority) != {
        "schemaVersion",
        "observedAt",
        "environment",
        "database",
        "position",
        "knowledge",
        "compatibleApplicationCommits",
    }:
        raise ReleaseManifestError("data release receipt authority is invalid")
    if authority.get("schemaVersion") != DATA_RELEASE_RECEIPT_VERSION:
        raise ReleaseManifestError("data release receipt schema is invalid")
    if authority.get("environment") != deployment_environment:
        raise ReleaseManifestError(
            "data release receipt environment does not match deployment"
        )
    try:
        observed_at = datetime.fromisoformat(authority["observedAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseManifestError("data release receipt timestamp is invalid") from exc
    if observed_at.tzinfo is None:
        raise ReleaseManifestError(
            "data release receipt timestamp must include a timezone"
        )
    database = authority.get("database")
    position = authority.get("position")
    knowledge = authority.get("knowledge")
    if (
        not isinstance(database, dict)
        or set(database)
        != {
            "databaseName",
            "clusterSystemIdentifierSha256",
            "alembicRevision",
        }
        or not isinstance(database.get("databaseName"), str)
        or not database["databaseName"]
        or not isinstance(database.get("alembicRevision"), str)
        or not database["alembicRevision"]
        or not isinstance(
            database.get("clusterSystemIdentifierSha256"),
            str,
        )
        or not HEX_SHA256.fullmatch(database["clusterSystemIdentifierSha256"])
    ):
        raise ReleaseManifestError("data release receipt database authority is invalid")
    if (
        not isinstance(position, dict)
        or set(position)
        != {
            "releaseId",
            "examCode",
            "examYear",
            "ruleSetVersion",
            "positionDataVersion",
            "sourceSnapshotSha256",
            "releaseContentSha256",
            "lifecycleState",
            "currentBusinessWindow",
        }
        or not isinstance(position.get("examCode"), str)
        or not position["examCode"]
        or not isinstance(position.get("examYear"), int)
        or not isinstance(position.get("ruleSetVersion"), str)
        or not position["ruleSetVersion"]
        or not isinstance(position.get("positionDataVersion"), str)
        or not position["positionDataVersion"]
    ):
        raise ReleaseManifestError("data release receipt position authority is invalid")
    lifecycle_state = position.get("lifecycleState")
    business_window = position.get("currentBusinessWindow")
    if lifecycle_state not in {"open", "closed"} or (lifecycle_state == "open") != (
        business_window is not None
    ):
        raise ReleaseManifestError("data release receipt position lifecycle is invalid")
    if business_window is None:
        return _validate_data_release_receipt_tail(
            authority,
            source_commit=source_commit,
            position=position,
            knowledge=knowledge,
            observed_at=observed_at,
        )
    if (
        not isinstance(business_window, dict)
        or set(business_window)
        != {
            "windowId",
            "kind",
            "sequenceNo",
            "opensAt",
            "closesAt",
            "officialSourceSha256",
        }
        or business_window.get("kind") not in {"primary", "supplemental"}
        or not isinstance(business_window.get("sequenceNo"), int)
        or isinstance(business_window.get("sequenceNo"), bool)
        or business_window["sequenceNo"] < 1
        or not isinstance(
            business_window.get("officialSourceSha256"),
            str,
        )
        or not HEX_SHA256.fullmatch(business_window["officialSourceSha256"])
    ):
        raise ReleaseManifestError(
            "data release receipt business window authority is invalid"
        )
    try:
        UUID(str(business_window["windowId"]))
        opens_at = datetime.fromisoformat(business_window["opensAt"])
        closes_at = datetime.fromisoformat(business_window["closesAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseManifestError(
            "data release receipt business window identity is invalid"
        ) from exc
    if (
        opens_at.tzinfo is None
        or closes_at.tzinfo is None
        or not opens_at <= observed_at < closes_at
    ):
        raise ReleaseManifestError(
            "data release receipt was not observed inside its business window"
        )
    _validate_data_release_receipt_tail(
        authority,
        source_commit=source_commit,
        position=position,
        knowledge=knowledge,
        observed_at=observed_at,
    )


def _validate_data_release_receipt_tail(
    authority: dict[str, Any],
    *,
    source_commit: str,
    position: dict[str, Any],
    knowledge: dict[str, Any],
    observed_at: datetime,
) -> None:
    if (
        not isinstance(knowledge, dict)
        or set(knowledge)
        != {
            "releaseId",
            "releaseVersion",
            "sourceDataVersion",
            "sourceFingerprint",
            "releaseContentSha256",
            "freshUntil",
        }
        or not isinstance(knowledge.get("releaseVersion"), str)
        or not knowledge["releaseVersion"]
        or not isinstance(knowledge.get("sourceDataVersion"), str)
        or not knowledge["sourceDataVersion"]
    ):
        raise ReleaseManifestError(
            "data release receipt knowledge authority is invalid"
        )
    try:
        fresh_until = datetime.fromisoformat(knowledge["freshUntil"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseManifestError(
            "data release receipt knowledge freshness is invalid"
        ) from exc
    if fresh_until.tzinfo is None or observed_at >= fresh_until:
        raise ReleaseManifestError("data release receipt knowledge authority is stale")
    for label, value in (
        ("position source snapshot", position.get("sourceSnapshotSha256")),
        ("position release content", position.get("releaseContentSha256")),
        ("knowledge source", knowledge.get("sourceFingerprint")),
        ("knowledge release content", knowledge.get("releaseContentSha256")),
    ):
        if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
            raise ReleaseManifestError(f"data release {label} digest is invalid")
    for label, value in (
        ("position", position.get("releaseId")),
        ("knowledge", knowledge.get("releaseId")),
    ):
        try:
            UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise ReleaseManifestError(f"data release {label} id is invalid") from exc
    compatible_commits = authority.get("compatibleApplicationCommits")
    if (
        not isinstance(compatible_commits, list)
        or len(compatible_commits) < 2
        or len(set(compatible_commits)) != len(compatible_commits)
        or not all(
            isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40,64}", commit)
            for commit in compatible_commits
        )
        or source_commit not in compatible_commits
    ):
        raise ReleaseManifestError(
            "data release receipt application compatibility is invalid"
        )


def _validate_wechat_receipt(
    authority: Any,
    *,
    upload_version: str,
    source_commit: str,
    source_git_tree: str,
    source_tree_sha256: str,
    project_config_sha256: str,
    expected_app_id: str,
    uploader_key_id: str,
    allow_test_tool_identity: bool = False,
) -> None:
    if not isinstance(authority, dict):
        raise ReleaseManifestError("WeChat upload receipt authority is invalid")
    if authority.get("schemaVersion") != WECHAT_RECEIPT_VERSION:
        raise ReleaseManifestError("WeChat upload receipt schema is invalid")
    try:
        receipt_id = UUID(str(authority.get("receiptId")))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReleaseManifestError("WeChat upload receipt id is invalid") from exc
    if str(receipt_id) != authority.get("receiptId"):
        raise ReleaseManifestError("WeChat upload receipt id is not canonical")
    if (
        authority.get("operation") != "devtools_upload"
        or authority.get("result") != "success"
    ):
        raise ReleaseManifestError(
            "WeChat upload receipt does not record a successful DevTools upload"
        )
    if authority.get("uploadVersion") != upload_version:
        raise ReleaseManifestError(
            "WeChat upload receipt version does not match the manifest"
        )
    description = authority.get("uploadDescription")
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > 200
        or any(ord(character) < 32 for character in description)
    ):
        raise ReleaseManifestError("WeChat upload receipt description is invalid")
    if authority.get("sourceGitCommit") != source_commit:
        raise ReleaseManifestError(
            "WeChat upload receipt commit does not match the manifest"
        )
    if authority.get("sourceGitTree") != source_git_tree:
        raise ReleaseManifestError(
            "WeChat upload receipt Git tree does not match the manifest"
        )
    if not re.fullmatch(
        r"[0-9a-f]{40,64}",
        str(authority.get("wechatTreeObjectId") or ""),
    ):
        raise ReleaseManifestError(
            "WeChat upload receipt project tree object is invalid"
        )
    if authority.get("sourceTreeListingSha256") != source_tree_sha256:
        raise ReleaseManifestError(
            "WeChat upload receipt source tree does not match the manifest"
        )
    if (
        authority.get("projectRelativePath") != "apps/wechat"
        or authority.get("projectConfigSha256") != project_config_sha256
        or authority.get("appId") != expected_app_id
        or authority.get("compileType") != "miniprogram"
        or authority.get("miniprogramRoot") != "miniprogram/"
    ):
        raise ReleaseManifestError(
            "WeChat upload receipt project identity does not match the artifact"
        )
    if authority.get("devtoolsCliExitCode") != 0:
        raise ReleaseManifestError(
            "WeChat upload receipt does not record a successful CLI exit"
        )
    for label, digest_key, bytes_key, maximum_bytes in (
        (
            "raw log",
            "rawCliLogSha256",
            "rawCliLogBytes",
            MAX_WECHAT_LOG_BYTES,
        ),
        (
            "info output",
            "infoOutputSha256",
            "infoOutputBytes",
            MAX_WECHAT_INFO_BYTES,
        ),
    ):
        digest = authority.get(digest_key)
        size = authority.get(bytes_key)
        if (
            not isinstance(digest, str)
            or not HEX_SHA256.fullmatch(digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size > maximum_bytes
        ):
            raise ReleaseManifestError(
                f"WeChat upload receipt {label} evidence is invalid"
            )
    _validate_wechat_info_output(authority.get("infoOutput"))
    parsed_times: list[datetime] = []
    for field in ("startedAt", "completedAt"):
        timestamp = authority.get(field)
        try:
            parsed = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError) as exc:
            raise ReleaseManifestError(
                f"WeChat upload receipt {field} timestamp is invalid"
            ) from exc
        if parsed.tzinfo is None:
            raise ReleaseManifestError(
                f"WeChat upload receipt {field} timestamp must include a timezone"
            )
        parsed_times.append(parsed)
    if parsed_times[1] < parsed_times[0]:
        raise ReleaseManifestError(
            "WeChat upload receipt completion precedes its start"
        )
    if authority.get("uploaderKeyId") != uploader_key_id:
        raise ReleaseManifestError(
            "WeChat upload receipt uploader key does not match the trusted key"
        )
    if authority.get("signatureAlgorithm") != "RSA-SHA256":
        raise ReleaseManifestError(
            "WeChat upload receipt signature algorithm is invalid"
        )
    tool = authority.get("tool")
    if not isinstance(tool, dict):
        raise ReleaseManifestError("WeChat upload receipt tool authority is invalid")
    identity_mode = tool.get("identityMode")
    if identity_mode not in {"macOS-codesign", "test-fixture"} or (
        identity_mode != "macOS-codesign" and not allow_test_tool_identity
    ):
        raise ReleaseManifestError(
            "WeChat deployable receipt lacks a production DevTools identity"
        )
    for field in (
        "bundleIdentifier",
        "shortVersion",
        "bundleVersion",
        "teamIdentifier",
    ):
        value = tool.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ReleaseManifestError(f"WeChat upload receipt tool {field} is invalid")
    for field in (
        "policySha256",
        "cliLauncherSha256",
        "cliJsSha256",
        "cliModuleSha256",
    ):
        value = tool.get(field)
        if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
            raise ReleaseManifestError(f"WeChat upload receipt tool {field} is invalid")


def _missing_external_evidence(
    *,
    api_contract_verified: bool,
    database_graph_verified: bool,
    wechat_upload_version: str,
    wechat_receipt: dict[str, Any] | None,
    data_release_receipt: dict[str, Any] | None,
    deployment_environment: str,
    rollback_release_id: str,
) -> list[str]:
    missing: list[str] = []
    if not api_contract_verified:
        missing.append("isolated_api_contract_check")
    if not database_graph_verified:
        missing.append("isolated_database_graph_check")
    if not wechat_upload_version:
        missing.append("wechat_upload_version")
    if wechat_receipt is None:
        missing.append("wechat_upload_receipt")
    if data_release_receipt is None:
        missing.append("data_release_receipt")
    if not deployment_environment:
        missing.append("deployment_environment")
    if not rollback_release_id:
        missing.append("rollback_release_id")
    return missing


def build_manifest(
    *,
    project_root: Path,
    artifact: Path,
    output: Path,
    release_id: str,
    mode: str,
    wechat_upload_version: str,
    wechat_upload_receipt_path: Path | None,
    wechat_upload_receipt_signature_path: Path | None,
    wechat_upload_info_output_path: Path | None,
    wechat_upload_raw_log_path: Path | None,
    wechat_uploader_trust_public_key_path: Path | None,
    data_release_receipt_path: Path | None,
    deployment_environment: str,
    rollback_release_id: str,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    artifact = artifact.resolve()
    output = output.resolve()
    if not RELEASE_ID.fullmatch(release_id):
        raise ReleaseManifestError("release id contains unsupported characters")
    if not artifact.is_file():
        raise ReleaseManifestError("release artifact does not exist")
    if mode not in {"candidate", "deployable"}:
        raise ReleaseManifestError("release manifest mode is invalid")
    commit, tree, branch = _require_clean_source(project_root)
    archive_roots, archive_commit = _assert_safe_archive(artifact)
    if archive_commit != commit:
        raise ReleaseManifestError(
            "release artifact Git commit does not match the clean source tree"
        )

    contract_path = project_root / DATABASE_CONTRACT_PATH
    source_database_head, source_compatible_revisions = _parse_database_contract(
        contract_path
    )
    (
        artifact_database_head,
        artifact_compatible_revisions,
        artifact_contract_sha256,
    ) = _artifact_database_contract(artifact)
    if (
        artifact_contract_sha256 != _sha256_file(contract_path)
        or artifact_database_head != source_database_head
        or artifact_compatible_revisions != source_compatible_revisions
    ):
        raise ReleaseManifestError(
            "release artifact database contract differs from the clean source"
        )
    if mode == "deployable":
        api_contract, verified_alembic_heads = _isolated_api_contract_authority(
            artifact,
            expected_database_head=artifact_database_head,
        )
        database_graph_check = "passed"
    else:
        api_contract = _unverified_api_contract_authority(artifact)
        verified_alembic_heads = None
        database_graph_check = "not_verified_isolated"
    project_config = _archive_json_object(
        artifact,
        WECHAT_PROJECT_CONFIG_PATH,
        label="WeChat project config",
    )
    app_id = project_config.get("appid")
    if (
        not isinstance(app_id, str)
        or re.fullmatch(r"wx[0-9A-Za-z]{16}", app_id) is None
        or project_config.get("compileType") != "miniprogram"
        or project_config.get("miniprogramRoot") != "miniprogram/"
    ):
        raise ReleaseManifestError(
            "release artifact WeChat project identity is invalid"
        )
    project_config_sha256 = _archive_member_sha256(
        artifact,
        WECHAT_PROJECT_CONFIG_PATH,
    )
    upload_version = wechat_upload_version.strip()
    supplied_wechat_evidence = (
        wechat_upload_receipt_path,
        wechat_upload_receipt_signature_path,
        wechat_upload_info_output_path,
        wechat_upload_raw_log_path,
        wechat_uploader_trust_public_key_path,
    )
    if bool(upload_version) != all(
        path is not None for path in supplied_wechat_evidence
    ) or (
        any(path is not None for path in supplied_wechat_evidence)
        and not all(path is not None for path in supplied_wechat_evidence)
    ):
        raise ReleaseManifestError(
            "WeChat upload version, receipt, signature, info output, raw log, "
            "and trusted uploader public key must be supplied together"
        )
    wechat_tree_sha256 = _wechat_tree_listing_sha256(project_root, commit)
    receipt = _receipt(
        wechat_upload_receipt_path,
        wechat_upload_receipt_signature_path,
        wechat_upload_info_output_path,
        wechat_upload_raw_log_path,
        wechat_uploader_trust_public_key_path,
        upload_version=upload_version,
        source_commit=commit,
        source_git_tree=tree,
        source_tree_sha256=wechat_tree_sha256,
        project_config_sha256=project_config_sha256,
        expected_app_id=app_id,
    )
    data_receipt = _data_release_receipt(
        data_release_receipt_path,
        source_commit=commit,
        deployment_environment=deployment_environment.strip(),
    )
    missing = _missing_external_evidence(
        api_contract_verified=(api_contract["generatedTypesCheck"] == "passed"),
        database_graph_verified=(database_graph_check == "passed"),
        wechat_upload_version=upload_version,
        wechat_receipt=receipt,
        data_release_receipt=data_receipt,
        deployment_environment=deployment_environment.strip(),
        rollback_release_id=rollback_release_id.strip(),
    )
    if mode == "deployable" and missing:
        raise ReleaseManifestError(
            f"deployable manifest is missing external evidence: {missing}"
        )
    environment = deployment_environment.strip()
    rollback_id = rollback_release_id.strip()
    if environment and not ENVIRONMENT_ID.fullmatch(environment):
        raise ReleaseManifestError("deployment environment id is invalid")
    if rollback_id and not RELEASE_ID.fullmatch(rollback_id):
        raise ReleaseManifestError("rollback release id is invalid")
    if rollback_id == release_id:
        raise ReleaseManifestError("rollback release must differ from the candidate")
    runtime_dependencies = _runtime_dependency_authority(project_root)
    guard_capability = _guard_capability_authority(project_root, artifact)
    if (
        _archive_member_sha256(artifact, runtime_dependencies["lockPath"])
        != runtime_dependencies["lockSha256"]
        or _archive_member_sha256(
            artifact,
            runtime_dependencies["pyprojectPath"],
        )
        != runtime_dependencies["pyprojectSha256"]
    ):
        raise ReleaseManifestError(
            "release artifact runtime dependency authority differs from source"
        )

    payload: dict[str, Any] = {
        "schemaVersion": FORMAT_VERSION,
        "releaseId": release_id,
        "status": "deployable_candidate" if not missing else "candidate_incomplete",
        "builtAt": datetime.now(UTC).isoformat(),
        "source": {
            "gitCommit": commit,
            "gitTree": tree,
            "branch": branch,
            "dirty": False,
        },
        "backendArtifact": {
            "fileName": artifact.name,
            "sha256": _sha256_file(artifact),
            "bytes": artifact.stat().st_size,
            "archiveRoots": list(archive_roots),
        },
        "database": {
            "alembicHead": artifact_database_head,
            "compatibleRevisions": list(artifact_compatible_revisions),
            "contractPath": DATABASE_CONTRACT_PATH,
            "contractSha256": artifact_contract_sha256,
            "alembicGraphCheck": database_graph_check,
            "verifiedHeads": (
                list(verified_alembic_heads)
                if verified_alembic_heads is not None
                else None
            ),
        },
        "runtimeDependencies": runtime_dependencies,
        "guardCapabilities": {
            "commercialDataPublication": guard_capability,
        },
        "apiContract": api_contract,
        "wechat": {
            "sourceTreeListingSha256": wechat_tree_sha256,
            "projectConfigPath": WECHAT_PROJECT_CONFIG_PATH,
            "projectConfigSha256": project_config_sha256,
            "appId": app_id,
            "uploadVersion": upload_version or None,
            "uploadReceipt": receipt,
        },
        "dataAuthorities": {
            "receipt": data_receipt,
        },
        "deployment": {
            "environment": environment or None,
            "rollbackReleaseId": rollback_id or None,
        },
        "missingExternalEvidence": missing,
    }
    if _require_clean_source(project_root) != (commit, tree, branch):
        raise ReleaseManifestError("release source changed while building the manifest")
    _write_json(output, payload)
    _write_checksum(output)
    return payload


def verify_manifest(
    *,
    manifest_path: Path,
    artifact_path: Path,
    expected_commit: str | None,
    require_deployable: bool,
    expected_release_id: str | None = None,
    expected_environment: str | None = None,
    expected_rollback_release_id: str | None = None,
    expected_compatible_commits: tuple[str, ...] = (),
    wechat_upload_receipt_path: Path | None = None,
    wechat_upload_receipt_signature_path: Path | None = None,
    wechat_upload_info_output_path: Path | None = None,
    wechat_upload_raw_log_path: Path | None = None,
    wechat_uploader_trust_public_key_path: Path | None = None,
    data_release_receipt_path: Path | None = None,
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError("release manifest is not valid JSON") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != FORMAT_VERSION
    ):
        raise ReleaseManifestError("release manifest schema version is invalid")
    release_id = manifest.get("releaseId")
    if not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id):
        raise ReleaseManifestError("release manifest id is invalid")
    source = manifest.get("source")
    artifact = manifest.get("backendArtifact")
    database = manifest.get("database")
    runtime_dependencies = manifest.get("runtimeDependencies")
    guard_capabilities = manifest.get("guardCapabilities")
    api = manifest.get("apiContract")
    wechat = manifest.get("wechat")
    data_authorities = manifest.get("dataAuthorities")
    deployment = manifest.get("deployment")
    if not all(
        isinstance(value, dict)
        for value in (
            source,
            artifact,
            database,
            runtime_dependencies,
            guard_capabilities,
            api,
            wechat,
            data_authorities,
            deployment,
        )
    ):
        raise ReleaseManifestError("release manifest authority sections are missing")
    if source.get("dirty") is not False:
        raise ReleaseManifestError("release manifest source must be clean")
    if set(guard_capabilities) != {"commercialDataPublication"}:
        raise ReleaseManifestError("release guard capability authority is invalid")
    _verify_guard_capability(
        guard_capabilities.get("commercialDataPublication"),
        artifact_path,
    )
    commit = source.get("gitCommit")
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", commit)
        or (expected_commit is not None and commit != expected_commit)
    ):
        raise ReleaseManifestError("release manifest commit does not match")
    tree = source.get("gitTree")
    if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        raise ReleaseManifestError("release manifest Git tree is invalid")
    if not isinstance(source.get("branch"), str) or not source["branch"].strip():
        raise ReleaseManifestError("release manifest branch is invalid")
    for label, value in (
        ("backend artifact", artifact.get("sha256")),
        ("database contract", database.get("contractSha256")),
        ("OpenAPI", api.get("openapiSha256")),
        ("generated types", api.get("generatedTypesSha256")),
        ("WeChat tree", wechat.get("sourceTreeListingSha256")),
    ):
        if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
            raise ReleaseManifestError(f"{label} SHA-256 is invalid")
    if artifact.get("fileName") != artifact_path.name:
        raise ReleaseManifestError("release artifact filename does not match manifest")
    if _sha256_file(artifact_path) != artifact.get("sha256"):
        raise ReleaseManifestError("release artifact digest does not match manifest")
    if artifact_path.stat().st_size != artifact.get("bytes"):
        raise ReleaseManifestError("release artifact size does not match manifest")
    archive_roots, archive_commit = _assert_safe_archive(artifact_path)
    if archive_commit != commit:
        raise ReleaseManifestError(
            "release artifact Git commit does not match manifest source"
        )
    if artifact.get("archiveRoots") != list(archive_roots):
        raise ReleaseManifestError("release artifact roots do not match manifest")
    if api.get("openapiSha256") != _archive_member_sha256(
        artifact_path, CANONICAL_OPENAPI_PATH
    ) or api.get("generatedTypesSha256") != _archive_member_sha256(
        artifact_path, GENERATED_TYPES_PATH
    ):
        raise ReleaseManifestError(
            "release API contract authority does not match artifact members"
        )
    project_config_path = wechat.get("projectConfigPath")
    if project_config_path != WECHAT_PROJECT_CONFIG_PATH:
        raise ReleaseManifestError("release WeChat project config path is invalid")
    project_config_sha256 = _archive_member_sha256(
        artifact_path,
        WECHAT_PROJECT_CONFIG_PATH,
    )
    project_config = _archive_json_object(
        artifact_path,
        WECHAT_PROJECT_CONFIG_PATH,
        label="WeChat project config",
    )
    app_id = project_config.get("appid")
    if (
        wechat.get("projectConfigSha256") != project_config_sha256
        or wechat.get("appId") != app_id
        or not isinstance(app_id, str)
        or re.fullmatch(r"wx[0-9A-Za-z]{16}", app_id) is None
        or project_config.get("compileType") != "miniprogram"
        or project_config.get("miniprogramRoot") != "miniprogram/"
    ):
        raise ReleaseManifestError(
            "release WeChat project identity does not match the artifact"
        )
    if (
        runtime_dependencies.get("resolver") != "uv"
        or runtime_dependencies.get("installMode") != "sync-frozen-no-dev-no-editable"
        or runtime_dependencies.get("lockPath") != "backend/uv.lock"
        or runtime_dependencies.get("pyprojectPath") != "backend/pyproject.toml"
        or not isinstance(runtime_dependencies.get("lockFormatVersion"), int)
        or not isinstance(runtime_dependencies.get("lockRevision"), int)
        or not isinstance(runtime_dependencies.get("requiresPython"), str)
        or not runtime_dependencies["requiresPython"]
    ):
        raise ReleaseManifestError("release runtime dependency authority is invalid")
    for label, path_key, digest_key in (
        ("uv lock", "lockPath", "lockSha256"),
        ("backend pyproject", "pyprojectPath", "pyprojectSha256"),
    ):
        digest = runtime_dependencies.get(digest_key)
        if (
            not isinstance(digest, str)
            or not HEX_SHA256.fullmatch(digest)
            or _archive_member_sha256(
                artifact_path,
                str(runtime_dependencies[path_key]),
            )
            != digest
        ):
            raise ReleaseManifestError(
                f"release {label} authority does not match the artifact"
            )
    database_head = database.get("alembicHead")
    compatible_revisions = database.get("compatibleRevisions")
    contract_path = database.get("contractPath")
    contract_sha256 = database.get("contractSha256")
    if (
        contract_path != DATABASE_CONTRACT_PATH
        or not isinstance(contract_sha256, str)
        or _archive_member_sha256(artifact_path, DATABASE_CONTRACT_PATH)
        != contract_sha256
    ):
        raise ReleaseManifestError(
            "release database contract authority does not match the artifact"
        )
    (
        artifact_database_head,
        artifact_compatible_revisions,
        _,
    ) = _artifact_database_contract(artifact_path)
    if (
        not isinstance(database_head, str)
        or not database_head
        or not isinstance(compatible_revisions, list)
        or database_head not in compatible_revisions
        or not all(
            isinstance(revision, str) and revision for revision in compatible_revisions
        )
        or database_head != artifact_database_head
        or compatible_revisions != list(artifact_compatible_revisions)
    ):
        raise ReleaseManifestError(
            "release database authority does not match the artifact contract"
        )
    database_graph_check = database.get("alembicGraphCheck")
    verified_heads = database.get("verifiedHeads")
    if database_graph_check == "passed":
        if verified_heads != [database_head]:
            raise ReleaseManifestError(
                "release database graph authority does not match its contract"
            )
    elif database_graph_check == "not_verified_isolated":
        if verified_heads is not None:
            raise ReleaseManifestError(
                "unverified database graph cannot claim verified heads"
            )
    else:
        raise ReleaseManifestError("release database graph authority is invalid")
    generated_types_check = api.get("generatedTypesCheck")
    if generated_types_check not in {"passed", "not_verified_isolated"}:
        raise ReleaseManifestError(
            "generated TypeScript contract check authority is invalid"
        )
    if generated_types_check == "passed":
        toolchain = api.get("toolchain")
        if (
            api.get("verificationEnvironment") != "isolated-artifact-lockfiles"
            or not isinstance(toolchain, dict)
            or set(toolchain) != {"uv", "node", "npm"}
            or any(
                not isinstance(tool, dict)
                or not isinstance(tool.get("version"), str)
                or not tool["version"]
                or not isinstance(tool.get("executableSha256"), str)
                or not HEX_SHA256.fullmatch(tool["executableSha256"])
                for tool in toolchain.values()
            )
        ):
            raise ReleaseManifestError(
                "isolated API contract toolchain authority is invalid"
            )
    elif (
        api.get("verificationEnvironment") is not None
        or api.get("toolchain") is not None
    ):
        raise ReleaseManifestError(
            "unverified API contract cannot claim a verification toolchain"
        )
    missing = manifest.get("missingExternalEvidence")
    if not isinstance(missing, list) or not all(
        isinstance(value, str) and value for value in missing
    ):
        raise ReleaseManifestError("missingExternalEvidence is invalid")
    environment = deployment.get("environment")
    rollback_release_id = deployment.get("rollbackReleaseId")
    if environment is not None and (
        not isinstance(environment, str) or not ENVIRONMENT_ID.fullmatch(environment)
    ):
        raise ReleaseManifestError("release deployment environment id is invalid")
    if rollback_release_id is not None and (
        not isinstance(rollback_release_id, str)
        or not RELEASE_ID.fullmatch(rollback_release_id)
        or rollback_release_id == release_id
    ):
        raise ReleaseManifestError("release rollback identity is invalid")
    expected_missing = _missing_external_evidence(
        api_contract_verified=generated_types_check == "passed",
        database_graph_verified=database_graph_check == "passed",
        wechat_upload_version=str(wechat.get("uploadVersion") or ""),
        wechat_receipt=(
            wechat.get("uploadReceipt")
            if isinstance(wechat.get("uploadReceipt"), dict)
            else None
        ),
        data_release_receipt=(
            data_authorities.get("receipt")
            if isinstance(data_authorities.get("receipt"), dict)
            else None
        ),
        deployment_environment=str(environment or ""),
        rollback_release_id=str(rollback_release_id or ""),
    )
    if missing != expected_missing or manifest.get("status") != (
        "deployable_candidate" if not expected_missing else "candidate_incomplete"
    ):
        raise ReleaseManifestError(
            "release manifest status disagrees with its external evidence"
        )
    if require_deployable and (
        manifest.get("status") != "deployable_candidate"
        or missing
        or generated_types_check != "passed"
        or database_graph_check != "passed"
        or not wechat.get("uploadVersion")
        or not isinstance(wechat.get("uploadReceipt"), dict)
        or not isinstance(data_authorities.get("receipt"), dict)
        or not deployment.get("environment")
        or not deployment.get("rollbackReleaseId")
    ):
        raise ReleaseManifestError(
            "release manifest lacks WeChat, deployment, or rollback evidence"
        )
    if require_deployable and (
        expected_release_id is None
        or expected_environment is None
        or expected_rollback_release_id is None
    ):
        raise ReleaseManifestError(
            "deployable verification requires expected release, environment, "
            "and rollback identities"
        )
    if expected_release_id is not None and release_id != expected_release_id:
        raise ReleaseManifestError(
            "release manifest id does not match the activation target"
        )
    if expected_environment is not None and environment != expected_environment:
        raise ReleaseManifestError(
            "release manifest environment does not match the trusted target"
        )
    if (
        expected_rollback_release_id is not None
        and rollback_release_id != expected_rollback_release_id
    ):
        raise ReleaseManifestError(
            "release manifest rollback id does not match the current release"
        )
    data_receipt = data_authorities.get("receipt")
    if isinstance(data_receipt, dict):
        data_receipt_sha256 = data_receipt.get("sha256")
        if (
            not isinstance(data_receipt.get("fileName"), str)
            or not data_receipt["fileName"]
            or not isinstance(data_receipt_sha256, str)
            or not HEX_SHA256.fullmatch(data_receipt_sha256)
            or not isinstance(data_receipt.get("bytes"), int)
            or data_receipt["bytes"] <= 0
        ):
            raise ReleaseManifestError("data release receipt file evidence is invalid")
        data_receipt_authority = data_receipt.get("authority")
        _validate_data_release_receipt(
            data_receipt_authority,
            source_commit=commit,
            deployment_environment=str(environment or ""),
        )
        compatible_commits = data_receipt_authority["compatibleApplicationCommits"]
        if any(
            expected not in compatible_commits
            for expected in expected_compatible_commits
        ):
            raise ReleaseManifestError(
                "data release is incompatible with an expected application commit"
            )
        if data_release_receipt_path is not None:
            actual_data_receipt = data_release_receipt_path.resolve()
            if (
                not actual_data_receipt.is_file()
                or _sha256_file(actual_data_receipt) != data_receipt["sha256"]
                or actual_data_receipt.stat().st_size != data_receipt["bytes"]
            ):
                raise ReleaseManifestError(
                    "data release receipt file does not match manifest evidence"
                )
            try:
                actual_data_authority = json.loads(
                    actual_data_receipt.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ReleaseManifestError(
                    "data release receipt file is invalid"
                ) from exc
            if actual_data_authority != data_receipt_authority:
                raise ReleaseManifestError(
                    "data release authority does not match the detached file"
                )
    wechat_receipt = wechat.get("uploadReceipt")
    detached_wechat_evidence = (
        wechat_upload_receipt_path,
        wechat_upload_receipt_signature_path,
        wechat_upload_info_output_path,
        wechat_upload_raw_log_path,
        wechat_uploader_trust_public_key_path,
    )
    if isinstance(wechat_receipt, dict):
        if not all(path is not None for path in detached_wechat_evidence):
            raise ReleaseManifestError(
                "WeChat upload receipt requires detached signature, info output, "
                "raw log, and trusted uploader public key verification"
            )
        assert wechat_upload_receipt_path is not None
        assert wechat_upload_receipt_signature_path is not None
        assert wechat_upload_info_output_path is not None
        assert wechat_upload_raw_log_path is not None
        assert wechat_uploader_trust_public_key_path is not None
        receipt = wechat_receipt
        receipt_sha256 = receipt.get("sha256")
        if (
            not isinstance(receipt.get("fileName"), str)
            or not receipt["fileName"]
            or not isinstance(receipt_sha256, str)
            or not HEX_SHA256.fullmatch(receipt_sha256)
            or not isinstance(receipt.get("bytes"), int)
            or receipt["bytes"] <= 0
        ):
            raise ReleaseManifestError("WeChat upload receipt file evidence is invalid")
        actual_receipt = wechat_upload_receipt_path.resolve()
        actual_signature = wechat_upload_receipt_signature_path.resolve()
        actual_info = wechat_upload_info_output_path.resolve()
        actual_raw_log = wechat_upload_raw_log_path.resolve()
        if (
            not actual_receipt.is_file()
            or _sha256_file(actual_receipt) != receipt["sha256"]
            or actual_receipt.stat().st_size != receipt["bytes"]
        ):
            raise ReleaseManifestError(
                "WeChat upload receipt file does not match manifest evidence"
            )
        uploader_key_id = _verify_detached_signature(
            payload=actual_receipt,
            signature=actual_signature,
            public_key=wechat_uploader_trust_public_key_path,
        )
        actual_authority = _json_object_without_duplicates(
            _read_bounded(
                actual_receipt,
                label="WeChat upload receipt",
                maximum_bytes=MAX_WECHAT_RECEIPT_BYTES,
            ),
            label="WeChat upload receipt",
        )
        receipt_authority = receipt.get("authority")
        if actual_authority != receipt_authority:
            raise ReleaseManifestError(
                "WeChat upload receipt authority does not match the detached file"
            )
        _validate_wechat_receipt(
            receipt_authority,
            upload_version=str(wechat.get("uploadVersion") or ""),
            source_commit=commit,
            source_git_tree=tree,
            source_tree_sha256=str(wechat.get("sourceTreeListingSha256") or ""),
            project_config_sha256=project_config_sha256,
            expected_app_id=str(app_id),
            uploader_key_id=uploader_key_id,
        )
        signature = receipt.get("signature")
        if (
            not isinstance(signature, dict)
            or not isinstance(signature.get("fileName"), str)
            or not signature["fileName"]
            or not isinstance(signature.get("sha256"), str)
            or not HEX_SHA256.fullmatch(signature["sha256"])
            or isinstance(signature.get("bytes"), bool)
            or not isinstance(signature.get("bytes"), int)
            or signature["bytes"] <= 0
            or signature["bytes"] > MAX_WECHAT_SIGNATURE_BYTES
            or signature.get("algorithm") != "RSA-SHA256"
            or signature.get("uploaderKeyId") != uploader_key_id
            or _sha256_file(actual_signature) != signature["sha256"]
            or actual_signature.stat().st_size != signature["bytes"]
        ):
            raise ReleaseManifestError(
                "WeChat upload receipt signature evidence is invalid"
            )
        info_output = receipt.get("infoOutput")
        actual_info_authority = _json_object_without_duplicates(
            _read_bounded(
                actual_info,
                label="WeChat CLI info output",
                maximum_bytes=MAX_WECHAT_INFO_BYTES,
            ),
            label="WeChat CLI info output",
        )
        _validate_wechat_info_output(actual_info_authority)
        if (
            not isinstance(info_output, dict)
            or not isinstance(info_output.get("fileName"), str)
            or not info_output["fileName"]
            or not isinstance(info_output.get("sha256"), str)
            or not HEX_SHA256.fullmatch(info_output["sha256"])
            or isinstance(info_output.get("bytes"), bool)
            or not isinstance(info_output.get("bytes"), int)
            or info_output["bytes"] <= 0
            or info_output["bytes"] > MAX_WECHAT_INFO_BYTES
            or _sha256_file(actual_info) != info_output["sha256"]
            or actual_info.stat().st_size != info_output["bytes"]
            or info_output.get("authority") != actual_info_authority
            or receipt_authority.get("infoOutput") != actual_info_authority
            or receipt_authority.get("infoOutputSha256") != info_output["sha256"]
            or receipt_authority.get("infoOutputBytes") != info_output["bytes"]
        ):
            raise ReleaseManifestError(
                "WeChat upload receipt info output evidence is invalid"
            )
        raw_log = receipt.get("rawCliLog")
        if (
            not isinstance(raw_log, dict)
            or not isinstance(raw_log.get("fileName"), str)
            or not raw_log["fileName"]
            or not isinstance(raw_log.get("sha256"), str)
            or not HEX_SHA256.fullmatch(raw_log["sha256"])
            or not isinstance(raw_log.get("bytes"), int)
            or raw_log["bytes"] <= 0
            or raw_log["bytes"] > MAX_WECHAT_LOG_BYTES
            or raw_log["sha256"] != receipt_authority.get("rawCliLogSha256")
            or raw_log["bytes"] != receipt_authority.get("rawCliLogBytes")
            or not actual_raw_log.is_file()
            or _sha256_file(actual_raw_log) != raw_log["sha256"]
            or actual_raw_log.stat().st_size != raw_log["bytes"]
        ):
            raise ReleaseManifestError(
                "WeChat upload receipt raw CLI log evidence is invalid"
            )
    elif wechat_receipt is not None:
        raise ReleaseManifestError("WeChat upload receipt authority is invalid")
    elif wechat.get("uploadVersion") is not None or any(
        path is not None for path in detached_wechat_evidence
    ):
        raise ReleaseManifestError("partial WeChat upload evidence is forbidden")
    if require_deployable and (
        wechat_upload_receipt_path is None
        or wechat_upload_receipt_signature_path is None
        or wechat_upload_info_output_path is None
        or wechat_upload_raw_log_path is None
        or wechat_uploader_trust_public_key_path is None
        or data_release_receipt_path is None
        or not expected_compatible_commits
    ):
        raise ReleaseManifestError(
            "deployable verification requires detached WeChat/data receipts "
            "and expected application compatibility"
        )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--project-root", type=Path, required=True)
    build.add_argument("--artifact", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--release-id", required=True)
    build.add_argument(
        "--mode", choices=("candidate", "deployable"), default="candidate"
    )
    build.add_argument("--wechat-upload-version", default="")
    build.add_argument("--wechat-upload-receipt", type=Path)
    build.add_argument("--wechat-upload-receipt-signature", type=Path)
    build.add_argument("--wechat-upload-info-output", type=Path)
    build.add_argument("--wechat-upload-raw-log", type=Path)
    build.add_argument("--wechat-uploader-trust-public-key", type=Path)
    build.add_argument("--data-release-receipt", type=Path)
    build.add_argument("--deployment-environment", default="")
    build.add_argument("--rollback-release-id", default="")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--expected-commit")
    verify.add_argument("--expected-release-id")
    verify.add_argument("--expected-environment")
    verify.add_argument("--expected-rollback-release-id")
    verify.add_argument(
        "--expected-compatible-commit",
        action="append",
        default=[],
    )
    verify.add_argument("--require-deployable", action="store_true")
    verify.add_argument("--wechat-upload-receipt", type=Path)
    verify.add_argument("--wechat-upload-receipt-signature", type=Path)
    verify.add_argument("--wechat-upload-info-output", type=Path)
    verify.add_argument("--wechat-upload-raw-log", type=Path)
    verify.add_argument("--wechat-uploader-trust-public-key", type=Path)
    verify.add_argument("--data-release-receipt", type=Path)

    signing_key = subparsers.add_parser("validate-signing-key")
    signing_key.add_argument("--path", type=Path, required=True)
    publish_set = subparsers.add_parser("publish-release-set")
    publish_set.add_argument("--staging-directory", type=Path, required=True)
    publish_set.add_argument("--destination-directory", type=Path, required=True)
    publish_set.add_argument("--release-id", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "build":
            payload = build_manifest(
                project_root=arguments.project_root,
                artifact=arguments.artifact,
                output=arguments.output,
                release_id=arguments.release_id,
                mode=arguments.mode,
                wechat_upload_version=arguments.wechat_upload_version,
                wechat_upload_receipt_path=arguments.wechat_upload_receipt,
                wechat_upload_receipt_signature_path=(
                    arguments.wechat_upload_receipt_signature
                ),
                wechat_upload_info_output_path=(arguments.wechat_upload_info_output),
                wechat_upload_raw_log_path=arguments.wechat_upload_raw_log,
                wechat_uploader_trust_public_key_path=(
                    arguments.wechat_uploader_trust_public_key
                ),
                data_release_receipt_path=arguments.data_release_receipt,
                deployment_environment=arguments.deployment_environment,
                rollback_release_id=arguments.rollback_release_id,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        elif arguments.command == "verify":
            payload = verify_manifest(
                manifest_path=arguments.manifest,
                artifact_path=arguments.artifact,
                expected_commit=arguments.expected_commit,
                require_deployable=arguments.require_deployable,
                expected_release_id=arguments.expected_release_id,
                expected_environment=arguments.expected_environment,
                expected_rollback_release_id=(arguments.expected_rollback_release_id),
                expected_compatible_commits=tuple(arguments.expected_compatible_commit),
                wechat_upload_receipt_path=arguments.wechat_upload_receipt,
                wechat_upload_receipt_signature_path=(
                    arguments.wechat_upload_receipt_signature
                ),
                wechat_upload_info_output_path=(arguments.wechat_upload_info_output),
                wechat_upload_raw_log_path=arguments.wechat_upload_raw_log,
                wechat_uploader_trust_public_key_path=(
                    arguments.wechat_uploader_trust_public_key
                ),
                data_release_receipt_path=arguments.data_release_receipt,
            )
            print(
                json.dumps(
                    {
                        "releaseId": payload["releaseId"],
                        "manifestSha256": _sha256_file(arguments.manifest),
                        "status": payload["status"],
                    },
                    sort_keys=True,
                )
            )
        elif arguments.command == "publish-release-set":
            print(
                publish_release_set(
                    staging_directory=arguments.staging_directory,
                    destination_directory=arguments.destination_directory,
                    release_id=arguments.release_id,
                )
            )
        else:
            validate_signing_private_key(arguments.path)
            print(
                json.dumps(
                    {
                        "path": str(arguments.path.resolve()),
                        "status": "valid",
                    },
                    sort_keys=True,
                )
            )
    except ReleaseManifestError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
