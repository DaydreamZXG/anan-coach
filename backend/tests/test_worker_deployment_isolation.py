from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import shlex


REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_ROOT = REPO_ROOT / "ops" / "systemd"
OPS_ROOT = REPO_ROOT / "ops"
SYSUSERS = OPS_ROOT / "sysusers.d" / "gongkao-dazi-workers.conf"
RUNBOOK = OPS_ROOT / "RUNBOOK.md"

WORKERS = {
    "gongkao-dazi-data-rights-worker": {
        "user": "gongkao-wkr-data",
        "workload_envs": ("worker-data-rights.env",),
        "heartbeat_env": "worker-heartbeat-data-rights.env",
    },
    "gongkao-dazi-cause-agent-worker": {
        "user": "gongkao-wkr-cause",
        "workload_envs": ("worker-cause-agent.env",),
        "heartbeat_env": "worker-heartbeat-cause-agent.env",
    },
    "gongkao-dazi-external-learning-worker": {
        "user": "gongkao-wkr-external",
        "workload_envs": ("worker-external-learning.env",),
        "heartbeat_env": "worker-heartbeat-external-learning.env",
    },
    "gongkao-dazi-essay-review-worker": {
        "user": "gongkao-wkr-essay",
        "workload_envs": ("worker-essay-review.env",),
        "heartbeat_env": "worker-heartbeat-essay-review.env",
    },
    "gongkao-dazi-coach-notification-worker": {
        "user": "gongkao-wkr-coach",
        "workload_envs": ("worker-coach-notification.env",),
        "heartbeat_env": "worker-heartbeat-coach-notification.env",
    },
    "gongkao-dazi-ilink-worker": {
        "user": "gongkao-wkr-ilink",
        "workload_envs": ("ilink-channel.env", "ilink-worker.env"),
        "heartbeat_env": "worker-heartbeat-ilink.env",
    },
}

HARDENING = {
    "NoNewPrivileges": "true",
    "PrivateTmp": "true",
    "PrivateDevices": "true",
    "ProtectSystem": "strict",
    "ProtectHome": "true",
    "ProtectControlGroups": "true",
    "ProtectKernelModules": "true",
    "ProtectKernelTunables": "true",
    "ProtectKernelLogs": "true",
    "ProtectClock": "true",
    "ProtectHostname": "true",
    "ProtectProc": "invisible",
    "ProcSubset": "pid",
    "RestrictNamespaces": "true",
    "RestrictRealtime": "true",
    "RestrictSUIDSGID": "true",
    "LockPersonality": "true",
    "RemoveIPC": "true",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
    "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
    "SystemCallArchitectures": "native",
    "UMask": "0077",
}

REQUIRED_WORKLOAD_KEYS = {
    "worker-data-rights.env": {
        "PRIVACY_EXPORT_EXPIRY_SECONDS",
        "TOS_ACCESS_KEY",
        "TOS_SECRET_KEY",
    },
    "worker-cause-agent.env": {
        "AI_API_KEY",
        "AI_XINGCE_CAUSE_PROVIDER_RELEASE_ID",
        "AI_PRICE_VERSION",
    },
    "worker-external-learning.env": {
        "TOS_ACCESS_KEY",
        "UPLOAD_SCANNER_API_KEY",
        "PACKET_RENDERER_API_KEY",
        "AI_API_KEY",
        "WEKNORA_INGEST_API_KEY",
        "XFYUN_APP_ID",
        "XFYUN_API_KEY",
        "XFYUN_API_SECRET",
    },
    "worker-essay-review.env": {
        "AI_API_KEY",
        "AI_ESSAY_TOKENRHYTHM_API_KEY",
        "CLOUDBASE_API_KEY",
        "AI_ESSAY_ADJUDICATOR_API_KEY",
        "AI_ESSAY_CHECKPOINT_KEYS_JSON",
    },
    "worker-coach-notification.env": {
        "WECHAT_MINI_APP_SECRET",
        "WECHAT_COACH_TEMPLATE_RELEASE_JSON",
    },
    "ilink-channel.env": {
        "ILINK_BOT_TOKEN",
        "ILINK_IDENTITY_ENCRYPTION_KEY",
        "ILINK_SECONDARY_AI_API_KEY",
    },
    "ilink-worker.env": {
        "TOS_ACCESS_KEY",
        "TOS_SECRET_KEY",
        "RECOGNITION_PROCESSOR_TOKEN",
        "AI_API_KEY",
    },
}

FORBIDDEN_PREFIXES = {
    "worker-data-rights.env": (
        "AI_",
        "CLOUDBASE_",
        "ILINK_",
        "PACKET_",
        "UPLOAD_",
        "WECHAT_",
        "WEKNORA_",
        "XFYUN_",
    ),
    "worker-cause-agent.env": (
        "AI_COMPANION_",
        "AI_ESSAY_",
        "CLOUDBASE_",
        "ILINK_",
        "PACKET_",
        "TOS_",
        "UPLOAD_",
        "WECHAT_",
        "WEKNORA_",
        "XFYUN_",
    ),
    "worker-external-learning.env": (
        "AI_ESSAY_",
        "AI_XINGCE_",
        "CLOUDBASE_",
        "ILINK_",
        "WECHAT_",
    ),
    "worker-essay-review.env": (
        "AI_COMPANION_",
        "AI_XINGCE_",
        "ILINK_",
        "PACKET_",
        "TOS_",
        "UPLOAD_",
        "WECHAT_",
        "WEKNORA_",
        "XFYUN_",
    ),
    "worker-coach-notification.env": (
        "AI_",
        "CLOUDBASE_",
        "ILINK_",
        "PACKET_",
        "TOS_",
        "UPLOAD_",
        "WEKNORA_",
        "XFYUN_",
    ),
    "ilink-channel.env": (
        "AI_COMPANION_",
        "AI_ESSAY_",
        "AI_XINGCE_",
        "CLOUDBASE_",
        "PACKET_",
        "TOS_",
        "UPLOAD_",
        "WECHAT_",
        "WEKNORA_",
        "XFYUN_",
    ),
    "ilink-worker.env": (
        "AI_ESSAY_",
        "AI_XINGCE_",
        "CLOUDBASE_",
        "PACKET_",
        "UPLOAD_",
        "WECHAT_",
        "WEKNORA_",
        "XFYUN_",
    ),
}


def test_six_workers_use_distinct_non_api_unix_principals() -> None:
    api_user = _unit_directives("gongkao-dazi-api")["Service"]["User"]
    users: list[str] = []

    for unit, contract in WORKERS.items():
        service = _unit_directives(unit)["Service"]
        user = service["User"]
        users.append(user)
        assert user == contract["user"]
        assert service["Group"] == user
        assert user != api_user

    assert len(users) == len(set(users)) == 6


def test_sysusers_provisions_exactly_the_six_non_login_accounts() -> None:
    rows = [
        shlex.split(line, comments=True)
        for line in SYSUSERS.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    worker_rows = [row for row in rows if row[1] != "gongkao-backup"]
    backup_rows = [row for row in rows if row[1] == "gongkao-backup"]

    assert len(worker_rows) == 6
    assert len(backup_rows) == 1
    assert {row[1] for row in worker_rows} == {
        str(contract["user"]) for contract in WORKERS.values()
    }
    for row in rows:
        assert len(row) == 6
        assert row[0] == "u"
        assert row[2] == "-"
        assert row[-1] == "/usr/sbin/nologin"
    for row in worker_rows:
        assert row[-2] == "/nonexistent"
    assert backup_rows[0][-2] == "/var/lib/gongkao-backup"


def test_worker_units_load_only_scoped_environment_files_and_fail_closed() -> None:
    all_heartbeat_files = {
        f"/etc/gongkao-dazi/{contract['heartbeat_env']}" for contract in WORKERS.values()
    }

    for unit, contract in WORKERS.items():
        service = _unit_directives(unit)["Service"]
        environment_files = set(service["EnvironmentFile"])
        expected_workload_files = {
            f"/etc/gongkao-dazi/{name}" for name in contract["workload_envs"]
        }
        expected = {
            *expected_workload_files,
            "/etc/gongkao-dazi/db.env",
            f"/etc/gongkao-dazi/{contract['heartbeat_env']}",
            "/etc/gongkao-dazi/release-runtime.env",
        }

        assert environment_files == expected
        assert "/etc/gongkao-dazi/api.env" not in environment_files
        assert not any(value.startswith("-") for value in environment_files)
        assert environment_files & all_heartbeat_files == {
            f"/etc/gongkao-dazi/{contract['heartbeat_env']}"
        }
        assert service["Environment"] == ["PYTHONDONTWRITEBYTECODE=1"]


def test_workload_env_contracts_are_secret_scoped_and_separate_from_runtime() -> None:
    for filename, required in REQUIRED_WORKLOAD_KEYS.items():
        values = _env_example(filename)
        keys = set(values)

        assert required <= keys
        assert (
            not {
                "DATABASE_URL",
                "WORKER_HEARTBEAT_DATABASE_URL",
                "APP_ENV",
                "APP_GIT_COMMIT",
                "APP_DATA_RECEIPT_SHA256",
                "REQUIRED_DATABASE_REVISION",
            }
            & keys
        )
        assert not {
            key
            for key in keys
            if any(key.startswith(prefix) for prefix in FORBIDDEN_PREFIXES[filename])
        }

    assert _env_example("worker-data-rights.env")["TOS_ACCESS_KEY"] == ""
    assert _env_example("worker-cause-agent.env")["AI_API_KEY"] == ""
    assert _env_example("worker-external-learning.env")["UPLOAD_SCANNER_API_KEY"] == ""
    assert _env_example("worker-essay-review.env")["CLOUDBASE_API_KEY"] == ""
    assert _env_example("worker-coach-notification.env")["WECHAT_MINI_APP_SECRET"] == ""
    assert _env_example("ilink-channel.env")["ILINK_BOT_TOKEN"] == ""
    assert _env_example("ilink-worker.env")["RECOGNITION_PROCESSOR_TOKEN"] == ""


def test_worker_units_share_a_consistent_process_hardening_floor() -> None:
    for unit in WORKERS:
        service = _unit_directives(unit)["Service"]
        for directive, expected in HARDENING.items():
            assert service[directive] == expected

        unit_directives = _unit_directives(unit)["Unit"]
        dependency_text = " ".join(
            value
            for key in ("After", "Before", "Requires", "Wants")
            for value in unit_directives.get(key, [])
        )
        assert "gongkao-dazi-api.service" not in dependency_text


def test_runbook_does_not_wait_for_mandatory_heartbeat_readiness_before_workers() -> None:
    runbook = RUNBOOK.read_text()

    assert "先启动 API 并验证 `/ready`，再启动" not in runbook
    assert "不得在 worker 启动前等待聚合 `/ready`" in runbook
    assert "mandatory consumer heartbeat 本来就是 `/ready` 的输入" in runbook
    assert "systemd-sysusers /etc/sysusers.d/gongkao-dazi-workers.conf" in runbook
    assert "六个 worker unit 都不得加载 `api.env`" in runbook


def _unit_directives(unit: str) -> dict[str, dict[str, object]]:
    path = SYSTEMD_ROOT / f"{unit}.service"
    result: dict[str, dict[str, object]] = defaultdict(dict)
    section = ""
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, separator, value = line.partition("=")
        assert separator and section
        existing = result[section].get(key)
        if key in {
            "EnvironmentFile",
            "Environment",
            "After",
            "Before",
            "Requires",
            "Wants",
            "InaccessiblePaths",
        }:
            if existing is None:
                result[section][key] = [value]
            else:
                assert isinstance(existing, list)
                existing.append(value)
        else:
            assert existing is None, f"duplicate {key} in {path}"
            result[section][key] = value
    return result


def _env_example(filename: str) -> dict[str, str]:
    path = OPS_ROOT / f"{filename}.example"
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator and key and key not in values, f"invalid env contract: {path}"
        values[key] = value
    return values
