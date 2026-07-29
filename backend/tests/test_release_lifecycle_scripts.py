from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVATE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "activate_release.sh"
ROLLBACK_SCRIPT = REPO_ROOT / "ops" / "scripts" / "rollback_release.sh"
PREVIOUS_REVISION = "20260727_69"
CANDIDATE_REVISION = "20260728_70"
RUNTIME_UNITS = (
    "gongkao-dazi-api",
    "gongkao-dazi-external-learning-worker",
    "gongkao-dazi-data-rights-worker",
)
ESSAY_REVIEW_WORKER_UNIT = "gongkao-dazi-essay-review-worker"
CAUSE_AGENT_WORKER_UNIT = "gongkao-dazi-cause-agent-worker"


@dataclass(frozen=True, slots=True)
class ReleaseHarness:
    environment: dict[str, str]
    release_root: Path
    current_link: Path
    previous_target: Path
    candidate_target: Path
    database_revision_file: Path
    events_file: Path

    def activate(self, *, fault: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ACTIVATE_SCRIPT), "candidate"],
            check=False,
            env={**self.environment, "HARNESS_FAULT": fault},
            capture_output=True,
            text=True,
            timeout=20,
        )

    def rollback(self, *, fault: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROLLBACK_SCRIPT), "previous"],
            check=False,
            env={**self.environment, "HARNESS_FAULT": fault},
            capture_output=True,
            text=True,
            timeout=20,
        )


@pytest.mark.parametrize("previous_contract", ("missing", "incompatible"))
def test_activation_rejects_unsafe_previous_before_database_upgrade(
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
    assert harness.database_revision_file.read_text(encoding="utf-8").strip() == (PREVIOUS_REVISION)
    assert "alembic upgrade head" not in harness.events_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("fault", ("restart", "readiness"))
def test_activation_restores_only_preflighted_previous_after_upgrade_failure(
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
    assert "alembic upgrade head" in events
    assert events.count("systemctl restart") >= 2


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
    assert f"systemctl enable {CAUSE_AGENT_WORKER_UNIT}" in events
    restart = next(
        line for line in events.splitlines() if line.startswith("systemctl restart ")
    )
    assert restart.split().count(CAUSE_AGENT_WORKER_UNIT) == 1


def test_successful_activation_keeps_bounded_releases_and_consumes_artifact(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
    )
    stale_releases = tuple(
        harness.release_root / f"stale-{index}" for index in range(4)
    )
    for index, release in enumerate(stale_releases):
        release.mkdir()
        os.utime(release, (index, index))

    result = harness.activate()

    assert result.returncode == 0, result.stderr
    retained = {
        path.name
        for path in harness.release_root.iterdir()
        if path.is_dir() and path.name not in {"current", "incoming"}
    }
    assert retained == {"candidate", "previous", "stale-3"}
    assert list((harness.release_root / "incoming").iterdir()) == []


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


def test_rollback_enables_and_validates_configured_essay_review_worker(
    tmp_path: Path,
) -> None:
    harness = _release_harness(
        tmp_path,
        previous_compatible_revisions=(PREVIOUS_REVISION, CANDIDATE_REVISION),
        database_revision=CANDIDATE_REVISION,
        current_points_to_candidate=True,
        essay_review_worker_enabled="true",
    )

    result = harness.rollback()

    assert result.returncode == 0, result.stderr
    events = harness.events_file.read_text(encoding="utf-8")
    assert "essay-worker-constructor" in events
    assert f"systemctl enable {ESSAY_REVIEW_WORKER_UNIT}" in events
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

    result = harness.rollback(fault="essay-worker")

    assert result.returncode != 0
    assert harness.current_link.resolve() == harness.candidate_target
    assert "essay-worker-constructor" in harness.events_file.read_text(encoding="utf-8")


def _release_harness(
    tmp_path: Path,
    *,
    previous_compatible_revisions: tuple[str, ...],
    database_revision: str = PREVIOUS_REVISION,
    current_points_to_candidate: bool = False,
    essay_review_worker_enabled: str = "false",
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
    )
    _write_release(
        artifact,
        revision=CANDIDATE_REVISION,
        compatible_revisions=(CANDIDATE_REVISION,),
        include_contract=True,
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

    current_link.symlink_to(candidate if current_points_to_candidate else previous)
    database_revision_file.write_text(database_revision, encoding="utf-8")
    events_file.write_text("", encoding="utf-8")
    (config_root / "api.env").write_text(
        "WEKNORA_PRIVATE_KNOWLEDGE_MODE=canonical\n"
        f"ESSAY_REVIEW_WORKER_ENABLED={essay_review_worker_enabled}\n"
        "COACH_NOTIFICATIONS_ENABLED=false\n"
        "COMMERCIAL_MODE=free_cold_start\n"
        "ILINK_CHANNEL_ENABLED=false\n"
        "GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION=knowledge-map@test\n"
        "GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION=question-bank@test\n"
        "GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL=2099-01-01T00:00:00Z\n"
        "GONGKAO_KNOWLEDGE_MAP_DATABASE_URL=postgresql:///knowledge\n",
        encoding="utf-8",
    )
    (config_root / "db.env").write_text(
        "MIGRATION_DATABASE_URL=postgresql+psycopg:///release-test\n"
        "MIGRATION_DATABASE_OS_USER=postgres\n"
        f"REQUIRED_DATABASE_REVISION={CANDIDATE_REVISION}\n",
        encoding="utf-8",
    )
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
    }
    return ReleaseHarness(
        environment=environment,
        release_root=release_root,
        current_link=current_link,
        previous_target=previous,
        candidate_target=candidate,
        database_revision_file=database_revision_file,
        events_file=events_file,
    )


def _write_release(
    root: Path,
    *,
    revision: str,
    compatible_revisions: tuple[str, ...],
    include_contract: bool,
) -> None:
    (root / "backend" / "migrations").mkdir(parents=True)
    (root / "backend" / "HEAD_REVISION").write_text(revision, encoding="utf-8")
    (root / "backend" / "alembic.ini").write_text(
        "[alembic]\nscript_location = %(here)s/migrations\n",
        encoding="utf-8",
    )
    (root / "ops" / "scripts").mkdir(parents=True)
    (root / "ops" / "scripts" / "activate_release.sh").write_text(
        ACTIVATE_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "ops" / "scripts" / "rollback_release.sh").write_text(
        ROLLBACK_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for unit in RUNTIME_UNITS:
        unit_path = root / "ops" / "systemd" / f"{unit}.service"
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(f"[Unit]\nDescription={unit}\n", encoding="utf-8")
    cause_agent_unit = root / "ops" / "systemd" / f"{CAUSE_AGENT_WORKER_UNIT}.service"
    cause_agent_unit.write_text(
        f"[Unit]\nDescription={CAUSE_AGENT_WORKER_UNIT}\n",
        encoding="utf-8",
    )
    essay_review_unit = root / "ops" / "systemd" / f"{ESSAY_REVIEW_WORKER_UNIT}.service"
    essay_review_unit.write_text(
        f"[Unit]\nDescription={ESSAY_REVIEW_WORKER_UNIT}\n",
        encoding="utf-8",
    )
    essay_review_entrypoint = root / "backend" / "app" / "workers" / "essay_reviews.py"
    essay_review_entrypoint.parent.mkdir(parents=True, exist_ok=True)
    essay_review_entrypoint.write_text("# essay review worker\n", encoding="utf-8")
    refresh = root / "scripts" / "server_refresh_knowledge_map_nodes.sh"
    _write_executable(refresh, "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n")
    if include_contract:
        (root / "ops" / "release-db-compatibility.env").write_text(
            "ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT="
            "artifact-db-compatibility@v1\n"
            f"ARTIFACT_DATABASE_MIGRATION_HEAD={revision}\n"
            "ARTIFACT_DATABASE_COMPATIBLE_REVISIONS="
            f"{','.join(compatible_revisions)}\n",
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
[[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]
mkdir -p "$3/bin"
cp "${HARNESS_SHIM_ROOT}/python" "$3/bin/python"
cp "${HARNESS_SHIM_ROOT}/alembic" "$3/bin/alembic"
cp "${HARNESS_SHIM_ROOT}/uvicorn" "$3/bin/uvicorn"
chmod 0755 "$3/bin/python" "$3/bin/alembic" "$3/bin/uvicorn"
""",
    )
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-u" ]]; then shift 2; fi
if [[ "${1:-}" == "env" ]]; then
  shift
  while [[ "${1:-}" == *=* ]]; do export "$1"; shift; done
fi
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "install",
        """#!/usr/bin/env bash
set -euo pipefail
while (( $# > 2 )); do
  case "$1" in
    -D) shift ;;
    -m) shift 2 ;;
    *) exit 43 ;;
  esac
done
mkdir -p "$(dirname "$2")"
cp "$1" "$2"
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
while [[ "${1:-}" == "--no-pager" || "${1:-}" == "--full" ]]; do shift; done
command_name="${1:?}"
shift
printf 'systemctl %s %s\\n' "$command_name" "$*" >> "${HARNESS_EVENTS_FILE}"
if [[ "$command_name" == "restart" && "${HARNESS_FAULT:-}" == "restart" ]] \
  && [[ ! -e "${HARNESS_RESTART_MARKER}" ]]; then
  touch "${HARNESS_RESTART_MARKER}"
  exit 44
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${HARNESS_FAULT:-}" == "readiness" ]]; then exit 22; fi
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
