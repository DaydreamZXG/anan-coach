from __future__ import annotations

import base64
import ast
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_CLUSTER_A = "7548921036482712345"
TEST_CLUSTER_B = "7548921036482712346"


def _load(name: str, relative: str):
    path = REPOSITORY_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


sustained = _load(
    "sustained_practice",
    "tools/audit/load/sustained_practice.py",
)
seed = _load(
    "seed_load_users",
    "tools/audit/load/seed_load_users.py",
)
probe = _load(
    "load_probe",
    "tools/audit/load/load_probe.py",
)
submit = _load(
    "submit_spike",
    "tools/audit/load/submit_spike.py",
)
target_safety = sys.modules["target_safety"]


IssueAttestation = Callable[..., tuple[Path, Path]]


class _DatabaseIdentityResult:
    def __init__(self, row: tuple[str, str]) -> None:
        self._row = row

    def fetchone(self) -> tuple[str, str]:
        return self._row


class _DatabaseIdentityConnection:
    def __init__(self, *, database_name: str, system_identifier: str) -> None:
        self._row = (database_name, system_identifier)

    def execute(self, _query: str) -> _DatabaseIdentityResult:
        return _DatabaseIdentityResult(self._row)

    def __enter__(self) -> _DatabaseIdentityConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def issue_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> IssueAttestation:
    private_key = Ed25519PrivateKey.generate()
    trust_directory = tmp_path / "trusted"
    trust_directory.mkdir(mode=0o700)
    public_key_path = trust_directory / "load-target-ed25519.pub"
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key_path.chmod(0o644)
    executor_id_path = trust_directory / "load-executor-id"
    executor_id_path.write_text("pytest-executor-a\n", encoding="utf-8")
    executor_id_path.chmod(0o644)
    monkeypatch.setattr(
        target_safety,
        "TRUSTED_ATTESTATION_PUBLIC_KEY_PATH",
        public_key_path,
    )
    monkeypatch.setattr(
        target_safety,
        "TRUSTED_ATTESTATION_PUBLIC_KEY_OWNER_UID",
        os.getuid(),
    )
    monkeypatch.setattr(
        target_safety,
        "TRUSTED_EXECUTOR_ID_PATH",
        executor_id_path,
    )
    monkeypatch.setattr(
        target_safety,
        "TRUSTED_EXECUTOR_ID_OWNER_UID",
        os.getuid(),
    )
    issued = 0

    def issue(
        *,
        kind: str,
        scheme: str,
        host: str,
        port: int,
        environment: str,
        database_name: str | None = None,
        cluster_system_identifier_sha256: str | None = None,
        production: bool = False,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
        overrides: dict[str, Any] | None = None,
        signing_key: Ed25519PrivateKey | None = None,
    ) -> tuple[Path, Path]:
        nonlocal issued
        issued += 1
        now = datetime.now(UTC)
        start = issued_at or now - timedelta(seconds=1)
        expiry = expires_at or now + timedelta(minutes=5)
        payload: dict[str, Any] = {
            "schemaVersion": target_safety.ATTESTATION_SCHEMA_VERSION,
            "attestationId": f"pytest-target-{issued}",
            "kind": kind,
            "scheme": scheme,
            "host": host,
            "port": port,
            "databaseName": (
                database_name
                if kind == "database" and database_name is not None
                else ("test" if kind == "database" else None)
            ),
            "clusterSystemIdentifierSha256": (
                cluster_system_identifier_sha256 or sha256(TEST_CLUSTER_A.encode()).hexdigest()
                if kind == "database"
                else None
            ),
            "environmentId": environment,
            "issuedAt": start.isoformat().replace("+00:00", "Z"),
            "expiresAt": expiry.isoformat().replace("+00:00", "Z"),
            "executorId": "pytest-executor-a",
            "production": production,
        }
        payload.update(overrides or {})
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        attestation_path = tmp_path / f"target-{issued}.json"
        signature_path = tmp_path / f"target-{issued}.sig"
        attestation_path.write_bytes(raw)
        signature_path.write_bytes(base64.b64encode((signing_key or private_key).sign(raw)))
        return attestation_path, signature_path

    return issue


@pytest.mark.parametrize("module", (sustained, probe, submit))
def test_http_load_tools_fail_closed_for_localhost_without_attestation(module) -> None:
    with pytest.raises(ValueError, match="signed target attestation"):
        module.validate_target(
            "http://localhost:8000",
            allow_nonlocal=False,
            target_environment_id="local_capacity",
        )


@pytest.mark.parametrize("module", (sustained, probe, submit))
def test_http_load_tools_fail_closed_for_self_asserted_remote_without_attestation(
    module,
) -> None:
    with pytest.raises(ValueError, match="signed target attestation"):
        module.validate_target(
            "https://staging.example.cn",
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="staging.example.cn",
        )


def test_seed_load_fails_closed_for_unix_socket_without_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres@/test?host=%2Fprivate%2Ftmp%2Fsocket",
    )

    with pytest.raises(RuntimeError, match="signed target attestation"):
        seed.database_url(target_environment_id="local_capacity")


def test_seed_load_fails_closed_for_self_asserted_remote_without_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@staging.example.cn:5432/test",
    )

    with pytest.raises(RuntimeError, match="signed target attestation"):
        seed.database_url(
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="staging.example.cn",
        )


@pytest.mark.parametrize("module", (sustained, probe, submit))
def test_http_load_tools_reject_attested_production_fqdn_with_terminal_dot(
    module,
    issue_attestation: IssueAttestation,
) -> None:
    attestation, signature = issue_attestation(
        kind="http",
        scheme="https",
        host="kg.xingguoxian.cn",
        port=443,
        environment="preproduction",
    )

    with pytest.raises(ValueError, match="production"):
        module.validate_target(
            "https://KG.XINGGUOXIAN.CN.",
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="kg.xingguoxian.cn.",
            attestation_path=attestation,
            signature_path=signature,
        )


def test_seed_load_rejects_attested_production_fqdn_with_terminal_dot(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@KG.XINGGUOXIAN.CN.:5432/test",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="kg.xingguoxian.cn",
        port=5432,
        environment="preproduction",
    )

    with pytest.raises(RuntimeError, match="production"):
        seed.database_url(
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="kg.xingguoxian.cn.",
            attestation_path=attestation,
            signature_path=signature,
        )


@pytest.mark.parametrize("module", (sustained, probe, submit))
def test_http_load_tools_accept_signed_preproduction_identity(
    module,
    issue_attestation: IssueAttestation,
) -> None:
    attestation, signature = issue_attestation(
        kind="http",
        scheme="https",
        host="staging.example.cn",
        port=443,
        environment="preproduction",
    )

    module.validate_target(
        "https://Staging.Example.CN.",
        allow_nonlocal=True,
        target_environment_id="preproduction",
        expected_target_host="staging.example.cn",
        attestation_path=attestation,
        signature_path=signature,
    )


@pytest.mark.parametrize("module", (sustained, probe, submit))
def test_http_load_tools_accept_signed_loopback_identity(
    module,
    issue_attestation: IssueAttestation,
) -> None:
    attestation, signature = issue_attestation(
        kind="http",
        scheme="http",
        host="127.0.0.1",
        port=8000,
        environment="local_capacity",
    )

    module.validate_target(
        "http://127.0.0.1:8000",
        allow_nonlocal=False,
        target_environment_id="local_capacity",
        attestation_path=attestation,
        signature_path=signature,
    )


@pytest.mark.parametrize("module", (sustained, probe, submit))
@pytest.mark.parametrize(
    "variable",
    (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ),
)
def test_http_load_tools_reject_inherited_proxy_routing(
    module,
    variable: str,
    issue_attestation: IssueAttestation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for candidate in target_safety.TARGET_OVERRIDING_HTTP_PROXY_ENVIRONMENT:
        monkeypatch.delenv(candidate, raising=False)
    monkeypatch.setenv(variable, "http://127.0.0.1:18991")
    attestation, signature = issue_attestation(
        kind="http",
        scheme="http",
        host="127.0.0.1",
        port=8000,
        environment="local_capacity",
    )

    with pytest.raises(ValueError, match="proxy environment variables are forbidden"):
        module.validate_target(
            "http://127.0.0.1:8000",
            allow_nonlocal=False,
            target_environment_id="local_capacity",
            attestation_path=attestation,
            signature_path=signature,
        )


@pytest.mark.parametrize("module", (sustained, probe, submit))
def test_http_load_tools_require_attestation_to_cover_declared_run(
    module,
    issue_attestation: IssueAttestation,
) -> None:
    now = datetime.now(UTC)
    attestation, signature = issue_attestation(
        kind="http",
        scheme="http",
        host="127.0.0.1",
        port=8000,
        environment="local_capacity",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=5),
    )

    with pytest.raises(ValueError, match="does not remain valid"):
        module.validate_target(
            "http://127.0.0.1:8000",
            allow_nonlocal=False,
            target_environment_id="local_capacity",
            attestation_path=attestation,
            signature_path=signature,
            minimum_validity_seconds=60,
        )


@pytest.mark.parametrize("module", (sustained, probe, submit))
def test_http_load_clients_disable_environment_proxy_inheritance(module) -> None:
    tree = ast.parse(inspect.getsource(module.main_async))
    clients = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "AsyncClient"
    ]
    assert len(clients) == 1
    trust_env = next(
        (keyword.value for keyword in clients[0].keywords if keyword.arg == "trust_env"),
        None,
    )
    assert isinstance(trust_env, ast.Constant)
    assert trust_env.value is False


def test_seed_load_accepts_signed_preproduction_identity(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@Staging.Example.CN.:5432/test",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="staging.example.cn",
        port=5432,
        environment="preproduction",
    )

    assert seed.database_url(
        allow_nonlocal=True,
        target_environment_id="preproduction",
        expected_target_host="staging.example.cn",
        attestation_path=attestation,
        signature_path=signature,
    ).startswith("postgresql://")


def test_seed_load_accepts_signed_explicit_unix_socket(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    socket_path = "/private/tmp/socket"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres@/test?host=%2Fprivate%2Ftmp%2Fsocket",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host=socket_path,
        port=5432,
        environment="local_capacity",
    )

    assert seed.database_url(
        target_environment_id="local_capacity",
        attestation_path=attestation,
        signature_path=signature,
    ).startswith("postgresql://")


def test_seed_load_accepts_signed_database_cluster_identity(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@127.0.0.1:5432/test",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="127.0.0.1",
        port=5432,
        environment="local_capacity",
    )
    _url, target = seed.validated_database_url(
        target_environment_id="local_capacity",
        attestation_path=attestation,
        signature_path=signature,
    )

    target_safety.validate_database_connection_identity(
        _DatabaseIdentityConnection(
            database_name="test",
            system_identifier=TEST_CLUSTER_A,
        ),
        target=target,
    )


@pytest.mark.parametrize(
    ("actual_database_name", "actual_system_identifier", "message"),
    (
        ("production_catalog", TEST_CLUSTER_A, "database name does not match"),
        ("test", TEST_CLUSTER_B, "cluster does not match"),
    ),
)
def test_seed_main_rejects_connection_identity_mismatch_before_any_write(
    actual_database_name: str,
    actual_system_identifier: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@127.0.0.1:5432/test",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="127.0.0.1",
        port=5432,
        environment="local_capacity",
    )
    output = tmp_path / "users.json"
    monkeypatch.setattr(
        seed,
        "arguments",
        lambda: SimpleNamespace(
            count=1,
            output=output,
            allow_nonlocal=False,
            target_environment_id="local_capacity",
            expected_target_host="",
            target_attestation=attestation,
            target_attestation_signature=signature,
        ),
    )
    monkeypatch.setattr(
        seed.psycopg,
        "connect",
        lambda _url: _DatabaseIdentityConnection(
            database_name=actual_database_name,
            system_identifier=actual_system_identifier,
        ),
    )

    def unexpected_seed(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("seed write primitive must not run")

    monkeypatch.setattr(seed, "seed_users_on_connection", unexpected_seed)

    with pytest.raises(RuntimeError, match=message):
        seed.main()
    assert not output.exists()


def test_seed_load_rejects_noncanonical_signed_cluster_hash(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@127.0.0.1:5432/test",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="127.0.0.1",
        port=5432,
        environment="local_capacity",
        cluster_system_identifier_sha256="A" * 64,
    )

    with pytest.raises(RuntimeError, match="cluster identity must be a SHA-256"):
        seed.database_url(
            target_environment_id="local_capacity",
            attestation_path=attestation,
            signature_path=signature,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"host": "qa.example.cn"}, "host does not match"),
        ({"port": 444}, "port does not match"),
        ({"environmentId": "staging"}, "environment does not match"),
        ({"kind": "database"}, "kind does not match"),
        ({"scheme": "http"}, "scheme does not match"),
        ({"databaseName": "production_catalog"}, "database name does not match"),
        (
            {"clusterSystemIdentifierSha256": "f" * 64},
            "cluster identity must be null",
        ),
        ({"executorId": "pytest-executor-b"}, "executor identity does not match"),
        ({"production": True}, "production=false"),
    ),
)
def test_signed_attestation_binds_every_target_identity_field(
    overrides: dict[str, Any],
    message: str,
    issue_attestation: IssueAttestation,
) -> None:
    attestation, signature = issue_attestation(
        kind="http",
        scheme="https",
        host="staging.example.cn",
        port=443,
        environment="preproduction",
        overrides=overrides,
    )

    with pytest.raises(ValueError, match=message):
        sustained.validate_target(
            "https://staging.example.cn",
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="staging.example.cn",
            attestation_path=attestation,
            signature_path=signature,
        )


def test_attestation_rejects_tampering_and_untrusted_signer(
    issue_attestation: IssueAttestation,
) -> None:
    attestation, signature = issue_attestation(
        kind="http",
        scheme="https",
        host="staging.example.cn",
        port=443,
        environment="preproduction",
    )
    attestation.write_bytes(attestation.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="signature verification failed"):
        sustained.validate_target(
            "https://staging.example.cn",
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="staging.example.cn",
            attestation_path=attestation,
            signature_path=signature,
        )

    untrusted_key = Ed25519PrivateKey.generate()
    attestation, signature = issue_attestation(
        kind="http",
        scheme="https",
        host="staging.example.cn",
        port=443,
        environment="preproduction",
        signing_key=untrusted_key,
    )
    with pytest.raises(ValueError, match="signature verification failed"):
        sustained.validate_target(
            "https://staging.example.cn",
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="staging.example.cn",
            attestation_path=attestation,
            signature_path=signature,
        )


def test_attestation_rejects_expired_or_long_lived_grants(
    issue_attestation: IssueAttestation,
) -> None:
    now = datetime.now(UTC)
    expired, expired_signature = issue_attestation(
        kind="http",
        scheme="https",
        host="staging.example.cn",
        port=443,
        environment="preproduction",
        issued_at=now - timedelta(minutes=5),
        expires_at=now - timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="expired"):
        sustained.validate_target(
            "https://staging.example.cn",
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="staging.example.cn",
            attestation_path=expired,
            signature_path=expired_signature,
        )

    long_lived, long_lived_signature = issue_attestation(
        kind="http",
        scheme="https",
        host="staging.example.cn",
        port=443,
        environment="preproduction",
        issued_at=now,
        expires_at=now + timedelta(minutes=16),
    )
    with pytest.raises(ValueError, match="lifetime exceeds"):
        sustained.validate_target(
            "https://staging.example.cn",
            allow_nonlocal=True,
            target_environment_id="preproduction",
            expected_target_host="staging.example.cn",
            attestation_path=long_lived,
            signature_path=long_lived_signature,
        )


def test_database_url_rejects_ambiguous_unattestable_target(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres@/test",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="/private/tmp/socket",
        port=5432,
        environment="local_capacity",
    )

    with pytest.raises(RuntimeError, match="explicit host or Unix socket"):
        seed.database_url(
            target_environment_id="local_capacity",
            attestation_path=attestation,
            signature_path=signature,
        )


def test_seed_load_rejects_same_server_cross_database_attestation_replay(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@127.0.0.1:5432/production_catalog",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="127.0.0.1",
        port=5432,
        database_name="staging_catalog",
        environment="local_capacity",
    )

    with pytest.raises(RuntimeError, match="database name does not match"):
        seed.database_url(
            target_environment_id="local_capacity",
            attestation_path=attestation,
            signature_path=signature,
        )


def test_database_url_rejects_query_database_name_override(
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@127.0.0.1:5432/test?dbname=production_catalog",
    )
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="127.0.0.1",
        port=5432,
        environment="local_capacity",
    )

    with pytest.raises(RuntimeError, match="dbname overrides are forbidden"):
        seed.database_url(
            target_environment_id="local_capacity",
            attestation_path=attestation,
            signature_path=signature,
        )


@pytest.mark.parametrize(
    "variable",
    (
        "PGHOSTADDR",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
    ),
)
def test_seed_load_rejects_libpq_environment_target_override_before_connect(
    variable: str,
    monkeypatch: pytest.MonkeyPatch,
    issue_attestation: IssueAttestation,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@127.0.0.1:5432/test",
    )
    monkeypatch.setenv(variable, "attacker-controlled")
    attestation, signature = issue_attestation(
        kind="database",
        scheme="postgresql",
        host="127.0.0.1",
        port=5432,
        environment="local_capacity",
    )

    with pytest.raises(RuntimeError, match="target-overriding libpq"):
        seed.database_url(
            target_environment_id="local_capacity",
            attestation_path=attestation,
            signature_path=signature,
        )
