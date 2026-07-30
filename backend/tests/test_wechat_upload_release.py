from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "ops" / "scripts" / "upload_wechat_release.py"
APP_ID = "wx1234567890abcdef"
TOOL_FILES = {
    "cli": "Contents/MacOS/cli",
    "cliJs": "Contents/MacOS/cli.js",
    "cliModule": "Contents/Resources/app.asar.unpacked/js/common/cli/index.js",
}


def _run(arguments: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, contents: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)


@dataclass(frozen=True)
class UploadFixture:
    root: Path
    repository: Path
    commit: str
    bundle: Path
    cli: Path
    policy: Path
    private_key: Path
    public_key: Path
    prefix: Path
    invocation: Path
    injection_marker: Path

    @property
    def outputs(self) -> tuple[Path, ...]:
        return tuple(
            Path(f"{self.prefix}{suffix}")
            for suffix in (".json", ".json.sig", ".info.json", ".log")
        )

    def arguments(
        self,
        *,
        expected_app_id: str = APP_ID,
        description: str | None = None,
    ) -> list[str]:
        return [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(self.repository),
            "--expected-git-commit",
            self.commit,
            "--expected-app-id",
            expected_app_id,
            "--upload-version",
            "2.4.1",
            "--description",
            description or f"candidate; touch {self.injection_marker}",
            "--devtools-cli",
            str(self.cli),
            "--tool-policy",
            str(self.policy),
            "--signing-private-key",
            str(self.private_key),
            "--output-prefix",
            str(self.prefix),
            "--allow-test-tool-identity",
        ]


def _fixture(
    tmp_path: Path,
    *,
    cli_exit_code: int = 0,
    info_json: str = ('{"size":{"packages":[{"name":"main","size":321}]},"subPackageCount":0}\n'),
) -> UploadFixture:
    repository = tmp_path / "source"
    repository.mkdir()
    _run(["git", "init", "-q"], cwd=repository)
    _run(["git", "config", "user.email", "tests@example.invalid"], cwd=repository)
    _run(["git", "config", "user.name", "Tests"], cwd=repository)
    _write(
        repository / ".gitignore",
        "apps/wechat/ignored-sentinel.txt\n",
    )
    _write(
        repository / "apps" / "wechat" / "project.config.json",
        json.dumps(
            {
                "appid": APP_ID,
                "compileType": "miniprogram",
                "miniprogramRoot": "miniprogram/",
            },
            sort_keys=True,
        )
        + "\n",
    )
    _write(
        repository / "apps" / "wechat" / "miniprogram" / "app.json",
        '{"pages":["pages/home/index"]}\n',
    )
    ignored_sentinel = repository / "apps" / "wechat" / "ignored-sentinel.txt"
    ignored_sentinel.write_text("must never be uploaded\n", encoding="utf-8")
    _run(["git", "add", "."], cwd=repository)
    _run(["git", "commit", "-qm", "fixture source"], cwd=repository)
    commit = _run(["git", "rev-parse", "HEAD"], cwd=repository).stdout.strip()

    invocation = tmp_path / "cli-arguments.txt"
    bundle = (tmp_path / "FixtureWeChatDevTools.app").resolve()
    cli = bundle / TOOL_FILES["cli"]
    cli_script = f"""#!/bin/sh
set -eu
printf '%s\\n' "$@" > {str(invocation)!r}
project=
info=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --project) project=$2; shift 2 ;;
    --info-output) info=$2; shift 2 ;;
    *) shift ;;
  esac
done
[ -n "$project" ]
[ -n "$info" ]
[ ! -e "$project/ignored-sentinel.txt" ]
printf '%s\\n' {info_json.strip()!r} > "$info"
printf '%s\\n' 'fake DevTools upload completed'
exit {cli_exit_code}
"""
    _write(cli, cli_script, mode=0o755)
    _write(bundle / TOOL_FILES["cliJs"], "fixture cli.js\n")
    _write(bundle / TOOL_FILES["cliModule"], "fixture cli module\n")
    identity = {
        "bundleIdentifier": "test.fixture.wechatdevtools",
        "shortVersion": "9.9.9-test",
        "bundleVersion": "999",
        "teamIdentifier": "TESTTEAM01",
    }
    _write(
        bundle / "Contents" / "fixture-identity.json",
        json.dumps(identity, sort_keys=True) + "\n",
    )
    policy = {
        "schemaVersion": "anan-wechat-devtools-policy/v1",
        "bundlePath": str(bundle),
        **identity,
        "identityMode": "test-fixture",
        "files": {
            name: {
                "relativePath": relative_path,
                "sha256": _sha256(bundle / relative_path),
            }
            for name, relative_path in TOOL_FILES.items()
        },
    }
    policy_path = tmp_path / "tool-policy.json"
    _write(policy_path, json.dumps(policy, sort_keys=True) + "\n", mode=0o644)

    private_key = tmp_path / "uploader-private.pem"
    _run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(private_key),
        ],
        cwd=tmp_path,
    )
    private_key.chmod(0o600)
    public_key = tmp_path / "uploader-public.pem"
    _run(
        [
            "openssl",
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-out",
            str(public_key),
        ],
        cwd=tmp_path,
    )
    output = tmp_path / "output"
    output.mkdir()
    return UploadFixture(
        root=tmp_path,
        repository=repository,
        commit=commit,
        bundle=bundle,
        cli=cli,
        policy=policy_path,
        private_key=private_key,
        public_key=public_key,
        prefix=output / "wechat-upload-2.4.1",
        invocation=invocation,
        injection_marker=tmp_path / "shell-injection-must-not-exist",
    )


def _invoke(fixture: UploadFixture, **kwargs: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        fixture.arguments(**kwargs),
        cwd=fixture.root,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_no_outputs(fixture: UploadFixture) -> None:
    assert not any(path.exists() or path.is_symlink() for path in fixture.outputs)


def test_uploads_only_archived_commit_and_issues_signed_v2_receipt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    completed = _invoke(fixture)

    assert completed.returncode == 0, completed.stderr
    receipt_path, signature_path, info_path, log_path = fixture.outputs
    assert all(path.is_file() for path in fixture.outputs)
    assert not fixture.injection_marker.exists()
    arguments = fixture.invocation.read_text(encoding="utf-8").splitlines()
    assert arguments[0] == "upload"
    assert arguments[1] == "--project"
    archived_project = Path(arguments[2])
    assert archived_project != fixture.repository / "apps" / "wechat"
    assert not archived_project.exists(), "private Git archive must be removed"
    assert arguments[3:] == [
        "--version",
        "2.4.1",
        "--desc",
        f"candidate; touch {fixture.injection_marker}",
        "--info-output",
        arguments[-1],
    ]

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    UUID(receipt["receiptId"])
    assert receipt["schemaVersion"] == "anan-wechat-upload-receipt/v2"
    assert receipt["operation"] == "devtools_upload"
    assert receipt["result"] == "success"
    assert receipt["sourceGitCommit"] == fixture.commit
    expected_tree = _run(
        ["git", "rev-parse", f"{fixture.commit}^{{tree}}"],
        cwd=fixture.repository,
    ).stdout.strip()
    expected_wechat_tree = _run(
        ["git", "rev-parse", f"{fixture.commit}:apps/wechat"],
        cwd=fixture.repository,
    ).stdout.strip()
    listing = subprocess.run(
        [
            "git",
            "ls-tree",
            "-r",
            "-z",
            fixture.commit,
            "--",
            "apps/wechat",
        ],
        cwd=fixture.repository,
        check=True,
        capture_output=True,
    ).stdout
    assert receipt["sourceGitTree"] == expected_tree
    assert receipt["wechatTreeObjectId"] == expected_wechat_tree
    assert receipt["sourceTreeListingSha256"] == sha256(listing).hexdigest()
    assert receipt["projectRelativePath"] == "apps/wechat"
    assert receipt["appId"] == APP_ID
    assert receipt["compileType"] == "miniprogram"
    assert receipt["miniprogramRoot"] == "miniprogram/"
    assert receipt["uploadVersion"] == "2.4.1"
    assert receipt["devtoolsCliExitCode"] == 0
    assert receipt["rawCliLogSha256"] == _sha256(log_path)
    assert receipt["rawCliLogBytes"] == log_path.stat().st_size
    assert receipt["infoOutputSha256"] == _sha256(info_path)
    assert receipt["infoOutputBytes"] == info_path.stat().st_size
    assert receipt["infoOutput"]["size"]["packages"] == [{"name": "main", "size": 321}]
    assert receipt["tool"]["identityMode"] == "test-fixture"
    assert receipt["tool"]["policySha256"] == _sha256(fixture.policy)
    assert receipt["signatureAlgorithm"] == "RSA-SHA256"
    assert receipt["uploaderKeyId"].startswith("sha256:")

    verified = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(fixture.public_key),
            "-signature",
            str(signature_path),
            str(receipt_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr

    receipt_path.write_text(
        receipt_path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    tampered = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(fixture.public_key),
            "-signature",
            str(signature_path),
            str(receipt_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert tampered.returncode != 0


def test_app_id_mismatch_fails_before_cli_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    completed = _invoke(fixture, expected_app_id="wx0000000000000000")

    assert completed.returncode == 2
    assert "AppID does not match" in completed.stderr
    assert not fixture.invocation.exists()
    _assert_no_outputs(fixture)


def test_tool_digest_mismatch_fails_before_cli_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    with (fixture.bundle / TOOL_FILES["cliJs"]).open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")

    completed = _invoke(fixture)

    assert completed.returncode == 2
    assert "digest does not match policy" in completed.stderr
    assert not fixture.invocation.exists()
    _assert_no_outputs(fixture)


def test_fixture_identity_requires_explicit_test_only_override(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    arguments = fixture.arguments()
    arguments.remove("--allow-test-tool-identity")

    completed = subprocess.run(
        arguments,
        cwd=fixture.root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "test-only override" in completed.stderr
    assert not fixture.invocation.exists()
    _assert_no_outputs(fixture)


@pytest.mark.parametrize(
    ("exit_code", "info_json", "message"),
    [
        (17, '{"size":{"packages":[{"name":"main","size":1}]}}\n', "exit code 17"),
        (0, '{"size":{"packages":[]}}\n', "size.packages must be non-empty"),
        (
            0,
            '{"size":{"packages":[{"name":"","size":-1}]}}\n',
            "invalid package record",
        ),
    ],
)
def test_cli_failure_or_invalid_info_publishes_no_evidence(
    tmp_path: Path,
    exit_code: int,
    info_json: str,
    message: str,
) -> None:
    fixture = _fixture(
        tmp_path,
        cli_exit_code=exit_code,
        info_json=info_json,
    )

    completed = _invoke(fixture)

    assert completed.returncode == 2
    assert message in completed.stderr
    _assert_no_outputs(fixture)


@pytest.mark.parametrize("existing_output_index", range(4))
def test_partial_or_existing_output_is_never_overwritten_and_retry_is_fail_closed(
    tmp_path: Path,
    existing_output_index: int,
) -> None:
    fixture = _fixture(tmp_path)
    existing_path = fixture.outputs[existing_output_index]
    existing_path.write_text("existing authority\n", encoding="utf-8")

    completed = _invoke(fixture)

    assert completed.returncode == 2
    assert "refusing to overwrite" in completed.stderr
    assert existing_path.read_text(encoding="utf-8") == "existing authority\n"
    assert not fixture.invocation.exists()
    assert [path.exists() for path in fixture.outputs] == [
        index == existing_output_index for index in range(4)
    ]
