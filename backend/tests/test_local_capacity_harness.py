from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = PROJECT_ROOT / "tools/audit/load/local_capacity_harness.py"


def _harness():
    specification = importlib.util.spec_from_file_location(
        "local_capacity_harness_under_test",
        HARNESS_PATH,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_local_capacity_harness_rejects_remote_and_production_targets() -> None:
    harness = _harness()

    assert harness.is_loopback_url("http://127.0.0.1:8123")
    assert harness.is_loopback_url("http://[::1]:8123")
    assert not harness.is_loopback_url("https://kg.xingguoxian.cn")
    assert not harness.is_loopback_url("http://192.0.2.1:8123")
    assert not harness.is_loopback_url("https://localhost:8123")


def test_local_capacity_child_environment_does_not_inherit_remote_credentials(
    monkeypatch,
) -> None:
    harness = _harness()
    monkeypatch.setenv("DATABASE_URL", "postgresql://real-production.invalid/app")
    monkeypatch.setenv("AI_API_KEY", "real-provider-secret")
    monkeypatch.setenv("TOS_ACCESS_KEY", "real-storage-key")

    environment = harness.safe_child_environment(
        database_url="postgresql://postgres@127.0.0.1:55432/postgres",
        api_base_url="http://127.0.0.1:58123",
        pool_size=5,
        max_overflow=5,
        application_git_commit=harness.LOCAL_APPLICATION_COMMIT,
        data_receipt_sha256="a" * 64,
    )

    assert environment["DATABASE_URL"].startswith("postgresql://postgres@127.0.0.1:55432/")
    assert environment["AI_API_KEY"] == ""
    assert environment["TOS_ACCESS_KEY"] == "local-capacity-access-key"
    assert environment["DATABASE_POOL_TIMEOUT_SECONDS"] == "12.0"
    assert environment["APP_ENV"] == "local_capacity"
    assert environment["APP_GIT_COMMIT"] == harness.LOCAL_APPLICATION_COMMIT
    assert environment["APP_DATA_RECEIPT_SHA256"] == "a" * 64
    assert "real-provider-secret" not in environment.values()
    assert "real-storage-key" not in environment.values()


def test_local_capacity_rejects_libpq_target_override_environment() -> None:
    harness = _harness()

    with pytest.raises(ValueError, match="target-overriding libpq"):
        harness.reject_target_overriding_libpq_environment({"PGHOSTADDR": "203.0.113.10"})


def test_local_capacity_percentiles_and_request_summary_use_observed_values() -> None:
    harness = _harness()
    records = [
        {
            "step": "today",
            "status": status,
            "elapsedMs": latency,
            "timeout": timeout,
            "error": error,
        }
        for status, latency, timeout, error in (
            (200, 10.0, False, None),
            (200, 20.0, False, None),
            (503, 30.0, False, None),
            (0, 40.0, True, "ReadTimeout"),
        )
    ]

    summary = harness.summarize_requests(
        records,
        wall_seconds=2.0,
        peak_in_flight=4,
    )

    assert summary["requests"] == 4
    assert summary["requestsPerSecond"] == 2.0
    assert summary["errors"] == 2
    assert summary["timeouts"] == 1
    assert summary["latencyMs"]["p50"] == 20.0
    assert summary["latencyMs"]["p95"] == 40.0
    assert summary["latencyMs"]["p99"] == 40.0


def test_local_capacity_evidence_command_binds_worker_topology() -> None:
    harness = _harness()
    args = argparse.Namespace(
        users=100,
        positions=200,
        pool_size=5,
        max_overflow=5,
        pool_timeout=12.0,
        api_workers=2,
        timeout=45.0,
        queue_observation_seconds=2.0,
    )

    command = harness.evidence_argv(args, Path("/tmp/capacity-evidence"))

    worker_flag = command.index("--api-workers")
    assert command[worker_flag + 1] == "2"


def test_local_capacity_consistency_requires_idempotent_jobs_and_zero_model_calls() -> None:
    harness = _harness()
    expected = 2
    results = {
        "xingceCommandsByType": [
            {"command_type": name, "count": expected}
            for name in (
                "session_start",
                "checkpoint_save",
                "attempt_submit",
                "reflection_submit",
            )
        ],
        "taskSessions": [
            {
                "total": expected,
                "users": expected,
                "sessions": expected,
                "submitted": expected,
                "duplicate_keys": 0,
            }
        ],
        "attempts": [
            {
                "attempts": expected * 6,
                "unique_session_questions": expected * 6,
                "attempt_groups": expected,
            }
        ],
        "assessments": [
            {
                "assessments": expected * 6,
                "unique_session_questions": expected * 6,
            }
        ],
        "reviewHeads": [{"heads": expected, "queued_heads": expected}],
        "reflections": [{"reflections": expected, "users": expected, "commands": expected}],
        "jobs": [
            {
                "job_type": "xingce_cause_agent_v1",
                "status": "queued",
                "count": expected,
            },
            {
                "job_type": "personal_data_export_v1",
                "status": "queued",
                "count": expected,
            },
        ],
        "causeReservations": [
            {
                "reservations": expected,
                "users": expected,
                "reserved": expected,
                "bound_jobs": expected,
                "unique_jobs": expected,
            }
        ],
        "causeUsage": [{"user_periods": expected, "used_count": expected, "maximum_per_user": 1}],
        "agentRuns": [{"heads": expected, "queued": expected}],
        "modelCalls": [{"model_calls": 0, "cost_micros": 0}],
        "uploads": [
            {
                "sessions": expected,
                "users": expected,
                "awaiting": expected,
                "object_refs": expected,
            }
        ],
        "uploadCommands": [{"commands": expected, "users": expected}],
        "uploadReservations": [{"reservations": expected, "users": expected, "reserved": expected}],
        "exports": [{"exports": expected, "users": expected, "queued": expected}],
        "queue": [
            {
                "queued": expected * 2,
                "running": 0,
                "failed": 0,
                "oldest_queue_age_seconds": 2.0,
            }
        ],
    }

    checks = harness.evaluate_consistency(results, expected_users=expected)

    assert checks
    assert all(item["passed"] for item in checks)
    results["modelCalls"][0]["model_calls"] = 1
    failed = harness.evaluate_consistency(results, expected_users=expected)
    assert not next(item for item in failed if item["id"] == "no_paid_model_call")["passed"]


def test_local_capacity_harness_import_is_position_independent() -> None:
    code = """
import importlib.util
import sys
from pathlib import Path
path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("capacity_import_probe", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print(module.LOCAL_APPLICATION_COMMIT)
"""
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    result = subprocess.run(
        [sys.executable, "-c", code, str(HARNESS_PATH)],
        cwd=PROJECT_ROOT / "backend",
        env=environment,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )

    assert result.stdout.strip() == _harness().LOCAL_APPLICATION_COMMIT


def test_local_capacity_harness_completes_real_small_rehearsal(tmp_path: Path) -> None:
    output_directory = tmp_path / "capacity"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PGDATABASE",
            "PGHOST",
            "PGHOSTADDR",
            "PGPORT",
            "PGSERVICE",
            "PGSERVICEFILE",
            "PGUSER",
            "PYTHONPATH",
            "PYTHONHOME",
        }
    }
    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS_PATH),
            "--users",
            "1",
            "--positions",
            "1",
            "--pool-size",
            "2",
            "--max-overflow",
            "1",
            "--pool-timeout",
            "12",
            "--api-workers",
            "1",
            "--timeout",
            "45",
            "--queue-observation-seconds",
            "0",
            "--output-dir",
            str(output_directory),
        ],
        cwd=PROJECT_ROOT / "backend",
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout
    summary = json.loads((output_directory / "load-test-summary.json").read_text(encoding="utf-8"))
    assert summary.get("infrastructureError") is None
    assert summary["users"] == {"completed": 1, "failed": 0, "failureSteps": {}}
    assert summary["requestSummary"]["errors"] == 0
    assert summary["consistencyPassed"] is True
