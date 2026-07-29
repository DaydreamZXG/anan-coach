"""Classify the completed backend pytest baseline by shared audit root cause."""

from __future__ import annotations

import csv
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
LOG = ROOT / "audit" / "baseline" / "backend-pytest.log"


def _tracked(path: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", f"backend/{path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _cluster(nodeid: str, *, tracked: bool) -> tuple[str, str]:
    if not tracked:
        return (
            "preexisting_untracked_worktree_test",
            "Failure belongs to a test file that was untracked before this audit.",
        )
    if nodeid.startswith("tests/test_migrations.py::"):
        return (
            "E-001_migration_release_authority",
            "Historical Alembic upgrade imports mutable current runtime code.",
        )
    if nodeid == (
        "tests/test_private_knowledge_weknora.py::"
        "test_operations_env_declares_weknora_activation_contract"
    ):
        return (
            "E-001_migration_release_authority",
            "Checked-in revision authorities disagree.",
        )
    if nodeid in {
        (
            "tests/test_knowledge_release_immutability.py::"
            "test_released_method_card_allows_exact_replay_and_rejects_content_drift"
        ),
        (
            "tests/test_companion_external.py::"
            "test_external_wrong_question_requires_existing_supported_map_node"
        ),
    }:
        return (
            "E-002_method_card_replay_contract",
            "The BEFORE INSERT immutability trigger rejects exact ON CONFLICT replay.",
        )
    return (
        "E-003_frozen_release_fixture_contract",
        "Test seed does not satisfy the active release, binding, and content snapshot gate.",
    )


def main() -> int:
    failures: list[dict[str, str]] = []
    for line in LOG.read_text().splitlines():
        if not line.startswith("FAILED "):
            continue
        nodeid = line.removeprefix("FAILED ").strip()
        path = nodeid.split("::", 1)[0]
        tracked = _tracked(path)
        cluster, basis = _cluster(nodeid, tracked=tracked)
        failures.append(
            {
                "nodeid": nodeid,
                "git_tracked": str(tracked).lower(),
                "cluster": cluster,
                "classification_basis": basis,
            }
        )

    writer = csv.DictWriter(sys.stdout, fieldnames=list(failures[0]))
    writer.writeheader()
    writer.writerows(failures)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
