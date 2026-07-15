from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def postgres_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    configured_url = os.getenv("TEST_DATABASE_URL")
    if configured_url:
        yield configured_url
        return

    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.fail("PostgreSQL initdb/pg_ctl is required for database contract tests")

    data_dir = tmp_path_factory.mktemp("postgres-data")
    log_file = tmp_path_factory.mktemp("postgres-log") / "postgres.log"
    socket_dir = Path(tempfile.mkdtemp(prefix="pgsock-", dir="/tmp"))
    port = _free_port()

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
            f"-p {port} -h 127.0.0.1 -k {socket_dir}",
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
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.fixture()
def db_engine(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine)
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


@pytest.fixture()
def db_session(db_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with db_session_factory() as session:
        yield session


def _reset_test_schema(engine: Engine) -> None:
    _drop_test_schema(engine)
    schema_sql = Path(__file__).with_name("sql").joinpath("minimal_schema.sql").read_text()
    with engine.begin() as conn:
        for statement in _split_postgres_statements(schema_sql):
            conn.execute(text(statement))


def _split_postgres_statements(script: str) -> list[str]:
    """Split fixture DDL without breaking quoted PL/pgSQL function bodies."""

    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment_depth = 0

    while index < len(script):
        if dollar_tag is not None:
            if script.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue
        if line_comment:
            if script[index] == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment_depth:
            if script.startswith("/*", index):
                block_comment_depth += 1
                index += 2
            elif script.startswith("*/", index):
                block_comment_depth -= 1
                index += 2
            else:
                index += 1
            continue
        if quote is not None:
            if script[index] == quote:
                if index + 1 < len(script) and script[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if script.startswith("--", index):
            line_comment = True
            index += 2
            continue
        if script.startswith("/*", index):
            block_comment_depth = 1
            index += 2
            continue
        if script[index] in {"'", '"'}:
            quote = script[index]
            index += 1
            continue
        if script[index] == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", script[index:])
            if match is not None:
                dollar_tag = match.group(0)
                index += len(dollar_tag)
                continue
        if script[index] == ";":
            statement = script[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1

    trailing = script[start:].strip()
    if trailing:
        statements.append(trailing)
    if quote is not None or dollar_tag is not None or block_comment_depth:
        raise ValueError("minimal_schema.sql contains an unterminated SQL construct")
    return statements


def _drop_test_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("drop schema if exists product cascade"))
        conn.execute(text("drop schema if exists learner cascade"))
        conn.execute(text("drop schema if exists ai_audit cascade"))
        conn.execute(text("drop schema if exists commerce cascade"))
        conn.execute(text("drop schema if exists platform cascade"))
        conn.execute(text("drop schema if exists learning cascade"))
