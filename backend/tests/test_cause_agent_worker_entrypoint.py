from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

import pytest

from app.workers.xingce_cause_agent import (
    STALE_RECOVERY_INTERVAL_SECONDS,
    run_forever,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_UNIT = REPO_ROOT / "ops" / "systemd" / "gongkao-dazi-cause-agent-worker.service"


class LoopStopped(Exception):
    pass


class FakeSession(AbstractContextManager[object]):
    def __enter__(self) -> object:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeWorker:
    def __init__(self, *, process: Callable[[object], object | None], recovered: int = 0) -> None:
        self._process = process
        self._recovered = recovered
        self.recovery_sessions: list[object] = []
        self.process_sessions: list[object] = []

    def recover_stale_claims(self, session: object) -> int:
        self.recovery_sessions.append(session)
        return self._recovered

    def process_next(self, session: object) -> object | None:
        self.process_sessions.append(session)
        return self._process(session)


def _session_factory() -> AbstractContextManager[object]:
    return FakeSession()


def test_cause_agent_worker_recovers_on_start_then_processes_and_sleeps() -> None:
    worker = FakeWorker(process=lambda _session: None, recovered=2)
    sleeps: list[float] = []

    def stop_after_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise LoopStopped

    with pytest.raises(LoopStopped):
        run_forever(
            worker=worker,
            session_factory=_session_factory,
            monotonic=lambda: 0.0,
            sleep=stop_after_sleep,
            heartbeat_runtime=nullcontext(),
        )

    assert len(worker.recovery_sessions) == 1
    assert len(worker.process_sessions) == 1
    assert sleeps == [1.0]


def test_cause_agent_worker_recovers_again_on_the_declared_cadence() -> None:
    calls = 0

    def process_then_stop(_session: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise LoopStopped
        return object()

    timestamps = iter((0.0, 0.0, STALE_RECOVERY_INTERVAL_SECONDS, 60.0))
    worker = FakeWorker(process=process_then_stop)

    with pytest.raises(LoopStopped):
        run_forever(
            worker=worker,
            session_factory=_session_factory,
            monotonic=lambda: next(timestamps),
            sleep=lambda _seconds: None,
            heartbeat_runtime=nullcontext(),
        )

    assert len(worker.recovery_sessions) == 2
    assert len(worker.process_sessions) == 2


def test_cause_agent_worker_does_not_swallow_process_errors() -> None:
    worker = FakeWorker(
        process=lambda _session: (_ for _ in ()).throw(RuntimeError("provider failed"))
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        run_forever(
            worker=worker,
            session_factory=_session_factory,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
            heartbeat_runtime=nullcontext(),
        )

    assert len(worker.recovery_sessions) == 1
    assert len(worker.process_sessions) == 1


def test_cause_agent_worker_systemd_unit_uses_formal_module_and_hardening() -> None:
    source = SERVICE_UNIT.read_text(encoding="utf-8")

    assert [line for line in source.splitlines() if line.startswith("EnvironmentFile=")] == [
        "EnvironmentFile=/etc/gongkao-dazi/worker-cause-agent.env",
        "EnvironmentFile=/etc/gongkao-dazi/db.env",
        "EnvironmentFile=/etc/gongkao-dazi/worker-heartbeat-cause-agent.env",
        "EnvironmentFile=/etc/gongkao-dazi/release-runtime.env",
    ]
    assert (
        "ExecStart=/srv/gongkao-dazi/current/backend/.venv/bin/python "
        "-m app.workers.xingce_cause_agent"
    ) in source
    assert "Restart=always" in source
    assert "NoNewPrivileges=true" in source
    assert "PrivateTmp=true" in source
    assert "ProtectSystem=strict" in source
    assert "ProtectHome=true" in source
    assert "UMask=0077" in source
