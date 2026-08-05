from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import (
    COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,
    DatabasePublicationUnavailable,
    _postgres_engine_options,
    commercial_data_publication_guard_installed,
    install_commercial_data_publication_guard,
)
from app.domains.companion import commands as companion_commands
from app.domains.companion.commands import _new_checkpoint_engine
from app.domains.operations import router as operations_router


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPOSITORY_ROOT / "ops" / "scripts"
SCRIPT_PATH = SCRIPT_ROOT / "commercial_data_publication.py"
sys.path.insert(0, str(SCRIPT_ROOT))
try:
    SPEC = importlib.util.spec_from_file_location(
        "commercial_data_publication",
        SCRIPT_PATH,
    )
    assert SPEC is not None and SPEC.loader is not None
    publication = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(publication)
finally:
    sys.path.remove(str(SCRIPT_ROOT))


CANDIDATE_COMMIT = "1" * 40
ROLLBACK_COMMIT = "7" * 40
BASELINE_RECEIPT = "a" * 64
FIRST_RECEIPT = "b" * 64
SECOND_RECEIPT = "c" * 64


def _request(*, candidate: str = CANDIDATE_COMMIT, data_version: str = "positions-v1"):
    return publication.build_request(
        candidate_commit=candidate,
        rollback_commit=ROLLBACK_COMMIT,
        environment="preproduction",
        expected_revision="20260805_97",
        position_exam_code="guokao",
        position_exam_year=2026,
        position_rule_set_version="rules-v1",
        position_data_version=data_version,
        position_window_manifest_sha256="d" * 64,
        knowledge_exam_scope="guokao",
        knowledge_track="xingce",
        knowledge_release_version="knowledge-v1",
        knowledge_source_data_version="knowledge-source-v1",
        knowledge_fresh_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
    )


def _database_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _libpq_url(engine: Engine) -> str:
    return _database_url(engine).replace(
        "postgresql+psycopg:",
        "postgresql:",
        1,
    )


def _activate(
    engine: Engine,
    *,
    request: dict[str, Any],
    previous_receipt: str,
    receipt: str,
) -> dict[str, Any]:
    database_url = _database_url(engine)
    started = publication.start_next_publication(
        database_url=database_url,
        request=request,
        previous_receipt_sha256=previous_receipt,
    )
    holder = psycopg.connect(_libpq_url(engine), autocommit=True)
    try:
        holder.execute(
            "select pg_advisory_lock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        publication.record_publication_receipt(
            database_url=database_url,
            expected_request_digest=started["requestDigest"],
            receipt_sha256=receipt,
        )
        return publication.finish_publication(
            database_url=database_url,
            expected_request_digest=started["requestDigest"],
            receipt_sha256=receipt,
        )
    finally:
        holder.execute(
            "select pg_advisory_unlock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        holder.close()


def test_shared_checkout_blocks_exclusive_until_existing_work_drains(
    db_engine: Engine,
) -> None:
    first_request = _request()
    _activate(
        db_engine,
        request=first_request,
        previous_receipt=BASELINE_RECEIPT,
        receipt=FIRST_RECEIPT,
    )
    guarded = install_commercial_data_publication_guard(
        create_engine(_database_url(db_engine), pool_pre_ping=True, future=True)
    )
    checked_out = guarded.connect()
    assert checked_out.execute(text("select 1")).scalar_one() == 1

    second_request = _request(
        candidate="2" * 40,
        data_version="positions-v2",
    )
    publication.start_next_publication(
        database_url=_database_url(db_engine),
        request=second_request,
        previous_receipt_sha256=FIRST_RECEIPT,
    )
    acquired = Event()
    release = Event()

    def acquire_exclusive() -> None:
        with psycopg.connect(_libpq_url(db_engine), autocommit=True) as connection:
            connection.execute(
                "select pg_advisory_lock(%s)",
                (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
            )
            acquired.set()
            assert release.wait(timeout=5)
            connection.execute(
                "select pg_advisory_unlock(%s)",
                (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(acquire_exclusive)
        assert not acquired.wait(timeout=0.5)
        checked_out.close()
        assert acquired.wait(timeout=5)
        release.set()
        future.result(timeout=5)
    guarded.dispose()


def test_runtime_guard_binds_application_commit_and_environment(
    db_engine: Engine,
) -> None:
    _activate(
        db_engine,
        request=_request(),
        previous_receipt=BASELINE_RECEIPT,
        receipt=FIRST_RECEIPT,
    )
    valid = install_commercial_data_publication_guard(
        create_engine(_database_url(db_engine), pool_pre_ping=True, future=True),
        runtime_environment="preproduction",
        application_git_commit=CANDIDATE_COMMIT,
        data_receipt_sha256=FIRST_RECEIPT,
    )
    wrong_commit = install_commercial_data_publication_guard(
        create_engine(_database_url(db_engine), pool_pre_ping=True, future=True),
        runtime_environment="preproduction",
        application_git_commit="3" * 40,
        data_receipt_sha256=FIRST_RECEIPT,
    )
    wrong_environment = install_commercial_data_publication_guard(
        create_engine(_database_url(db_engine), pool_pre_ping=True, future=True),
        runtime_environment="production",
        application_git_commit=CANDIDATE_COMMIT,
        data_receipt_sha256=FIRST_RECEIPT,
    )
    wrong_receipt = install_commercial_data_publication_guard(
        create_engine(_database_url(db_engine), pool_pre_ping=True, future=True),
        runtime_environment="preproduction",
        application_git_commit=CANDIDATE_COMMIT,
        data_receipt_sha256=SECOND_RECEIPT,
    )
    try:
        with valid.connect() as connection:
            assert connection.execute(text("select 1")).scalar_one() == 1
        for invalid in (wrong_commit, wrong_environment, wrong_receipt):
            with pytest.raises(
                DatabasePublicationUnavailable,
                match="runtime application identity",
            ):
                invalid.connect()
    finally:
        valid.dispose()
        wrong_commit.dispose()
        wrong_environment.dispose()
        wrong_receipt.dispose()


def test_pointer_application_and_database_pairing_is_exact(
    db_engine: Engine,
) -> None:
    request = _request()
    active = _activate(
        db_engine,
        request=request,
        previous_receipt=BASELINE_RECEIPT,
        receipt=FIRST_RECEIPT,
    )
    paired = publication.assert_active_publication_pairing(
        database_url=_database_url(db_engine),
        receipt_sha256=FIRST_RECEIPT,
        environment="preproduction",
        expected_revision=request["expectedDatabaseRevision"],
        expected_compatible_commits=(
            CANDIDATE_COMMIT,
            ROLLBACK_COMMIT,
        ),
    )
    assert paired["requestDigest"] == active["requestDigest"]
    invalid_cases = (
        {
            "receipt_sha256": SECOND_RECEIPT,
            "environment": "preproduction",
            "expected_revision": request["expectedDatabaseRevision"],
            "expected_compatible_commits": (
                CANDIDATE_COMMIT,
                ROLLBACK_COMMIT,
            ),
        },
        {
            "receipt_sha256": FIRST_RECEIPT,
            "environment": "production",
            "expected_revision": request["expectedDatabaseRevision"],
            "expected_compatible_commits": (
                CANDIDATE_COMMIT,
                ROLLBACK_COMMIT,
            ),
        },
        {
            "receipt_sha256": FIRST_RECEIPT,
            "environment": "preproduction",
            "expected_revision": "wrong-revision",
            "expected_compatible_commits": (
                CANDIDATE_COMMIT,
                ROLLBACK_COMMIT,
            ),
        },
        {
            "receipt_sha256": FIRST_RECEIPT,
            "environment": "preproduction",
            "expected_revision": request["expectedDatabaseRevision"],
            "expected_compatible_commits": (
                CANDIDATE_COMMIT,
                "3" * 40,
            ),
        },
    )
    for invalid in invalid_cases:
        with pytest.raises(
            publication.CommercialDataPublicationError,
        ):
            publication.assert_active_publication_pairing(
                database_url=_database_url(db_engine),
                **invalid,
            )

    next_request = _request(
        candidate="2" * 40,
        data_version="positions-v2",
    )
    publishing = publication.start_next_publication(
        database_url=_database_url(db_engine),
        request=next_request,
        previous_receipt_sha256=FIRST_RECEIPT,
    )
    holder = psycopg.connect(_libpq_url(db_engine), autocommit=True)
    try:
        holder.execute(
            "select pg_advisory_lock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        publication.record_publication_receipt(
            database_url=_database_url(db_engine),
            expected_request_digest=publishing["requestDigest"],
            receipt_sha256=SECOND_RECEIPT,
        )
        with pytest.raises(
            publication.CommercialDataPublicationError,
            match="not active",
        ):
            publication.assert_active_publication_pairing(
                database_url=_database_url(db_engine),
                receipt_sha256=SECOND_RECEIPT,
                environment="preproduction",
                expected_revision=next_request["expectedDatabaseRevision"],
                expected_compatible_commits=(
                    "2" * 40,
                    ROLLBACK_COMMIT,
                ),
            )
        recorded = publication.assert_active_publication_pairing(
            database_url=_database_url(db_engine),
            receipt_sha256=SECOND_RECEIPT,
            environment="preproduction",
            expected_revision=next_request["expectedDatabaseRevision"],
            expected_compatible_commits=(
                "2" * 40,
                ROLLBACK_COMMIT,
            ),
            allow_recorded_publishing=True,
        )
        assert recorded["state"] == "publishing"
    finally:
        holder.execute(
            "select pg_advisory_unlock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        holder.close()


def test_publishing_empty_missing_and_corrupt_authorities_fail_closed(
    db_engine: Engine,
) -> None:
    database_url = _database_url(db_engine)
    guarded = install_commercial_data_publication_guard(
        create_engine(database_url, pool_pre_ping=True, future=True)
    )
    with pytest.raises(DatabasePublicationUnavailable):
        guarded.connect()
    guarded.dispose()

    request = _request()
    publication.start_next_publication(
        database_url=database_url,
        request=request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    guarded = install_commercial_data_publication_guard(
        create_engine(database_url, pool_pre_ping=True, future=True)
    )
    with pytest.raises(DatabasePublicationUnavailable):
        guarded.connect()
    guarded.dispose()

    _activate(
        db_engine,
        request=request,
        previous_receipt=BASELINE_RECEIPT,
        receipt=FIRST_RECEIPT,
    )
    with db_engine.begin() as connection:
        connection.execute(
            text("set local gongkao.commercial_data_publication_writer = 'publisher-v1'")
        )
        connection.execute(
            text(
                "update platform.commercial_data_publication_state "
                "set active_receipt_sha256 = repeat('e', 64)"
            )
        )
    guarded = install_commercial_data_publication_guard(
        create_engine(database_url, pool_pre_ping=True, future=True)
    )
    with pytest.raises(DatabasePublicationUnavailable):
        guarded.connect()
    guarded.dispose()

    with db_engine.begin() as connection:
        connection.execute(
            text(
                "alter table platform.commercial_data_publication_state "
                "drop constraint "
                "commercial_data_publication_state_state_check"
            )
        )
        connection.execute(
            text(
                "alter table platform.commercial_data_publication_state "
                "drop constraint "
                "commercial_data_publication_state_active_contract_check"
            )
        )
        connection.execute(
            text("set local gongkao.commercial_data_publication_writer = 'publisher-v1'")
        )
        connection.execute(
            text("update platform.commercial_data_publication_state set state = 'unknown'")
        )
    guarded = install_commercial_data_publication_guard(
        create_engine(database_url, pool_pre_ping=True, future=True)
    )
    with pytest.raises(DatabasePublicationUnavailable):
        guarded.connect()
    guarded.dispose()

    with db_engine.begin() as connection:
        connection.execute(text("drop table platform.commercial_data_publication_state"))
    guarded = install_commercial_data_publication_guard(
        create_engine(database_url, pool_pre_ping=True, future=True)
    )
    with pytest.raises(DatabasePublicationUnavailable):
        guarded.connect()
    guarded.dispose()


def test_different_request_is_rejected_and_exact_request_resumes(
    db_engine: Engine,
) -> None:
    database_url = _database_url(db_engine)
    first_request = _request()
    first = publication.start_next_publication(
        database_url=database_url,
        request=first_request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    resumed = publication.start_next_publication(
        database_url=database_url,
        request=first_request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    assert first["action"] == "started"
    assert resumed["action"] == "resume"
    assert resumed["publicationId"] == first["publicationId"]

    different_request = _request(
        candidate="2" * 40,
        data_version="positions-v2",
    )
    with pytest.raises(
        publication.CommercialDataPublicationError,
        match="another commercial data publication request",
    ):
        publication.start_next_publication(
            database_url=database_url,
            request=different_request,
            previous_receipt_sha256=BASELINE_RECEIPT,
        )

    assert (
        publication.classify_pointer(
            state=publication.inspect_publication_state(database_url=database_url),
            pointer_receipt_sha256=BASELINE_RECEIPT,
        )
        == "redo-publication"
    )
    holder = psycopg.connect(_libpq_url(db_engine), autocommit=True)
    try:
        holder.execute(
            "select pg_advisory_lock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        publication.record_publication_receipt(
            database_url=database_url,
            expected_request_digest=first["requestDigest"],
            receipt_sha256=FIRST_RECEIPT,
        )
        publishing = publication.inspect_publication_state(database_url=database_url)
        assert (
            publication.classify_pointer(
                state=publishing,
                pointer_receipt_sha256=BASELINE_RECEIPT,
            )
            == "redo-publication"
        )
        assert (
            publication.classify_pointer(
                state=publishing,
                pointer_receipt_sha256=FIRST_RECEIPT,
            )
            == "verify-pointer-and-finish"
        )
        publication.finish_publication(
            database_url=database_url,
            expected_request_digest=first["requestDigest"],
            receipt_sha256=FIRST_RECEIPT,
        )
    finally:
        holder.execute(
            "select pg_advisory_unlock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        holder.close()
    active = publication.inspect_publication_state(database_url=database_url)
    assert active["publicationDigest"] == publication.publication_digest_for(
        request_digest_value=first["requestDigest"],
        receipt_sha256=FIRST_RECEIPT,
    )
    assert active["publicationDigest"] != FIRST_RECEIPT
    assert (
        publication.classify_pointer(
            state=active,
            pointer_receipt_sha256=FIRST_RECEIPT,
        )
        == "verify-active-noop-or-start"
    )
    no_op = publication.start_next_publication(
        database_url=database_url,
        request=first_request,
        previous_receipt_sha256=FIRST_RECEIPT,
    )
    assert no_op["action"] == "already_active"


def test_killed_exclusive_controller_cannot_escape_inherited_database_fence(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    request = _request()
    state = publication.start_next_publication(
        database_url=_database_url(db_engine),
        request=request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                create table platform.publication_kill_window_probe (
                  stage text primary key
                )
                """
            )
        )

    ready = tmp_path / "exclusive-ready.json"
    first_ready = tmp_path / "first-mutation-ready"
    first_gate = tmp_path / "first-mutation-gate"
    first_done = tmp_path / "first-mutation-done"
    child_pid_file = tmp_path / "first-mutation-pid"
    first_mutation = tmp_path / "first_mutation.py"
    first_mutation.write_text(
        """
from pathlib import Path
import os
import signal
import stat
import sys
import time

import psycopg

database_url, ready, gate, done, pid_file = sys.argv[1:]
descriptor = os.fstat(8)
assert stat.S_ISSOCK(descriptor.st_mode)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")
Path(ready).touch()
while not Path(gate).exists():
    time.sleep(0.01)
with psycopg.connect(database_url, autocommit=True) as connection:
    connection.execute(
        "insert into platform.publication_kill_window_probe(stage) "
        "values ('in-flight')"
    )
Path(done).touch()
""".lstrip(),
        encoding="utf-8",
    )
    controller = tmp_path / "mutation_controller.py"
    controller.write_text(
        """
import os
from pathlib import Path
import subprocess
import sys

(
    python,
    publication_script,
    mutation,
    database_url,
    ready,
    gate,
    done,
    pid_file,
) = sys.argv[1:]
subprocess.run(
    [
        python,
        publication_script,
        "assert-inherited-fence",
        "--request-digest",
        os.environ["COMMERCIAL_DATA_PUBLICATION_FENCE_REQUEST_DIGEST"],
        "--backend-pid",
        os.environ["COMMERCIAL_DATA_PUBLICATION_FENCE_BACKEND_PID"],
        "--client-address",
        os.environ["COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_ADDRESS"],
        "--client-port",
        os.environ["COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_PORT"],
        "--fence-fd",
        os.environ["COMMERCIAL_DATA_PUBLICATION_FENCE_FD"],
    ],
    check=True,
    pass_fds=(8,),
)
child = subprocess.Popen(
    [python, mutation, database_url, ready, gate, done, pid_file],
    pass_fds=(8,),
    start_new_session=True,
)
child.wait()
with __import__("psycopg").connect(database_url, autocommit=True) as connection:
    connection.execute(
        "insert into platform.publication_kill_window_probe(stage) "
        "values ('next')"
    )
""".lstrip(),
        encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment["MIGRATION_DATABASE_URL"] = _database_url(db_engine)
    process = subprocess.Popen(
        [
            str(REPOSITORY_ROOT / "backend" / ".venv" / "bin" / "python"),
            str(SCRIPT_PATH),
            "exec-exclusive",
            "--request-digest",
            state["requestDigest"],
            "--ready-file",
            str(ready),
            "--",
            str(REPOSITORY_ROOT / "backend" / ".venv" / "bin" / "python"),
            str(controller),
            str(REPOSITORY_ROOT / "backend" / ".venv" / "bin" / "python"),
            str(SCRIPT_PATH),
            str(first_mutation),
            _libpq_url(db_engine),
            str(first_ready),
            str(first_gate),
            str(first_done),
            str(child_pid_file),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not first_ready.is_file() and process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            stderr = process.communicate(timeout=5)[1]
            pytest.fail(f"exclusive controller did not become ready: {stderr}")
        time.sleep(0.01)
    assert process.poll() is None
    ready_payload = json.loads(ready.read_text(encoding="utf-8"))
    assert isinstance(ready_payload["databaseBackendPid"], int)
    assert isinstance(ready_payload["databaseClientAddress"], str)
    assert isinstance(ready_payload["databaseClientPort"], int)
    assert ready_payload == {
        "schemaVersion": "commercial-data-publication-fence@v2",
        "controllerPid": process.pid,
        "databaseBackendPid": ready_payload["databaseBackendPid"],
        "databaseClientAddress": ready_payload["databaseClientAddress"],
        "databaseClientPort": ready_payload["databaseClientPort"],
        "fenceFd": 8,
        "requestDigest": state["requestDigest"],
    }

    def try_shared_lock() -> bool:
        with psycopg.connect(_libpq_url(db_engine), autocommit=True) as connection:
            acquired = connection.execute(
                "select pg_try_advisory_lock_shared(%s)",
                (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
            ).fetchone()[0]
            if acquired:
                connection.execute(
                    "select pg_advisory_unlock_shared(%s)",
                    (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
                )
            return bool(acquired)

    assert try_shared_lock() is False
    process.kill()
    process.wait(timeout=5)
    assert process.returncode is not None and process.returncode < 0

    # The controller is gone, but its already-running mutation inherited the
    # live PostgreSQL session.  The API shared lock therefore remains fenced.
    assert try_shared_lock() is False
    first_gate.touch()
    deadline = time.monotonic() + 10
    while not first_done.is_file():
        if time.monotonic() >= deadline:
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            os.kill(child_pid, 9)
            pytest.fail("in-flight fenced mutation did not finish")
        time.sleep(0.01)

    after_kill = publication.inspect_publication_state(database_url=_database_url(db_engine))
    assert after_kill["state"] == "publishing"
    assert after_kill["requestDigest"] == state["requestDigest"]
    deadline = time.monotonic() + 10
    while not try_shared_lock():
        if time.monotonic() >= deadline:
            pytest.fail("fence was not released after the in-flight mutation")
        time.sleep(0.01)
    with db_engine.connect() as connection:
        stages = (
            connection.execute(
                text("select stage from platform.publication_kill_window_probe order by stage")
            )
            .scalars()
            .all()
        )
    assert stages == ["in-flight"]


def test_inherited_fence_rejects_a_forged_descriptor(
    db_engine: Engine,
) -> None:
    request = _request()
    state = publication.start_next_publication(
        database_url=_database_url(db_engine),
        request=request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    try:
        saved_fd8 = os.dup(8)
    except OSError:
        saved_fd8 = None
    read_fd, write_fd = os.pipe()
    os.dup2(read_fd, 8, inheritable=True)
    try:
        with pytest.raises(
            publication.CommercialDataPublicationError,
            match="not an inheritable socket",
        ):
            publication.assert_inherited_publication_fence(
                database_url=_database_url(db_engine),
                expected_request_digest=state["requestDigest"],
                expected_backend_pid=os.getpid(),
                expected_client_address="127.0.0.1",
                expected_client_port=5432,
                fence_fd=publication.COMMERCIAL_DATA_PUBLICATION_FENCE_FD,
            )
    finally:
        os.close(read_fd)
        os.close(write_fd)
        if saved_fd8 is None:
            try:
                os.close(8)
            except OSError:
                pass
        else:
            os.dup2(saved_fd8, 8)
            os.close(saved_fd8)


def test_inherited_fence_rejects_an_unrelated_live_database_socket(
    db_engine: Engine,
) -> None:
    request = _request()
    state = publication.start_next_publication(
        database_url=_database_url(db_engine),
        request=request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    holder = psycopg.connect(_libpq_url(db_engine), autocommit=True)
    unrelated = psycopg.connect(_libpq_url(db_engine), autocommit=True)
    try:
        holder.execute(
            "select pg_advisory_lock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        identity = holder.execute(
            """
            select
              pg_backend_pid(),
              host(inet_client_addr()),
              inet_client_port()
            """
        ).fetchone()
        try:
            saved_fd8 = os.dup(8)
        except OSError:
            saved_fd8 = None
        os.dup2(unrelated.fileno(), 8, inheritable=True)
        try:
            with pytest.raises(
                publication.CommercialDataPublicationError,
                match="socket is not the lock session",
            ):
                publication.assert_inherited_publication_fence(
                    database_url=_database_url(db_engine),
                    expected_request_digest=state["requestDigest"],
                    expected_backend_pid=identity[0],
                    expected_client_address=identity[1],
                    expected_client_port=identity[2],
                    fence_fd=publication.COMMERCIAL_DATA_PUBLICATION_FENCE_FD,
                )
        finally:
            if saved_fd8 is None:
                os.close(8)
            else:
                os.dup2(saved_fd8, 8)
                os.close(saved_fd8)
    finally:
        holder.execute(
            "select pg_advisory_unlock(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        unrelated.close()
        holder.close()


def test_transition_ledger_is_append_only_and_migration_is_forward_only(
    db_engine: Engine,
) -> None:
    request = _request()
    publication.start_next_publication(
        database_url=_database_url(db_engine),
        request=request,
        previous_receipt_sha256=BASELINE_RECEIPT,
    )
    with pytest.raises(DBAPIError) as changed:
        with db_engine.begin() as connection:
            connection.execute(
                text("set local gongkao.commercial_data_publication_writer = 'publisher-v1'")
            )
            connection.execute(
                text(
                    "update platform.commercial_data_publication_transitions "
                    "set event_kind = 'publication_resumed'"
                )
            )
    assert changed.value.orig.sqlstate == "42501"

    with db_engine.connect() as connection:
        public_writes = connection.execute(
            text(
                """
                select count(*)
                  from pg_catalog.pg_class relation
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = relation.relnamespace
                  cross join lateral pg_catalog.aclexplode(
                    coalesce(
                      relation.relacl,
                      pg_catalog.acldefault('r', relation.relowner)
                    )
                  ) privilege
                 where namespace.nspname = 'platform'
                   and relation.relname in (
                     'commercial_data_publication_state',
                     'commercial_data_publication_transitions'
                   )
                   and privilege.grantee = 0
                   and privilege.privilege_type in (
                     'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'
                   )
                """
            )
        ).scalar_one()
    assert public_writes == 0
    migration = importlib.import_module(
        "migrations.versions.20260730_81_commercial_data_publication_protocol"
    )
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_runtime_role_cannot_forge_publication_despite_default_grants_and_guc(
    db_engine: Engine,
) -> None:
    migration83 = importlib.import_module(
        "migrations.versions.20260730_83_commercial_publication_acl_hardening"
    )
    with db_engine.begin() as connection:
        role_exists = connection.execute(
            text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
        ).scalar_one()
        if role_exists:
            connection.exec_driver_sql("drop owned by gongkao_api")
            connection.exec_driver_sql("drop role gongkao_api")
        connection.exec_driver_sql("create role gongkao_api login")
        connection.exec_driver_sql("grant usage on schema platform to gongkao_api")
        connection.exec_driver_sql(
            "grant select, insert, update, delete, truncate, references, "
            "trigger on "
            "platform.commercial_data_publication_state, "
            "platform.commercial_data_publication_transitions "
            "to gongkao_api"
        )
        connection.exec_driver_sql(
            "grant usage, select, update on sequence "
            "platform."
            "commercial_data_publication_transitions_transition_id_seq "
            "to gongkao_api"
        )
        migration83.harden_runtime_publication_authority(connection)
        for table_name in (
            "platform.commercial_data_publication_state",
            "platform.commercial_data_publication_transitions",
        ):
            assert connection.execute(
                text("select has_table_privilege('gongkao_api', :table_name, 'SELECT')"),
                {"table_name": table_name},
            ).scalar_one()
            for privilege in (
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert not connection.execute(
                    text("select has_table_privilege('gongkao_api', :table_name, :privilege)"),
                    {
                        "table_name": table_name,
                        "privilege": privilege,
                    },
                ).scalar_one()
        for privilege in ("USAGE", "SELECT", "UPDATE"):
            assert not connection.execute(
                text(
                    "select has_sequence_privilege("
                    "'gongkao_api', "
                    "'platform."
                    "commercial_data_publication_transitions_"
                    "transition_id_seq', :privilege)"
                ),
                {"privilege": privilege},
            ).scalar_one()

    runtime = psycopg.connect(_libpq_url(db_engine), autocommit=True)
    try:
        runtime.execute("set role gongkao_api")
        runtime.execute(
            "select set_config('gongkao.commercial_data_publication_writer', 'publisher-v1', false)"
        )
        assert (
            runtime.execute(
                "select count(*) from platform.commercial_data_publication_state"
            ).fetchone()
            is not None
        )
        forbidden_statements = (
            """
            insert into platform.commercial_data_publication_state (
              singleton_key, publication_id, state, request_digest,
              request_payload, publication_digest,
              previous_receipt_sha256, active_receipt_sha256,
              expected_database_revision, compatible_application_commits,
              publication_started_at, state_changed_at, activated_at
            ) values (
              true, gen_random_uuid(), 'active', repeat('a', 64),
              '{}'::jsonb, repeat('b', 64), repeat('c', 64),
              repeat('d', 64), '20260804_95', '[]'::jsonb,
              now(), now(), now()
            )
            """,
            "update platform.commercial_data_publication_state set state = state",
            "delete from platform.commercial_data_publication_state",
            "truncate platform.commercial_data_publication_state",
            "select nextval('platform.commercial_data_publication_transitions_transition_id_seq')",
        )
        for statement in forbidden_statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(statement)
    finally:
        try:
            runtime.execute("reset role")
        finally:
            runtime.close()
        with db_engine.begin() as connection:
            connection.exec_driver_sql("drop owned by gongkao_api")
            connection.exec_driver_sql("drop role gongkao_api")

    with pytest.raises(RuntimeError, match="forward-only"):
        migration83.downgrade()


def test_companion_checkpoint_inherits_the_production_publication_guard(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate(
        db_engine,
        request=_request(),
        previous_receipt=BASELINE_RECEIPT,
        receipt=FIRST_RECEIPT,
    )
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="publication-metrics-token"),
    )
    with Session(bind=db_engine) as metrics_session:
        payload = operations_router.metrics(
            metrics_session,
            authorization="Bearer publication-metrics-token",
        )
    assert "anan_commercial_data_publication_active 1" in payload
    assert "anan_commercial_data_publication_exact 1" in payload
    assert "anan_commercial_data_publication_transitions_total 3" in payload

    guarded = install_commercial_data_publication_guard(
        create_engine(_database_url(db_engine), pool_pre_ping=True, future=True)
    )
    with Session(bind=guarded) as session:
        checkpoint = _new_checkpoint_engine(session)
    try:
        assert commercial_data_publication_guard_installed(checkpoint)
        with checkpoint.connect() as connection:
            assert connection.execute(text("select 1")).scalar_one() == 1
    finally:
        checkpoint.dispose()
        guarded.dispose()


def test_companion_checkpoint_inherits_timeouts_and_releases_on_row_lock(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate(
        db_engine,
        request=_request(),
        previous_receipt=BASELINE_RECEIPT,
        receipt=FIRST_RECEIPT,
    )
    settings = Settings(
        database_statement_timeout_ms=1_000,
        database_lock_timeout_ms=200,
        database_idle_transaction_timeout_ms=1_500,
    )
    monkeypatch.setattr(
        companion_commands,
        "get_settings",
        lambda: settings,
    )
    main_options: dict[str, Any] = {
        "pool_pre_ping": True,
        "future": True,
    }
    main_options.update(_postgres_engine_options(settings))
    guarded = install_commercial_data_publication_guard(
        create_engine(_database_url(db_engine), **main_options)
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "create table platform.checkpoint_timeout_probe ("
                "probe_id integer primary key, value integer not null)"
            )
        )
        connection.execute(text("insert into platform.checkpoint_timeout_probe values (1, 0)"))

    with Session(bind=guarded) as session:
        checkpoint = _new_checkpoint_engine(session)
    blocker = psycopg.connect(_libpq_url(db_engine))
    try:
        with guarded.connect() as main_connection:
            main_timeouts = main_connection.execute(
                text(
                    "select "
                    "current_setting('statement_timeout'), "
                    "current_setting('lock_timeout'), "
                    "current_setting("
                    "'idle_in_transaction_session_timeout')"
                )
            ).one()
        with checkpoint.connect() as checkpoint_connection:
            checkpoint_timeouts = checkpoint_connection.execute(
                text(
                    "select "
                    "current_setting('statement_timeout'), "
                    "current_setting('lock_timeout'), "
                    "current_setting("
                    "'idle_in_transaction_session_timeout')"
                )
            ).one()
        assert checkpoint_timeouts == main_timeouts
        assert checkpoint_timeouts == ("1s", "200ms", "1500ms")

        blocker.execute(
            "update platform.checkpoint_timeout_probe set value = value + 1 where probe_id = 1"
        )
        started = time.monotonic()
        with pytest.raises(DBAPIError) as timed_out:
            with checkpoint.begin() as connection:
                connection.execute(
                    text(
                        "update platform.checkpoint_timeout_probe "
                        "set value = value + 1 where probe_id = 1"
                    )
                )
        elapsed = time.monotonic() - started
        assert timed_out.value.orig.sqlstate == "55P03"
        assert 0.1 <= elapsed < 2

        with psycopg.connect(
            _libpq_url(db_engine),
            autocommit=True,
        ) as exclusive:
            acquired = exclusive.execute(
                "select pg_try_advisory_lock(%s)",
                (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
            ).fetchone()[0]
            assert acquired is True
            exclusive.execute(
                "select pg_advisory_unlock(%s)",
                (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
            )
    finally:
        blocker.rollback()
        blocker.close()
        checkpoint.dispose()
        guarded.dispose()
        with db_engine.begin() as connection:
            connection.execute(text("drop table platform.checkpoint_timeout_probe"))
