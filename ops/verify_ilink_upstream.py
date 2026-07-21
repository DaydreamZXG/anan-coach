#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Any
from urllib.request import Request, urlopen


MAX_TARBALL_BYTES = 10 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = PROJECT_ROOT / "ops/upstreams/tencent-openclaw-weixin-2.4.6.json"


class UpstreamVerificationError(RuntimeError):
    pass


def verify(lock_path: Path) -> dict[str, str]:
    lock = _load_lock(lock_path)
    tarball_url = _required_text(lock, "tarball")
    if not tarball_url.startswith("https://registry.npmjs.org/"):
        raise UpstreamVerificationError("tarball_origin_unapproved")
    request = Request(tarball_url, headers={"Accept": "application/octet-stream"})
    with urlopen(request, timeout=30) as response:
        if response.geturl() != tarball_url:
            raise UpstreamVerificationError("tarball_redirect_rejected")
        declared_length = response.headers.get("Content-Length")
        if declared_length and int(declared_length) > MAX_TARBALL_BYTES:
            raise UpstreamVerificationError("tarball_too_large")
        artifact = response.read(MAX_TARBALL_BYTES + 1)
    if len(artifact) > MAX_TARBALL_BYTES:
        raise UpstreamVerificationError("tarball_too_large")

    _verify_digest(artifact, "sha256", _required_text(lock, "tarballSha256"))
    _verify_digest(artifact, "sha1", _required_text(lock, "npmShasum"))
    integrity = _required_text(lock, "npmIntegrity")
    expected_integrity = "sha512-" + base64.b64encode(hashlib.sha512(artifact).digest()).decode()
    if integrity != expected_integrity:
        raise UpstreamVerificationError("npm_integrity_mismatch")

    source_digests = lock.get("sourceSha256")
    if not isinstance(source_digests, dict) or not source_digests:
        raise UpstreamVerificationError("source_digest_lock_missing")
    sources: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise UpstreamVerificationError("unsafe_tar_member")
        for name, expected in source_digests.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise UpstreamVerificationError("source_digest_lock_invalid")
            try:
                member = archive.getmember(name)
            except KeyError as exc:
                raise UpstreamVerificationError("locked_source_missing") from exc
            if not member.isfile() or member.size > 2 * 1024 * 1024:
                raise UpstreamVerificationError("locked_source_invalid")
            handle = archive.extractfile(member)
            if handle is None:
                raise UpstreamVerificationError("locked_source_unreadable")
            content = handle.read()
            _verify_digest(content, "sha256", expected)
            sources[name] = content

    package = json.loads(sources["package/package.json"])
    if package.get("name") != lock.get("package") or package.get("version") != lock.get(
        "version"
    ):
        raise UpstreamVerificationError("package_identity_mismatch")
    if package.get("license") != lock.get("license"):
        raise UpstreamVerificationError("package_license_mismatch")
    if package.get("peerDependencies") != lock.get("peerDependencies"):
        raise UpstreamVerificationError("package_peer_dependency_mismatch")

    _verify_protocol(lock, package, sources["package/src/auth/login-qr.ts"])
    return {
        "package": str(lock["package"]),
        "version": str(lock["version"]),
        "sha256": hashlib.sha256(artifact).hexdigest(),
        "integrity": expected_integrity,
    }


def _verify_protocol(lock: dict[str, Any], package: dict[str, Any], login_source: bytes) -> None:
    protocol = lock.get("protocol")
    if not isinstance(protocol, dict):
        raise UpstreamVerificationError("protocol_lock_missing")
    version = _required_text(lock, "version")
    parts = [int(part) for part in version.split(".")]
    client_version = (parts[0] << 16) | (parts[1] << 8) | parts[2]
    expected = {
        "baseUrl": "https://ilinkai.weixin.qq.com",
        "botType": "3",
        "appId": package.get("ilink_appid"),
        "appClientVersion": client_version,
        "qrCreateEndpoint": "ilink/bot/get_bot_qrcode",
        "qrStatusEndpoint": "ilink/bot/get_qrcode_status",
    }
    for name, value in expected.items():
        if protocol.get(name) != value:
            raise UpstreamVerificationError(f"protocol_lock_mismatch:{name}")
    source = login_source.decode("utf-8")
    required_literals = [
        expected["baseUrl"],
        expected["qrCreateEndpoint"],
        expected["qrStatusEndpoint"],
        "local_token_list",
        *protocol.get("statuses", []),
    ]
    if any(not isinstance(value, str) or value not in source for value in required_literals):
        raise UpstreamVerificationError("protocol_source_literal_missing")


def _load_lock(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpstreamVerificationError("upstream_lock_unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise UpstreamVerificationError("upstream_lock_invalid")
    return payload


def _required_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise UpstreamVerificationError(f"upstream_lock_missing:{name}")
    return value


def _verify_digest(content: bytes, algorithm: str, expected: str) -> None:
    actual = hashlib.new(algorithm, content).hexdigest()
    if actual != expected:
        raise UpstreamVerificationError(f"{algorithm}_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the exact Tencent iLink upstream artifact locked by Anan."
    )
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args()
    try:
        result = verify(args.lock)
    except UpstreamVerificationError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(
        f"VERIFIED {result['package']}@{result['version']} "
        f"sha256={result['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
