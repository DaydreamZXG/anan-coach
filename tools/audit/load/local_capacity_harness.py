#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import importlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types
from typing import Any, Iterator
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx
import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _load_target_safety() -> Any:
    """Load the sibling safety authority without depending on the caller's cwd."""

    module_path = Path(__file__).resolve().with_name("target_safety.py")
    specification = importlib.util.spec_from_file_location(
        "anan_local_capacity_target_safety",
        module_path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("local capacity target-safety module cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


reject_target_overriding_libpq_environment = (
    _load_target_safety().reject_target_overriding_libpq_environment
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
BACKEND_TESTS_ROOT = BACKEND_ROOT / "tests"
DEFAULT_OUTPUT_DIRECTORY = Path(
    "/Users/zxg/Documents/新项目/audit/commercial-repair/overnight-20260730-010444/capacity-local"
)
PRODUCTION_HOSTS = frozenset({"kg.xingguoxian.cn", "img.xingguoxian.cn"})
LOCAL_API_APPLICATION_NAME = "anan_capacity_api"
LOCAL_MONITOR_APPLICATION_NAME = "anan_capacity_monitor"
QUESTION_COUNT = 6
LOCAL_APPLICATION_COMMIT = sha256(
    b"anan-local-capacity-application-commit@v1"
).hexdigest()[:40]
LOCAL_ROLLBACK_APPLICATION_COMMIT = sha256(
    b"anan-local-capacity-rollback-application-commit@v1"
).hexdigest()[:40]
LOCAL_PREVIOUS_RECEIPT_SHA256 = sha256(
    b"anan-local-capacity-previous-receipt@v1"
).hexdigest()
LOCAL_PUBLICATION_ID = "00000000-0000-4000-8000-000000000001"


@dataclass(frozen=True, slots=True)
class LoadUser:
    index: int
    user_id: str
    access_token: str


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    payload: Any
    elapsed_ms: float
    error: str | None


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 2)


def latency_summary(values: list[float]) -> dict[str, float | None]:
    return {
        "min": round(min(values), 2) if values else None,
        "mean": round(statistics.fmean(values), 2) if values else None,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 2) if values else None,
    }


def summarize_requests(
    records: list[dict[str, Any]],
    *,
    wall_seconds: float,
    peak_in_flight: int,
) -> dict[str, Any]:
    latencies = [float(item["elapsedMs"]) for item in records]
    by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_step[str(record["step"])].append(record)
    step_summaries: dict[str, Any] = {}
    for step, items in sorted(by_step.items()):
        step_latencies = [float(item["elapsedMs"]) for item in items]
        errors = sum(
            int(
                item["status"] < 200
                or item["status"] >= 300
                or item.get("error") is not None
            )
            for item in items
        )
        step_summaries[step] = {
            "requests": len(items),
            "statusCounts": dict(Counter(str(item["status"]) for item in items)),
            "errors": errors,
            "timeouts": sum(int(item.get("timeout", False)) for item in items),
            "latencyMs": latency_summary(step_latencies),
        }
    errors = sum(
        int(
            item["status"] < 200
            or item["status"] >= 300
            or item.get("error") is not None
        )
        for item in records
    )
    return {
        "requests": len(records),
        "wallSeconds": round(wall_seconds, 3),
        "requestsPerSecond": (
            round(len(records) / wall_seconds, 2) if wall_seconds > 0 else None
        ),
        "peakInFlightRequests": peak_in_flight,
        "statusCounts": dict(Counter(str(item["status"]) for item in records)),
        "errors": errors,
        "errorRate": round(errors / len(records), 6) if records else 0,
        "timeouts": sum(int(item.get("timeout", False)) for item in records),
        "latencyMs": latency_summary(latencies),
        "steps": step_summaries,
    }


def is_loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    if parsed.hostname.casefold() in PRODUCTION_HOSTS:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return parsed.hostname.casefold() == "localhost"


def safe_child_environment(
    *,
    database_url: str,
    api_base_url: str,
    pool_size: int,
    max_overflow: int,
    application_git_commit: str,
    data_receipt_sha256: str,
    pool_timeout_seconds: float = 12.0,
) -> dict[str, str]:
    if not is_loopback_url(api_base_url):
        raise ValueError("local capacity API must bind to a loopback HTTP URL")
    parsed_database = urlparse(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    )
    if parsed_database.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("local capacity database must bind to loopback")
    inherited = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
        if os.environ.get(key)
    }
    return {
        **inherited,
        "PYTHONUNBUFFERED": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "APP_ENV": "local_capacity",
        "APP_GIT_COMMIT": application_git_commit,
        "APP_DATA_RECEIPT_SHA256": data_receipt_sha256,
        "COMMERCIAL_MODE": "free_cold_start",
        "USAGE_ACCESS_MODE": "unlimited",
        "API_BASE_URL": api_base_url,
        "DATABASE_URL": database_url,
        "DATABASE_POOL_SIZE": str(pool_size),
        "DATABASE_MAX_OVERFLOW": str(max_overflow),
        "DATABASE_POOL_TIMEOUT_SECONDS": str(pool_timeout_seconds),
        "DATABASE_STATEMENT_TIMEOUT_MS": "15000",
        "DATABASE_LOCK_TIMEOUT_MS": "3000",
        "DATABASE_IDLE_TRANSACTION_TIMEOUT_MS": "120000",
        "OPERATIONS_METRICS_TOKEN": "local-capacity-metrics-only",
        # These local-only values permit deterministic signing and enqueue-time
        # identity capture. The harness never uploads bytes, completes an upload,
        # starts a worker, or performs provider I/O.
        "TOS_BUCKET": "anan-local-capacity",
        "TOS_REGION": "local-capacity-1",
        "TOS_ENDPOINT": "https://tos.local.invalid",
        "TOS_PRESIGN_ENDPOINT": "https://tos.local.invalid",
        "TOS_ACCESS_KEY": "local-capacity-access-key",
        "TOS_SECRET_KEY": "local-capacity-secret-key",
        "TOS_QUARANTINE_PREFIX": "private/quarantine/local-capacity",
        "UPLOAD_QUOTA_POLICY_VERSION": "local-capacity-upload-quota@v1",
        "UPLOAD_MAXIMUM_PENDING_BYTES_PER_USER": str(10 * 1024 * 1024),
        "UPLOAD_MAXIMUM_STORED_BYTES_PER_USER": str(10 * 1024 * 1024),
        "UPLOAD_SCANNER_BASE_URL": "https://scanner.local.invalid",
        "UPLOAD_SCANNER_API_KEY": "local-capacity-scanner-key",
        "UPLOAD_SCANNER_RELEASE_ID": "local-capacity-scanner-v1",
        "UPLOAD_SCANNER_HTTPS_HOST_ALLOWLIST": "scanner.local.invalid",
        "UPLOAD_SCANNER_ROUTE": "local-capacity-scanner",
        "UPLOAD_SCANNER_POLICY_VERSION": "local-capacity-scanner-policy@v1",
        "UPLOAD_SCANNER_DPA_VERSION": "local-capacity-no-external-processing",
        "UPLOAD_SCANNER_MAINLAND_ONLY": "true",
        "UPLOAD_SCANNER_MAXIMUM_DEFINITION_AGE_SECONDS": "86400",
        "AI_XINGCE_CAUSE_MODEL": "local-capacity-never-invoked",
        "AI_XINGCE_CAUSE_PROVIDER_RELEASE_ID": "local-capacity-never-invoked-v1",
        "AI_PROVIDER_BASE_URL": "https://provider.local.invalid/v1",
        "AI_API_KEY": "",
        "RECOGNITION_PROCESSOR_ENABLED": "false",
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 180,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


class LocalPostgres:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data_directory = root / "postgres-data"
        self.log_file = root / "postgres.log"
        self.port = _free_port()
        self.initdb = shutil.which("initdb")
        self.pg_ctl = shutil.which("pg_ctl")
        if self.initdb is None or self.pg_ctl is None:
            raise RuntimeError("local initdb and pg_ctl are required")
        self.started = False

    @property
    def psycopg_url(self) -> str:
        return f"postgresql://postgres@127.0.0.1:{self.port}/postgres"

    @property
    def sqlalchemy_url(self) -> str:
        return f"postgresql+psycopg://postgres@127.0.0.1:{self.port}/postgres"

    @property
    def application_url(self) -> str:
        query = urlencode({"application_name": LOCAL_API_APPLICATION_NAME})
        return f"{self.sqlalchemy_url}?{query}"

    def start(self) -> None:
        _run_checked(
            [
                self.initdb or "",
                "-D",
                str(self.data_directory),
                "--auth=trust",
                "--username=postgres",
                "--no-sync",
            ],
            cwd=REPOSITORY_ROOT,
        )
        _run_checked(
            [
                self.pg_ctl or "",
                "-D",
                str(self.data_directory),
                "-l",
                str(self.log_file),
                "-o",
                (
                    f"-p {self.port} -h 127.0.0.1 "
                    "-c unix_socket_directories='' "
                    "-c max_connections=160 "
                    "-c track_io_timing=on "
                    "-c log_lock_waits=on "
                    "-c deadlock_timeout=200ms"
                ),
                "start",
                "-w",
            ],
            cwd=REPOSITORY_ROOT,
        )
        self.started = True

    def stop(self) -> None:
        if not self.started:
            return
        subprocess.run(
            [
                self.pg_ctl or "",
                "-D",
                str(self.data_directory),
                "stop",
                "-m",
                "fast",
                "-w",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        self.started = False


@contextmanager
def local_postgres(root: Path) -> Iterator[LocalPostgres]:
    postgres = LocalPostgres(root)
    postgres.start()
    try:
        yield postgres
    finally:
        postgres.stop()


def _install_local_tests_package() -> None:
    backend = str(BACKEND_ROOT)
    if backend not in sys.path:
        sys.path.insert(0, backend)
    existing = sys.modules.get("tests")
    if existing is not None:
        paths = [str(item) for item in getattr(existing, "__path__", ())]
        if str(BACKEND_TESTS_ROOT) not in paths:
            for name in tuple(sys.modules):
                if name == "tests" or name.startswith("tests."):
                    del sys.modules[name]
    if "tests" not in sys.modules:
        package = types.ModuleType("tests")
        package.__path__ = [str(BACKEND_TESTS_ROOT)]  # type: ignore[attr-defined]
        package.__package__ = "tests"
        sys.modules["tests"] = package


def bootstrap_schema(database_url: str) -> str:
    _install_local_tests_package()
    conftest = importlib.import_module("tests.conftest")
    # Migration 84+ deliberately requires pre-provisioned heartbeat principals.
    # This helper only targets the harness-created loopback cluster and mirrors
    # the isolated PostgreSQL fixture setup before Alembic runs.
    conftest._provision_test_heartbeat_roles(database_url)
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        conftest._reset_test_schema(engine)
        with engine.connect() as connection:
            return str(
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def establish_local_publication_authority(
    database_url: str,
    *,
    expected_revision: str,
) -> dict[str, str]:
    """Create one exact active authority in the isolated loopback database."""

    parsed_database = urlparse(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    )
    if parsed_database.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("local publication authority requires a loopback database")
    request = {
        "schemaVersion": "commercial-data-publication-request@v1",
        "deploymentEnvironment": "local_capacity",
        "expectedDatabaseRevision": expected_revision,
        "candidateApplicationCommit": LOCAL_APPLICATION_COMMIT,
        "rollbackApplicationCommit": LOCAL_ROLLBACK_APPLICATION_COMMIT,
        "scope": "isolated-temporary-postgresql",
    }
    request_digest = sha256(_canonical_json_bytes(request)).hexdigest()
    active_receipt = sha256(
        _canonical_json_bytes(
            {
                "schemaVersion": "anan-local-capacity-data-receipt@v1",
                "requestDigest": request_digest,
                "scope": "isolated-temporary-postgresql",
            }
        )
    ).hexdigest()
    publication_digest = sha256(
        _canonical_json_bytes(
            {
                "schemaVersion": "commercial-data-publication@v1",
                "requestDigest": request_digest,
                "activeReceiptSha256": active_receipt,
            }
        )
    ).hexdigest()
    psycopg_url = database_url.replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )
    with psycopg.connect(psycopg_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select version_num from public.alembic_version order by version_num"
            )
            revisions = [str(row[0]) for row in cursor.fetchall()]
            if revisions != [expected_revision]:
                raise RuntimeError(
                    "local publication revision does not match the migrated database"
                )
            cursor.execute(
                "select count(*) from platform.commercial_data_publication_state"
            )
            if cursor.fetchone() != (0,):
                raise RuntimeError("local publication authority must start empty")
            cursor.execute(
                "select set_config("
                "'gongkao.commercial_data_publication_writer', "
                "'publisher-v1', true)"
            )
            parameters = {
                "publication_id": LOCAL_PUBLICATION_ID,
                "request_digest": request_digest,
                "request_payload": json.dumps(
                    request,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "publication_digest": publication_digest,
                "previous_receipt": LOCAL_PREVIOUS_RECEIPT_SHA256,
                "active_receipt": active_receipt,
                "expected_revision": expected_revision,
                "compatible_commits": json.dumps(
                    [
                        LOCAL_APPLICATION_COMMIT,
                        LOCAL_ROLLBACK_APPLICATION_COMMIT,
                    ],
                    separators=(",", ":"),
                ),
            }
            cursor.execute(
                """
                insert into platform.commercial_data_publication_state (
                  singleton_key,
                  publication_id,
                  state,
                  request_digest,
                  request_payload,
                  publication_digest,
                  previous_receipt_sha256,
                  active_receipt_sha256,
                  expected_database_revision,
                  compatible_application_commits,
                  publication_started_at,
                  state_changed_at,
                  activated_at
                ) values (
                  true,
                  %(publication_id)s::uuid,
                  'active',
                  %(request_digest)s,
                  %(request_payload)s::jsonb,
                  %(publication_digest)s,
                  %(previous_receipt)s,
                  %(active_receipt)s,
                  %(expected_revision)s,
                  %(compatible_commits)s::jsonb,
                  transaction_timestamp(),
                  transaction_timestamp(),
                  transaction_timestamp()
                )
                """,
                parameters,
            )
            cursor.execute(
                """
                insert into platform.commercial_data_publication_transitions (
                  publication_id,
                  event_kind,
                  from_state,
                  to_state,
                  request_digest,
                  request_payload,
                  publication_digest,
                  previous_receipt_sha256,
                  active_receipt_sha256,
                  expected_database_revision,
                  compatible_application_commits
                ) values (
                  %(publication_id)s::uuid,
                  'publication_activated',
                  'publishing',
                  'active',
                  %(request_digest)s,
                  %(request_payload)s::jsonb,
                  %(publication_digest)s,
                  %(previous_receipt)s,
                  %(active_receipt)s,
                  %(expected_revision)s,
                  %(compatible_commits)s::jsonb
                )
                """,
                parameters,
            )
    return {
        "schemaVersion": "anan-local-capacity-publication-authority@v1",
        "applicationGitCommit": LOCAL_APPLICATION_COMMIT,
        "rollbackApplicationGitCommit": LOCAL_ROLLBACK_APPLICATION_COMMIT,
        "dataReceiptSha256": active_receipt,
        "requestDigest": request_digest,
        "publicationDigest": publication_digest,
        "expectedDatabaseRevision": expected_revision,
        "scope": "isolated-temporary-postgresql",
    }


def seed_world(
    database_url: str,
    *,
    user_count: int,
    position_count: int,
    run_id: str,
) -> tuple[list[LoadUser], dict[str, Any]]:
    _install_local_tests_package()
    planning = importlib.import_module("tests.test_planning_routes")
    frozen_release = importlib.import_module("tests.factories.frozen_release")
    qualification_releases = importlib.import_module(
        "app.domains.qualification.rule_releases"
    )
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    first_token = f"anan-local-capacity-{run_id}-000-{uuid4().hex}"
    first_user_id = planning._seed_learner_and_published_essay_content(
        factory,
        access_token=first_token,
        include_objective_questions=False,
    )
    release = frozen_release.seed_route_frozen_release(
        factory,
        count=QUESTION_COUNT,
    )
    users = [LoadUser(index=0, user_id=first_user_id, access_token=first_token)]
    seed_invocation: dict[str, Any] | None = None
    if user_count > 1:
        seed_module_path = REPOSITORY_ROOT / "tools/audit/load/seed_load_users.py"
        specification = importlib.util.spec_from_file_location(
            "anan_local_capacity_seed_users",
            seed_module_path,
        )
        if specification is None or specification.loader is None:
            raise RuntimeError("seed_load_users module cannot be loaded")
        seed_module = importlib.util.module_from_spec(specification)
        sys.path.insert(0, str(seed_module_path.parent))
        try:
            specification.loader.exec_module(seed_module)
        finally:
            sys.path.pop(0)
        with psycopg.connect(
            database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        ) as seed_connection:
            manifest = seed_module.seed_users_on_connection(
                seed_connection,
                count=user_count - 1,
            )
        for index, record in enumerate(manifest["users"], start=1):
            users.append(
                LoadUser(
                    index=index,
                    user_id=str(record["userId"]),
                    access_token=str(record["accessToken"]),
                )
            )
        seed_invocation = {
            "primitive": "seed_users_on_connection",
            "count": user_count - 1,
            "connectionAuthority": (
                "caller-owned connection to harness-created temporary PostgreSQL"
            ),
            "externalTargetAccepted": False,
        }
    positions = [
        {
            "position_id": str(uuid4()),
            "source_id": f"local-capacity-position-{run_id}-{index:04d}",
            "position_code": f"LC{index:010d}",
            "name": f"本地容量岗位-{index:04d}",
            "department": f"本地容量测试部门-{index % 20:02d}",
            "city": f"南昌市-本地分区-{index % 10:02d}",
            "headcount": 1 + (position_count - index),
        }
        for index in range(position_count)
    ]
    position_data_version = f"local-capacity-position-data-{run_id}"
    rule_set_version = f"local-capacity-position-rules-{run_id}"
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.positions (
                  position_id, source_id, year, position_code, name,
                  department_full, system_code, region, city, paper_type,
                  headcount, education_min, major_required, political_req,
                  service_min_yrs, hukou_req, remark, raw_json
                ) values (
                  cast(:position_id as uuid), :source_id, 2026, :position_code,
                  :name, :department, 'local-capacity', '江西', :city,
                  'administrativeLaw', :headcount, '本科', '法学', '不限',
                  0, '不限', '无特殊要求', '{}'::jsonb
                )
                """
            ),
            positions,
        )
        session.execute(
            text(
                """
                insert into product.position_import_batches (
                  position_data_version,
                  source_sha256,
                  position_count,
                  imported_at
                ) values (
                  :position_data_version,
                  :source_sha256,
                  :position_count,
                  transaction_timestamp()
                )
                """
            ),
            {
                "position_data_version": position_data_version,
                "source_sha256": sha256(_canonical_json_bytes(positions)).hexdigest(),
                "position_count": position_count,
            },
        )
        session.execute(
            text(
                """
                insert into product.position_snapshots (
                  position_id,
                  position_data_version,
                  source_id,
                  year,
                  position_code,
                  name,
                  department_full,
                  system_code,
                  region,
                  city,
                  paper_type,
                  headcount,
                  education_min,
                  major_required,
                  political_req,
                  hukou_req,
                  service_min_yrs,
                  has_extra_exam,
                  remark,
                  raw_json,
                  source_fingerprint,
                  captured_at
                ) values (
                  cast(:position_id as uuid),
                  :position_data_version,
                  :source_id,
                  2026,
                  :position_code,
                  :name,
                  :department,
                  'local-capacity',
                  '江西',
                  :city,
                  'administrativeLaw',
                  :headcount,
                  '本科',
                  '法学',
                  '不限',
                  '不限',
                  0,
                  false,
                  '无特殊要求',
                  '{}'::jsonb,
                  :source_fingerprint,
                  transaction_timestamp()
                )
                """
            ),
            [
                {
                    **position,
                    "position_data_version": position_data_version,
                    "source_fingerprint": sha256(
                        _canonical_json_bytes(position)
                    ).hexdigest(),
                }
                for position in positions
            ],
        )
    activation_time = datetime.now(UTC)
    release_service = qualification_releases.PositionRuleReleaseService()
    with factory.begin() as session:
        position_release_id = release_service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
        )
        release_service.compile_draft(
            session,
            release_id=position_release_id,
            compiled_at=activation_time,
        )
        release_service.configure_windows(
            session,
            release_id=position_release_id,
            windows=[
                qualification_releases.PositionReleaseWindowInput(
                    kind="primary",
                    sequence_no=1,
                    opens_at=activation_time - timedelta(days=1),
                    closes_at=activation_time + timedelta(days=30),
                    official_source_url=(
                        "https://local.invalid/isolated-capacity/position-window"
                    ),
                    official_source_title="本地隔离容量演练岗位窗口",
                    official_source_published_at=activation_time - timedelta(days=3),
                    official_source_received_at=activation_time - timedelta(days=2),
                    official_source_sha256=sha256(
                        b"anan-local-capacity-position-window@v1"
                    ).hexdigest(),
                    official_source_receipt=("isolated-local-capacity-fixture-receipt"),
                )
            ],
        )
        release_service.activate(
            session,
            release_id=position_release_id,
            activated_at=activation_time,
        )
    with factory() as session:
        counts = {
            "loadUsers": int(
                session.execute(
                    text(
                        "select count(*) from learner.users "
                        "where display_name like '压测账号-%' or user_id = cast(:first as uuid)"
                    ),
                    {"first": first_user_id},
                ).scalar_one()
            ),
            "positions": int(
                session.execute(
                    text(
                        "select count(*) from product.positions "
                        "where source_id like 'local-capacity-position-%'"
                    )
                ).scalar_one()
            ),
            "questions": int(
                session.execute(
                    text(
                        "select count(*) from product.questions "
                        "where question_id like 'xingce-route-question-%'"
                    )
                ).scalar_one()
            ),
        }
    engine.dispose()
    if len(users) != user_count or counts["loadUsers"] != user_count:
        raise RuntimeError("local user seed count drifted")
    return users, {
        "source": (
            "real PostgreSQL fixture bootstrap plus existing "
            "seed_load_users.seed_users_on_connection"
        ),
        "seedInvocation": seed_invocation,
        "knowledgeRelease": release.release_version,
        "knowledgeSourceDataVersion": release.source_data_version,
        "positionReleaseId": position_release_id,
        "positionRuleSetVersion": rule_set_version,
        "positionDataVersion": position_data_version,
        "counts": counts,
    }


class RequestRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def request(
        self,
        client: httpx.AsyncClient,
        *,
        user_index: int,
        step: str,
        method: str,
        path: str,
        token: str,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        request_headers = {"Authorization": f"Bearer {token}"}
        if idempotency_key is not None:
            request_headers["Idempotency-Key"] = idempotency_key
        request_headers.update(headers or {})
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        started = time.perf_counter()
        response: httpx.Response | None = None
        payload: Any = None
        error: str | None = None
        timeout = False
        try:
            response = await client.request(
                method,
                path,
                headers=request_headers,
                json=body,
            )
            try:
                payload = response.json()
            except Exception:
                payload = response.text[:1000]
            status = response.status_code
        except Exception as exc:
            status = 0
            timeout = isinstance(exc, httpx.TimeoutException)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            self.in_flight -= 1
        response_bytes = (
            response.content if response is not None else (error or "").encode("utf-8")
        )
        error_body = None
        if response is not None and not 200 <= response.status_code < 300:
            error_body = response.text[:1000]
        self.records.append(
            {
                "userIndex": user_index,
                "step": step,
                "method": method,
                "path": path,
                "status": status,
                "elapsedMs": elapsed_ms,
                "timeout": timeout,
                "error": error,
                "errorBody": error_body,
                "requestBodySha256": (
                    sha256(
                        json.dumps(
                            body,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    if body is not None
                    else None
                ),
                "responseBytes": len(response_bytes),
                "responseSha256": sha256(response_bytes).hexdigest(),
                "requestId": (
                    response.headers.get("X-Request-ID")
                    if response is not None
                    else None
                ),
            }
        )
        return HttpResult(
            status=status,
            payload=payload,
            elapsed_ms=elapsed_ms,
            error=error,
        )


def _data(result: HttpResult) -> dict[str, Any] | None:
    if result.status != 200 or not isinstance(result.payload, dict):
        return None
    data = result.payload.get("data")
    return data if isinstance(data, dict) else None


async def _duplicate(
    recorder: RequestRecorder,
    client: httpx.AsyncClient,
    *,
    user_index: int,
    step: str,
    method: str,
    path: str,
    token: str,
    body: dict[str, Any],
    idempotency_key: str,
    headers: dict[str, str] | None = None,
) -> tuple[HttpResult, HttpResult]:
    return tuple(
        await asyncio.gather(
            *(
                recorder.request(
                    client,
                    user_index=user_index,
                    step=f"{step}_{suffix}",
                    method=method,
                    path=path,
                    token=token,
                    body=body,
                    idempotency_key=idempotency_key,
                    headers=headers,
                )
                for suffix in ("primary", "duplicate")
            )
        )
    )  # type: ignore[return-value]


async def virtual_user(
    client: httpx.AsyncClient,
    *,
    user: LoadUser,
    recorder: RequestRecorder,
    gate: asyncio.Event,
    run_id: str,
) -> dict[str, Any]:
    await gate.wait()
    outcome: dict[str, Any] = {
        "userIndex": user.index,
        "completed": False,
        "failureStep": None,
        "duplicateStartStable": False,
        "duplicateCheckpointStable": False,
        "duplicateSubmitStable": False,
        "duplicateReflectionStable": False,
        "positionItemCount": None,
        "uploadSessionId": None,
        "exportRequestId": None,
    }

    async def fail(step: str) -> dict[str, Any]:
        outcome["failureStep"] = step
        return outcome

    authenticated = await recorder.request(
        client,
        user_index=user.index,
        step="session_auth",
        method="GET",
        path="/api/v1/auth/me",
        token=user.access_token,
    )
    if authenticated.status != 200:
        return await fail("session_auth")
    today = await recorder.request(
        client,
        user_index=user.index,
        step="today",
        method="GET",
        path="/api/v2/plans/today",
        token=user.access_token,
    )
    today_data = _data(today)
    if today_data is None:
        return await fail("today")
    task = next(
        (
            item
            for item in today_data.get("tasks", [])
            if isinstance(item, dict) and item.get("track") == "xingce"
        ),
        None,
    )
    if task is None:
        return await fail("today_xingce_task")
    start_body = {"planTaskId": task["id"]}
    start_key = f"local-capacity-start-{run_id}-{user.index:03d}"
    starts = await _duplicate(
        recorder,
        client,
        user_index=user.index,
        step="start",
        method="POST",
        path="/api/v2/xingce/task-sessions",
        token=user.access_token,
        body=start_body,
        idempotency_key=start_key,
    )
    start_data = [_data(item) for item in starts]
    if any(item is None for item in start_data):
        return await fail("start")
    first_start = start_data[0] or {}
    second_start = start_data[1] or {}
    outcome["duplicateStartStable"] = first_start.get("sessionId") == second_start.get(
        "sessionId"
    ) and first_start.get("revision") == second_start.get("revision")
    session_id = str(first_start["sessionId"])
    lease = await recorder.request(
        client,
        user_index=user.index,
        step="lease",
        method="POST",
        path="/api/v2/task-editing/leases",
        token=user.access_token,
        body={
            "resourceType": "xingce_task_session",
            "resourceId": session_id,
            "deviceId": f"anan-local-capacity-device-{run_id}-{user.index:03d}",
            "deviceLabel": "岸岸本地容量设备",
            "localBaseRevision": first_start["revision"],
            "hasLocalChanges": False,
        },
    )
    lease_data = _data(lease)
    if lease_data is None:
        return await fail("lease")
    lease_headers = {
        "X-Task-Edit-Lease": str(lease_data["leaseId"]),
        "X-Task-Edit-Generation": str(lease_data["generation"]),
    }
    questions = first_start.get("bundle", {}).get("questions", [])
    if not questions:
        return await fail("start_bundle")
    answers: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        selected = (
            [question["options"][0]["optionId"]]
            if index == 0 and question.get("options")
            else []
        )
        answers.append(
            {
                "questionId": question["questionId"],
                "selectedOptionIds": selected,
                "eliminatedOptionIds": [],
                "confidence": "low" if selected else None,
                "marked": False,
                "skipped": not selected,
                "elapsedSeconds": 1 if selected else 0,
                "draftText": "",
                "materialPosition": None,
                "annotations": [],
                "actionEvents": [],
            }
        )
    checkpoint_body = {
        "expectedRevision": first_start["revision"],
        "currentQuestionId": questions[-1]["questionId"],
        "answers": answers,
        "totalElapsedSeconds": 1,
    }
    checkpoint_key = f"local-capacity-checkpoint-{run_id}-{user.index:03d}"
    checkpoints = await _duplicate(
        recorder,
        client,
        user_index=user.index,
        step="checkpoint",
        method="PUT",
        path=f"/api/v2/xingce/task-sessions/{session_id}/checkpoint",
        token=user.access_token,
        body=checkpoint_body,
        idempotency_key=checkpoint_key,
        headers=lease_headers,
    )
    checkpoint_data = [_data(item) for item in checkpoints]
    if any(item is None for item in checkpoint_data):
        return await fail("checkpoint")
    first_checkpoint = checkpoint_data[0] or {}
    second_checkpoint = checkpoint_data[1] or {}
    outcome["duplicateCheckpointStable"] = first_checkpoint.get(
        "sessionId"
    ) == second_checkpoint.get("sessionId") and first_checkpoint.get(
        "revision"
    ) == second_checkpoint.get("revision")
    submit_body = {
        "expectedRevision": first_checkpoint["revision"],
        "totalElapsedSeconds": first_checkpoint["totalElapsedSeconds"],
    }
    submit_key = f"local-capacity-submit-{run_id}-{user.index:03d}"
    submits = await _duplicate(
        recorder,
        client,
        user_index=user.index,
        step="submit",
        method="POST",
        path=f"/api/v2/xingce/task-sessions/{session_id}/submit",
        token=user.access_token,
        body=submit_body,
        idempotency_key=submit_key,
        headers=lease_headers,
    )
    submit_data = [_data(item) for item in submits]
    if any(item is None for item in submit_data):
        return await fail("submit")
    first_submit = submit_data[0] or {}
    second_submit = submit_data[1] or {}
    outcome["duplicateSubmitStable"] = (
        first_submit.get("sessionId") == second_submit.get("sessionId")
        and first_submit.get("revision") == second_submit.get("revision")
        and first_submit.get("review", {}).get("reviewId")
        == second_submit.get("review", {}).get("reviewId")
    )
    review = first_submit.get("review")
    if not isinstance(review, dict):
        return await fail("submit_review")
    reflection_body = {
        "expectedRevision": review["revision"],
        "rawText": "本地容量演练：我需要重新核对题干条件。",
        "tags": ["审题"],
        "strategyNote": "先圈限定词再作答。",
        "doubtNote": None,
        "voiceArtifactVersionIds": [],
        "itemInterpretations": [],
    }
    reflection_key = f"local-capacity-reflection-{run_id}-{user.index:03d}"
    reflections = await _duplicate(
        recorder,
        client,
        user_index=user.index,
        step="reflection_enqueue",
        method="PUT",
        path=(f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection"),
        token=user.access_token,
        body=reflection_body,
        idempotency_key=reflection_key,
    )
    reflection_data = [_data(item) for item in reflections]
    if any(item is None for item in reflection_data):
        return await fail("reflection_enqueue")
    first_reflection = reflection_data[0] or {}
    second_reflection = reflection_data[1] or {}
    outcome["duplicateReflectionStable"] = first_reflection.get("review", {}).get(
        "reviewId"
    ) == second_reflection.get("review", {}).get("reviewId") and first_reflection.get(
        "review", {}
    ).get("revision") == second_reflection.get("review", {}).get("revision")
    position = await recorder.request(
        client,
        user_index=user.index,
        step="position_filter",
        method="GET",
        path=(
            "/api/v2/positions?examYear=2026&region=%E6%B1%9F%E8%A5%BF"
            "&city=%E5%8D%97%E6%98%8C&limit=50"
        ),
        token=user.access_token,
    )
    position_data = _data(position)
    if position_data is None:
        return await fail("position_filter")
    position_items = position_data.get("items")
    outcome["positionItemCount"] = (
        len(position_items) if isinstance(position_items, list) else None
    )
    content = f"local-capacity-image-{run_id}-{user.index:03d}".encode("utf-8")
    upload = await recorder.request(
        client,
        user_index=user.index,
        step="upload_start",
        method="POST",
        path="/api/v2/external-learning/uploads",
        token=user.access_token,
        idempotency_key=f"local-capacity-upload-{run_id}-{user.index:03d}",
        body={
            "sourceKind": "image_import",
            "originalFilename": f"local-capacity-{user.index:03d}.jpg",
            "mimeType": "image/jpeg",
            "byteSize": len(content),
            "contentSha256": sha256(content).hexdigest(),
            "pageCount": None,
            "partSha256s": [],
        },
    )
    upload_data = _data(upload)
    if upload_data is None:
        return await fail("upload_start")
    outcome["uploadSessionId"] = upload_data.get("session", {}).get("sessionId")
    export = await recorder.request(
        client,
        user_index=user.index,
        step="job_enqueue",
        method="POST",
        path="/api/v2/data-rights/exports",
        token=user.access_token,
        body={"commandKey": f"local-capacity-export-{run_id}-{user.index:03d}"},
    )
    export_data = _data(export)
    if export_data is None:
        return await fail("job_enqueue")
    outcome["exportRequestId"] = export_data.get("exportRequestId")
    outcome["completed"] = True
    return outcome


class RuntimeMonitor:
    def __init__(
        self, *, database_url: str, api_pid: int, interval: float = 0.1
    ) -> None:
        self.database_url = database_url
        self.api_pid = api_pid
        self.interval = interval
        self.samples: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="anan-local-capacity-monitor",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            self.errors.append("monitor thread did not stop within 10 seconds")

    def _run(self) -> None:
        try:
            connection = psycopg.connect(
                self.database_url,
                autocommit=True,
                application_name=LOCAL_MONITOR_APPLICATION_NAME,
            )
        except Exception as exc:
            self.errors.append(
                f"monitor connection failed: {type(exc).__name__}: {exc}"
            )
            return
        with connection:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            select
                              count(*) filter (
                                where application_name = %s
                              ) as api_connections,
                              count(*) filter (
                                where application_name = %s
                                  and state = 'active'
                              ) as api_active,
                              count(*) filter (
                                where application_name = %s
                                  and wait_event_type = 'Lock'
                              ) as api_lock_waiters,
                              count(*) filter (
                                where application_name = %s
                                  and wait_event_type is not null
                              ) as api_waiters
                            from pg_stat_activity
                            where datname = current_database()
                            """,
                            (
                                LOCAL_API_APPLICATION_NAME,
                                LOCAL_API_APPLICATION_NAME,
                                LOCAL_API_APPLICATION_NAME,
                                LOCAL_API_APPLICATION_NAME,
                            ),
                        )
                        activity = cursor.fetchone()
                        cursor.execute(
                            """
                            select
                              count(*) filter (where status = 'queued') as queued,
                              count(*) filter (where status = 'running') as running,
                              count(*) filter (where status = 'failed') as failed,
                              coalesce(
                                extract(epoch from (
                                  now() - min(run_after) filter (
                                    where status = 'queued'
                                  )
                                )),
                                0
                              ) as oldest_queue_age
                            from platform.background_jobs
                            """
                        )
                        queue = cursor.fetchone()
                    cpu_percent, rss_bytes, process_count = _process_sample(
                        self.api_pid
                    )
                    self.samples.append(
                        {
                            "monotonicSeconds": round(time.monotonic(), 6),
                            "apiCpuPercent": cpu_percent,
                            "apiRssBytes": rss_bytes,
                            "apiProcessCount": process_count,
                            "database": {
                                "apiConnections": int(activity[0]),
                                "apiActive": int(activity[1]),
                                "apiLockWaiters": int(activity[2]),
                                "apiWaiters": int(activity[3]),
                            },
                            "jobs": {
                                "queued": int(queue[0]),
                                "running": int(queue[1]),
                                "failed": int(queue[2]),
                                "oldestQueueAgeSeconds": round(float(queue[3]), 3),
                            },
                        }
                    )
                except Exception as exc:
                    self.errors.append(f"{type(exc).__name__}: {exc}")
                elapsed = time.monotonic() - started
                self._stop.wait(max(self.interval - elapsed, 0.01))

    def summary(self) -> dict[str, Any]:
        cpu = [
            float(item["apiCpuPercent"])
            for item in self.samples
            if item["apiCpuPercent"] is not None
        ]
        rss = [
            int(item["apiRssBytes"])
            for item in self.samples
            if item["apiRssBytes"] is not None
        ]
        return {
            "sampleIntervalSeconds": self.interval,
            "samples": len(self.samples),
            "errors": self.errors,
            "apiProcess": {
                "supervisorPid": self.api_pid,
                "cpuPercent": latency_summary(cpu),
                "peakRssBytes": max(rss) if rss else None,
                "meanRssBytes": (round(statistics.fmean(rss), 2) if rss else None),
                "peakProcessCount": max(
                    (
                        int(item["apiProcessCount"])
                        for item in self.samples
                        if item["apiProcessCount"] is not None
                    ),
                    default=0,
                ),
            },
            "database": {
                "peakApiConnections": max(
                    (int(item["database"]["apiConnections"]) for item in self.samples),
                    default=0,
                ),
                "peakActiveApiConnections": max(
                    (int(item["database"]["apiActive"]) for item in self.samples),
                    default=0,
                ),
                "peakLockWaiters": max(
                    (int(item["database"]["apiLockWaiters"]) for item in self.samples),
                    default=0,
                ),
                "peakWaiters": max(
                    (int(item["database"]["apiWaiters"]) for item in self.samples),
                    default=0,
                ),
            },
            "jobs": {
                "peakQueued": max(
                    (int(item["jobs"]["queued"]) for item in self.samples),
                    default=0,
                ),
                "peakRunning": max(
                    (int(item["jobs"]["running"]) for item in self.samples),
                    default=0,
                ),
                "peakFailed": max(
                    (int(item["jobs"]["failed"]) for item in self.samples),
                    default=0,
                ),
                "maximumObservedQueueAgeSeconds": max(
                    (
                        float(item["jobs"]["oldestQueueAgeSeconds"])
                        for item in self.samples
                    ),
                    default=0.0,
                ),
            },
        }


def _process_sample(pid: int) -> tuple[float | None, int | None, int | None]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=", "-o", "ppid=", "-o", "%cpu=", "-o", "rss="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        processes: dict[int, tuple[int, float, int]] = {}
        for line in completed.stdout.splitlines():
            fields = line.split()
            if len(fields) != 4:
                continue
            process_id, parent_id = int(fields[0]), int(fields[1])
            processes[process_id] = (parent_id, float(fields[2]), int(fields[3]))
        if pid not in processes:
            return None, None, None
        selected = {pid}
        changed = True
        while changed:
            changed = False
            for process_id, (parent_id, _, _) in processes.items():
                if process_id not in selected and parent_id in selected:
                    selected.add(process_id)
                    changed = True
        cpu_percent = sum(processes[item][1] for item in selected)
        rss_bytes = sum(processes[item][2] for item in selected) * 1024
        return cpu_percent, rss_bytes, len(selected)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None, None


def database_stats(database_url: str) -> dict[str, int]:
    with psycopg.connect(
        database_url,
        application_name="anan_capacity_snapshot",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select xact_commit, xact_rollback, blks_read, blks_hit,
                       temp_files, temp_bytes, deadlocks
                from pg_stat_database
                where datname = current_database()
                """
            )
            row = cursor.fetchone()
    keys = (
        "xactCommit",
        "xactRollback",
        "blocksRead",
        "blocksHit",
        "tempFiles",
        "tempBytes",
        "deadlocks",
    )
    return dict(zip(keys, (int(value) for value in row), strict=True))


def database_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: after[key] - before[key] for key in before}


def consistency_snapshot(database_url: str, *, expected_users: int) -> dict[str, Any]:
    queries = {
        "xingceCommandsByType": """
            select command_type, count(*) as count
            from learning.xingce_commands
            group by command_type
            order by command_type
        """,
        "taskSessions": """
            select count(*) as total,
                   count(distinct user_id) as users,
                   count(distinct session_id) as sessions,
                   count(*) filter (where state = 'submitted') as submitted,
                   count(*) - count(distinct (user_id, work_item_key)) as duplicate_keys
            from learning.xingce_task_sessions
        """,
        "attempts": """
            select count(*) as attempts,
                   count(distinct (session_id, question_id)) as unique_session_questions,
                   count(distinct attempt_group_id) as attempt_groups
            from learning.xingce_attempt_records
        """,
        "assessments": """
            select count(*) as assessments,
                   count(distinct (session_id, question_id)) as unique_session_questions
            from learning.xingce_assessments
        """,
        "reviewHeads": """
            select count(*) as heads,
                   count(*) filter (where state = 'cause_candidate_pending') as queued_heads
            from learning.xingce_attempt_review_versions review
            where not exists (
              select 1 from learning.xingce_attempt_review_versions successor
              where successor.supersedes_review_version_id = review.review_version_id
            )
        """,
        "reflections": """
            select count(*) as reflections,
                   count(distinct user_id) as users,
                   count(distinct command_id) as commands
            from learning.xingce_reflection_versions
        """,
        "jobs": """
            select job_type, status, count(*) as count
            from platform.background_jobs
            group by job_type, status
            order by job_type, status
        """,
        "causeReservations": """
            select count(*) as reservations,
                   count(distinct user_id) as users,
                   count(*) filter (where status = 'reserved') as reserved,
                   count(*) filter (where job_id is not null) as bound_jobs,
                   count(distinct job_id) as unique_jobs
            from commerce.feature_usage_reservations
            where feature = 'xingce_cause_analysis'
        """,
        "causeUsage": """
            select count(*) as user_periods,
                   coalesce(sum(used_count), 0) as used_count,
                   coalesce(max(used_count), 0) as maximum_per_user
            from commerce.entitlement_usage_daily
            where feature = 'xingce_cause_analysis'
        """,
        "agentRuns": """
            select count(*) as heads,
                   count(*) filter (where state = 'queued') as queued
            from ai_audit.agent_run_versions run
            where not exists (
              select 1 from ai_audit.agent_run_versions successor
              where successor.supersedes_run_version_id = run.run_version_id
            )
        """,
        "modelCalls": """
            select count(*) as model_calls,
                   coalesce(
                     round(sum(total_cost_cny) * 1000000),
                     0
                   )::bigint as cost_micros
            from ai_audit.model_call_ledger
        """,
        "uploads": """
            select count(*) as sessions,
                   count(distinct owner_user_id) as users,
                   count(*) filter (where state = 'awaiting_upload') as awaiting,
                   count(distinct object_ref) as object_refs
            from learning.upload_sessions
        """,
        "uploadCommands": """
            select count(*) as commands,
                   count(distinct owner_user_id) as users
            from learning.upload_commands
            where command_type = 'source_upload_start'
        """,
        "uploadReservations": """
            select count(*) as reservations,
                   count(distinct owner_user_id) as users,
                   count(*) filter (where state = 'reserved') as reserved
            from learning.upload_reservations
        """,
        "exports": """
            select count(*) as exports,
                   count(distinct user_id) as users,
                   count(*) filter (where state = 'queued') as queued
            from privacy.data_export_requests
        """,
        "queue": """
            select count(*) filter (where status = 'queued') as queued,
                   count(*) filter (where status = 'running') as running,
                   count(*) filter (where status = 'failed') as failed,
                   coalesce(
                     extract(epoch from (
                       now() - min(run_after) filter (
                         where status = 'queued'
                       )
                     )),
                     0
                   ) as oldest_queue_age_seconds
            from platform.background_jobs
        """,
    }
    results: dict[str, Any] = {}
    with psycopg.connect(
        database_url,
        application_name="anan_capacity_consistency",
    ) as connection:
        with connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
            for name, query in queries.items():
                cursor.execute(query)
                rows = [dict(row) for row in cursor.fetchall()]
                for row in rows:
                    for key, value in tuple(row.items()):
                        if isinstance(value, (int, float, Decimal)):
                            row[key] = (
                                round(float(value), 3)
                                if key.endswith("_seconds")
                                else int(value)
                            )
                results[name] = rows
    checks = evaluate_consistency(results, expected_users=expected_users)
    return {
        "expectedUsers": expected_users,
        "queries": queries,
        "results": results,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def evaluate_consistency(
    results: dict[str, list[dict[str, Any]]],
    *,
    expected_users: int,
) -> list[dict[str, Any]]:
    def row(name: str) -> dict[str, Any]:
        values = results.get(name, [])
        return values[0] if values else {}

    commands = {
        str(item["command_type"]): int(item["count"])
        for item in results.get("xingceCommandsByType", [])
    }
    jobs = {
        (str(item["job_type"]), str(item["status"])): int(item["count"])
        for item in results.get("jobs", [])
    }
    checks: list[tuple[str, bool, Any, Any]] = [
        (
            "one_session_per_user",
            row("taskSessions").get("total") == expected_users
            and row("taskSessions").get("users") == expected_users
            and row("taskSessions").get("duplicate_keys") == 0,
            row("taskSessions"),
            expected_users,
        ),
        (
            "one_command_per_duplicate_write",
            all(
                commands.get(command_type) == expected_users
                for command_type in (
                    "session_start",
                    "checkpoint_save",
                    "attempt_submit",
                    "reflection_submit",
                )
            ),
            commands,
            expected_users,
        ),
        (
            "attempt_and_assessment_uniqueness",
            row("attempts").get("attempts")
            == row("attempts").get("unique_session_questions")
            == row("assessments").get("assessments")
            == row("assessments").get("unique_session_questions")
            and row("attempts").get("attempt_groups") == expected_users,
            {
                "attempts": row("attempts"),
                "assessments": row("assessments"),
            },
            "all attempt facts unique; one group per user",
        ),
        (
            "one_reflection_and_review_head_per_user",
            row("reflections").get("reflections") == expected_users
            and row("reflections").get("users") == expected_users
            and row("reviewHeads").get("heads") == expected_users
            and row("reviewHeads").get("queued_heads") == expected_users,
            {
                "reflections": row("reflections"),
                "reviewHeads": row("reviewHeads"),
            },
            expected_users,
        ),
        (
            "one_cause_job_and_reservation_per_user",
            jobs.get(("xingce_cause_agent_v1", "queued")) == expected_users
            and row("causeReservations").get("reservations") == expected_users
            and row("causeReservations").get("users") == expected_users
            and row("causeReservations").get("reserved") == expected_users
            and row("causeReservations").get("bound_jobs") == expected_users
            and row("causeReservations").get("unique_jobs") == expected_users
            and row("agentRuns").get("heads") == expected_users
            and row("agentRuns").get("queued") == expected_users,
            {
                "jobs": jobs.get(("xingce_cause_agent_v1", "queued")),
                "reservations": row("causeReservations"),
                "agentRuns": row("agentRuns"),
            },
            expected_users,
        ),
        (
            "no_paid_model_call",
            row("modelCalls").get("model_calls") == 0
            and row("modelCalls").get("cost_micros") == 0,
            row("modelCalls"),
            {"model_calls": 0, "cost_micros": 0},
        ),
        (
            "cause_usage_once_per_user",
            row("causeUsage").get("user_periods") == expected_users
            and row("causeUsage").get("used_count") == expected_users
            and row("causeUsage").get("maximum_per_user") == 1,
            row("causeUsage"),
            expected_users,
        ),
        (
            "one_upload_reservation_per_user",
            row("uploads").get("sessions") == expected_users
            and row("uploads").get("users") == expected_users
            and row("uploads").get("awaiting") == expected_users
            and row("uploads").get("object_refs") == expected_users
            and row("uploadCommands").get("commands") == expected_users
            and row("uploadReservations").get("reservations") == expected_users
            and row("uploadReservations").get("reserved") == expected_users,
            {
                "uploads": row("uploads"),
                "commands": row("uploadCommands"),
                "reservations": row("uploadReservations"),
            },
            expected_users,
        ),
        (
            "one_export_job_per_user",
            row("exports").get("exports") == expected_users
            and row("exports").get("users") == expected_users
            and row("exports").get("queued") == expected_users
            and jobs.get(("personal_data_export_v1", "queued")) == expected_users,
            {
                "exports": row("exports"),
                "jobs": jobs.get(("personal_data_export_v1", "queued")),
            },
            expected_users,
        ),
        (
            "queue_has_no_running_or_failed_jobs",
            row("queue").get("queued") == expected_users * 2
            and row("queue").get("running") == 0
            and row("queue").get("failed") == 0,
            row("queue"),
            {"queued": expected_users * 2, "running": 0, "failed": 0},
        ),
    ]
    return [
        {
            "id": identifier,
            "passed": passed,
            "observed": observed,
            "expected": expected,
        }
        for identifier, passed, observed, expected in checks
    ]


def source_state_digest() -> dict[str, Any]:
    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    digest = sha256()
    count = 0
    for encoded in sorted(item for item in listed if item):
        relative = encoded.decode("utf-8", errors="surrogateescape")
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            continue
        digest.update(encoded)
        digest.update(b"\0")
        digest.update(sha256(path.read_bytes()).digest())
        count += 1
    head = _run_checked(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    return {
        "gitHead": head,
        "sourceFileCount": count,
        "sourceStateSha256": digest.hexdigest(),
        "gitStatusSha256": sha256(status).hexdigest(),
        "dirty": bool(status),
    }


def _write_bytes(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o600)
    temporary.replace(path)
    digest = sha256(content).hexdigest()
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    sidecar.chmod(0o600)
    return digest


def write_json(path: Path, value: Any) -> str:
    return _write_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def copy_log(source: Path, target: Path) -> str | None:
    if not source.exists():
        return None
    return _write_bytes(target, source.read_bytes())


def wait_for_api(
    base_url: str, process: subprocess.Popen[str], timeout: float = 60
) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(base_url=base_url, timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"uvicorn exited before readiness ({process.returncode})"
                )
            try:
                response = client.get("/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise TimeoutError("uvicorn did not become ready within 60 seconds")


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


async def run_load(
    *,
    base_url: str,
    users: list[LoadUser],
    recorder: RequestRecorder,
    run_id: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], float]:
    limits = httpx.Limits(
        max_connections=max(len(users) * 2, 100),
        max_keepalive_connections=max(len(users), 100),
    )
    gate = asyncio.Event()
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout),
        limits=limits,
        trust_env=False,
    ) as client:
        tasks = [
            asyncio.create_task(
                virtual_user(
                    client,
                    user=user,
                    recorder=recorder,
                    gate=gate,
                    run_id=run_id,
                )
            )
            for user in users
        ]
        await asyncio.sleep(0)
        started = time.perf_counter()
        gate.set()
        outcomes = await asyncio.gather(*tasks)
        wall_seconds = time.perf_counter() - started
    return outcomes, wall_seconds


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a loopback-only real HTTP + temporary PostgreSQL capacity rehearsal. "
            "The harness never accepts a remote target and never starts AI workers."
        )
    )
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--positions", type=int, default=200)
    parser.add_argument("--pool-size", type=int, default=5)
    parser.add_argument("--max-overflow", type=int, default=5)
    parser.add_argument("--pool-timeout", type=float, default=12.0)
    parser.add_argument("--api-workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--queue-observation-seconds", type=float, default=2)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    return parser.parse_args()


def evidence_argv(args: argparse.Namespace, output_directory: Path) -> list[str]:
    return [
        "backend/.venv/bin/python",
        "tools/audit/load/local_capacity_harness.py",
        "--users",
        str(args.users),
        "--positions",
        str(args.positions),
        "--pool-size",
        str(args.pool_size),
        "--max-overflow",
        str(args.max_overflow),
        "--pool-timeout",
        str(args.pool_timeout),
        "--api-workers",
        str(args.api_workers),
        "--timeout",
        str(args.timeout),
        "--queue-observation-seconds",
        str(args.queue_observation_seconds),
        "--output-dir",
        str(output_directory),
    ]


def main() -> None:
    args = arguments()
    reject_target_overriding_libpq_environment()
    if not 1 <= args.users <= 500:
        raise ValueError("--users must be between 1 and 500")
    if not 1 <= args.positions <= 5_000:
        raise ValueError("--positions must be between 1 and 5000")
    if args.pool_size <= 0 or args.max_overflow < 0:
        raise ValueError("database pool bounds are invalid")
    if not 1 <= args.api_workers <= 8:
        raise ValueError("--api-workers must be between 1 and 8")
    if (
        args.timeout <= 0
        or args.queue_observation_seconds < 0
        or not 0 < args.pool_timeout < 15
    ):
        raise ValueError("timeouts must be positive")
    output_directory = args.output_dir.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    output_directory.chmod(0o700)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    started_at = utc_now()
    source_state = source_state_digest()
    api_port = _free_port()
    base_url = f"http://127.0.0.1:{api_port}"
    if not is_loopback_url(base_url):
        raise AssertionError("generated API URL is not loopback")
    evidence_hashes: dict[str, str | None] = {}
    infrastructure_error: str | None = None
    raw: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    consistency: dict[str, Any] = {}
    postgres_stats: dict[str, Any] = {}
    api_log_source: Path | None = None
    postgres_log_source: Path | None = None

    with tempfile.TemporaryDirectory(prefix="anan-local-capacity-") as temp:
        temporary_directory = Path(temp)
        temporary_directory.chmod(0o700)
        api_log_source = temporary_directory / "api.log"
        try:
            with local_postgres(temporary_directory) as postgres:
                postgres_log_source = postgres.log_file
                os.environ["DATABASE_URL"] = postgres.sqlalchemy_url
                migration_head = bootstrap_schema(postgres.sqlalchemy_url)
                publication_authority = establish_local_publication_authority(
                    postgres.sqlalchemy_url,
                    expected_revision=migration_head,
                )
                users, seed_metadata = seed_world(
                    postgres.sqlalchemy_url,
                    user_count=args.users,
                    position_count=args.positions,
                    run_id=run_id,
                )
                stats_before = database_stats(postgres.psycopg_url)
                environment = safe_child_environment(
                    database_url=postgres.application_url,
                    api_base_url=base_url,
                    pool_size=args.pool_size,
                    max_overflow=args.max_overflow,
                    application_git_commit=publication_authority[
                        "applicationGitCommit"
                    ],
                    data_receipt_sha256=publication_authority["dataReceiptSha256"],
                    pool_timeout_seconds=args.pool_timeout,
                )
                with api_log_source.open("w", encoding="utf-8") as api_log:
                    api_process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "app.main:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(api_port),
                            "--workers",
                            str(args.api_workers),
                            "--no-access-log",
                        ],
                        cwd=BACKEND_ROOT,
                        env=environment,
                        stdout=api_log,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    try:
                        wait_for_api(base_url, api_process)
                        monitor = RuntimeMonitor(
                            database_url=postgres.psycopg_url,
                            api_pid=api_process.pid,
                        )
                        recorder = RequestRecorder()
                        monitor.start()
                        try:
                            outcomes, wall_seconds = asyncio.run(
                                run_load(
                                    base_url=base_url,
                                    users=users,
                                    recorder=recorder,
                                    run_id=run_id,
                                    timeout=args.timeout,
                                )
                            )
                            if args.queue_observation_seconds:
                                time.sleep(args.queue_observation_seconds)
                        finally:
                            monitor.stop()
                        consistency = consistency_snapshot(
                            postgres.psycopg_url,
                            expected_users=args.users,
                        )
                        stats_after = database_stats(postgres.psycopg_url)
                        postgres_stats = {
                            "before": stats_before,
                            "after": stats_after,
                            "delta": database_delta(stats_before, stats_after),
                        }
                        duplicate_assertions = {
                            key: sum(int(bool(item[key])) for item in outcomes)
                            for key in (
                                "duplicateStartStable",
                                "duplicateCheckpointStable",
                                "duplicateSubmitStable",
                                "duplicateReflectionStable",
                            )
                        }
                        request_summary = summarize_requests(
                            recorder.records,
                            wall_seconds=wall_seconds,
                            peak_in_flight=recorder.peak_in_flight,
                        )
                        completed_users = sum(
                            int(bool(item["completed"])) for item in outcomes
                        )
                        raw = {
                            "schemaVersion": "anan-local-capacity-raw@v1",
                            "runId": run_id,
                            "startedAt": started_at,
                            "finishedAt": utc_now(),
                            "sourceState": source_state,
                            "environment": {
                                "scope": "local_loopback_only",
                                "api": {
                                    "implementation": "real uvicorn/FastAPI application",
                                    "bindHost": "127.0.0.1",
                                    "workers": args.api_workers,
                                    "configuredDatabasePoolSize": args.pool_size,
                                    "configuredDatabaseMaxOverflow": args.max_overflow,
                                    "configuredDatabasePoolCeilingPerWorker": (
                                        args.pool_size + args.max_overflow
                                    ),
                                    "configuredAggregateDatabasePoolCeiling": (
                                        args.api_workers
                                        * (args.pool_size + args.max_overflow)
                                    ),
                                },
                                "database": {
                                    "implementation": "temporary real PostgreSQL",
                                    "bindHost": "127.0.0.1",
                                    "port": postgres.port,
                                    "migrationHead": migration_head,
                                    "publicationAuthority": publication_authority,
                                    "durability": {
                                        "fsync": "server default on",
                                        "synchronousCommit": "server default on",
                                        "fullPageWrites": "server default on",
                                    },
                                },
                                "virtualUsers": args.users,
                                "positions": args.positions,
                                "seed": seed_metadata,
                                "egressIsolationEnforced": False,
                                "workerProcessesStarted": [],
                                "paidModelCallsAllowed": False,
                            },
                            "requests": recorder.records,
                            "userOutcomes": outcomes,
                            "runtimeSamples": monitor.samples,
                            "monitorSummary": monitor.summary(),
                            "postgresStats": postgres_stats,
                            "consistency": consistency,
                        }
                        summary = {
                            "schemaVersion": "anan-local-capacity-summary@v1",
                            "runId": run_id,
                            "sourceState": source_state,
                            "scope": {
                                "environment": "local_loopback_only",
                                "realHttp": True,
                                "realPostgresql": True,
                                "virtualUsers": args.users,
                                "apiWorkers": args.api_workers,
                                "workerProcessesStarted": 0,
                                "providerRequestsObserved": "not_measured",
                                "productionRequestsObserved": "not_measured",
                                "realAiE2e": "not_run_external_blocker",
                            },
                            "requestSummary": request_summary,
                            "users": {
                                "completed": completed_users,
                                "failed": args.users - completed_users,
                                "failureSteps": dict(
                                    Counter(
                                        str(item["failureStep"])
                                        for item in outcomes
                                        if item["failureStep"] is not None
                                    )
                                ),
                            },
                            "duplicateReplayAssertions": {
                                **duplicate_assertions,
                                "expectedPerAssertion": args.users,
                            },
                            "runtime": monitor.summary(),
                            "postgresStatsDelta": postgres_stats["delta"],
                            "consistencyPassed": consistency["passed"],
                            "evidenceBoundary": [
                                (
                                    f"Measured {args.api_workers} local API worker(s) "
                                    "plus the uvicorn supervisor process tree."
                                ),
                                "Measured temporary PostgreSQL connections, waits, locks, queue depth, and durable rows.",
                                "Upload coverage stops after private presigned authorization; egress was not instrumented or isolated.",
                                "Cause-agent and export workers were intentionally not started so queue accumulation could be observed.",
                                "No provider worker was intentionally started; provider and production egress were not measured.",
                                "The database proves no model-cost row was created during this run.",
                                "This is level-5 local capacity evidence, not staging or production capacity evidence.",
                            ],
                        }
                    finally:
                        stop_process(api_process)
        except Exception as exc:
            infrastructure_error = f"{type(exc).__name__}: {exc}"
            summary = {
                "schemaVersion": "anan-local-capacity-summary@v1",
                "runId": run_id,
                "sourceState": source_state,
                "scope": "local_loopback_only",
                "infrastructureError": infrastructure_error,
                "finishedAt": utc_now(),
            }
        finally:
            evidence_hashes["api.log"] = (
                copy_log(api_log_source, output_directory / "api.log")
                if api_log_source is not None
                else None
            )
            evidence_hashes["postgres.log"] = (
                copy_log(postgres_log_source, output_directory / "postgres.log")
                if postgres_log_source is not None
                else None
            )

    if raw:
        evidence_hashes["load-test-raw.json"] = write_json(
            output_directory / "load-test-raw.json",
            raw,
        )
    if consistency:
        evidence_hashes["consistency.json"] = write_json(
            output_directory / "consistency.json",
            consistency,
        )
    if postgres_stats:
        evidence_hashes["postgres-stats.json"] = write_json(
            output_directory / "postgres-stats.json",
            postgres_stats,
        )
    evidence_hashes["load-test-summary.json"] = write_json(
        output_directory / "load-test-summary.json",
        summary,
    )
    manifest = {
        "schemaVersion": "anan-local-capacity-evidence@v1",
        "runId": run_id,
        "createdAt": utc_now(),
        "sourceState": source_state,
        "artifacts": evidence_hashes,
        "command": {
            "argv": evidence_argv(args, output_directory),
        },
    }
    write_json(output_directory / "evidence-manifest.json", manifest)
    printable = {
        "runId": run_id,
        "outputDirectory": str(output_directory),
        "infrastructureError": infrastructure_error,
        "requestSummary": summary.get("requestSummary"),
        "users": summary.get("users"),
        "duplicateReplayAssertions": summary.get("duplicateReplayAssertions"),
        "consistencyPassed": summary.get("consistencyPassed"),
    }
    print(json.dumps(printable, ensure_ascii=False, sort_keys=True))
    if infrastructure_error is not None:
        raise SystemExit(3)
    request_errors = int(summary["requestSummary"]["errors"])
    incomplete_users = int(summary["users"]["failed"])
    if request_errors or incomplete_users or not consistency.get("passed", False):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
