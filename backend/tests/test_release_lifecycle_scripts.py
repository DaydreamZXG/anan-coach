from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVATE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "activate_release.sh"
ROLLBACK_SCRIPT = REPO_ROOT / "ops" / "scripts" / "rollback_release.sh"
BACKUP_SCRIPT = REPO_ROOT / "ops" / "scripts" / "backup.sh"
DATA_PUBLISH_SCRIPT = REPO_ROOT / "ops" / "scripts" / "publish_commercial_data_release.sh"
LOCKED_DATA_PUBLISH_SCRIPT = (
    REPO_ROOT / "ops" / "scripts" / "publish_commercial_data_release_locked.sh"
)
POSITION_RELEASE_SCRIPT = REPO_ROOT / "backend" / "scripts" / "compile_position_rule_release.py"
PREVIOUS_REVISION = "20260727_69"
CANDIDATE_REVISION = "20260728_70"
CANDIDATE_COMMIT = "1" * 40
ROLLBACK_COMMIT = "7" * 40
# Deadlock guard for shell lifecycle harnesses, not a release latency SLO.
# A 20-second per-call budget was below observed full-suite host jitter even
# though the same activation path completed in 9 seconds when reproduced alone.
RELEASE_HARNESS_TIMEOUT_SECONDS = 60
RUNTIME_UNITS = (
    "gongkao-dazi-api",
    "gongkao-dazi-external-learning-worker",
    "gongkao-dazi-data-rights-worker",
    "gongkao-dazi-cause-agent-worker",
)
ESSAY_REVIEW_WORKER_UNIT = "gongkao-dazi-essay-review-worker"
COACH_NOTIFICATION_WORKER_UNIT = "gongkao-dazi-coach-notification-worker"
COMMERCE_WORKER_UNIT = "gongkao-dazi-commerce-worker"
ILINK_WORKER_UNIT = "gongkao-dazi-ilink-object-deletion-worker"
CAUSE_AGENT_WORKER_UNIT = "gongkao-dazi-cause-agent-worker"
LOCK_VALIDATION_WITH_ROOT_STAT = """
import os
from pathlib import Path
real_lstat = Path.lstat
def root_owned_lstat(path):
    current = real_lstat(path)
    values = list(current)
    values[4] = 0
    return os.stat_result(values)
Path.lstat = root_owned_lstat
from scripts.compile_position_rule_release import (
    _validate_data_release_coordination_lock,
)
_validate_data_release_coordination_lock()
"""


@dataclass(frozen=True, slots=True)
class ReleaseHarness:
    environment: dict[str, str]
    release_root: Path
    current_link: Path
    previous_target: Path
    candidate_target: Path
    database_revision_file: Path
    events_file: Path
    controller_root: Path
    systemd_root: Path

    def activate(
        self,
        *,
        fault: str = "",
        release_id: str = "candidate",
        environment_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ACTIVATE_SCRIPT), release_id],
            check=False,
            env={
                **self.environment,
                **(environment_overrides or {}),
                "HARNESS_FAULT": fault,
            },
            capture_output=True,
            text=True,
            timeout=RELEASE_HARNESS_TIMEOUT_SECONDS,
        )

    def rollback(self, *, fault: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROLLBACK_SCRIPT), "previous"],
            check=False,
            env={**self.environment, "HARNESS_FAULT": fault},
            capture_output=True,
            text=True,
            timeout=RELEASE_HARNESS_TIMEOUT_SECONDS,
        )


@pytest.mark.parametrize("previous_contract", ("missing", "incompatible"))
def test_activation_rejects_unsafe_previous_without_database_mutation(
    tmp_path: Path,
    previous_contract: str,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(
            () if previous_contract == "missing" else (PREVIOUS_REVISION,)
        ),
    )

    result = harness.activate()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert harness.database_revision_file.read_text(encoding="utf-8").strip() == (
        CANDIDATE_REVISION
    )
    assert "alembic upgrade head" not in harness.events_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("fault", ("restart", "readiness"))
def test_activation_restores_only_preflighted_previous_after_runtime_failure(
    tmp_path: Path,
    fault: str,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(
            PREVIOUS_REVISION,
            CANDIDATE_REVISION,
        ),
    )

    result = harness.activate(fault=fault)

    assert result.returncode != 0
    assert harness.database_revision_file.read_text(encoding="utf-8").strip() == (
        CANDIDATE_REVISION
    )
    assert harness.current_link.resolve() == harness.previous_target
    events = harness.events_file.read_text(encoding="utf-8")
    assert "alembic upgrade head" not in events
    assert "data-release-authority verify" in events
    assert events.count("systemctl restart") >= 2
    assert events.count(f"systemctl disable --now {COMMERCE_WORKER_UNIT}") >= 2
    assert (harness.candidate_target / "release-authority" / "activation-failed.json").is_file()
    assert not (harness.candidate_target / "release-authority" / "activation.json").exists()
    _assert_runtime_identity(harness, commit=ROLLBACK_COMMIT)


@pytest.mark.parametrize(
    ("essay_enabled", "ilink_enabled", "optional_unit"),
    (
        ("true", "false", ESSAY_REVIEW_WORKER_UNIT),
        ("false", "true", ILINK_WORKER_UNIT),
    ),
)
def test_failed_activation_restores_complete_previous_runtime_set(
    tmp_path: Path,
    essay_enabled: str,
    ilink_enabled: str,
    optional_unit: str,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        essay_review_worker_enabled=essay_enabled,
        ilink_channel_enabled=ilink_enabled,
    )

    result = harness.activate(fault="readiness")

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    restart_events = [
        line
        for line in harness.events_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("systemctl restart ")
    ]
    restored_units = restart_events[-1].split()
    assert all(restored_units.count(unit) == 1 for unit in RUNTIME_UNITS)
    assert restored_units.count(optional_unit) == 1


def test_rollback_accepts_newer_database_declared_by_old_head_artifact(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(
            PREVIOUS_REVISION,
            CANDIDATE_REVISION,
        ),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == harness.previous_target
    assert harness.database_revision_file.read_text(encoding="utf-8").strip() == (
        CANDIDATE_REVISION
    )
    _assert_runtime_identity(harness, commit=ROLLBACK_COMMIT)


def test_activation_from_legacy_current_stays_stopped_and_grants_no_rollback(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        previous_guard_capable=False,
    )

    result = harness.activate()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")
    assert not (
        harness.previous_target / "release-authority" / "rollback-eligible" / "candidate.json"
    ).exists()


def test_rollback_rejects_legacy_target_without_control_plane_change(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
        previous_guard_capable=False,
    )

    result = harness.rollback()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


def test_rollback_to_signed_guard_capable_bridge_succeeds(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == harness.previous_target


def test_rollback_prefers_current_release_eligibility_over_stale_activation(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    _write_current_rollback_eligibility(
        harness,
        activation_receipt="1" * 64,
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == harness.previous_target


def test_rollback_rejects_tampered_current_release_eligibility_without_fallback(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    _write_current_rollback_eligibility(
        harness,
        activation_receipt=_global_receipt_sha256(harness),
        eligibility_receipt="2" * 64,
    )
    harness.environment["PYTHONOPTIMIZE"] = "1"

    result = harness.rollback()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


def test_rollback_uses_global_data_authority_not_artifact_history(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    (harness.candidate_target / "release-authority" / "data-release-receipt.json").write_text(
        '{"staleArtifactHistory":true}\n', encoding="utf-8"
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == harness.previous_target


def test_activation_enables_and_restarts_cause_agent_in_standard_topology(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )

    result = harness.activate()

    assert result.returncode == 0, result.stderr
    events = harness.events_file.read_text(encoding="utf-8")
    enable = next(line for line in events.splitlines() if line.startswith("systemctl enable "))
    assert enable.split().count(CAUSE_AGENT_WORKER_UNIT) == 1
    assert all(enable.split().count(unit) == 1 for unit in RUNTIME_UNITS)
    restart = next(line for line in events.splitlines() if line.startswith("systemctl restart "))
    assert restart.split().count(CAUSE_AGENT_WORKER_UNIT) == 1
    assert "alembic upgrade head" not in events
    assert "position-release-compile" not in events
    assert "knowledge-release-refresh" not in events
    assert "systemd-sysusers " in events
    assert "gongkao-backup" in (
        REPO_ROOT / "ops" / "sysusers.d" / "gongkao-dazi-workers.conf"
    ).read_text(encoding="utf-8")
    assert "gongkao-backup" in (REPO_ROOT / "ops" / "scripts" / "activate_release.sh").read_text(
        encoding="utf-8"
    )
    assert events.index("data-release-authority verify") < events.index("systemctl restart")
    _assert_runtime_identity(harness, commit=CANDIDATE_COMMIT)


@pytest.mark.parametrize("operation", ("activate", "rollback"))
def test_runtime_units_consume_the_sealed_snapshot_after_atomic_config_rotation(
    tmp_path: Path,
    operation: str,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=operation == "rollback",
    )
    harness.environment["HARNESS_ROTATE_CONFIG_ON_RESTART"] = "true"

    result = harness.rollback() if operation == "rollback" else harness.activate()

    assert result.returncode == 0, result.stderr
    config_root = Path(harness.environment["GONGKAO_CONFIG_ROOT"])
    assert "rotated-after-preflight" in (config_root / "db.env").read_text(encoding="utf-8")
    active_release = harness.current_link.resolve()
    snapshot = _runtime_snapshot(harness, active_release)
    manifest = _snapshot_manifest(snapshot)
    assert (snapshot / "db.env").read_bytes() != (config_root / "db.env").read_bytes()
    assert (snapshot / "db.env").read_bytes() == manifest["db.env"]
    assert snapshot.stat().st_mode & 0o777 == 0o500
    assert all(path.stat().st_mode & 0o777 == 0o400 for path in snapshot.glob("*.env"))
    expected_environment_files = {
        "gongkao-dazi-data-rights-worker.service": (
            "worker-data-rights.env",
            "db.env",
            "worker-heartbeat-data-rights.env",
            "release-runtime.env",
        ),
        "gongkao-dazi-cause-agent-worker.service": (
            "worker-cause-agent.env",
            "db.env",
            "worker-heartbeat-cause-agent.env",
            "release-runtime.env",
        ),
        "gongkao-dazi-external-learning-worker.service": (
            "worker-external-learning.env",
            "db.env",
            "worker-heartbeat-external-learning.env",
            "release-runtime.env",
        ),
        "gongkao-dazi-essay-review-worker.service": (
            "worker-essay-review.env",
            "db.env",
            "worker-heartbeat-essay-review.env",
            "release-runtime.env",
        ),
        "gongkao-dazi-coach-notification-worker.service": (
            "worker-coach-notification.env",
            "db.env",
            "worker-heartbeat-coach-notification.env",
            "release-runtime.env",
        ),
        "gongkao-dazi-ilink-object-deletion-worker.service": (
            "worker-ilink-object-deletion.env",
            "db.env",
            "worker-heartbeat-ilink.env",
            "release-runtime.env",
        ),
    }
    for unit, names in expected_environment_files.items():
        content = (harness.systemd_root / unit).read_text(encoding="utf-8")
        actual = tuple(
            line.split("=", 1)[1]
            for line in content.splitlines()
            if line.startswith("EnvironmentFile=")
        )
        assert actual == tuple(str(snapshot / name) for name in names)


def test_activation_uses_application_boolean_aliases_for_optional_workers(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        essay_review_worker_enabled="1",
        coach_notifications_enabled="yes",
        ilink_channel_enabled="on",
    )

    result = harness.activate()

    assert result.returncode == 0, result.stderr
    events = harness.events_file.read_text(encoding="utf-8")
    enable = next(line for line in events.splitlines() if line.startswith("systemctl enable "))
    restart = next(line for line in events.splitlines() if line.startswith("systemctl restart "))
    for unit in (
        ESSAY_REVIEW_WORKER_UNIT,
        COACH_NOTIFICATION_WORKER_UNIT,
        ILINK_WORKER_UNIT,
    ):
        assert enable.split().count(unit) == 1
        assert restart.split().count(unit) == 1


@pytest.mark.parametrize(
    ("operation", "current_points_to_candidate"),
    (("activate", False), ("rollback", True)),
)
def test_disabled_coach_worker_is_stopped_and_disabled(
    tmp_path: Path,
    operation: str,
    current_points_to_candidate: bool,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=current_points_to_candidate,
    )

    result = getattr(harness, operation)()

    assert result.returncode == 0, result.stderr
    assert (
        f"systemctl disable --now {COACH_NOTIFICATION_WORKER_UNIT}"
        in harness.events_file.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("operation", "current_points_to_candidate"),
    (("activate", False), ("rollback", True)),
)
def test_free_mode_commerce_worker_is_stopped_and_disabled(
    tmp_path: Path,
    operation: str,
    current_points_to_candidate: bool,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=current_points_to_candidate,
    )

    result = getattr(harness, operation)()

    assert result.returncode == 0, result.stderr
    assert f"systemctl disable --now {COMMERCE_WORKER_UNIT}" in harness.events_file.read_text(
        encoding="utf-8"
    )


def test_activation_rejects_reused_heartbeat_secret_before_control_change(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    config_root = Path(harness.environment["GONGKAO_CONFIG_ROOT"])
    cause_agent_environment = config_root / "worker-heartbeat-cause-agent.env"
    cause_agent_environment.write_text(
        cause_agent_environment.read_text(encoding="utf-8").replace(
            "release-harness-heartbeat-secret-2",
            "release-harness-heartbeat-secret-1",
        ),
        encoding="utf-8",
    )

    result = harness.activate()

    assert result.returncode != 0
    assert "worker heartbeat credentials must be distinct" in result.stderr
    assert harness.current_link.resolve() == harness.previous_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("operation", "current_points_to_candidate"),
    (("activate", False), ("rollback", True)),
)
def test_worker_database_ca_must_be_a_trusted_regular_file(
    tmp_path: Path,
    operation: str,
    current_points_to_candidate: bool,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=current_points_to_candidate,
    )
    config_root = Path(harness.environment["GONGKAO_CONFIG_ROOT"])
    database_ca = config_root / "db-ca.pem"
    untrusted_ca = tmp_path / "caller-controlled-ca.pem"
    untrusted_ca.write_text(database_ca.read_text(encoding="utf-8"), encoding="utf-8")
    database_ca.unlink()
    database_ca.symlink_to(untrusted_ca)

    result = getattr(harness, operation)()

    assert result.returncode != 0
    expected_current = (
        harness.candidate_target if current_points_to_candidate else harness.previous_target
    )
    assert harness.current_link.resolve() == expected_current
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


def test_activation_rejects_worker_workload_key_drift_before_control_change(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    workload_environment = (
        Path(harness.environment["GONGKAO_CONFIG_ROOT"]) / "worker-data-rights.env"
    )
    workload_environment.write_text(
        workload_environment.read_text(encoding="utf-8")
        + "AI_API_KEY=must-not-reach-data-rights\n",
        encoding="utf-8",
    )

    result = harness.activate()

    assert result.returncode != 0
    assert "does not match its release-scoped key contract" in result.stderr
    assert harness.current_link.resolve() == harness.previous_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("operation", "current_points_to_candidate", "workload_key"),
    (
        ("activate", False, "TOS_SECRET_KEY"),
        ("rollback", True, "AI_API_KEY"),
        ("activate", False, "UPLOAD_SCANNER_API_KEY"),
        ("rollback", True, "ESSAY_REVIEW_WORKER_ENABLED"),
        ("activate", False, "WECHAT_MINI_APP_SECRET"),
        ("rollback", True, "ILINK_BOT_TOKEN"),
    ),
)
def test_shared_database_environment_cannot_override_worker_workload_keys(
    tmp_path: Path,
    operation: str,
    current_points_to_candidate: bool,
    workload_key: str,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=current_points_to_candidate,
    )
    database_environment = Path(harness.environment["GONGKAO_CONFIG_ROOT"]) / "db.env"
    database_environment.write_text(
        database_environment.read_text(encoding="utf-8")
        + f"{workload_key}=shared-db-env-must-not-override\n",
        encoding="utf-8",
    )

    result = getattr(harness, operation)()

    assert result.returncode != 0
    assert "gongkao-database-environment/v1" in result.stderr
    assert workload_key in result.stderr
    expected_current = (
        harness.candidate_target if current_points_to_candidate else harness.previous_target
    )
    assert harness.current_link.resolve() == expected_current
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("operation", "current_points_to_candidate"),
    (("activate", False), ("rollback", True)),
)
def test_release_accepts_database_environment_allowlist_contract(
    tmp_path: Path,
    operation: str,
    current_points_to_candidate: bool,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=current_points_to_candidate,
    )
    database_environment = Path(harness.environment["GONGKAO_CONFIG_ROOT"]) / "db.env"
    database_environment.write_text(
        database_environment.read_text(encoding="utf-8")
        + "DATABASE_POOL_SIZE=5\n"
        + "DATABASE_MAX_OVERFLOW=5\n"
        + "DATABASE_POOL_TIMEOUT_SECONDS=12\n"
        + "DATABASE_STATEMENT_TIMEOUT_MS=15000\n"
        + "DATABASE_LOCK_TIMEOUT_MS=3000\n"
        + "DATABASE_IDLE_TRANSACTION_TIMEOUT_MS=120000\n",
        encoding="utf-8",
    )

    result = getattr(harness, operation)()

    assert result.returncode == 0, result.stderr


def test_rollback_rejects_worker_environment_file_sequence_drift(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    worker_unit = harness.previous_target / "ops" / "systemd" / f"{CAUSE_AGENT_WORKER_UNIT}.service"
    worker_unit.write_text(
        worker_unit.read_text(encoding="utf-8").replace(
            "EnvironmentFile=/etc/gongkao-dazi/release-runtime.env",
            "EnvironmentFile=/etc/gongkao-dazi/release-runtime.env\n"
            "EnvironmentFile=/etc/gongkao-dazi/db.env",
        ),
        encoding="utf-8",
    )

    result = harness.rollback()

    assert result.returncode != 0
    assert "EnvironmentFile load sequence" in result.stderr
    assert harness.current_link.resolve() == harness.candidate_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


def test_rollback_rejects_a_symlinked_heartbeat_environment(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    config_root = Path(harness.environment["GONGKAO_CONFIG_ROOT"])
    ilink_environment = config_root / "worker-heartbeat-ilink.env"
    ilink_environment.unlink()
    ilink_environment.symlink_to(config_root / "worker-heartbeat-data-rights.env")

    result = harness.rollback()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


def test_activation_requires_detached_data_authority_before_control_change(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    receipt = harness.release_root / "incoming" / "anan-candidate.data-release-receipt.json"
    receipt.unlink()

    result = harness.activate()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert harness.database_revision_file.read_text(encoding="utf-8").strip() == (
        CANDIDATE_REVISION
    )
    assert "alembic upgrade head" not in harness.events_file.read_text(encoding="utf-8")


def test_activation_reverifies_signed_bundle_before_extraction(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.activate(fault="manifest-verifier")

    assert result.returncode != 0
    assert not harness.candidate_target.exists()
    assert harness.current_link.resolve() == harness.previous_target
    assert _control_plane_snapshot(harness) == control_plane_before
    events = harness.events_file.read_text(encoding="utf-8")
    assert "release-manifest-verifier" in events
    assert "uv sync" not in events
    assert "systemctl enable" not in events


def test_live_data_authority_mismatch_fails_before_control_plane_change(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.activate(fault="data-authority")

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert _control_plane_snapshot(harness) == control_plane_before
    events = harness.events_file.read_text(encoding="utf-8")
    assert "data-release-authority verify" in events
    assert "systemctl enable" not in events
    assert "systemctl restart" not in events


def test_candidate_receipt_must_equal_global_current_data_authority(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    current = Path(harness.environment["DATA_RELEASE_AUTHORITY_ROOT"]) / "current" / "receipt.json"
    current.write_text(
        json.dumps(
            {
                "environment": "preproduction",
                "database": {"alembicRevision": CANDIDATE_REVISION},
                "compatibleApplicationCommits": [
                    CANDIDATE_COMMIT,
                    ROLLBACK_COMMIT,
                ],
                "newerIndependentDataPublication": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.activate()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert _control_plane_snapshot(harness) == control_plane_before
    events = harness.events_file.read_text(encoding="utf-8")
    assert "data-release-authority verify" not in events
    assert "systemctl enable" not in events


def test_successful_activation_keeps_bounded_releases_and_consumes_artifact(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    stale_releases = tuple(harness.release_root / f"stale-{index}" for index in range(4))
    for index, release in enumerate(stale_releases):
        release.mkdir()
        os.utime(release, (index, index))

    result = harness.activate()

    assert result.returncode == 0, result.stderr
    retained = {
        path.name
        for path in harness.release_root.iterdir()
        if path.is_dir() and path.name not in {"current", "incoming", "runtime-config"}
    }
    assert retained == {"candidate", "previous", "stale-3"}
    assert list((harness.release_root / "incoming").iterdir()) == []
    assert (harness.candidate_target / "release-authority" / "release.json").is_file()
    assert (harness.candidate_target / "release-authority" / "release.json.sig").is_file()
    assert (harness.candidate_target / "release-authority" / "wechat-upload-receipt.json").is_file()
    assert (
        harness.candidate_target / "release-authority" / "wechat-upload-receipt.json.sig"
    ).is_file()
    assert (harness.candidate_target / "release-authority" / "wechat-upload-info.json").is_file()
    assert (harness.candidate_target / "release-authority" / "wechat-upload.log").is_file()
    assert (harness.candidate_target / "release-authority" / "data-release-receipt.json").is_file()
    assert (
        harness.candidate_target / "release-authority" / "data-release-receipt.json.sig"
    ).is_file()
    assert (
        harness.candidate_target / "release-authority" / "data-release-verification.json"
    ).is_file()
    assert (harness.candidate_target / "release-authority" / "activation.json").is_file()
    assert (
        harness.previous_target / "release-authority" / "rollback-eligible" / "candidate.json"
    ).is_file()
    events = harness.events_file.read_text(encoding="utf-8")
    assert events.count("wechat-upload-receipt.json.sig") >= 2


def test_activation_rejects_missing_wechat_uploader_signature_before_runtime_change(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    (harness.release_root / "incoming" / "anan-candidate.wechat-upload-receipt.json.sig").unlink()

    result = harness.activate()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


def test_rollback_rejects_database_revision_not_declared_by_old_head_artifact(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION,),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
    )

    result = harness.rollback()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target


def test_rollback_rejects_target_without_success_or_eligibility_marker(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    (harness.previous_target / "release-authority" / "activation.json").unlink()

    result = harness.rollback()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    assert "no successful activation" in result.stderr


def test_rollback_uses_current_controller_verifier_not_target_copy(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    (harness.previous_target / "ops" / "scripts" / "data_release_authority.py").unlink()

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == harness.previous_target


def test_rollback_enables_and_validates_configured_essay_review_worker(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
        essay_review_worker_enabled="yes",
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    events = harness.events_file.read_text(encoding="utf-8")
    assert "essay-worker-constructor" in events
    enable = next(line for line in events.splitlines() if line.startswith("systemctl enable "))
    assert enable.split().count(ESSAY_REVIEW_WORKER_UNIT) == 1
    assert f"systemctl restart {' '.join((*RUNTIME_UNITS, ESSAY_REVIEW_WORKER_UNIT))}" in events


def test_rollback_disables_unconfigured_essay_review_worker(tmp_path: Path) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    events = harness.events_file.read_text(encoding="utf-8")
    assert "essay-worker-constructor" not in events
    assert f"systemctl disable --now {ESSAY_REVIEW_WORKER_UNIT}" in events


def test_rollback_rejects_invalid_essay_worker_before_pointer_switch(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
        essay_review_worker_enabled="true",
    )

    control_plane_before = _control_plane_snapshot(harness)
    result = harness.rollback(fault="essay-worker")

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    assert "essay-worker-constructor" in harness.events_file.read_text(encoding="utf-8")
    assert _control_plane_snapshot(harness) == control_plane_before


def test_activation_rejects_invalid_worker_without_changing_control_plane(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        essay_review_worker_enabled="true",
    )
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.activate(fault="essay-worker")

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert _control_plane_snapshot(harness) == control_plane_before


@pytest.mark.parametrize("operation", ("activate", "rollback"))
def test_partial_control_plane_install_is_restored_exactly(
    tmp_path: Path,
    operation: str,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=(CANDIDATE_REVISION),
        current_points_to_candidate=operation == "rollback",
    )
    original_pointer = harness.current_link.resolve()
    control_plane_before = _control_plane_snapshot(harness)

    result = getattr(harness, operation)(fault="control-plane-install")

    assert result.returncode != 0
    assert harness.current_link.resolve() == original_pointer
    assert _control_plane_snapshot(harness) == control_plane_before


def test_activation_marker_failure_restores_pointer_and_control_plane(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    eligibility = (
        harness.previous_target / "release-authority" / "rollback-eligible" / "candidate.json"
    )
    eligibility.parent.mkdir()
    eligibility.write_text('{"preexisting":true}\n', encoding="utf-8")
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.activate()

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert _control_plane_snapshot(harness) == control_plane_before
    assert eligibility.read_text(encoding="utf-8") == '{"preexisting":true}\n'
    assert not (harness.candidate_target / "release-authority" / "activation.json").exists()
    assert (harness.candidate_target / "release-authority" / "activation-failed.json").is_file()


def test_failed_rollback_restores_original_pointer_and_complete_runtime_set(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
        essay_review_worker_enabled="true",
    )

    result = harness.rollback(fault="readiness")

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    restart_events = [
        line
        for line in harness.events_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("systemctl restart ")
    ]
    assert len(restart_events) == 2
    restored_units = restart_events[-1].split()
    assert all(restored_units.count(unit) == 1 for unit in RUNTIME_UNITS)
    assert restored_units.count(ESSAY_REVIEW_WORKER_UNIT) == 1
    assert (
        harness.events_file.read_text(encoding="utf-8").count(
            f"systemctl disable --now {COMMERCE_WORKER_UNIT}"
        )
        >= 2
    )
    _assert_runtime_identity(harness, commit=CANDIDATE_COMMIT)


def test_second_rollback_waiter_revalidates_current_after_lock(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
    )
    lock_path = Path(harness.environment["DATA_RELEASE_COORDINATION_LOCK"])
    lock_path.touch(mode=0o600)
    lock_path.chmod(0o600)
    environment = {
        **harness.environment,
        "HARNESS_FAULT": "",
        "HARNESS_REAL_FLOCK": "true",
    }
    processes: list[subprocess.Popen[str]] = []
    results: list[tuple[int, str]] = []
    try:
        with lock_path.open("a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            for _ in range(2):
                processes.append(
                    subprocess.Popen(
                        [str(ROLLBACK_SCRIPT), "previous"],
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                events = harness.events_file.read_text(encoding="utf-8")
                if events.count("flock -x 9") >= 2:
                    break
                if any(process.poll() is not None for process in processes):
                    break
                time.sleep(0.02)
            assert events.count("flock -x 9") >= 2
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            for process in processes:
                _, stderr = process.communicate(timeout=20)
                results.append((process.returncode, stderr))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    assert sum(returncode == 0 for returncode, _ in results) == 1
    loser_errors = [stderr for returncode, stderr in results if returncode != 0]
    assert len(loser_errors) == 1
    assert (
        "current release changed while waiting for the data release coordination lock"
        in loser_errors[0]
    )
    assert harness.current_link.resolve() == harness.previous_target


def test_activation_waiter_revalidates_current_after_prior_lock_holder_switch(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=CANDIDATE_REVISION,
    )
    control_plane_before = _control_plane_snapshot(harness)
    lock_path = Path(harness.environment["DATA_RELEASE_COORDINATION_LOCK"])
    lock_path.touch(mode=0o600)
    lock_path.chmod(0o600)
    environment = {
        **harness.environment,
        "HARNESS_FAULT": "",
        "HARNESS_REAL_FLOCK": "true",
    }
    process: subprocess.Popen[str] | None = None
    try:
        with lock_path.open("a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            process = subprocess.Popen(
                [str(ACTIVATE_SCRIPT), "candidate"],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 10
            events = ""
            while time.monotonic() < deadline:
                events = harness.events_file.read_text(encoding="utf-8")
                if "flock -x 9" in events:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.02)
            assert "flock -x 9" in events
            assert harness.candidate_target.is_dir()
            replacement = harness.current_link.with_name(".current-after-holder")
            replacement.symlink_to(harness.candidate_target)
            os.replace(replacement, harness.current_link)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            _, stderr = process.communicate(timeout=20)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process is not None
    assert process.returncode != 0
    assert "current release changed while waiting for the data release coordination lock" in stderr
    assert harness.current_link.resolve() == harness.candidate_target
    assert _control_plane_snapshot(harness) == control_plane_before


def test_data_publication_and_application_switch_share_one_lock_contract() -> None:
    activation = ACTIVATE_SCRIPT.read_text(encoding="utf-8")
    rollback = ROLLBACK_SCRIPT.read_text(encoding="utf-8")
    publication = DATA_PUBLISH_SCRIPT.read_text(encoding="utf-8")
    locked_publication = LOCKED_DATA_PUBLISH_SCRIPT.read_text(encoding="utf-8")

    lock_default = "${config_root}/data-release.lock"
    assert lock_default in activation
    assert lock_default in rollback
    assert lock_default in publication
    assert activation.index("flock -x 9") < activation.index("data_release_authority.py")
    assert rollback.index("flock -x 9") < rollback.index("data_release_authority.py")
    mutation_start = locked_publication.index(
        "# From this point every mutation inherits the live PostgreSQL fence fd"
    )
    begin = publication.index('"${publication_helper}" begin')
    execute_exclusive = publication.index(
        '"${publication_helper}" exec-exclusive',
        begin,
    )
    inherited_fence = locked_publication.index('"${publication_helper}" assert-inherited-fence')
    migration = locked_publication.index('alembic" upgrade head', mutation_start)
    position = locked_publication.index(
        '"${root}/backend/scripts/compile_position_rule_release.py"',
        mutation_start,
    )
    knowledge = locked_publication.index(
        '"${root}/scripts/server_refresh_knowledge_map_nodes.sh" --local',
        mutation_start,
    )
    capture = locked_publication.index("data_release_authority.py", knowledge)
    pointer = locked_publication.index(
        'mv -Tf "${current_staging}" "${authority_root}/current"',
        capture,
    )
    record = locked_publication.index(
        '"${publication_helper}" record-receipt',
        capture,
    )
    finish = locked_publication.index('"${publication_helper}" finish', pointer)
    assert publication.index("flock -x 9") < execute_exclusive
    assert begin < execute_exclusive
    assert inherited_fence < mutation_start
    assert migration < position < knowledge < capture < pointer < finish
    assert capture < record < pointer
    assert 'sudo -C 10 -u "${migration_database_os_user}"' in locked_publication
    assert 'sudo -u "${migration_database_os_user}"' not in locked_publication
    assert 'alembic" upgrade head' not in publication
    assert '-- /bin/bash "${locked_publication}"' in publication
    knowledge_script = (REPO_ROOT / "scripts" / "server_refresh_knowledge_map_nodes.sh").read_text(
        encoding="utf-8"
    )
    assert 'readlink -f "/proc/$$/fd/9"' in knowledge_script
    assert "flock -n 9" in knowledge_script
    assert "compile_position_rule_release.py" not in activation
    assert "server_refresh_knowledge_map_nodes.sh" not in activation
    assert 'alembic" upgrade head' not in activation
    assert "/release-authority/data-release-receipt.json" not in rollback
    assert "/data-releases" in rollback


def test_release_controllers_provision_isolated_backup_principal_before_control_change() -> None:
    sysusers = (REPO_ROOT / "ops" / "sysusers.d" / "gongkao-dazi-workers.conf").read_text(
        encoding="utf-8"
    )
    assert "u gongkao-backup" in sysusers
    assert "/usr/sbin/nologin" in sysusers

    for script_path in (ACTIVATE_SCRIPT, ROLLBACK_SCRIPT):
        script = script_path.read_text(encoding="utf-8")
        assert "systemd-sysusers" in script
        assert "systemd-tmpfiles" in script
        assert "gongkao-backup" in script
        assert 'api_uid="$(id -u deploy)"' in script
        if script_path == ACTIVATE_SCRIPT:
            authority_call = 'ensure_isolated_runtime_principals "${target}"'
            assert '${shell}" != */nologin' in script
        else:
            authority_call = 'provision_host_backup_authority "${previous_target}"'
            assert '${backup_shell}" != */nologin' in script
            assert 'ensure_target_runtime_principals "${target}"' in script
            assert "gongkao-dazi-backup.timer" in script
            assert script.rindex(authority_call) < script.rindex(
                'ensure_target_runtime_principals "${target}"'
            )
        assert script.rindex(authority_call) < script.rindex("prepare_control_plane_backup")
        assert script.index('systemd-sysusers "${sysusers_contract}"') < script.rindex(
            authority_call
        )
        assert script.index('systemd-tmpfiles --create "${tmpfiles_contract}"') < script.rindex(
            authority_call
        )
        assert script.rindex("validate_backup_environment_authority_paths") < script.rindex(
            authority_call
        )


@pytest.mark.parametrize(
    ("operation", "current_points_to_candidate"),
    (("activate", False), ("rollback", True)),
)
def test_release_rejects_backup_authority_path_escape_before_control_change(
    tmp_path: Path,
    operation: str,
    current_points_to_candidate: bool,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=current_points_to_candidate,
    )
    config_root = Path(harness.environment["GONGKAO_CONFIG_ROOT"])
    (config_root / "backup.env").write_text(
        "BACKUP_ENABLED=false\n"
        "BACKUP_TARGET_AUTHORITY_FILE=/tmp/caller-selected-authority.json\n"
        "PGPASSFILE=/etc/gongkao-dazi/secrets/backup.pgpass\n"
        "BACKUP_SIGNING_PRIVATE_KEY_FILE=/etc/gongkao-dazi/secrets/backup-signing.pem\n"
        "BACKUP_DIRECTORY=/var/backups/gongkao-dazi/encrypted\n"
        "BACKUP_METRICS_FILE=/var/lib/gongkao-backup/metrics/anan-backup.prom\n",
        encoding="utf-8",
    )
    (config_root / "backup.env").chmod(0o600)

    result = getattr(harness, operation)()

    assert result.returncode != 0
    assert "violates the systemd isolation contract" in result.stderr
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("operation", "current_points_to_candidate"),
    (("activate", False), ("rollback", True)),
)
def test_release_rejects_backup_database_revision_copy_before_control_change(
    tmp_path: Path,
    operation: str,
    current_points_to_candidate: bool,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=current_points_to_candidate,
    )
    backup_environment = Path(harness.environment["GONGKAO_CONFIG_ROOT"]) / "backup.env"
    backup_environment.write_text(
        backup_environment.read_text(encoding="utf-8") + "REQUIRED_DATABASE_REVISION=stale-copy\n",
        encoding="utf-8",
    )
    backup_environment.chmod(0o600)

    result = getattr(harness, operation)()

    assert result.returncode != 0
    assert "backup.env must not define REQUIRED_DATABASE_REVISION" in result.stderr
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")


def test_rollback_rejects_symlinked_sysusers_authority_before_control_change(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    contract = harness.previous_target / "ops" / "sysusers.d" / "gongkao-dazi-workers.conf"
    replacement = contract.with_name("mutable-sysusers.conf")
    replacement.write_text(contract.read_text(encoding="utf-8"), encoding="utf-8")
    contract.unlink()
    contract.symlink_to(replacement)

    result = harness.rollback()

    assert result.returncode != 0
    events = harness.events_file.read_text(encoding="utf-8")
    assert events.count("systemd-sysusers ") == 1
    assert str(harness.candidate_target / "ops" / "sysusers.d") in events
    assert "systemctl " not in harness.events_file.read_text(encoding="utf-8")
    assert harness.current_link.resolve() == harness.candidate_target


def test_rollback_legacy_target_uses_current_backup_authority_and_keeps_timer_disabled(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    legacy_sysusers = harness.previous_target / "ops" / "sysusers.d" / "gongkao-dazi-workers.conf"
    legacy_sysusers.write_text(
        "\n".join(
            line
            for line in legacy_sysusers.read_text(encoding="utf-8").splitlines()
            if "gongkao-backup" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    (harness.previous_target / "ops" / "tmpfiles.d" / "gongkao-dazi-backup.conf").unlink()
    legacy_backup_service = (
        harness.previous_target / "ops" / "systemd" / "gongkao-dazi-backup.service"
    )
    legacy_backup_service.write_text(
        legacy_backup_service.read_text(encoding="utf-8")
        .replace("User=gongkao-backup", "User=deploy")
        .replace("Group=gongkao-backup", "Group=deploy"),
        encoding="utf-8",
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == harness.previous_target
    events = harness.events_file.read_text(encoding="utf-8")
    assert (
        f"systemd-tmpfiles --create "
        f"{harness.candidate_target}/ops/tmpfiles.d/gongkao-dazi-backup.conf"
    ) in events
    timer_disable = "systemctl disable --now gongkao-dazi-backup.timer"
    assert timer_disable in events
    assert events.index(timer_disable) < events.index("systemctl daemon-reload")
    installed_backup_service = (
        Path(harness.environment["GONGKAO_SYSTEMD_ROOT"]) / "gongkao-dazi-backup.service"
    ).read_text(encoding="utf-8")
    assert "User=gongkao-backup" in installed_backup_service
    assert "Group=gongkao-backup" in installed_backup_service
    assert "User=deploy" not in installed_backup_service


def test_rollback_backup_timer_disable_is_a_hard_interlock(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
        backup_enabled="true",
    )
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.rollback(fault="backup-timer-disable")

    assert result.returncode != 0
    assert "failed to disable the backup timer before rollback" in result.stderr
    assert harness.current_link.resolve() == harness.candidate_target
    assert _control_plane_snapshot(harness) == control_plane_before
    assert _backup_timer_states(harness) == ("enabled", "active")
    systemctl_events = [
        line
        for line in harness.events_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("systemctl ")
    ]
    assert systemctl_events == ["systemctl disable --now gongkao-dazi-backup.timer"]


def test_successful_rollback_keeps_one_runnable_donor_backup_binding(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
        previous_backup_required_revision=PREVIOUS_REVISION,
    )
    _write_backup_compatibility_entrypoints(harness)
    old_entrypoint = harness.previous_target / "ops" / "scripts" / "backup.sh"
    old_entrypoint_before = old_entrypoint.read_bytes()

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == harness.previous_target
    entrypoint, environment_file = _installed_backup_binding(harness)
    assert entrypoint == harness.candidate_target / "ops" / "scripts" / "backup.sh"
    assert environment_file == (_runtime_snapshot(harness, harness.candidate_target) / "backup.env")
    execution = _execute_installed_backup_binding(harness)
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout == "candidate-backup-controller\n"
    assert old_entrypoint.read_bytes() == old_entrypoint_before
    assert _backup_timer_states(harness) == ("disabled", "inactive")
    events = harness.events_file.read_text(encoding="utf-8")
    assert "systemctl disable --now gongkao-dazi-backup.timer" in events
    assert "systemctl enable gongkao-dazi-backup.timer" not in events


def test_failed_rollback_restores_original_runnable_backup_binding(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
        previous_backup_required_revision=PREVIOUS_REVISION,
    )
    _write_backup_compatibility_entrypoints(harness)
    installed_service = harness.systemd_root / "gongkao-dazi-backup.service"
    candidate_environment = _runtime_snapshot(harness, harness.candidate_target) / "backup.env"
    installed_service.write_text(
        installed_service.read_text(encoding="utf-8").replace(
            "/etc/gongkao-dazi/backup.env",
            str(candidate_environment),
        ),
        encoding="utf-8",
    )
    original_service = installed_service.read_bytes()
    before = _execute_installed_backup_binding(harness)
    assert before.returncode == 0, before.stderr
    assert before.stdout == "candidate-backup-controller\n"

    result = harness.rollback(fault="readiness")

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    assert installed_service.read_bytes() == original_service
    execution = _execute_installed_backup_binding(harness)
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout == "candidate-backup-controller\n"
    events = harness.events_file.read_text(encoding="utf-8")
    assert events.count("systemctl disable --now gongkao-dazi-backup.timer") >= 2
    assert "systemctl enable gongkao-dazi-backup.timer" not in events


def test_activation_rejects_invalid_target_backup_control_plane_and_keeps_donor(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    rollback = harness.rollback()
    assert rollback.returncode == 0, rollback.stderr
    donor_service = (harness.systemd_root / "gongkao-dazi-backup.service").read_bytes()
    systemctl_events_before = _systemctl_events(harness)

    for index, backup_fault in enumerate(
        (
            "missing-service",
            "invalid-service",
            "missing-timer",
            "invalid-timer",
            "missing-script",
            "invalid-script",
        )
    ):
        release_id = f"third-invalid-{index}"
        _stage_activation_release(
            harness,
            release_id=release_id,
            backup_fault=backup_fault,
        )
        result = harness.activate(
            release_id=release_id,
            environment_overrides=_third_activation_environment(),
        )

        assert result.returncode != 0, backup_fault
        assert harness.current_link.resolve() == harness.previous_target
        assert (harness.systemd_root / "gongkao-dazi-backup.service").read_bytes() == donor_service
        assert _systemctl_events(harness) == systemctl_events_before
        assert harness.candidate_target.is_dir()
        assert _runtime_snapshot(harness, harness.candidate_target).is_dir()
        assert _installed_backup_binding(harness) == (
            harness.candidate_target / "ops" / "scripts" / "backup.sh",
            _runtime_snapshot(harness, harness.candidate_target) / "backup.env",
        )


def test_failed_third_activation_restores_and_retains_rollback_donor(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    rollback = harness.rollback()
    assert rollback.returncode == 0, rollback.stderr
    _stage_activation_release(harness, release_id="third")
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.activate(
        fault="readiness",
        release_id="third",
        environment_overrides=_third_activation_environment(),
    )

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.previous_target
    assert _control_plane_snapshot(harness) == control_plane_before
    assert harness.candidate_target.is_dir()
    assert _runtime_snapshot(harness, harness.candidate_target).is_dir()
    assert _installed_backup_binding(harness) == (
        harness.candidate_target / "ops" / "scripts" / "backup.sh",
        _runtime_snapshot(harness, harness.candidate_target) / "backup.env",
    )
    assert _backup_timer_states(harness) == ("disabled", "inactive")


def test_activation_surfaces_control_plane_restore_failure_and_retains_recovery(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    rollback = harness.rollback()
    assert rollback.returncode == 0, rollback.stderr
    _stage_activation_release(harness, release_id="third")
    control_plane_before = _control_plane_snapshot(harness)

    result = harness.activate(
        fault="control-plane-restore",
        release_id="third",
        environment_overrides=_third_activation_environment(),
    )

    assert result.returncode != 0
    assert "failed to restore the previous control plane" in result.stderr
    retained_line = next(
        line for line in result.stderr.splitlines() if "recovery material is retained at " in line
    )
    retained = Path(retained_line.rsplit(" ", 1)[1])
    try:
        assert retained.is_dir()
        assert retained.name.startswith("anan-control-plane.")
        assert harness.current_link.resolve() == harness.previous_target
        assert _control_plane_snapshot(harness) == control_plane_before
        assert harness.candidate_target.is_dir()
        assert _runtime_snapshot(harness, harness.candidate_target).is_dir()
        assert _installed_backup_binding(harness) == (
            harness.candidate_target / "ops" / "scripts" / "backup.sh",
            _runtime_snapshot(harness, harness.candidate_target) / "backup.env",
        )
    finally:
        shutil.rmtree(retained)


def test_third_activation_replaces_donor_before_retention_two_prunes_it(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    rollback = harness.rollback()
    assert rollback.returncode == 0, rollback.stderr
    _stage_activation_release(harness, release_id="third")

    result = harness.activate(
        release_id="third",
        environment_overrides=_third_activation_environment(),
    )

    third = harness.release_root / "third"
    third_snapshot = _runtime_snapshot(harness, third)
    assert result.returncode == 0, result.stderr
    assert harness.current_link.resolve() == third
    assert _installed_backup_binding(harness) == (
        harness.current_link / "ops" / "scripts" / "backup.sh",
        third_snapshot / "backup.env",
    )
    assert harness.previous_target.is_dir()
    assert third.is_dir()
    assert not harness.candidate_target.exists()
    assert not _runtime_snapshot(harness, harness.candidate_target).exists()
    assert _backup_timer_states(harness) == ("disabled", "inactive")


def test_post_commit_cleanup_failure_never_restores_a_pruned_donor(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        current_points_to_candidate=True,
    )
    rollback = harness.rollback()
    assert rollback.returncode == 0, rollback.stderr
    _stage_activation_release(harness, release_id="third")

    result = harness.activate(
        fault="post-donor-prune-cleanup",
        release_id="third",
        environment_overrides=_third_activation_environment(),
    )

    third = harness.release_root / "third"
    third_snapshot = _runtime_snapshot(harness, third)
    assert result.returncode != 0
    assert "activation is committed; post-commit cleanup failed" in result.stderr
    assert harness.current_link.resolve() == third
    assert _installed_backup_binding(harness) == (
        harness.current_link / "ops" / "scripts" / "backup.sh",
        third_snapshot / "backup.env",
    )
    assert not harness.candidate_target.exists()
    assert _runtime_snapshot(harness, harness.candidate_target).is_dir()
    assert (third / "release-authority" / "activation.json").is_file()
    assert not (third / "release-authority" / "activation-failed.json").exists()


def test_release_mutators_require_the_signed_guard_capability_contract() -> None:
    activation = ACTIVATE_SCRIPT.read_text(encoding="utf-8")
    rollback = ROLLBACK_SCRIPT.read_text(encoding="utf-8")
    publication = DATA_PUBLISH_SCRIPT.read_text(encoding="utf-8")

    for script in (activation, rollback, publication):
        assert "commercial-data-publication-guard.json" in script
        assert "commercial-data-publication-guard@v1" in script
        assert 'manifest.get("schemaVersion") != 4' in script
        assert "assert_guard_capable_release" in script
    assert publication.index(
        'assert_guard_capable_release "${current_manifest}"'
    ) < publication.index('source_trusted_env "${config_root}/db.env"')
    assert activation.index(
        'assert_guard_capable_release "${previous_manifest}"'
    ) < activation.index("trap restore_previous_release ERR")
    assert rollback.index('assert_guard_capable_release "${target_manifest}"') < rollback.index(
        "trap restore_original_pointer ERR"
    )
    assert rollback.index('assert_guard_capable_release "${current_manifest}"') < rollback.index(
        "trap restore_original_pointer ERR"
    )


def test_release_readiness_probe_cannot_inherit_a_proxy_or_curl_config() -> None:
    for script_path in (ACTIVATE_SCRIPT, ROLLBACK_SCRIPT):
        script = script_path.read_text(encoding="utf-8")
        readiness = script[script.index("ready=false") :]
        probe = readiness[: readiness.index("done")]
        assert "curl \\\n    --disable" in probe
        assert "--noproxy '*'" in probe
        assert "--connect-timeout 1" in probe
        assert "--max-time 2" in probe
        assert "--proto '=http'" in probe
        assert "http://127.0.0.1:8000/ready" in probe


def test_direct_position_activation_without_publication_lock_is_rejected(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment.pop("DATA_RELEASE_COORDINATION_LOCK_HELD", None)
    environment.pop("DATA_RELEASE_COORDINATION_LOCK_PATH", None)
    environment["PYTHONPATH"] = str(REPO_ROOT / "backend")

    result = subprocess.run(
        [
            sys.executable,
            str(POSITION_RELEASE_SCRIPT),
            "--exam-year",
            "2026",
            "--rule-set-version",
            "position-rules@test",
            "--position-data-version",
            "positions@test",
            "--window-manifest",
            str(tmp_path / "not-read-without-lock.json"),
            "--activate",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert "requires the data publication lock" in result.stderr
    assert "DATA_RELEASE_COORDINATION_LOCK_HELD must equal 1" in result.stderr


def test_position_activation_accepts_inherited_locked_descriptor(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "data-release.lock"
    lock_path.touch(mode=0o600)
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "backend"),
        "DATA_RELEASE_COORDINATION_LOCK_HELD": "1",
        "DATA_RELEASE_COORDINATION_LOCK_PATH": str(lock_path),
    }

    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            saved_fd9 = os.dup(9)
        except OSError:
            saved_fd9 = None
        os.dup2(lock_handle.fileno(), 9)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    LOCK_VALIDATION_WITH_ROOT_STAT,
                ],
                cwd=REPO_ROOT / "backend",
                env=environment,
                pass_fds=(9,),
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        finally:
            if saved_fd9 is None:
                os.close(9)
            else:
                os.dup2(saved_fd9, 9)
                os.close(saved_fd9)

    assert result.returncode == 0, result.stderr


def test_position_activation_rejects_unlocked_fd9_and_forged_sentinel(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "data-release.lock"
    lock_path.touch(mode=0o600)
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "backend"),
        "DATA_RELEASE_COORDINATION_LOCK_HELD": "1",
        "DATA_RELEASE_COORDINATION_LOCK_PATH": str(lock_path),
    }

    with lock_path.open("a+") as lock_handle:
        try:
            saved_fd9 = os.dup(9)
        except OSError:
            saved_fd9 = None
        os.dup2(lock_handle.fileno(), 9)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    LOCK_VALIDATION_WITH_ROOT_STAT,
                ],
                cwd=REPO_ROOT / "backend",
                env=environment,
                pass_fds=(9,),
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        finally:
            if saved_fd9 is None:
                os.close(9)
            else:
                os.dup2(saved_fd9, 9)
                os.close(saved_fd9)

    assert result.returncode != 0
    assert "was not already held by the inherited descriptor" in result.stderr


def test_publisher_sudo_closefrom_contract_preserves_locked_fd9(
    tmp_path: Path,
) -> None:
    fake_sudo = tmp_path / "sudo"
    _write_executable(
        fake_sudo,
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "-C" && "${2:-}" == "10" ]]
shift 2
[[ "${1:-}" == "-u" && -n "${2:-}" ]]
shift 2
exec "$@"
""",
    )
    lock_path = tmp_path / "data-release.lock"
    lock_path.touch(mode=0o600)
    validation = """
import errno
import fcntl
import os
import stat
import sys

lock_path = sys.argv[1]
inherited = os.fstat(9)
expected = os.stat(lock_path, follow_symlinks=False)
assert stat.S_ISREG(inherited.st_mode)
assert (inherited.st_dev, inherited.st_ino) == (expected.st_dev, expected.st_ino)
probe = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC)
try:
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        assert exc.errno in (errno.EACCES, errno.EAGAIN)
    else:
        raise AssertionError("fd 9 did not carry the publisher lock")
finally:
    os.close(probe)
"""
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            saved_fd9 = os.dup(9)
        except OSError:
            saved_fd9 = None
        os.dup2(lock_handle.fileno(), 9)
        try:
            result = subprocess.run(
                [
                    str(fake_sudo),
                    "-C",
                    "10",
                    "-u",
                    "postgres",
                    sys.executable,
                    "-c",
                    validation,
                    str(lock_path),
                ],
                pass_fds=(9,),
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        finally:
            if saved_fd9 is None:
                os.close(9)
            else:
                os.dup2(saved_fd9, 9)
                os.close(saved_fd9)

    assert result.returncode == 0, result.stderr


def _release_harness(
    tmp_path: Path,
    *,
    previous_compatible_revisions: tuple[str, ...],
    database_revision: str = CANDIDATE_REVISION,
    current_points_to_candidate: bool = False,
    previous_guard_capable: bool = True,
    essay_review_worker_enabled: str = "false",
    coach_notifications_enabled: str = "false",
    ilink_channel_enabled: str = "false",
    previous_backup_required_revision: str | None = None,
    backup_enabled: str = "false",
) -> ReleaseHarness:
    release_root = tmp_path / "releases"
    incoming = release_root / "incoming"
    previous = release_root / "previous"
    candidate = release_root / "candidate"
    artifact = tmp_path / "artifact"
    config_root = tmp_path / "config"
    fake_bin = tmp_path / "bin"
    shim_root = tmp_path / "shims"
    state_root = tmp_path / "state"
    controller_root = tmp_path / "controller"
    systemd_root = tmp_path / "systemd"
    current_link = release_root / "current"
    database_revision_file = state_root / "database-revision"
    events_file = state_root / "events"
    restart_marker = state_root / "restart-failed"
    install_marker = state_root / "install-failed"
    daemon_reload_marker = state_root / "daemon-reload-seen"
    backup_timer_unit_file_state = state_root / "backup-timer-unit-file-state"
    backup_timer_active_state = state_root / "backup-timer-active-state"

    for directory in (
        incoming,
        previous,
        artifact,
        config_root,
        fake_bin,
        shim_root,
        state_root,
        controller_root,
        systemd_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    _write_shims(fake_bin=fake_bin, shim_root=shim_root)
    _write_release(
        previous,
        revision=PREVIOUS_REVISION,
        compatible_revisions=previous_compatible_revisions,
        include_contract=bool(previous_compatible_revisions),
        guard_capable=previous_guard_capable,
    )
    _write_release(
        artifact,
        revision=CANDIDATE_REVISION,
        compatible_revisions=(CANDIDATE_REVISION,),
        include_contract=True,
        current_link=current_link,
    )
    _install_runtime_shims(previous, shim_root=shim_root)
    if current_points_to_candidate:
        _write_release(
            candidate,
            revision=CANDIDATE_REVISION,
            compatible_revisions=(CANDIDATE_REVISION,),
            include_contract=True,
        )
        _install_runtime_shims(candidate, shim_root=shim_root)

    archive_path = incoming / "anan-candidate.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for child in artifact.iterdir():
            archive.add(child, arcname=child.name)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (incoming / "anan-candidate.tar.gz.sha256").write_text(
        f"{digest}  {archive_path.name}\n",
        encoding="utf-8",
    )
    data_authority = {
        "environment": "preproduction",
        "database": {"alembicRevision": CANDIDATE_REVISION},
        "compatibleApplicationCommits": [
            CANDIDATE_COMMIT,
            ROLLBACK_COMMIT,
        ],
    }
    incoming_manifest = incoming / "anan-candidate.release.json"
    incoming_manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "releaseId": "candidate",
                "source": {"gitCommit": CANDIDATE_COMMIT},
                "deployment": {
                    "environment": "preproduction",
                    "rollbackReleaseId": "previous",
                },
                "dataAuthorities": {
                    "receipt": {"authority": data_authority},
                },
                "guardCapabilities": {
                    "commercialDataPublication": {
                        "capability": "commercial-data-publication-guard",
                        "contractVersion": ("commercial-data-publication-guard@v1"),
                        "markerPath": ("ops/commercial-data-publication-guard.json"),
                        "markerSha256": hashlib.sha256(
                            (
                                artifact / "ops" / "commercial-data-publication-guard.json"
                            ).read_bytes()
                        ).hexdigest(),
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_digest = hashlib.sha256(incoming_manifest.read_bytes()).hexdigest()
    (incoming / "anan-candidate.release.json.sha256").write_text(
        f"{manifest_digest}  {incoming_manifest.name}\n",
        encoding="utf-8",
    )
    (incoming / "anan-candidate.release.json.sig").write_bytes(b"release-harness-signature")
    incoming_verifier = incoming / "anan-candidate.verifier.py"
    incoming_verifier.write_text(
        "# release harness verifier\n",
        encoding="utf-8",
    )
    verifier_sha256 = hashlib.sha256(incoming_verifier.read_bytes()).hexdigest()
    (incoming / "anan-candidate.wechat-upload-receipt.json").write_text(
        '{"schemaVersion":"release-harness"}\n',
        encoding="utf-8",
    )
    (incoming / "anan-candidate.wechat-upload-receipt.json.sig").write_bytes(
        b"wechat-upload-harness-signature"
    )
    (incoming / "anan-candidate.wechat-upload-info.json").write_text(
        '{"size":{"packages":[{"name":"__FULL__","size":1024}]}}\n',
        encoding="utf-8",
    )
    (incoming / "anan-candidate.wechat-upload.log").write_text(
        "release harness upload log\n",
        encoding="utf-8",
    )
    (incoming / "anan-candidate.data-release-receipt.json").write_text(
        json.dumps(data_authority) + "\n",
        encoding="utf-8",
    )
    (incoming / "anan-candidate.data-release-receipt.json.sig").write_bytes(
        b"data-release-harness-signature"
    )
    data_release_trust_key = config_root / "data-release-trust.pem"
    data_release_trust_key.write_text(
        "release harness public key\n",
        encoding="utf-8",
    )
    release_trust_key = config_root / "release-trust.pem"
    release_trust_key.write_text(
        "release harness application public key\n",
        encoding="utf-8",
    )
    wechat_uploader_trust_key = config_root / "wechat-uploader-trust.pem"
    wechat_uploader_trust_key.write_text(
        "release harness WeChat uploader public key\n",
        encoding="utf-8",
    )

    current_link.symlink_to(candidate if current_points_to_candidate else previous)
    active_control_plane = candidate if current_points_to_candidate else previous
    data_authority_root = config_root / "data-releases"
    data_authority_revision = data_authority_root / "revisions" / ("a" * 64)
    data_authority_revision.mkdir(parents=True)
    (data_authority_revision / "receipt.json").write_text(
        json.dumps(data_authority) + "\n",
        encoding="utf-8",
    )
    data_receipt_sha256 = hashlib.sha256(
        (data_authority_revision / "receipt.json").read_bytes()
    ).hexdigest()
    (data_authority_revision / "receipt.json.sig").write_bytes(b"data-release-harness-signature")
    (data_authority_root / "current").symlink_to(data_authority_revision)
    for release in (previous, candidate):
        activation = release / "release-authority" / "activation.json"
        if not activation.is_file():
            continue
        activation_payload = json.loads(activation.read_text(encoding="utf-8"))
        activation_payload["dataReceiptSha256"] = data_receipt_sha256
        activation.write_text(
            json.dumps(activation_payload) + "\n",
            encoding="utf-8",
        )
    for source in (active_control_plane / "ops" / "systemd").iterdir():
        shutil.copy2(source, systemd_root / source.name)
    for name in ("activate_release.sh", "rollback_release.sh"):
        shutil.copy2(
            active_control_plane / "ops" / "scripts" / name,
            controller_root / name,
        )
    database_revision_file.write_text(database_revision, encoding="utf-8")
    events_file.write_text("", encoding="utf-8")
    if backup_enabled == "true":
        backup_timer_unit_file_state.write_text("enabled\n", encoding="utf-8")
        backup_timer_active_state.write_text("active\n", encoding="utf-8")
    else:
        backup_timer_unit_file_state.write_text("disabled\n", encoding="utf-8")
        backup_timer_active_state.write_text("inactive\n", encoding="utf-8")
    (config_root / "api.env").write_text(
        "WEKNORA_PRIVATE_KNOWLEDGE_MODE=canonical\n"
        f"ESSAY_REVIEW_WORKER_ENABLED={essay_review_worker_enabled}\n"
        f"COACH_NOTIFICATIONS_ENABLED={coach_notifications_enabled}\n"
        "COMMERCIAL_MODE=free_cold_start\n"
        f"ILINK_CHANNEL_ENABLED={ilink_channel_enabled}\n"
        "GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION=knowledge-map@test\n"
        "GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION=question-bank@test\n"
        "GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL=2099-01-01T00:00:00Z\n"
        "TOS_PRESIGN_ENDPOINT=https://assets.example.invalid\n"
        "GONGKAO_POSITION_RELEASE_EXAM_YEAR=2026\n"
        "GONGKAO_POSITION_RULE_SET_VERSION=position-rules@test\n"
        "GONGKAO_POSITION_DATA_VERSION=positions@test\n"
        f"GONGKAO_POSITION_RELEASE_WINDOW_MANIFEST={config_root / 'position-windows.json'}\n",
        encoding="utf-8",
    )
    (config_root / "position-windows.json").write_text(
        '{"windows":[{"fixture":"release-harness"}]}\n',
        encoding="utf-8",
    )
    (config_root / "release-environment").write_text(
        "preproduction\n",
        encoding="utf-8",
    )
    (config_root / "db.env").write_text(
        "DATABASE_URL=postgresql+psycopg:///release-runtime\n"
        "MIGRATION_DATABASE_URL=postgresql+psycopg:///release-test\n"
        "MIGRATION_DATABASE_OS_USER=postgres\n"
        f"MIGRATION_PGPASSFILE={config_root / 'migration.pgpass'}\n"
        f"REQUIRED_DATABASE_REVISION={CANDIDATE_REVISION}\n",
        encoding="utf-8",
    )
    (config_root / "db.env").chmod(0o600)
    (config_root / "backup.env").write_text(
        f"BACKUP_ENABLED={backup_enabled}\n"
        "BACKUP_DATABASE_URL=postgresql://gongkao@127.0.0.1:5432/gongkao\n"
        "PGPASSFILE=/etc/gongkao-dazi/secrets/backup.pgpass\n"
        "BACKUP_DIRECTORY=/var/backups/gongkao-dazi/encrypted\n"
        "AGE_RECIPIENT=age1releaseharness\n"
        "BACKUP_SIGNING_PRIVATE_KEY_FILE=/etc/gongkao-dazi/secrets/backup-signing.pem\n"
        "BACKUP_SOURCE_ENVIRONMENT_ID=preproduction\n"
        "BACKUP_TARGET_AUTHORITY_FILE=/etc/gongkao-dazi/backup-target-authority.json\n"
        "BACKUP_RETENTION_DAYS=30\n"
        "BACKUP_METRICS_FILE=/var/lib/gongkao-backup/metrics/anan-backup.prom\n",
        encoding="utf-8",
    )
    (config_root / "backup.env").chmod(0o600)
    (state_root / "rotated-db.env").write_text(
        "DATABASE_URL=postgresql+psycopg:///rotated-after-preflight\n"
        "MIGRATION_DATABASE_URL=postgresql+psycopg:///release-test\n"
        "MIGRATION_DATABASE_OS_USER=postgres\n"
        f"MIGRATION_PGPASSFILE={config_root / 'migration.pgpass'}\n"
        f"REQUIRED_DATABASE_REVISION={CANDIDATE_REVISION}\n",
        encoding="utf-8",
    )
    workload_environment_names = (
        "worker-data-rights.env",
        "worker-cause-agent.env",
        "worker-external-learning.env",
        "worker-essay-review.env",
        "worker-coach-notification.env",
        "ilink-channel.env",
        "worker-ilink-object-deletion.env",
    )
    for name in workload_environment_names:
        source = REPO_ROOT / "ops" / f"{name}.example"
        content = source.read_text(encoding="utf-8")
        if name == "worker-essay-review.env":
            content = content.replace(
                "ESSAY_REVIEW_WORKER_ENABLED=false",
                f"ESSAY_REVIEW_WORKER_ENABLED={essay_review_worker_enabled}",
            )
        elif name == "worker-coach-notification.env":
            content = content.replace(
                "COACH_NOTIFICATIONS_ENABLED=false",
                f"COACH_NOTIFICATIONS_ENABLED={coach_notifications_enabled}",
            )
        elif name == "ilink-channel.env":
            content = content.replace(
                "ILINK_CHANNEL_ENABLED=false",
                f"ILINK_CHANNEL_ENABLED={ilink_channel_enabled}",
            )
        destination = config_root / name
        destination.write_text(content, encoding="utf-8")
        destination.chmod(0o600)
    (config_root / "db-ca.pem").write_text(
        "release harness database CA\n",
        encoding="utf-8",
    )
    heartbeat_principals = {
        "data-rights": "gongkao_worker_heartbeat_data_rights_login",
        "cause-agent": "gongkao_worker_heartbeat_cause_agent_login",
        "external-learning": "gongkao_worker_heartbeat_external_learning_login",
        "essay-review": "gongkao_worker_heartbeat_essay_review_login",
        "coach-notification": "gongkao_worker_heartbeat_coach_notification_login",
        "ilink": "gongkao_worker_heartbeat_ilink_login",
    }
    for index, (name, principal) in enumerate(heartbeat_principals.items(), start=1):
        heartbeat_environment = config_root / f"worker-heartbeat-{name}.env"
        heartbeat_environment.write_text(
            "WORKER_HEARTBEAT_DATABASE_URL="
            f"postgresql+psycopg://{principal}:"
            f"release-harness-heartbeat-secret-{index}"
            "@127.0.0.1:5432/gongkao_dazi"
            "?sslmode=verify-full"
            f"&sslrootcert={config_root / 'db-ca.pem'}\n",
            encoding="utf-8",
        )
        heartbeat_environment.chmod(0o600)
    migration_pgpass = config_root / "migration.pgpass"
    migration_pgpass.write_text(
        "127.0.0.1:5432:release-test:release-test:unused\n",
        encoding="utf-8",
    )
    migration_pgpass.chmod(0o600)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "STAGING_RELEASE_ROOT": str(release_root),
        "GONGKAO_CURRENT_LINK": str(current_link),
        "GONGKAO_CONFIG_ROOT": str(config_root),
        "GONGKAO_CONTROLLER_ROOT": str(controller_root),
        "GONGKAO_SYSTEMD_ROOT": str(systemd_root),
        "HARNESS_SHIM_ROOT": str(shim_root),
        "HARNESS_DATABASE_REVISION_FILE": str(database_revision_file),
        "HARNESS_EVENTS_FILE": str(events_file),
        "HARNESS_RESTART_MARKER": str(restart_marker),
        "HARNESS_INSTALL_MARKER": str(install_marker),
        "HARNESS_DAEMON_RELOAD_MARKER": str(daemon_reload_marker),
        "HARNESS_BACKUP_TIMER_UNIT_FILE_STATE": str(backup_timer_unit_file_state),
        "HARNESS_BACKUP_TIMER_ACTIVE_STATE": str(backup_timer_active_state),
        "HARNESS_CONFIG_ROTATION_SOURCE": str(state_root / "rotated-db.env"),
        "HARNESS_CONFIG_ROTATION_TARGET": str(config_root / "db.env"),
        "HARNESS_REAL_PYTHON": sys.executable,
        "EXPECTED_DEPLOYMENT_ENVIRONMENT": "preproduction",
        "EXPECTED_ROLLBACK_RELEASE_ID": (
            "candidate" if current_points_to_candidate else "previous"
        ),
        "EXPECTED_GIT_COMMIT": CANDIDATE_COMMIT,
        "EXPECTED_ROLLBACK_GIT_COMMIT": ROLLBACK_COMMIT,
        "EXPECTED_VERIFIER_SHA256": verifier_sha256,
        "DATA_RELEASE_TRUST_PUBLIC_KEY": str(data_release_trust_key),
        "RELEASE_TRUST_PUBLIC_KEY": str(release_trust_key),
        "WECHAT_UPLOADER_TRUST_PUBLIC_KEY": str(wechat_uploader_trust_key),
        "DATA_RELEASE_COORDINATION_LOCK": str(state_root / "data-release.lock"),
        "DATA_RELEASE_AUTHORITY_ROOT": str(data_authority_root),
    }
    backup_environment = config_root / "backup.env"
    current_backup_environment = backup_environment.read_text(encoding="utf-8")
    if previous_backup_required_revision is not None:
        backup_environment.write_text(
            current_backup_environment
            + f"REQUIRED_DATABASE_REVISION={previous_backup_required_revision}\n",
            encoding="utf-8",
        )
        backup_environment.chmod(0o600)
    try:
        _seed_release_config_snapshot(
            previous,
            config_root=config_root,
            commit=ROLLBACK_COMMIT,
            receipt_sha256=data_receipt_sha256,
        )
    finally:
        if previous_backup_required_revision is not None:
            backup_environment.write_text(
                current_backup_environment,
                encoding="utf-8",
            )
            backup_environment.chmod(0o600)
    if current_points_to_candidate:
        _seed_release_config_snapshot(
            candidate,
            config_root=config_root,
            commit=CANDIDATE_COMMIT,
            receipt_sha256=data_receipt_sha256,
        )
    return ReleaseHarness(
        environment=environment,
        release_root=release_root,
        current_link=current_link,
        previous_target=previous,
        candidate_target=candidate,
        database_revision_file=database_revision_file,
        events_file=events_file,
        controller_root=controller_root,
        systemd_root=systemd_root,
    )


def _third_activation_environment() -> dict[str, str]:
    return {
        "EXPECTED_ROLLBACK_RELEASE_ID": "previous",
        "EXPECTED_GIT_COMMIT": CANDIDATE_COMMIT,
        "EXPECTED_ROLLBACK_GIT_COMMIT": ROLLBACK_COMMIT,
        "RELEASE_RETENTION_COUNT": "2",
    }


def _stage_activation_release(
    harness: ReleaseHarness,
    *,
    release_id: str,
    backup_fault: str = "",
) -> None:
    artifact = harness.release_root.parent / f"{release_id}-artifact"
    artifact.mkdir()
    _write_release(
        artifact,
        revision=CANDIDATE_REVISION,
        compatible_revisions=(CANDIDATE_REVISION,),
        include_contract=True,
        current_link=harness.current_link,
    )
    backup_service = artifact / "ops" / "systemd" / "gongkao-dazi-backup.service"
    backup_timer = artifact / "ops" / "systemd" / "gongkao-dazi-backup.timer"
    backup_script = artifact / "ops" / "scripts" / "backup.sh"
    match backup_fault:
        case "":
            pass
        case "missing-service":
            backup_service.unlink()
        case "invalid-service":
            backup_service.write_text(
                backup_service.read_text(encoding="utf-8").replace(
                    f"ExecStart={harness.current_link}/ops/scripts/backup.sh",
                    (f"ExecStart={harness.candidate_target}/ops/scripts/backup.sh"),
                ),
                encoding="utf-8",
            )
        case "missing-timer":
            backup_timer.unlink()
        case "invalid-timer":
            backup_timer.write_text(
                backup_timer.read_text(encoding="utf-8").replace(
                    "Unit=gongkao-dazi-backup.service",
                    "Unit=legacy-backup.service",
                ),
                encoding="utf-8",
            )
        case "missing-script":
            backup_script.unlink()
        case "invalid-script":
            backup_script.chmod(0o644)
        case _:
            raise AssertionError(f"unsupported backup fault: {backup_fault}")

    incoming = harness.release_root / "incoming"
    archive_path = incoming / f"anan-{release_id}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for child in artifact.iterdir():
            archive.add(child, arcname=child.name)
    archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (incoming / f"anan-{release_id}.tar.gz.sha256").write_text(
        f"{archive_digest}  {archive_path.name}\n",
        encoding="utf-8",
    )

    data_authority_root = Path(harness.environment["DATA_RELEASE_AUTHORITY_ROOT"])
    data_receipt = (data_authority_root / "current").resolve() / "receipt.json"
    data_receipt_bytes = data_receipt.read_bytes()
    data_authority = json.loads(data_receipt_bytes)
    manifest = incoming / f"anan-{release_id}.release.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "releaseId": release_id,
                "source": {"gitCommit": CANDIDATE_COMMIT},
                "deployment": {
                    "environment": "preproduction",
                    "rollbackReleaseId": "previous",
                },
                "dataAuthorities": {
                    "receipt": {"authority": data_authority},
                },
                "guardCapabilities": {
                    "commercialDataPublication": {
                        "capability": "commercial-data-publication-guard",
                        "contractVersion": ("commercial-data-publication-guard@v1"),
                        "markerPath": ("ops/commercial-data-publication-guard.json"),
                        "markerSha256": hashlib.sha256(
                            (
                                artifact / "ops" / "commercial-data-publication-guard.json"
                            ).read_bytes()
                        ).hexdigest(),
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (incoming / f"anan-{release_id}.release.json.sha256").write_text(
        f"{manifest_digest}  {manifest.name}\n",
        encoding="utf-8",
    )
    (incoming / f"anan-{release_id}.release.json.sig").write_bytes(b"release-harness-signature")
    verifier = incoming / f"anan-{release_id}.verifier.py"
    verifier.write_text("# release harness verifier\n", encoding="utf-8")
    assert (
        hashlib.sha256(verifier.read_bytes()).hexdigest()
        == (harness.environment["EXPECTED_VERIFIER_SHA256"])
    )
    (incoming / f"anan-{release_id}.wechat-upload-receipt.json").write_text(
        '{"schemaVersion":"release-harness"}\n',
        encoding="utf-8",
    )
    (incoming / f"anan-{release_id}.wechat-upload-receipt.json.sig").write_bytes(
        b"wechat-upload-harness-signature"
    )
    (incoming / f"anan-{release_id}.wechat-upload-info.json").write_text(
        '{"size":{"packages":[{"name":"__FULL__","size":1024}]}}\n',
        encoding="utf-8",
    )
    (incoming / f"anan-{release_id}.wechat-upload.log").write_text(
        "release harness upload log\n",
        encoding="utf-8",
    )
    (incoming / f"anan-{release_id}.data-release-receipt.json").write_bytes(data_receipt_bytes)
    (incoming / f"anan-{release_id}.data-release-receipt.json.sig").write_bytes(
        b"data-release-harness-signature"
    )


def _write_release(
    root: Path,
    *,
    revision: str,
    compatible_revisions: tuple[str, ...],
    include_contract: bool,
    guard_capable: bool = True,
    current_link: Path | None = None,
) -> None:
    (root / "backend" / "migrations").mkdir(parents=True)
    (root / "backend" / "HEAD_REVISION").write_text(revision, encoding="utf-8")
    (root / "backend" / "alembic.ini").write_text(
        "[alembic]\nscript_location = %(here)s/migrations\n",
        encoding="utf-8",
    )
    (root / "backend" / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (root / "backend" / "pyproject.toml").write_text(
        '[project]\nname = "release-harness"\nversion = "0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (root / "ops" / "scripts").mkdir(parents=True)
    (root / "ops" / "sysusers.d").mkdir(parents=True)
    (root / "ops" / "tmpfiles.d").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "ops" / "sysusers.d" / "gongkao-dazi-workers.conf",
        root / "ops" / "sysusers.d" / "gongkao-dazi-workers.conf",
    )
    shutil.copy2(
        REPO_ROOT / "ops" / "tmpfiles.d" / "gongkao-dazi-backup.conf",
        root / "ops" / "tmpfiles.d" / "gongkao-dazi-backup.conf",
    )
    for name in (
        "worker-data-rights.env.example",
        "worker-cause-agent.env.example",
        "worker-external-learning.env.example",
        "worker-essay-review.env.example",
        "worker-coach-notification.env.example",
        "ilink-channel.env.example",
        "worker-ilink-object-deletion.env.example",
    ):
        shutil.copy2(REPO_ROOT / "ops" / name, root / "ops" / name)
    if guard_capable:
        (root / "ops" / "commercial-data-publication-guard.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "anan-release-capability/v1",
                    "capability": "commercial-data-publication-guard",
                    "contractVersion": ("commercial-data-publication-guard@v1"),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    (root / "ops" / "scripts" / "activate_release.sh").write_text(
        ACTIVATE_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "ops" / "scripts" / "rollback_release.sh").write_text(
        ROLLBACK_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    shutil.copy2(
        BACKUP_SCRIPT,
        root / "ops" / "scripts" / "backup.sh",
    )
    _write_executable(
        root / "ops" / "scripts" / "probe_tos_public_presign.py",
        "#!/usr/bin/env python3\n",
    )
    _write_executable(
        root / "ops" / "scripts" / "data_release_authority.py",
        "#!/usr/bin/env python3\n",
    )
    for unit in (
        *RUNTIME_UNITS,
        ESSAY_REVIEW_WORKER_UNIT,
        COACH_NOTIFICATION_WORKER_UNIT,
        ILINK_WORKER_UNIT,
    ):
        unit_path = root / "ops" / "systemd" / f"{unit}.service"
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_source = (REPO_ROOT / "ops" / "systemd" / f"{unit}.service").read_text(
            encoding="utf-8"
        )
        unit_path.write_text(
            unit_source.replace(
                "Description=",
                f"Description={unit}@{revision} ",
                1,
            ),
            encoding="utf-8",
        )
    backup_systemd_root = root / "ops" / "systemd"
    for name in (
        "gongkao-dazi-backup.service",
        "gongkao-dazi-backup.timer",
    ):
        source = REPO_ROOT / "ops" / "systemd" / name
        destination = backup_systemd_root / name
        if name == "gongkao-dazi-backup.service":
            destination.write_text(
                source.read_text(encoding="utf-8").replace(
                    "/srv/gongkao-dazi/current",
                    str(current_link or root.parent / "current"),
                ),
                encoding="utf-8",
            )
            destination.chmod(source.stat().st_mode & 0o777)
        else:
            shutil.copy2(source, destination)
    essay_review_entrypoint = root / "backend" / "app" / "workers" / "essay_reviews.py"
    essay_review_entrypoint.parent.mkdir(parents=True, exist_ok=True)
    essay_review_entrypoint.write_text("# essay review worker\n", encoding="utf-8")
    coach_notification_entrypoint = root / "backend" / "app" / "workers" / "coach_notifications.py"
    coach_notification_entrypoint.write_text(
        "# coach notification worker\n",
        encoding="utf-8",
    )
    ilink_entrypoint = root / "backend" / "app" / "workers" / "ilink_object_deletions.py"
    ilink_entrypoint.write_text("# ilink object deletion worker\n", encoding="utf-8")
    compile_position_release = root / "backend" / "scripts" / "compile_position_rule_release.py"
    compile_position_release.parent.mkdir(parents=True, exist_ok=True)
    compile_position_release.write_text(
        "# position release compiler harness entrypoint\n",
        encoding="utf-8",
    )
    refresh = root / "scripts" / "server_refresh_knowledge_map_nodes.sh"
    _write_executable(refresh, "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n")
    release_name = "previous" if root.name == "previous" else "candidate"
    release_commit = ROLLBACK_COMMIT if release_name == "previous" else CANDIDATE_COMMIT
    authority_root = root / "release-authority"
    authority_root.mkdir(parents=True, exist_ok=True)
    release_manifest = {
        "schemaVersion": 4,
        "releaseId": release_name,
        "source": {"gitCommit": release_commit},
        "deployment": {"environment": "preproduction"},
    }
    if guard_capable:
        guard_marker = root / "ops" / "commercial-data-publication-guard.json"
        release_manifest["guardCapabilities"] = {
            "commercialDataPublication": {
                "capability": "commercial-data-publication-guard",
                "contractVersion": "commercial-data-publication-guard@v1",
                "markerPath": "ops/commercial-data-publication-guard.json",
                "markerSha256": hashlib.sha256(guard_marker.read_bytes()).hexdigest(),
            }
        }
    (authority_root / "release.json").write_text(
        json.dumps(release_manifest) + "\n",
        encoding="utf-8",
    )
    (authority_root / "release.json.sig").write_bytes(b"release-harness-signature")
    (authority_root / "data-release-receipt.json").write_text(
        json.dumps(
            {
                "environment": "preproduction",
                "database": {"alembicRevision": CANDIDATE_REVISION},
                "compatibleApplicationCommits": [
                    CANDIDATE_COMMIT,
                    ROLLBACK_COMMIT,
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (authority_root / "data-release-receipt.json.sig").write_bytes(
        b"data-release-harness-signature"
    )
    if root.name in {"previous", "candidate"}:
        (authority_root / "activation.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "anan-application-activation/v1",
                    "status": "succeeded",
                    "releaseId": release_name,
                    "gitCommit": release_commit,
                    "environment": "preproduction",
                    "previousReleaseId": "bootstrap",
                    "dataReceiptSha256": "a" * 64,
                    "activatedAt": "2026-07-30T00:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    if include_contract:
        (root / "ops" / "release-db-compatibility.env").write_text(
            "ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT="
            "artifact-db-compatibility@v1\n"
            f"ARTIFACT_DATABASE_MIGRATION_HEAD={revision}\n"
            "ARTIFACT_DATABASE_COMPATIBLE_REVISIONS="
            f"{','.join(compatible_revisions)}\n",
            encoding="utf-8",
        )


def _global_receipt_sha256(harness: ReleaseHarness) -> str:
    authority_root = Path(harness.environment["DATA_RELEASE_AUTHORITY_ROOT"])
    receipt = (authority_root / "current").resolve() / "receipt.json"
    return hashlib.sha256(receipt.read_bytes()).hexdigest()


def _write_current_rollback_eligibility(
    harness: ReleaseHarness,
    *,
    activation_receipt: str,
    eligibility_receipt: str | None = None,
) -> None:
    activation = harness.previous_target / "release-authority" / "activation.json"
    activation_payload = json.loads(activation.read_text(encoding="utf-8"))
    activation_payload["dataReceiptSha256"] = activation_receipt
    activation.write_text(
        json.dumps(activation_payload) + "\n",
        encoding="utf-8",
    )
    eligibility_root = harness.previous_target / "release-authority" / "rollback-eligible"
    eligibility_root.mkdir(parents=True, exist_ok=True)
    eligibility = {
        "schemaVersion": "anan-rollback-eligibility/v1",
        "status": "eligible",
        "targetReleaseId": "previous",
        "authorizedByReleaseId": "candidate",
        "authorizedByGitCommit": CANDIDATE_COMMIT,
        "environment": "preproduction",
        "dataReceiptSha256": (eligibility_receipt or _global_receipt_sha256(harness)),
        "recordedAt": "2026-07-30T00:00:00+00:00",
    }
    (eligibility_root / "candidate.json").write_text(
        json.dumps(eligibility) + "\n",
        encoding="utf-8",
    )


def _install_runtime_shims(root: Path, *, shim_root: Path) -> None:
    bin_root = root / "backend" / ".venv" / "bin"
    bin_root.mkdir(parents=True, exist_ok=True)
    for name in ("python", "alembic", "uvicorn"):
        source = shim_root / name
        target = bin_root / name
        target.write_bytes(source.read_bytes())
        target.chmod(0o755)


def _write_shims(*, fake_bin: Path, shim_root: Path) -> None:
    _write_executable(
        fake_bin / "env",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "-i" ]]; then
  exec /usr/bin/env "$@"
fi
shift
assignments=()
while [[ "${1:-}" == *=* ]]; do
  assignments+=("$1")
  shift
done
if [[ -n "${HARNESS_EVENTS_FILE:-}" ]]; then
  assignments+=("HARNESS_EVENTS_FILE=${HARNESS_EVENTS_FILE}")
fi
if [[ -n "${HARNESS_FAULT:-}" ]]; then
  assignments+=("HARNESS_FAULT=${HARNESS_FAULT}")
fi
exec /usr/bin/env -i "${assignments[@]}" "$@"
""",
    )
    _write_executable(
        shim_root / "python",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
  exit 0
fi
if [[ "${1:-}" == "-c" ]]; then
  if [[ "${2:-}" == *"GroundedEssayReviewWorker"* ]]; then
    printf '%s\\n' "essay-worker-constructor" >> "${HARNESS_EVENTS_FILE}"
    if [[ "${HARNESS_FAULT:-}" == "essay-worker" ]]; then
      exit 45
    fi
  fi
  exit 0
fi
if [[ "${1:-}" == */ops/scripts/probe_tos_public_presign.py ]]; then
  exit 0
fi
if [[ "${1:-}" == */ops/scripts/data_release_authority.py ]]; then
  printf '%s\\n' "data-release-authority verify $*" \
    >> "${HARNESS_EVENTS_FILE}"
  if [[ "${HARNESS_FAULT:-}" == "data-authority" ]]; then
    exit 47
  fi
  printf '%s\\n' '{"schemaVersion":"release-harness-verification"}'
  exit 0
fi
if [[ "${1:-}" == */ops/scripts/commercial_data_publication.py \
  && "${2:-}" == "assert-pairing" ]]; then
  printf '%s\\n' "commercial-data-publication assert-pairing $*" \
    >> "${HARNESS_EVENTS_FILE}"
  exit 0
fi
if [[ "${1:-}" == */backend/scripts/compile_position_rule_release.py ]]; then
  printf '%s\\n' "position-release-compile $*" >> "${HARNESS_EVENTS_FILE}"
  exit 0
fi
if [[ "${1:-}" != "-m" || "${2:-}" != "app.core.database_compatibility" ]]; then
  exit 40
fi
command_name="$3"
shift 3
backend_root=""
contract=""
revision=""
while (( $# )); do
  case "$1" in
    --backend-root) backend_root="$2"; shift 2 ;;
    --contract) contract="$2"; shift 2 ;;
    --revision) revision="$2"; shift 2 ;;
    *) exit 41 ;;
  esac
done
head_revision="$(<"${backend_root}/HEAD_REVISION")"
if [[ "$command_name" == "head" ]]; then
  printf '%s\\n' "$head_revision"
  exit 0
fi
test -f "$contract"
declared_head="$(sed -n 's/^ARTIFACT_DATABASE_MIGRATION_HEAD=//p' "$contract")"
compatible="$(sed -n 's/^ARTIFACT_DATABASE_COMPATIBLE_REVISIONS=//p' "$contract")"
[[ "$declared_head" == "$head_revision" ]]
contains_revision() {
  local candidate="$1"
  local normalized="${compatible//,/ }"
  local value
  for value in $normalized; do
    if [[ "$value" == "$candidate" ]]; then
      return 0
    fi
  done
  return 1
}
case "$command_name" in
  compatible-revisions)
    printf '%s\\n' "$compatible"
    ;;
  assert-revision)
    contains_revision "$revision"
    printf '%s\\n' "$revision"
    ;;
  assert-current)
    current_revision="$(<"${HARNESS_DATABASE_REVISION_FILE}")"
    contains_revision "$current_revision"
    printf '%s\\n' "$current_revision"
    ;;
  *)
    exit 42
    ;;
esac
""",
    )
    _write_executable(
        shim_root / "alembic",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'alembic %s\\n' "$*" >> "${HARNESS_EVENTS_FILE}"
if [[ " $* " == *" upgrade head "* ]]; then
  printf '%s\\n' "$(<HEAD_REVISION)" > "${HARNESS_DATABASE_REVISION_FILE}"
fi
""",
    )
    _write_executable(
        shim_root / "uvicorn",
        "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n",
    )
    _write_executable(
        fake_bin / "python3",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-" ]]; then
  exec "${HARNESS_REAL_PYTHON}" "$@"
fi
if [[ "${1:-}" == *.verifier.py && "${2:-}" == "verify" ]]; then
  printf '%s\\n' "release-manifest-verifier $*" \
    >> "${HARNESS_EVENTS_FILE}"
  if [[ "${HARNESS_FAULT:-}" == "manifest-verifier" ]]; then
    exit 48
  fi
  exit 0
fi
[[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]
mkdir -p "$3/bin"
cp "${HARNESS_SHIM_ROOT}/python" "$3/bin/python"
cp "${HARNESS_SHIM_ROOT}/alembic" "$3/bin/alembic"
cp "${HARNESS_SHIM_ROOT}/uvicorn" "$3/bin/uvicorn"
chmod 0755 "$3/bin/python" "$3/bin/alembic" "$3/bin/uvicorn"
""",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "sync" ]]
project=""
while (( $# )); do
  case "$1" in
    --project) project="$2"; shift 2 ;;
    *) shift ;;
  esac
done
test -f "${project}/uv.lock"
test -f "${project}/pyproject.toml"
mkdir -p "${project}/.venv/bin"
for name in python alembic uvicorn; do
  cp "${HARNESS_SHIM_ROOT}/${name}" "${project}/.venv/bin/${name}"
  chmod 0755 "${project}/.venv/bin/${name}"
done
printf '%s\\n' "uv sync --frozen --no-dev --no-editable" \
  >> "${HARNESS_EVENTS_FILE}"
""",
    )
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-C" ]]; then shift 2; fi
if [[ "${1:-}" == "-u" ]]; then shift 2; fi
if [[ "${1:-}" == "env" ]]; then
  shift
  if [[ "${1:-}" == "-i" ]]; then shift; fi
  while [[ "${1:-}" == *=* ]]; do export "$1"; shift; done
fi
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "install",
        """#!/usr/bin/env bash
set -euo pipefail
directory_mode=false
while (( $# )); do
  case "$1" in
    -D) shift ;;
    -m) shift 2 ;;
    -d) directory_mode=true; shift ;;
    *) break ;;
  esac
done
if [[ "${directory_mode}" == "true" ]]; then
  (( $# > 0 ))
  mkdir -p "$@"
  exit 0
fi
[[ $# == 2 ]]
mkdir -p "$(dirname "$2")"
cp "$1" "$2"
if [[ "${HARNESS_FAULT:-}" == "control-plane-install" \
  && "$2" == "${GONGKAO_SYSTEMD_ROOT}/"* \
  && ! -e "${HARNESS_INSTALL_MARKER}" ]]; then
  touch "${HARNESS_INSTALL_MARKER}"
  exit 46
fi
""",
    )
    _write_executable(
        fake_bin / "rm",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${HARNESS_FAULT:-}" == "post-donor-prune-cleanup" ]]; then
  for argument in "$@"; do
    if [[ "$argument" == \
      "${STAGING_RELEASE_ROOT}/incoming/anan-third.tar.gz" ]]; then
      exit 56
    fi
  done
fi
exec /bin/rm "$@"
""",
    )
    _write_executable(
        fake_bin / "find",
        """#!/usr/bin/env bash
set -euo pipefail
args=()
for arg in "$@"; do
  if [[ "$arg" == "/022" ]]; then
    args+=("-022")
  else
    args+=("$arg")
  fi
done
exec /usr/bin/find "${args[@]}"
""",
    )
    _write_executable(
        fake_bin / "stat",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-c" && "${2:-}" == "%u" ]]; then
  printf '%s\\n' 0
  exit 0
fi
if [[ "${1:-}" == "-c" && "${2:-}" == "%a" ]]; then
  printf '%s\\n' 600
  exit 0
fi
exec /usr/bin/stat "$@"
""",
    )
    _write_executable(
        fake_bin / "id",
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  -u)
    case "${2:-postgres}" in
      postgres) printf '%s\\n' 0 ;;
      deploy) printf '%s\\n' 1000 ;;
      gongkao-wkr-data) printf '%s\\n' 2001 ;;
      gongkao-wkr-cause) printf '%s\\n' 2002 ;;
      gongkao-wkr-external) printf '%s\\n' 2003 ;;
      gongkao-wkr-essay) printf '%s\\n' 2004 ;;
      gongkao-wkr-coach) printf '%s\\n' 2005 ;;
      gongkao-wkr-ilink) printf '%s\\n' 2006 ;;
      gongkao-backup) printf '%s\\n' 2100 ;;
      *) exit 1 ;;
    esac
    ;;
  -gn)
    printf '%s\\n' "${2:?}"
    ;;
  *)
    exit 1
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "getent",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "passwd" ]]
user="${2:?}"
uid="$(id -u "${user}")"
printf '%s:x:%s:%s::/nonexistent:/usr/sbin/nologin\\n' \
  "${user}" "${uid}" "${uid}"
""",
    )
    _write_executable(
        fake_bin / "systemd-sysusers",
        """#!/usr/bin/env bash
set -euo pipefail
test -f "${1:?}"
printf 'systemd-sysusers %s\\n' "$1" >> "${HARNESS_EVENTS_FILE}"
""",
    )
    _write_executable(
        fake_bin / "systemd-tmpfiles",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--create" ]]
test -f "${2:?}"
printf 'systemd-tmpfiles %s %s\\n' "$1" "$2" >> "${HARNESS_EVENTS_FILE}"
""",
    )
    _write_executable(
        fake_bin / "flock",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'flock %s\\n' "$*" >> "${HARNESS_EVENTS_FILE}"
if [[ "${HARNESS_REAL_FLOCK:-false}" == "true" ]]; then
  "${HARNESS_REAL_PYTHON}" -c \
    'import fcntl; fcntl.flock(9, fcntl.LOCK_EX)'
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "openssl",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'openssl %s\\n' "$*" >> "${HARNESS_EVENTS_FILE}"
exit 0
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
while [[ "${1:-}" == "--no-pager" || "${1:-}" == "--full" ]]; do shift; done
command_name="${1:?}"
shift
if [[ "$command_name" != "show" ]]; then
  printf 'systemctl %s %s\\n' "$command_name" "$*" >> "${HARNESS_EVENTS_FILE}"
fi
if [[ "$command_name" == "disable" \
  && "$*" == "--now gongkao-dazi-backup.timer" ]]; then
  if [[ "${HARNESS_FAULT:-}" == "backup-timer-disable" ]]; then
    exit 45
  fi
  printf '%s\\n' "disabled" > "${HARNESS_BACKUP_TIMER_UNIT_FILE_STATE}"
  printf '%s\\n' "inactive" > "${HARNESS_BACKUP_TIMER_ACTIVE_STATE}"
  exit 0
fi
if [[ "$command_name" == "enable" \
  && "$*" == "--now gongkao-dazi-backup.timer" ]]; then
  printf '%s\\n' "enabled" > "${HARNESS_BACKUP_TIMER_UNIT_FILE_STATE}"
  printf '%s\\n' "active" > "${HARNESS_BACKUP_TIMER_ACTIVE_STATE}"
  exit 0
fi
if [[ "$command_name" == "show" ]]; then
  case "$*" in
    "--property=UnitFileState --value gongkao-dazi-backup.timer")
      cat "${HARNESS_BACKUP_TIMER_UNIT_FILE_STATE}"
      ;;
    "--property=ActiveState --value gongkao-dazi-backup.timer")
      cat "${HARNESS_BACKUP_TIMER_ACTIVE_STATE}"
      ;;
    "--property=DropInPaths --value gongkao-dazi-backup.service")
      printf '%s\\n' "${HARNESS_BACKUP_SERVICE_DROPIN_PATHS:-}"
      ;;
    "--property=DropInPaths --value gongkao-dazi-backup.timer")
      printf '%s\\n' "${HARNESS_BACKUP_TIMER_DROPIN_PATHS:-}"
      ;;
    *)
      exit 47
      ;;
  esac
  exit 0
fi
if [[ "$command_name" == "daemon-reload" \
  && "${HARNESS_FAULT:-}" == "control-plane-restore" ]]; then
  if [[ -e "${HARNESS_DAEMON_RELOAD_MARKER}" ]]; then
    exit 49
  fi
  touch "${HARNESS_DAEMON_RELOAD_MARKER}"
fi
if [[ "$command_name" == "restart" && "${HARNESS_FAULT:-}" == "restart" ]] \
  && [[ ! -e "${HARNESS_RESTART_MARKER}" ]]; then
  touch "${HARNESS_RESTART_MARKER}"
  exit 44
fi
if [[ "$command_name" == "restart" \
  && "${HARNESS_ROTATE_CONFIG_ON_RESTART:-false}" == "true" \
  && ! -e "${HARNESS_CONFIG_ROTATION_TARGET}.rotated" ]]; then
  temporary="${HARNESS_CONFIG_ROTATION_TARGET}.rotation"
  cp "${HARNESS_CONFIG_ROTATION_SOURCE}" "${temporary}"
  mv -f "${temporary}" "${HARNESS_CONFIG_ROTATION_TARGET}"
  touch "${HARNESS_CONFIG_ROTATION_TARGET}.rotated"
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${HARNESS_FAULT:-}" == "readiness" \
  || "${HARNESS_FAULT:-}" == "control-plane-restore" ]]; then
  exit 22
fi
printf '{}\\n'
""",
    )
    _write_executable(
        fake_bin / "sleep",
        "#!/usr/bin/env bash\nexit 0\n",
    )


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _control_plane_snapshot(harness: ReleaseHarness) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for root in (harness.systemd_root, harness.controller_root):
        for path in sorted(root.iterdir()):
            if path.is_file():
                snapshot[f"{root.name}/{path.name}"] = path.read_bytes()
    return snapshot


def _systemctl_events(harness: ReleaseHarness) -> list[str]:
    return [
        line
        for line in harness.events_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("systemctl ")
    ]


def _backup_timer_states(harness: ReleaseHarness) -> tuple[str, str]:
    return (
        Path(harness.environment["HARNESS_BACKUP_TIMER_UNIT_FILE_STATE"])
        .read_text(encoding="utf-8")
        .strip(),
        Path(harness.environment["HARNESS_BACKUP_TIMER_ACTIVE_STATE"])
        .read_text(encoding="utf-8")
        .strip(),
    )


def _write_backup_compatibility_entrypoints(harness: ReleaseHarness) -> None:
    _write_executable(
        harness.previous_target / "ops" / "scripts" / "backup.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail
: "${{REQUIRED_DATABASE_REVISION:?legacy revision is required}}"
[[ "${{REQUIRED_DATABASE_REVISION}}" == "{PREVIOUS_REVISION}" ]]
printf '%s\\n' "previous-backup-controller"
""",
    )
    _write_executable(
        harness.candidate_target / "ops" / "scripts" / "backup.sh",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${REQUIRED_DATABASE_REVISION+x}" != "x" ]]
[[ "${BACKUP_ENABLED}" == "false" ]]
printf '%s\\n' "candidate-backup-controller"
""",
    )


def _installed_backup_binding(harness: ReleaseHarness) -> tuple[Path, Path]:
    service = harness.systemd_root / "gongkao-dazi-backup.service"
    values: dict[str, list[str]] = {"ExecStart": [], "EnvironmentFile": []}
    for raw in service.read_text(encoding="utf-8").splitlines():
        for key in values:
            prefix = f"{key}="
            if raw.startswith(prefix):
                values[key].append(raw.removeprefix(prefix))
    assert len(values["ExecStart"]) == 1
    assert len(values["EnvironmentFile"]) == 1
    entrypoint = values["ExecStart"][0]
    environment = values["EnvironmentFile"][0]
    assert entrypoint and not any(character.isspace() for character in entrypoint)
    assert environment and not environment.startswith("-")
    return Path(entrypoint), Path(environment)


def _execute_installed_backup_binding(
    harness: ReleaseHarness,
) -> subprocess.CompletedProcess[str]:
    entrypoint, environment_file = _installed_backup_binding(harness)
    assignments: dict[str, str] = {}
    for raw in environment_file.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#"):
            continue
        name, value = raw.split("=", 1)
        assert name not in assignments
        assignments[name] = value
    return subprocess.run(
        [str(entrypoint)],
        check=False,
        env={"PATH": os.environ["PATH"], **assignments},
        capture_output=True,
        text=True,
        timeout=5,
    )


def _assert_runtime_identity(
    harness: ReleaseHarness,
    *,
    commit: str,
) -> None:
    receipt = Path(harness.environment["DATA_RELEASE_AUTHORITY_ROOT"]) / "current" / "receipt.json"
    receipt_sha256 = hashlib.sha256(receipt.read_bytes()).hexdigest()
    identity = _runtime_snapshot(harness, harness.current_link.resolve()) / "release-runtime.env"
    assert identity.read_text(encoding="utf-8") == (
        "APP_ENV=preproduction\n"
        f"APP_GIT_COMMIT={commit}\n"
        f"APP_DATA_RECEIPT_SHA256={receipt_sha256}\n"
    )
    assert identity.stat().st_mode & 0o777 == 0o400


def _seed_release_config_snapshot(
    release: Path,
    *,
    config_root: Path,
    commit: str,
    receipt_sha256: str,
) -> None:
    snapshot = config_root.parent / "releases" / "runtime-config" / release.name
    snapshot.mkdir(mode=0o700, parents=True)
    for source in config_root.glob("*.env"):
        destination = snapshot / source.name
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o400)
    (snapshot / "release-runtime.env").write_text(
        "APP_ENV=preproduction\n"
        f"APP_GIT_COMMIT={commit}\n"
        f"APP_DATA_RECEIPT_SHA256={receipt_sha256}\n",
        encoding="utf-8",
    )
    (snapshot / "release-runtime.env").chmod(0o400)
    entries = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(snapshot.glob("*.env"))
    ]
    (snapshot / "config.snapshot.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")
    (snapshot / "config.snapshot.sha256").chmod(0o400)
    snapshot.chmod(0o500)


def _snapshot_manifest(snapshot: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    for raw in (snapshot / "config.snapshot.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = raw.split("  ", 1)
        content = (snapshot / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
        entries[name] = content
    return entries


def _runtime_snapshot(harness: ReleaseHarness, release: Path) -> Path:
    return harness.release_root / "runtime-config" / release.name
