from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CLIENT_REAL_DATA_TEST = (
    "tests/work_packages/vj-01-real-today-r2/"
    "vj-01-real-today-r2-as-01-client.test.ts"
)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def postgres_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run package PostgreSQL entirely inside the verifier-owned writable tree."""

    configured_url = os.getenv("TEST_DATABASE_URL")
    if configured_url:
        yield configured_url
        return

    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.fail("PostgreSQL initdb/pg_ctl is required for real Today acceptance tests")

    data_dir = tmp_path_factory.mktemp("postgres-data")
    log_file = tmp_path_factory.mktemp("postgres-log") / "postgres.log"
    port = _free_loopback_port()

    subprocess.run(
        [initdb, "-D", str(data_dir), "--auth=trust", "--username=postgres"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_file),
            "-o",
            f"-p {port} -h 127.0.0.1 -c unix_socket_directories=''",
            "start",
            "-w",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        yield f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "stop", "-m", "fast", "-w"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


def test_vj_01_real_today_r2_as_01(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "real-today-r2-plan-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )

    response = _client(db_session_factory).get(
        "/api/v2/plans/today",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    dashboard = response.json()["data"]
    assert dashboard["planVersionId"]
    assert dashboard["rationale"].strip()
    assert dashboard["dualTrackExplanation"]["summary"].strip()
    assert {item["track"] for item in dashboard["dualTrackExplanation"]["allocations"]} == {
        "xingce",
        "essay",
    }
    assert {task["track"] for task in dashboard["tasks"]} == {"xingce", "essay"}
    assert all(task["evidenceRefs"] for task in dashboard["tasks"])
    assert all(value for value in dashboard["inputVersions"].values())
    assert "prototype" not in json.dumps(dashboard, ensure_ascii=False).casefold()

    client_result = subprocess.run(
        [
            "npm",
            "--prefix",
            "apps/wechat",
            "run",
            "test",
            "--",
            CLIENT_REAL_DATA_TEST,
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"},
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert client_result.returncode == 0, (
        f"client real-data assertion failed\nstdout:\n{client_result.stdout}\n"
        f"stderr:\n{client_result.stderr}"
    )


def test_vj_01_real_today_r2_as_04(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "real-today-r2-capacity-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    initial = client.get("/api/v2/plans/today", headers=auth).json()["data"]
    command = {
        "basedOnCapacityVersionId": initial["capacity"]["capacityVersionId"],
        "availableMinutes": 35,
        "energy": 4,
        "stress": 2,
        "confidenceLevel": 4,
    }
    headers = {**auth, "Idempotency-Key": "real-today-r2-capacity-replay"}

    first = client.put(
        "/api/v2/plans/today/capacity",
        headers=headers,
        json=command,
    )
    replay = client.put(
        "/api/v2/plans/today/capacity",
        headers=headers,
        json=command,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    first_data = first.json()["data"]
    replay_data = replay.json()["data"]
    assert first_data["capacity"]["capacityVersionId"] == replay_data["capacity"][
        "capacityVersionId"
    ]
    assert first_data["plan"]["planVersionId"] == replay_data["plan"]["planVersionId"]
    assert first_data["plan"]["planVersionId"] != initial["planVersionId"]

    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.daily_capacity_versions")
        ).scalar_one() == 2
        assert session.execute(
            text("select count(*) from learning.daily_plan_versions")
        ).scalar_one() == 2
        assert session.execute(
            text(
                "select count(*) from platform.outbox_events "
                "where event_type = 'daily_capacity.changed'"
            )
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from platform.outbox_events "
                "where event_type = 'daily_plan.changed'"
            )
        ).scalar_one() == 2
        assert str(
            session.execute(
                text(
                    "select supersedes_capacity_version_id "
                    "from learning.daily_capacity_versions where version_no = 2"
                )
            ).scalar_one()
        ) == command["basedOnCapacityVersionId"]
        assert str(
            session.execute(
                text(
                    "select supersedes_plan_version_id "
                    "from learning.daily_plan_versions where version_no = 2"
                )
            ).scalar_one()
        ) == initial["planVersionId"]
