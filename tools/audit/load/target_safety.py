#!/usr/bin/env python3
"""Fail-closed, independently attested target validation for load tools."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from ipaddress import ip_address
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ATTESTATION_SCHEMA_VERSION = "anan-load-target-attestation@v2"
TRUSTED_ATTESTATION_PUBLIC_KEY_PATH = Path(
    "/etc/anan/load-target-attestation-ed25519.pub"
)
TRUSTED_EXECUTOR_ID_PATH = Path("/etc/anan/load-target-executor-id")
# Tests replace these constants with temporary trust files owned by the test user.
# Runtime callers cannot override either through CLI arguments or environment.
TRUSTED_ATTESTATION_PUBLIC_KEY_OWNER_UID = 0
TRUSTED_EXECUTOR_ID_OWNER_UID = 0

PRODUCTION_HOSTS = frozenset({"kg.xingguoxian.cn"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
ALLOWED_NONPRODUCTION_ENVIRONMENTS = frozenset(
    {
        "development",
        "local",
        "local_capacity",
        "preproduction",
        "qa",
        "staging",
        "test",
        "testing",
    }
)
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})

_ATTESTATION_FIELDS = frozenset(
    {
        "schemaVersion",
        "attestationId",
        "kind",
        "scheme",
        "host",
        "port",
        "databaseName",
        "clusterSystemIdentifierSha256",
        "environmentId",
        "issuedAt",
        "expiresAt",
        "executorId",
        "production",
    }
)
_MAX_ATTESTATION_BYTES = 16 * 1024
_MAX_SIGNATURE_BYTES = 1024
_MAX_PUBLIC_KEY_BYTES = 16 * 1024
_MAX_ATTESTATION_LIFETIME = timedelta(minutes=15)
_CLOCK_SKEW = timedelta(seconds=30)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_ATTESTATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_EXECUTOR_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_POSTGRES_SYSTEM_IDENTIFIER = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")
TARGET_OVERRIDING_LIBPQ_ENVIRONMENT = frozenset(
    {
        "PGDATABASE",
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGUSER",
    }
)
TARGET_OVERRIDING_HTTP_PROXY_ENVIRONMENT = frozenset(
    {
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedDatabaseTarget:
    host: str
    database_name: str
    cluster_system_identifier_sha256: str


def normalize_target_host(host: str) -> str:
    """Return a canonical ASCII DNS name or compressed IP literal."""

    candidate = host.strip()
    if not candidate:
        raise ValueError("target host is required")

    # A terminal dot is legal DNS spelling for an FQDN but must not change
    # identity comparisons. IDNA also maps the Unicode DNS dot variants.
    candidate = candidate.rstrip(".")
    try:
        canonical = candidate.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise ValueError("target host is not valid IDNA") from exc
    if not canonical:
        raise ValueError("target host is required")

    try:
        return ip_address(canonical).compressed.casefold()
    except ValueError:
        pass

    if len(canonical) > 253:
        raise ValueError("target DNS host is too long")
    labels = canonical.split(".")
    if any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise ValueError("target host is not a valid DNS name or IP literal")
    return canonical


def _normalize_environment(value: str) -> str:
    environment = value.strip().casefold()
    if environment in PRODUCTION_ENVIRONMENTS:
        raise ValueError("production load environments are forbidden")
    if environment not in ALLOWED_NONPRODUCTION_ENVIRONMENTS:
        allowed = ", ".join(sorted(ALLOWED_NONPRODUCTION_ENVIRONMENTS))
        raise ValueError(
            f"unapproved load environment {environment!r}; expected one of: {allowed}"
        )
    return environment


def _normalize_unix_socket(value: str) -> str:
    if not value.startswith("/") or "\x00" in value or "," in value:
        raise ValueError("database Unix socket must be one explicit absolute path")
    normalized = os.path.normpath(value)
    if normalized != value:
        raise ValueError("database Unix socket path must already be canonical")
    return normalized


def _normalize_database_name(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or "\x00" in value
        or "/" in value
        or "\\" in value
    ):
        raise ValueError("database name must be one explicit canonical identifier")
    if len(value.encode("utf-8")) > 63:
        raise ValueError("database name exceeds PostgreSQL's 63-byte identifier limit")
    return value


def _read_limited(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum + 1)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    if not content:
        raise ValueError(f"{label} is empty")
    if len(content) > maximum:
        raise ValueError(f"{label} exceeds {maximum} bytes")
    return content


def reject_target_overriding_libpq_environment(
    environ: Mapping[str, str] | None = None,
) -> None:
    source = os.environ if environ is None else environ
    present = sorted(TARGET_OVERRIDING_LIBPQ_ENVIRONMENT.intersection(source))
    if present:
        raise ValueError(
            "target-overriding libpq environment variables are forbidden: "
            + ", ".join(present)
        )


def reject_target_overriding_http_proxy_environment(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Reject inherited proxies that can redirect an attested HTTP origin."""

    source = os.environ if environ is None else environ
    present = sorted(TARGET_OVERRIDING_HTTP_PROXY_ENVIRONMENT.intersection(source))
    if present:
        raise ValueError(
            "HTTP proxy environment variables are forbidden for attested load "
            "targets: " + ", ".join(present)
        )


def _read_trusted_root_file(
    path: Path,
    *,
    expected_owner_uid: int,
    maximum: int,
    label: str,
) -> bytes:
    try:
        parent_status = path.parent.stat()
    except OSError as exc:
        raise ValueError(f"{label} directory is unavailable: {path.parent}") from exc
    if parent_status.st_uid != expected_owner_uid:
        raise ValueError(f"{label} directory has an untrusted owner")
    if parent_status.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"{label} directory is group/world writable")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if status.st_uid != expected_owner_uid:
            raise ValueError(f"{label} has an untrusted owner")
        if status.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"{label} is group/world writable")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(maximum + 1)
    finally:
        os.close(descriptor)
    if not content or len(content) > maximum:
        raise ValueError(f"{label} is empty or oversized")
    return content


def _read_trusted_public_key() -> Ed25519PublicKey:
    content = _read_trusted_root_file(
        TRUSTED_ATTESTATION_PUBLIC_KEY_PATH,
        expected_owner_uid=TRUSTED_ATTESTATION_PUBLIC_KEY_OWNER_UID,
        maximum=_MAX_PUBLIC_KEY_BYTES,
        label="trusted attestation public key",
    )
    try:
        public_key = serialization.load_pem_public_key(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("trusted attestation public key is invalid PEM") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("trusted attestation public key must be Ed25519")
    return public_key


def _read_trusted_executor_id() -> str:
    content = _read_trusted_root_file(
        TRUSTED_EXECUTOR_ID_PATH,
        expected_owner_uid=TRUSTED_EXECUTOR_ID_OWNER_UID,
        maximum=256,
        label="trusted load executor identity",
    )
    try:
        rendered = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("trusted load executor identity must be UTF-8") from exc
    executor_id = rendered.removesuffix("\n")
    if executor_id != rendered.strip() or not _EXECUTOR_ID.fullmatch(executor_id):
        raise ValueError("trusted load executor identity is not canonical")
    return executor_id


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"target attestation contains duplicate key {key!r}")
        result[key] = value
    return result


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"target attestation {field} must be an ISO-8601 timestamp")
    rendered = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as exc:
        raise ValueError(
            f"target attestation {field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(f"target attestation {field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _load_verified_attestation(
    *,
    attestation_path: Path | str | None,
    signature_path: Path | str | None,
    kind: str,
    scheme: str,
    host: str,
    port: int,
    database_name: str | None,
    environment: str,
    unix_socket: bool,
) -> dict[str, Any]:
    if attestation_path is None or signature_path is None:
        raise ValueError(
            "a signed target attestation and detached signature are required"
        )
    raw_attestation = _read_limited(
        Path(attestation_path),
        maximum=_MAX_ATTESTATION_BYTES,
        label="target attestation",
    )
    encoded_signature = _read_limited(
        Path(signature_path),
        maximum=_MAX_SIGNATURE_BYTES,
        label="target attestation signature",
    ).strip()
    try:
        signature = base64.b64decode(encoded_signature, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("target attestation signature is not strict base64") from exc
    if len(signature) != 64:
        raise ValueError("target attestation signature must be 64-byte Ed25519")
    try:
        _read_trusted_public_key().verify(signature, raw_attestation)
    except InvalidSignature as exc:
        raise ValueError("target attestation signature verification failed") from exc

    try:
        payload = json.loads(
            raw_attestation,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("target attestation must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("target attestation must be a JSON object")
    fields = frozenset(payload)
    if fields != _ATTESTATION_FIELDS:
        missing = ", ".join(sorted(_ATTESTATION_FIELDS - fields)) or "none"
        unexpected = ", ".join(sorted(fields - _ATTESTATION_FIELDS)) or "none"
        raise ValueError(
            "target attestation schema fields mismatch "
            f"(missing: {missing}; unexpected: {unexpected})"
        )
    if payload["schemaVersion"] != ATTESTATION_SCHEMA_VERSION:
        raise ValueError("target attestation schema version is unsupported")
    if not isinstance(payload["attestationId"], str) or not _ATTESTATION_ID.fullmatch(
        payload["attestationId"]
    ):
        raise ValueError("target attestation ID is invalid")
    if payload["kind"] != kind:
        raise ValueError("target attestation kind does not match the target")
    if payload["scheme"] != scheme:
        raise ValueError("target attestation scheme does not match the target")
    if type(payload["port"]) is not int or not 1 <= payload["port"] <= 65535:
        raise ValueError("target attestation port is invalid")
    if payload["port"] != port:
        raise ValueError("target attestation port does not match the target")
    if payload["databaseName"] != database_name:
        raise ValueError("target attestation database name does not match the target")
    cluster_identity = payload["clusterSystemIdentifierSha256"]
    if kind == "http":
        if cluster_identity is not None:
            raise ValueError("HTTP target attestation cluster identity must be null")
    elif not isinstance(cluster_identity, str) or not _SHA256.fullmatch(
        cluster_identity
    ):
        raise ValueError(
            "database target attestation cluster identity must be a SHA-256"
        )
    if payload["environmentId"] != environment:
        raise ValueError("target attestation environment does not match the target")
    if payload["executorId"] != _read_trusted_executor_id():
        raise ValueError(
            "target attestation executor identity does not match this host"
        )
    if payload["production"] is not False:
        raise ValueError("target attestation must explicitly declare production=false")

    attested_host_value = payload["host"]
    if not isinstance(attested_host_value, str):
        raise ValueError("target attestation host must be a string")
    attested_host = (
        _normalize_unix_socket(attested_host_value)
        if unix_socket
        else normalize_target_host(attested_host_value)
    )
    if attested_host_value != attested_host:
        raise ValueError("target attestation host must already be canonical")
    if attested_host != host:
        raise ValueError("target attestation host does not match the target")

    issued_at = _parse_timestamp(payload["issuedAt"], field="issuedAt")
    expires_at = _parse_timestamp(payload["expiresAt"], field="expiresAt")
    now = datetime.now(UTC)
    if expires_at <= issued_at:
        raise ValueError("target attestation expiry must follow issuance")
    if expires_at - issued_at > _MAX_ATTESTATION_LIFETIME:
        raise ValueError("target attestation lifetime exceeds 15 minutes")
    if issued_at > now + _CLOCK_SKEW:
        raise ValueError("target attestation is not valid yet")
    if expires_at <= now:
        raise ValueError("target attestation has expired")
    return payload


def _validate_target_identity(
    *,
    kind: str,
    scheme: str,
    host: str,
    port: int,
    database_name: str | None,
    allow_nonlocal: bool,
    target_environment_id: str,
    expected_target_host: str,
    attestation_path: Path | str | None,
    signature_path: Path | str | None,
    unix_socket: bool = False,
) -> dict[str, Any]:
    if host in PRODUCTION_HOSTS:
        raise ValueError(f"production {kind} load targets are forbidden")
    if attestation_path is None or signature_path is None:
        raise ValueError(
            "a signed target attestation and detached signature are required"
        )

    environment = _normalize_environment(target_environment_id)
    is_local = unix_socket or host in LOCAL_HOSTS
    if not is_local:
        if not allow_nonlocal:
            raise ValueError("non-local load targets require --allow-nonlocal")
        if not expected_target_host.strip():
            raise ValueError("non-local load targets require --expected-target-host")
        expected = normalize_target_host(expected_target_host)
        if expected in PRODUCTION_HOSTS:
            raise ValueError(f"production {kind} load targets are forbidden")
        if expected != host:
            raise ValueError(
                "target identity mismatch: normalized target host "
                f"{host!r} does not equal expected host {expected!r}"
            )

    return _load_verified_attestation(
        attestation_path=attestation_path,
        signature_path=signature_path,
        kind=kind,
        scheme=scheme,
        host=host,
        port=port,
        database_name=database_name,
        environment=environment,
        unix_socket=unix_socket,
    )


def validate_http_load_target(
    base_url: str,
    *,
    allow_nonlocal: bool,
    target_environment_id: str,
    expected_target_host: str = "",
    attestation_path: Path | str | None = None,
    signature_path: Path | str | None = None,
    minimum_validity_seconds: float = 0,
) -> str:
    """Validate an independently attested HTTP load origin."""

    reject_target_overriding_http_proxy_environment()
    if (
        not isinstance(minimum_validity_seconds, (int, float))
        or isinstance(minimum_validity_seconds, bool)
        or not math.isfinite(float(minimum_validity_seconds))
        or minimum_validity_seconds < 0
    ):
        raise ValueError(
            "minimum target-attestation validity must be finite and non-negative"
        )
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        explicit_port = parsed.port
    except ValueError as exc:
        raise ValueError("base URL must contain a valid host and port") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not host:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL must not contain user information")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query or fragment")
    canonical_host = normalize_target_host(host)
    port = (
        explicit_port
        if explicit_port is not None
        else (443 if scheme == "https" else 80)
    )
    authority = _validate_target_identity(
        kind="http",
        scheme=scheme,
        host=canonical_host,
        port=port,
        database_name=None,
        allow_nonlocal=allow_nonlocal,
        target_environment_id=target_environment_id,
        expected_target_host=expected_target_host,
        attestation_path=attestation_path,
        signature_path=signature_path,
    )
    expires_at = _parse_timestamp(authority["expiresAt"], field="expiresAt")
    if expires_at <= datetime.now(UTC) + timedelta(
        seconds=float(minimum_validity_seconds)
    ):
        raise ValueError(
            "target attestation does not remain valid for the declared load run"
        )
    return canonical_host


def validate_database_load_target(
    database_url: str,
    *,
    allow_nonlocal: bool,
    target_environment_id: str,
    expected_target_host: str = "",
    attestation_path: Path | str | None = None,
    signature_path: Path | str | None = None,
) -> ValidatedDatabaseTarget:
    """Validate an independently attested PostgreSQL target."""

    normalized_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        parsed = urlsplit(normalized_url)
        authority_host = parsed.hostname or ""
        authority_port = parsed.port
    except ValueError as exc:
        raise ValueError("database URL must contain a valid host and port") from exc
    if parsed.scheme.casefold() not in {"postgres", "postgresql"}:
        raise ValueError("database URL must be a PostgreSQL URI")

    query = parse_qs(parsed.query, keep_blank_values=True)
    if "service" in query or "hostaddr" in query or "dbname" in query:
        raise ValueError(
            "database URL service, hostaddr, and dbname overrides are forbidden"
        )
    query_hosts = query.get("host", [])
    query_ports = query.get("port", [])
    if len(query_hosts) > 1 or len(query_ports) > 1:
        raise ValueError("database URL must contain one host and port selector")
    if authority_host and query_hosts:
        raise ValueError("database URL contains conflicting host selectors")
    if authority_port is not None and query_ports:
        raise ValueError("database URL contains conflicting port selectors")

    effective_host = query_hosts[0] if query_hosts else authority_host
    if not effective_host:
        raise ValueError("database URL must identify an explicit host or Unix socket")
    if "," in effective_host:
        raise ValueError("multi-host database load targets are forbidden")
    if query_ports:
        try:
            effective_port = int(query_ports[0])
        except ValueError as exc:
            raise ValueError("database URL port selector is invalid") from exc
    else:
        effective_port = authority_port if authority_port is not None else 5432
    if not 1 <= effective_port <= 65535:
        raise ValueError("database URL port selector is invalid")
    if not parsed.path.startswith("/") or parsed.path == "/":
        raise ValueError("database URL must identify an explicit database name")
    try:
        database_name = unquote_to_bytes(parsed.path[1:]).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("database URL name must be valid UTF-8") from exc
    database_name = _normalize_database_name(database_name)

    unix_socket = effective_host.startswith("/")
    canonical_host = (
        _normalize_unix_socket(effective_host)
        if unix_socket
        else normalize_target_host(effective_host)
    )
    payload = _validate_target_identity(
        kind="database",
        scheme="postgresql",
        host=canonical_host,
        port=effective_port,
        database_name=database_name,
        allow_nonlocal=allow_nonlocal,
        target_environment_id=target_environment_id,
        expected_target_host=expected_target_host,
        attestation_path=attestation_path,
        signature_path=signature_path,
        unix_socket=unix_socket,
    )
    return ValidatedDatabaseTarget(
        host=canonical_host,
        database_name=database_name,
        cluster_system_identifier_sha256=payload["clusterSystemIdentifierSha256"],
    )


def validate_database_connection_identity(
    connection: Any,
    *,
    target: ValidatedDatabaseTarget,
) -> None:
    """Verify the server-side cluster and database before any load write."""

    row = connection.execute(
        "select current_database(), "
        "(select system_identifier::text from pg_control_system())"
    ).fetchone()
    if row is None or len(row) != 2:
        raise ValueError("database target identity query returned an invalid result")
    database_name, cluster_system_identifier = row
    if str(database_name) != target.database_name:
        raise ValueError(
            "connected database name does not match the signed target attestation"
        )
    if not isinstance(
        cluster_system_identifier,
        str,
    ) or not _POSTGRES_SYSTEM_IDENTIFIER.fullmatch(cluster_system_identifier):
        raise ValueError(
            "connected database returned an invalid PostgreSQL system identifier"
        )
    if int(cluster_system_identifier) > (2**64 - 1):
        raise ValueError(
            "connected database returned an invalid PostgreSQL system identifier"
        )
    observed_cluster_sha256 = sha256(
        cluster_system_identifier.encode("utf-8")
    ).hexdigest()
    if observed_cluster_sha256 != target.cluster_system_identifier_sha256:
        raise ValueError(
            "connected database cluster does not match the signed target attestation"
        )
