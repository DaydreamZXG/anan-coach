from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.data_rights.repository import DELETION_JOB_TYPE, EXPORT_JOB_TYPE
from app.domains.operations import router as operations_router
from app.domains.xingce_learning.cause_agent.service import CAUSE_AGENT_JOB_TYPE
from app.platform.jobs import (
    JOB_REGISTRY,
    BackgroundJobRepository,
    JobContractError,
    validate_job_registry,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVATE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "activate_release.sh"
SYSTEMD_ROOT = REPO_ROOT / "ops" / "systemd"


def test_registry_binds_each_scoped_producer_to_one_consumer_and_recovery_owner() -> None:
    validate_job_registry()
    identities = {
        (registration.queue_name, registration.job_type)
        for registration in JOB_REGISTRY
    }

    assert len(identities) == len(JOB_REGISTRY)
    assert {
        EXPORT_JOB_TYPE,
        DELETION_JOB_TYPE,
        CAUSE_AGENT_JOB_TYPE,
        "ilink_object_deletion_v1",
    } == {registration.job_type for registration in JOB_REGISTRY}
    assert all(len(registration.consumer_units) == 1 for registration in JOB_REGISTRY)
    assert all(registration.producer for registration in JOB_REGISTRY)
    assert all(registration.recovery_owner for registration in JOB_REGISTRY)


def test_registry_rejects_duplicate_or_multi_consumer_bindings() -> None:
    duplicate = (*JOB_REGISTRY, JOB_REGISTRY[0])
    with pytest.raises(JobContractError, match="Duplicate job registration"):
        validate_job_registry(duplicate)

    invalid = (
        replace(
            JOB_REGISTRY[0],
            consumer_units=("worker-a", "worker-b"),
        ),
    )
    with pytest.raises(JobContractError, match="exactly one deployed consumer"):
        validate_job_registry(invalid)


def test_registry_consumers_have_one_unit_and_one_activation_declaration() -> None:
    activation = ACTIVATE_SCRIPT.read_text(encoding="utf-8")
    runtime_blocks = re.findall(
        r"runtime_units(?:=|\+=)\((.*?)\)",
        activation,
        flags=re.DOTALL,
    )
    runtime_declarations = [
        unit
        for block in runtime_blocks
        for unit in re.findall(r"gongkao-dazi-[a-z-]+", block)
    ]

    for registration in JOB_REGISTRY:
        unit = registration.consumer_unit
        assert len(tuple(SYSTEMD_ROOT.glob(f"{unit}.service"))) == 1
        assert runtime_declarations.count(unit) == 1

    cause_units = tuple(
        SYSTEMD_ROOT.glob("gongkao-dazi-cause-agent-worker.service")
    )
    assert len(cause_units) == 1
    assert "systemctl enable gongkao-dazi-cause-agent-worker" in activation


def test_metrics_expose_queue_and_running_age_for_each_registered_type(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    jobs = BackgroundJobRepository()
    jobs.enqueue(
        db_session,
        job_key="registry:cause:queued-age",
        job_type=CAUSE_AGENT_JOB_TYPE,
        input_version="cause-agent-run@v1",
        payload={"runId": str(uuid4())},
        run_after=now - timedelta(minutes=10),
    )
    jobs.enqueue(
        db_session,
        job_key="registry:cause:running-age",
        job_type=CAUSE_AGENT_JOB_TYPE,
        input_version="cause-agent-run@v1",
        payload={"runId": str(uuid4())},
        run_after=now - timedelta(minutes=10),
    )
    claimed = jobs.claim_next(
        db_session,
        job_types=(CAUSE_AGENT_JOB_TYPE,),
        now=now - timedelta(minutes=10),
    )
    assert claimed is not None
    db_session.commit()
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="jobs-metrics-token"),
    )

    payload = operations_router.metrics(
        db_session,
        authorization="Bearer jobs-metrics-token",
    )

    for registration in JOB_REGISTRY:
        assert (
            f'anan_job_queue_oldest_age_seconds{{job_type="{registration.job_type}"}}'
            in payload
        )
        assert (
            f'anan_job_running_oldest_age_seconds{{job_type="{registration.job_type}"}}'
            in payload
        )
    queue_age = _metric_value(
        payload,
        'anan_job_queue_oldest_age_seconds{job_type="xingce_cause_agent_v1"}',
    )
    running_age = _metric_value(
        payload,
        'anan_job_running_oldest_age_seconds{job_type="xingce_cause_agent_v1"}',
    )
    assert 590 <= queue_age <= 620
    assert 590 <= running_age <= 620


def _metric_value(payload: str, name: str) -> int:
    match = re.search(rf"^{re.escape(name)} ([0-9]+)$", payload, flags=re.MULTILINE)
    assert match is not None
    return int(match.group(1))
