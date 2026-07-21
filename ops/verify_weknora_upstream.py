#!/usr/bin/env python3
from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import ssl
import tarfile
from urllib.request import HTTPSHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "ops" / "upstreams" / "tencent-weknora-0.7.0.json"
MAXIMUM_TARBALL_BYTES = 80_000_000


class _RejectRedirects(HTTPSHandler):
    def http_error_301(self, request, file, code, message, headers):
        raise RuntimeError("weknora_upstream_redirect_rejected")

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


def main() -> int:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    _validate_lock(lock)
    request = Request(
        lock["tarballUrl"],
        headers={
            "Accept": "application/gzip",
            "User-Agent": "Anan-WeKnora-Upstream-Verifier/1",
        },
    )
    opener = build_opener(
        _RejectRedirects(context=ssl.create_default_context())
    )
    with opener.open(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError("weknora_upstream_http_rejected")
        payload = response.read(MAXIMUM_TARBALL_BYTES + 1)
    if len(payload) > MAXIMUM_TARBALL_BYTES:
        raise RuntimeError("weknora_upstream_tarball_too_large")
    digest = sha256(payload).hexdigest()
    if digest != lock["tarballSha256"]:
        raise RuntimeError("weknora_upstream_tarball_digest_mismatch")
    selected = _verified_selected_files(payload, lock)
    _validate_reviewed_contract(selected)
    print(
        "VERIFIED Tencent/WeKnora@v0.7.0 "
        f"commit={lock['commit']} sha256={digest}"
    )
    return 0


def _validate_lock(lock: dict[str, object]) -> None:
    expected = {
        "repository": "Tencent/WeKnora",
        "version": "0.7.0",
        "tag": "v0.7.0",
        "commit": "e8dcbb07d09db88894601b564ebabe21dda6830d",
        "tree": "a26c29e1ec8c4d5c45dedd4c1edee2af87a7300c",
        "license": "MIT",
        "tarballUrl": "https://codeload.github.com/Tencent/WeKnora/tar.gz/refs/tags/v0.7.0",
        "rootDirectory": "WeKnora-0.7.0",
    }
    for key, value in expected.items():
        if lock.get(key) != value:
            raise RuntimeError(f"weknora_upstream_lock_{key}_mismatch")
    digest = lock.get("tarballSha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise RuntimeError("weknora_upstream_lock_digest_invalid")
    selected = lock.get("selectedFiles")
    if not isinstance(selected, dict) or not selected:
        raise RuntimeError("weknora_upstream_lock_selected_files_invalid")


def _verified_selected_files(
    payload: bytes, lock: dict[str, object]
) -> dict[str, bytes]:
    root = str(lock["rootDirectory"])
    selected_hashes = lock["selectedFiles"]
    if not isinstance(selected_hashes, dict):
        raise RuntimeError("weknora_upstream_lock_selected_files_invalid")
    selected: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise RuntimeError("weknora_upstream_tar_member_unsafe")
            if not member.name.startswith(f"{root}/") and member.name != root:
                raise RuntimeError("weknora_upstream_root_mismatch")
        by_name = {member.name: member for member in members}
        for relative, expected_digest in selected_hashes.items():
            if not isinstance(relative, str) or not isinstance(expected_digest, str):
                raise RuntimeError("weknora_upstream_lock_selected_files_invalid")
            member = by_name.get(f"{root}/{relative}")
            if member is None or not member.isfile():
                raise RuntimeError(f"weknora_upstream_file_missing:{relative}")
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError(f"weknora_upstream_file_unreadable:{relative}")
            content = handle.read()
            if sha256(content).hexdigest() != expected_digest:
                raise RuntimeError(f"weknora_upstream_file_digest_mismatch:{relative}")
            selected[relative] = content
    return selected


def _validate_reviewed_contract(selected: dict[str, bytes]) -> None:
    router = selected["internal/router/router.go"].decode("utf-8")
    rbac = selected["internal/router/rbac.go"].decode("utf-8")
    tenant_types = selected["internal/types/tenant_api_key.go"].decode("utf-8")
    knowledge = selected["docs/api/knowledge.md"].decode("utf-8")
    knowledge_base = selected["docs/api/knowledge-base.md"].decode("utf-8")
    required_router = (
        'r.Group("/knowledge-bases/:id/knowledge")',
        'kb.POST("/manual"',
        'k.DELETE("/:id"',
        'kbManagement.POST(""',
        'kbManagement.DELETE("/:id"',
        'kb.POST("/:id/hybrid-search"',
    )
    required_rbac = (
        "apiKeyIngest",
        "apiKeyRetrieve",
        "apiKeyManageKnowledgeBases",
        "KBAccessRead",
        "KBAccessWrite",
    )
    required_types = (
        'APIKeyCapabilityRetrieve',
        'APIKeyCapabilityIngest',
        'APIKeyCapabilityManageKnowledgeBases',
        'KnowledgeBaseIDs',
    )
    required_docs = (
        "POST `/knowledge-bases/:id/knowledge/manual`",
        "DELETE `/knowledge/:id`",
        "parse_status",
        "knowledge_ids",
        "POST `/knowledge-bases/:id/hybrid-search`",
    )
    for marker in required_router:
        if marker not in router:
            raise RuntimeError(f"weknora_upstream_router_contract_drift:{marker}")
    for marker in required_rbac:
        if marker not in rbac:
            raise RuntimeError(f"weknora_upstream_rbac_contract_drift:{marker}")
    for marker in required_types:
        if marker not in tenant_types:
            raise RuntimeError(f"weknora_upstream_api_key_contract_drift:{marker}")
    combined_docs = f"{knowledge}\n{knowledge_base}"
    for marker in required_docs:
        if marker not in combined_docs:
            raise RuntimeError(f"weknora_upstream_api_documentation_drift:{marker}")
    license_text = selected["LICENSE"].decode("utf-8")
    if "MIT License" not in license_text:
        raise RuntimeError("weknora_upstream_license_drift")


if __name__ == "__main__":
    raise SystemExit(main())
