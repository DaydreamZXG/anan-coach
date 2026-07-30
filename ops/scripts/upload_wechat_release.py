#!/usr/bin/env python3
"""Upload one immutable WeChat source tree and issue a signed receipt.

The uploader deliberately operates on a private ``git archive`` extraction,
not on the caller's working tree.  A receipt is published only after the
official CLI reports success, emits structurally valid package-size JSON, and
the source and pinned tool identities are revalidated.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO
from uuid import uuid4


RECEIPT_SCHEMA = "anan-wechat-upload-receipt/v2"
TOOL_POLICY_SCHEMA = "anan-wechat-devtools-policy/v1"
SIGNATURE_ALGORITHM = "RSA-SHA256"
PROJECT_RELATIVE_PATH = "apps/wechat"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
APP_ID = re.compile(r"^wx[0-9A-Za-z]{16}$")
MAX_POLICY_BYTES = 256 * 1024
MAX_PROJECT_CONFIG_BYTES = 4 * 1024 * 1024
MAX_INFO_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
CLI_TIMEOUT_SECONDS = 20 * 60
TOOL_FILES = {
    "cli": "Contents/MacOS/cli",
    "cliJs": "Contents/MacOS/cli.js",
    "cliModule": "Contents/Resources/app.asar.unpacked/js/common/cli/index.js",
}


class UploadError(RuntimeError):
    """Raised when an upload cannot produce trustworthy evidence."""


def _sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_without_duplicates(encoded: bytes, *, label: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise UploadError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise UploadError(f"{label} contains non-finite JSON value {value}")

    try:
        return json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UploadError(f"{label} must be valid UTF-8 JSON") from exc


def _secure_read(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    allowed_owners: set[int],
    exact_mode: int | None = None,
    forbid_writable_by_group_or_world: bool = False,
) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise UploadError(f"{label} is unreadable") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in allowed_owners
        or metadata.st_size <= 0
        or metadata.st_size > maximum_bytes
        or (exact_mode is not None and mode != exact_mode)
        or (forbid_writable_by_group_or_world and mode & 0o022)
    ):
        raise UploadError(f"{label} has unsafe ownership, mode, type, or size")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UploadError(f"{label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_uid not in allowed_owners
        ):
            raise UploadError(f"{label} changed while it was being opened")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_bytes + 1)
        if not payload or len(payload) > maximum_bytes:
            raise UploadError(f"{label} has an invalid size")
        after = os.fstat(descriptor)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise UploadError(f"{label} changed while it was being read")
        return payload
    finally:
        os.close(descriptor)


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    check: bool = True,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=text,
            shell=False,
        )
    except OSError as exc:
        raise UploadError(f"cannot execute {arguments[0]}") from exc
    if check and completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise UploadError(
            f"command failed ({completed.returncode}): {arguments[0]} "
            f"{str(stderr or '').strip()}"
        )
    return completed


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
    )
    return environment


def _git(project_root: Path, *arguments: str, binary: bool = False) -> bytes | str:
    completed = _run(
        ["git", *arguments],
        cwd=project_root,
        environment=_git_environment(),
        text=not binary,
    )
    if binary:
        assert isinstance(completed.stdout, bytes)
        return completed.stdout
    assert isinstance(completed.stdout, str)
    return completed.stdout.strip()


def _require_immutable_source(
    project_root: Path,
    *,
    expected_commit: str,
) -> dict[str, str]:
    if not GIT_OBJECT_ID.fullmatch(expected_commit):
        raise UploadError("expected Git commit must be a full lowercase object id")
    top_level = Path(str(_git(project_root, "rev-parse", "--show-toplevel"))).resolve()
    if top_level != project_root:
        raise UploadError("project root must be the Git repository top level")
    head = str(_git(project_root, "rev-parse", "HEAD"))
    if head != expected_commit:
        raise UploadError("clean Git HEAD does not match the expected commit")
    status = str(
        _git(
            project_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    )
    if status:
        raise UploadError("project root must have a clean Git working tree")
    commit_type = str(_git(project_root, "cat-file", "-t", expected_commit))
    if commit_type != "commit":
        raise UploadError("expected Git object is not a commit")
    source_tree = str(_git(project_root, "rev-parse", f"{expected_commit}^{{tree}}"))
    wechat_tree = str(
        _git(
            project_root,
            "rev-parse",
            f"{expected_commit}:{PROJECT_RELATIVE_PATH}",
        )
    )
    if str(_git(project_root, "cat-file", "-t", wechat_tree)) != "tree":
        raise UploadError("commit does not contain a WeChat source tree")
    listing = _git(
        project_root,
        "ls-tree",
        "-r",
        "-z",
        expected_commit,
        "--",
        PROJECT_RELATIVE_PATH,
        binary=True,
    )
    assert isinstance(listing, bytes)
    if not listing:
        raise UploadError("commit contains no WeChat source files")
    return {
        "sourceGitCommit": head,
        "sourceGitTree": source_tree,
        "wechatTreeObjectId": wechat_tree,
        "sourceTreeListingSha256": _sha256_bytes(listing),
    }


def _safe_member_path(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise UploadError("Git archive contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UploadError("Git archive contains an unsafe path")
    if path.parts != ("apps",) and path.parts[:2] != ("apps", "wechat"):
        raise UploadError("Git archive contains a path outside apps/wechat")
    return path


def _copy_archive_member(
    source: BinaryIO,
    destination: Path,
    *,
    executable: bool,
) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o700 if executable else 0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def _extract_wechat_archive(archive_path: Path, destination: Path) -> Path:
    seen: set[PurePosixPath] = set()
    total_bytes = 0
    try:
        archive = tarfile.open(archive_path, "r:")
    except (OSError, tarfile.TarError) as exc:
        raise UploadError("Git archive is unreadable") from exc
    with archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise UploadError("Git archive has an invalid member count")
        for member in members:
            relative = _safe_member_path(member.name.rstrip("/"))
            if relative.parts == ("apps",) and not member.isdir():
                raise UploadError("Git archive contains an unsafe apps entry")
            if relative in seen:
                raise UploadError("Git archive contains duplicate paths")
            seen.add(relative)
            output = destination.joinpath(*relative.parts)
            if member.isdir():
                output.mkdir(mode=0o700, parents=True, exist_ok=False)
                continue
            if not member.isfile() or member.size < 0:
                raise UploadError(
                    "Git archive may contain only regular files and directories"
                )
            total_bytes += member.size
            if total_bytes > MAX_ARCHIVE_BYTES:
                raise UploadError("Git archive is too large")
            source = archive.extractfile(member)
            if source is None:
                raise UploadError("Git archive member cannot be read")
            with source:
                _copy_archive_member(
                    source,
                    output,
                    executable=bool(member.mode & 0o111),
                )
    project = destination / PROJECT_RELATIVE_PATH
    if not project.is_dir():
        raise UploadError("Git archive does not contain apps/wechat")
    return project


def _archive_source(
    project_root: Path,
    *,
    commit: str,
    temporary_root: Path,
) -> Path:
    archive_path = temporary_root / "wechat-source.tar"
    completed = _run(
        [
            "git",
            "archive",
            "--format=tar",
            "--output",
            str(archive_path),
            commit,
            "--",
            PROJECT_RELATIVE_PATH,
        ],
        cwd=project_root,
        environment=_git_environment(),
        text=True,
    )
    if completed.returncode != 0 or not archive_path.is_file():
        raise UploadError("cannot create the immutable WeChat Git archive")
    extracted = temporary_root / "source"
    extracted.mkdir(mode=0o700)
    return _extract_wechat_archive(archive_path, extracted)


def _validate_project_config(
    archive_project: Path,
    *,
    expected_app_id: str,
) -> dict[str, str]:
    if not APP_ID.fullmatch(expected_app_id):
        raise UploadError("expected WeChat AppID is invalid")
    config_path = archive_project / "project.config.json"
    try:
        metadata = config_path.lstat()
    except OSError as exc:
        raise UploadError("archived project.config.json is missing") from exc
    if config_path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise UploadError("archived project.config.json is not a regular file")
    if metadata.st_size <= 0 or metadata.st_size > MAX_PROJECT_CONFIG_BYTES:
        raise UploadError("archived project.config.json has an invalid size")
    encoded = config_path.read_bytes()
    config = _json_without_duplicates(encoded, label="project.config.json")
    if not isinstance(config, dict):
        raise UploadError("project.config.json must contain a JSON object")
    app_id = config.get("appid")
    compile_type = config.get("compileType")
    miniprogram_root = config.get("miniprogramRoot")
    if app_id != expected_app_id:
        raise UploadError("project.config.json AppID does not match the expected AppID")
    if compile_type != "miniprogram":
        raise UploadError("project.config.json compileType must be miniprogram")
    if (
        miniprogram_root != "miniprogram/"
        or "\x00" in miniprogram_root
        or "\\" in miniprogram_root
    ):
        raise UploadError("project.config.json miniprogramRoot is invalid")
    relative_root = PurePosixPath(miniprogram_root)
    if relative_root.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_root.parts
    ):
        raise UploadError("project.config.json miniprogramRoot is unsafe")
    extracted_root = archive_project.joinpath(*relative_root.parts)
    if not extracted_root.is_dir():
        raise UploadError("project.config.json miniprogramRoot does not exist")
    return {
        "projectConfigSha256": _sha256_bytes(encoded),
        "appId": app_id,
        "compileType": compile_type,
        "miniprogramRoot": miniprogram_root,
    }


def _strict_tool_policy(path: Path) -> tuple[dict[str, Any], str]:
    encoded = _secure_read(
        path,
        label="WeChat DevTools policy",
        maximum_bytes=MAX_POLICY_BYTES,
        allowed_owners={0, os.geteuid()},
        forbid_writable_by_group_or_world=True,
    )
    policy = _json_without_duplicates(encoded, label="WeChat DevTools policy")
    required = {
        "schemaVersion",
        "bundlePath",
        "bundleIdentifier",
        "shortVersion",
        "bundleVersion",
        "teamIdentifier",
        "identityMode",
        "files",
    }
    if not isinstance(policy, dict) or set(policy) != required:
        raise UploadError("WeChat DevTools policy fields are invalid")
    if policy["schemaVersion"] != TOOL_POLICY_SCHEMA:
        raise UploadError("WeChat DevTools policy schema is invalid")
    for field in (
        "bundlePath",
        "bundleIdentifier",
        "shortVersion",
        "bundleVersion",
        "teamIdentifier",
    ):
        if not isinstance(policy[field], str) or not policy[field]:
            raise UploadError(f"WeChat DevTools policy {field} is invalid")
    if policy["identityMode"] not in {"macOS-codesign", "test-fixture"}:
        raise UploadError("WeChat DevTools policy identityMode is invalid")
    bundle_path = Path(policy["bundlePath"])
    if not bundle_path.is_absolute():
        raise UploadError("WeChat DevTools policy bundlePath must be absolute")
    files = policy["files"]
    if not isinstance(files, dict) or set(files) != set(TOOL_FILES):
        raise UploadError("WeChat DevTools policy file authority is invalid")
    for name, required_relative_path in TOOL_FILES.items():
        authority = files[name]
        if (
            not isinstance(authority, dict)
            or set(authority) != {"relativePath", "sha256"}
            or authority.get("relativePath") != required_relative_path
            or not isinstance(authority.get("sha256"), str)
            or not HEX_SHA256.fullmatch(authority["sha256"])
        ):
            raise UploadError(f"WeChat DevTools policy {name} authority is invalid")
    return policy, _sha256_bytes(encoded)


def _regular_tool_file(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise UploadError(f"{label} is missing") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise UploadError(f"{label} must be a non-symlink regular file")
    return path


def _fixture_identity(bundle_path: Path) -> dict[str, str]:
    identity_path = _regular_tool_file(
        bundle_path / "Contents" / "fixture-identity.json",
        label="fixture tool identity",
    )
    encoded = identity_path.read_bytes()
    identity = _json_without_duplicates(encoded, label="fixture tool identity")
    expected = {
        "bundleIdentifier",
        "shortVersion",
        "bundleVersion",
        "teamIdentifier",
    }
    if (
        not isinstance(identity, dict)
        or set(identity) != expected
        or not all(isinstance(identity[key], str) and identity[key] for key in expected)
    ):
        raise UploadError("fixture tool identity fields are invalid")
    return identity


def _macos_identity(bundle_path: Path) -> dict[str, str]:
    if sys.platform != "darwin":
        raise UploadError("macOS-codesign identity validation requires macOS")
    info_path = _regular_tool_file(
        bundle_path / "Contents" / "Info.plist",
        label="WeChat DevTools Info.plist",
    )
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise UploadError("WeChat DevTools Info.plist is invalid") from exc
    verify = _run(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(bundle_path),
        ],
        cwd=bundle_path.parent,
        environment={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        check=False,
        text=True,
    )
    if verify.returncode != 0:
        raise UploadError("WeChat DevTools code signature verification failed")
    display = _run(
        [
            "/usr/bin/codesign",
            "-dv",
            "--verbose=4",
            str(bundle_path),
        ],
        cwd=bundle_path.parent,
        environment={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        check=False,
        text=True,
    )
    if display.returncode != 0:
        raise UploadError("WeChat DevTools code signature identity is unavailable")
    codesign_output = f"{display.stdout}\n{display.stderr}"
    values: dict[str, str] = {}
    for key in ("Identifier", "TeamIdentifier"):
        match = re.search(rf"(?m)^{key}=([^\r\n]+)$", codesign_output)
        if match is None:
            raise UploadError(f"WeChat DevTools code signature lacks {key}")
        values[key] = match.group(1).strip()
    identity = {
        "bundleIdentifier": values["Identifier"],
        "shortVersion": info.get("CFBundleShortVersionString"),
        "bundleVersion": info.get("CFBundleVersion"),
        "teamIdentifier": values["TeamIdentifier"],
    }
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise UploadError("WeChat DevTools bundle identity is invalid")
    return identity


def _validate_tool(
    *,
    devtools_cli: Path,
    policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, str]:
    bundle_path = Path(policy["bundlePath"])
    try:
        bundle_metadata = bundle_path.lstat()
    except OSError as exc:
        raise UploadError("WeChat DevTools bundle path is missing") from exc
    if (
        bundle_path.is_symlink()
        or not stat.S_ISDIR(bundle_metadata.st_mode)
        or bundle_path.resolve() != bundle_path
    ):
        raise UploadError("WeChat DevTools bundle path is unsafe")
    expected_cli = bundle_path / TOOL_FILES["cli"]
    if devtools_cli.is_symlink() or devtools_cli.resolve() != expected_cli.resolve():
        raise UploadError("DevTools CLI path does not match the pinned tool policy")
    actual_hashes: dict[str, str] = {}
    for name, relative_path in TOOL_FILES.items():
        tool_path = _regular_tool_file(
            bundle_path / relative_path,
            label=f"WeChat DevTools {name}",
        )
        if tool_path.resolve() != tool_path:
            raise UploadError(f"WeChat DevTools {name} path is unsafe")
        actual_hashes[name] = _sha256_file(tool_path)
        if actual_hashes[name] != policy["files"][name]["sha256"]:
            raise UploadError(f"WeChat DevTools {name} digest does not match policy")
    if not os.access(expected_cli, os.X_OK):
        raise UploadError("DevTools CLI launcher is not executable")
    if policy["identityMode"] == "macOS-codesign":
        identity = _macos_identity(bundle_path)
    else:
        identity = _fixture_identity(bundle_path)
    for field in (
        "bundleIdentifier",
        "shortVersion",
        "bundleVersion",
        "teamIdentifier",
    ):
        if identity[field] != policy[field]:
            raise UploadError(f"WeChat DevTools {field} does not match policy")
    return {
        "identityMode": policy["identityMode"],
        "policySha256": policy_sha256,
        **identity,
        "cliLauncherSha256": actual_hashes["cli"],
        "cliJsSha256": actual_hashes["cliJs"],
        "cliModuleSha256": actual_hashes["cliModule"],
    }


def _copy_signing_key(signing_private_key: Path, destination: Path) -> None:
    encoded = _secure_read(
        signing_private_key,
        label="WeChat uploader signing private key",
        maximum_bytes=128 * 1024,
        allowed_owners={os.geteuid()},
        exact_mode=0o600,
    )
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _rsa_key_id(private_key: Path) -> str:
    completed = _run(
        [
            "openssl",
            "rsa",
            "-in",
            str(private_key),
            "-pubout",
            "-outform",
            "DER",
        ],
        cwd=private_key.parent,
        text=False,
    )
    assert isinstance(completed.stdout, bytes)
    if not completed.stdout:
        raise UploadError("WeChat uploader signing key is not a valid RSA key")
    details = _run(
        ["openssl", "rsa", "-in", str(private_key), "-text", "-noout"],
        cwd=private_key.parent,
        text=True,
    )
    assert isinstance(details.stdout, str)
    match = re.search(r"(?:Private-Key|RSA Private-Key): \((\d+) bit", details.stdout)
    if match is None or int(match.group(1)) < 2048:
        raise UploadError("WeChat uploader RSA key must contain at least 2048 bits")
    return f"sha256:{_sha256_bytes(completed.stdout)}"


def _validate_cli_info(encoded: bytes) -> dict[str, Any]:
    info = _json_without_duplicates(encoded, label="DevTools CLI info output")
    if not isinstance(info, dict):
        raise UploadError("DevTools CLI info output must be a JSON object")
    size = info.get("size")
    packages = size.get("packages") if isinstance(size, dict) else None
    if not isinstance(packages, list) or not packages:
        raise UploadError("DevTools CLI info output size.packages must be non-empty")
    for package in packages:
        if (
            not isinstance(package, dict)
            or not isinstance(package.get("name"), str)
            or not package["name"].strip()
            or not isinstance(package.get("size"), int)
            or isinstance(package.get("size"), bool)
            or package["size"] < 0
        ):
            raise UploadError(
                "DevTools CLI info output contains an invalid package record"
            )
    return info


def _cli_environment() -> dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    for name in ("HOME", "USER", "LOGNAME", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _run_upload_cli(
    *,
    devtools_cli: Path,
    archive_project: Path,
    upload_version: str,
    description: str,
    temporary_root: Path,
) -> tuple[Path, Path, datetime, datetime]:
    raw_log = temporary_root / "wechat-upload.log"
    info_path = temporary_root / "wechat-upload.info.json"
    arguments = [
        str(devtools_cli),
        "upload",
        "--project",
        str(archive_project),
        "--version",
        upload_version,
        "--desc",
        description,
        "--info-output",
        str(info_path),
    ]
    started_at = datetime.now(UTC)
    try:
        with raw_log.open("xb") as log:
            completed = subprocess.run(
                arguments,
                cwd=temporary_root,
                env=_cli_environment(),
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
                timeout=CLI_TIMEOUT_SECONDS,
            )
            log.flush()
            os.fsync(log.fileno())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UploadError("WeChat DevTools upload could not complete") from exc
    completed_at = datetime.now(UTC)
    if completed.returncode != 0:
        raise UploadError(
            f"WeChat DevTools upload failed with exit code {completed.returncode}"
        )
    try:
        log_metadata = raw_log.lstat()
        info_metadata = info_path.lstat()
    except OSError as exc:
        raise UploadError(
            "successful DevTools upload did not emit all evidence"
        ) from exc
    if (
        raw_log.is_symlink()
        or not stat.S_ISREG(log_metadata.st_mode)
        or log_metadata.st_size <= 0
        or log_metadata.st_size > MAX_LOG_BYTES
    ):
        raise UploadError("DevTools raw CLI log has an invalid type or size")
    if (
        info_path.is_symlink()
        or not stat.S_ISREG(info_metadata.st_mode)
        or info_metadata.st_size <= 0
        or info_metadata.st_size > MAX_INFO_BYTES
    ):
        raise UploadError("DevTools CLI info output has an invalid type or size")
    return raw_log, info_path, started_at, completed_at


def _validated_text(value: str, *, label: str, maximum_length: int) -> str:
    if (
        not value
        or len(value) > maximum_length
        or value.startswith("-")
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise UploadError(f"{label} is invalid")
    return value


def _output_paths(prefix: Path) -> dict[str, Path]:
    if not prefix.name or prefix.name in {".", ".."}:
        raise UploadError("output prefix is invalid")
    try:
        parent = prefix.parent.resolve(strict=True)
    except OSError as exc:
        raise UploadError("output directory does not exist") from exc
    if not parent.is_dir():
        raise UploadError("output parent is not a directory")
    resolved_prefix = parent / prefix.name
    paths = {
        "receipt": Path(f"{resolved_prefix}.json"),
        "signature": Path(f"{resolved_prefix}.json.sig"),
        "info": Path(f"{resolved_prefix}.info.json"),
        "log": Path(f"{resolved_prefix}.log"),
    }
    for path in paths.values():
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise UploadError(f"cannot inspect output path {path.name}") from exc
        raise UploadError(f"refusing to overwrite output path {path.name}")
    return paths


def _write_staged(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise UploadError(f"cannot durably publish invalid file {path.name}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_without_overwrite(
    staged: dict[str, Path],
    destinations: dict[str, Path],
) -> None:
    created: list[tuple[Path, os.stat_result]] = []
    try:
        for name in ("log", "info", "receipt", "signature"):
            source = staged[name]
            destination = destinations[name]
            os.link(source, destination, follow_symlinks=False)
            created.append((destination, source.stat()))
        directory_descriptor = os.open(
            destinations["receipt"].parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        for destination, source_metadata in reversed(created):
            try:
                current = destination.lstat()
                if (
                    current.st_dev == source_metadata.st_dev
                    and current.st_ino == source_metadata.st_ino
                ):
                    destination.unlink()
            except FileNotFoundError:
                pass
        raise UploadError("could not atomically publish upload evidence") from exc


def upload_and_issue_receipt(
    *,
    project_root: Path,
    expected_git_commit: str,
    expected_app_id: str,
    upload_version: str,
    description: str,
    devtools_cli: Path,
    tool_policy: Path,
    signing_private_key: Path,
    output_prefix: Path,
    allow_test_tool_identity: bool = False,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise UploadError("project root is not a directory")
    upload_version = _validated_text(
        upload_version,
        label="upload version",
        maximum_length=64,
    )
    description = _validated_text(
        description,
        label="upload description",
        maximum_length=200,
    )
    destinations = _output_paths(output_prefix)
    source_authority = _require_immutable_source(
        project_root,
        expected_commit=expected_git_commit,
    )
    policy, policy_sha256 = _strict_tool_policy(tool_policy)
    if policy["identityMode"] == "macOS-codesign":
        if tool_policy.lstat().st_uid != 0:
            raise UploadError("production WeChat DevTools policy must be root-owned")
    elif not allow_test_tool_identity:
        raise UploadError(
            "test-fixture DevTools identity is forbidden without the explicit "
            "test-only override"
        )
    tool = _validate_tool(
        devtools_cli=devtools_cli,
        policy=policy,
        policy_sha256=policy_sha256,
    )

    with tempfile.TemporaryDirectory(prefix="anan-wechat-upload-") as temporary:
        temporary_root = Path(temporary)
        temporary_root.chmod(0o700)
        archive_project = _archive_source(
            project_root,
            commit=expected_git_commit,
            temporary_root=temporary_root,
        )
        project = _validate_project_config(
            archive_project,
            expected_app_id=expected_app_id,
        )
        copied_key = temporary_root / "uploader-private-key.pem"
        _copy_signing_key(signing_private_key, copied_key)
        uploader_key_id = _rsa_key_id(copied_key)
        raw_log, info_path, started_at, completed_at = _run_upload_cli(
            devtools_cli=devtools_cli,
            archive_project=archive_project,
            upload_version=upload_version,
            description=description,
            temporary_root=temporary_root,
        )
        info_encoded = info_path.read_bytes()
        info_output = _validate_cli_info(info_encoded)
        raw_log_encoded = raw_log.read_bytes()

        if (
            _require_immutable_source(
                project_root,
                expected_commit=expected_git_commit,
            )
            != source_authority
        ):
            raise UploadError("Git source authority changed during upload")
        if (
            _validate_tool(
                devtools_cli=devtools_cli,
                policy=policy,
                policy_sha256=policy_sha256,
            )
            != tool
        ):
            raise UploadError("WeChat DevTools identity changed during upload")

        receipt: dict[str, Any] = {
            "schemaVersion": RECEIPT_SCHEMA,
            "receiptId": str(uuid4()),
            "operation": "devtools_upload",
            "result": "success",
            "startedAt": started_at.isoformat(),
            "completedAt": completed_at.isoformat(),
            **source_authority,
            "projectRelativePath": PROJECT_RELATIVE_PATH,
            **project,
            "uploadVersion": upload_version,
            "uploadDescription": description,
            "devtoolsCliExitCode": 0,
            "rawCliLogSha256": _sha256_bytes(raw_log_encoded),
            "rawCliLogBytes": len(raw_log_encoded),
            "infoOutputSha256": _sha256_bytes(info_encoded),
            "infoOutputBytes": len(info_encoded),
            "infoOutput": info_output,
            "tool": tool,
            "uploaderKeyId": uploader_key_id,
            "signatureAlgorithm": SIGNATURE_ALGORITHM,
        }
        receipt_encoded = (
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        output_parent = destinations["receipt"].parent
        with tempfile.TemporaryDirectory(
            prefix=".anan-wechat-evidence-",
            dir=output_parent,
        ) as staging_directory:
            staging_root = Path(staging_directory)
            staging_root.chmod(0o700)
            staged = {
                "receipt": staging_root / "published-receipt.json",
                "signature": staging_root / "published-receipt.json.sig",
                "info": staging_root / "published-info.json",
                "log": staging_root / "published.log",
            }
            _write_staged(staged["receipt"], receipt_encoded)
            _write_staged(staged["info"], info_encoded)
            _write_staged(staged["log"], raw_log_encoded)
            signature = _run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-sign",
                    str(copied_key),
                    "-out",
                    str(staged["signature"]),
                    str(staged["receipt"]),
                ],
                cwd=staging_root,
                text=False,
            )
            if signature.returncode != 0:
                raise UploadError("could not sign the WeChat upload receipt")
            try:
                signature_metadata = staged["signature"].lstat()
            except OSError as exc:
                raise UploadError("receipt signature was not produced") from exc
            if (
                staged["signature"].is_symlink()
                or not stat.S_ISREG(signature_metadata.st_mode)
                or signature_metadata.st_size <= 0
            ):
                raise UploadError("receipt signature is invalid")
            staged["signature"].chmod(0o600)
            _fsync_file(staged["signature"])
            staging_descriptor = os.open(
                staging_root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(staging_descriptor)
            finally:
                os.close(staging_descriptor)
            for destination in destinations.values():
                if destination.exists() or destination.is_symlink():
                    raise UploadError(
                        f"refusing to overwrite output path {destination.name}"
                    )
            _publish_without_overwrite(staged, destinations)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload an immutable WeChat release and sign its receipt.",
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-app-id", required=True)
    parser.add_argument("--upload-version", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--devtools-cli", required=True, type=Path)
    parser.add_argument("--tool-policy", required=True, type=Path)
    parser.add_argument("--signing-private-key", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True, type=Path)
    parser.add_argument(
        "--allow-test-tool-identity",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        upload_and_issue_receipt(
            project_root=arguments.project_root,
            expected_git_commit=arguments.expected_git_commit,
            expected_app_id=arguments.expected_app_id,
            upload_version=arguments.upload_version,
            description=arguments.description,
            devtools_cli=arguments.devtools_cli,
            tool_policy=arguments.tool_policy,
            signing_private_key=arguments.signing_private_key,
            output_prefix=arguments.output_prefix,
            allow_test_tool_identity=arguments.allow_test_tool_identity,
        )
    except UploadError as exc:
        print(f"wechat upload receipt not issued: {exc}", file=sys.stderr)
        return 2
    print(f"{arguments.output_prefix}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
