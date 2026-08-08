from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
BACKEND_GATE = REPOSITORY_ROOT / "scripts" / "verify_backend.sh"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
SETUP_UV = (
    "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b"
    " # v8.1.0"
)
UV_VERSION = 'version: "0.11.32"'


def job_block(workflow: str, job: str, next_job: str | None = None) -> str:
    start_marker = f"\n  {job}:\n"
    start = workflow.index(start_marker) + len(start_marker)
    if next_job is None:
        return workflow[start:]
    end = workflow.index(f"\n  {next_job}:\n", start)
    return workflow[start:end]


def write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class GateRuntimeContractTests(unittest.TestCase):
    def test_ci_pins_uv_for_backend_and_wechat_without_pip_resolution(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        backend = job_block(workflow, "backend", "wechat")
        wechat = job_block(workflow, "wechat")

        self.assertEqual(workflow.count(SETUP_UV), 2)
        self.assertEqual(workflow.count(UV_VERSION), 2)
        for block in (backend, wechat):
            self.assertIn(SETUP_UV, block)
            self.assertIn(UV_VERSION, block)
            self.assertNotIn("pip install", block)
            self.assertNotIn("cache: pip", block)
        self.assertIn("- run: scripts/verify_backend.sh", backend)
        self.assertNotIn("PYTHON=", backend)
        self.assertIn("- run: scripts/verify_wechat.sh", wechat)

    def test_backend_ci_uses_the_session_isolated_postgres_runtime(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        backend = job_block(workflow, "backend", "wechat")

        self.assertIn("Expose isolated PostgreSQL test tools", backend)
        self.assertNotIn("services:", backend)
        self.assertNotIn("TEST_DATABASE_URL", backend)

    def test_backend_gate_defaults_to_one_sanitized_locked_uv_runtime(self) -> None:
        gate_source = BACKEND_GATE.read_text(encoding="utf-8")
        self.assertNotIn(".venv/bin/python", gate_source)

        with tempfile.TemporaryDirectory(prefix="gate-runtime-") as temporary:
            temporary_root = Path(temporary)
            arguments_log = temporary_root / "arguments.log"
            environment_log = temporary_root / "environment.log"
            fake_uv = temporary_root / "uv"
            write_executable(
                fake_uv,
                """
                #!/bin/sh
                : > "$GATE_ARGUMENTS_LOG"
                for argument in "$@"; do
                  printf '%s\\n' "$argument" >> "$GATE_ARGUMENTS_LOG"
                done
                {
                  printf '%s\\n' "${PYTHONHOME-unset}"
                  printf '%s\\n' "${PYTHONPATH-unset}"
                  printf '%s\\n' "${VIRTUAL_ENV-unset}"
                  printf '%s\\n' "${UV_NO_SYNC-unset}"
                  printf '%s\\n' "${UV_PROJECT_ENVIRONMENT-unset}"
                } > "$GATE_ENVIRONMENT_LOG"
                """,
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "GATE_ARGUMENTS_LOG": str(arguments_log),
                    "GATE_ENVIRONMENT_LOG": str(environment_log),
                    "PATH": f"{temporary_root}:/usr/bin:/bin",
                    "PYTHONHOME": "/tmp/host-python",
                    "PYTHONPATH": "/tmp/host-packages",
                    "UV_NO_SYNC": "1",
                    "UV_PROJECT_ENVIRONMENT": "/tmp/arbitrary-venv",
                    "VIRTUAL_ENV": "/tmp/active-venv",
                }
            )
            environment.pop("PYTHON", None)

            result = subprocess.run(
                ["/bin/bash", str(BACKEND_GATE)],
                check=False,
                capture_output=True,
                env=environment,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                arguments_log.read_text(encoding="utf-8").splitlines(),
                [
                    "run",
                    "--project",
                    str(BACKEND_ROOT),
                    "--locked",
                    "--isolated",
                    "--extra",
                    "dev",
                    "--no-env-file",
                    "bash",
                    str(BACKEND_GATE),
                    "--locked-runtime",
                ],
            )
            self.assertEqual(
                environment_log.read_text(encoding="utf-8").splitlines(),
                ["unset"] * 5,
            )

    def test_backend_gate_fails_closed_without_uv(self) -> None:
        environment = os.environ.copy()
        environment["PATH"] = "/usr/bin:/bin"
        environment.pop("PYTHON", None)

        result = subprocess.run(
            ["/bin/bash", str(BACKEND_GATE)],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

        self.assertEqual(result.returncode, 127)
        self.assertIn("uv is required for the locked backend gate", result.stderr)
        self.assertIn("set PYTHON explicitly", result.stderr)

    def test_backend_gate_uses_only_an_explicit_python_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gate-python-fallback-") as temporary:
            temporary_root = Path(temporary)
            calls_log = temporary_root / "calls.log"
            fake_python = temporary_root / "python"
            write_executable(
                fake_python,
                """
                #!/bin/sh
                printf '%s\\n' "$*" >> "$GATE_CALLS_LOG"
                if [ "$*" = "-m alembic heads" ]; then
                  printf '%s\\n' "compatibility_revision (head)"
                fi
                """,
            )
            environment = os.environ.copy()
            environment.pop("CI", None)
            environment.update(
                {
                    "GATE_CALLS_LOG": str(calls_log),
                    "PATH": "/usr/bin:/bin",
                    "PYTHON": str(fake_python),
                }
            )

            result = subprocess.run(
                ["/bin/bash", str(BACKEND_GATE)],
                check=False,
                capture_output=True,
                env=environment,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls_log.read_text(encoding="utf-8").splitlines(),
                [
                    "-m ruff check .",
                    "-m pytest",
                    "-m alembic heads",
                ],
            )
            self.assertIn(
                "Alembic migration head: compatibility_revision (head)",
                result.stdout,
            )

    def test_backend_gate_forbids_python_fallback_in_ci(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "CI": "true",
                "PATH": "/usr/bin:/bin",
                "PYTHON": "/tmp/forbidden-python",
            }
        )

        result = subprocess.run(
            ["/bin/bash", str(BACKEND_GATE)],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("PYTHON compatibility fallback is forbidden in CI", result.stderr)


if __name__ == "__main__":
    unittest.main()
