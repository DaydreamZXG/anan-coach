#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import traceback
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
DEFAULT_OUTPUT_DIRECTORY = Path(
    "/Users/zxg/Documents/新项目/audit/commercial-repair/"
    "overnight-20260730-010444"
)
BINDING_COUNT = 99_409
NODE_COUNT = 563
CANONICAL_MODULES = (
    ("political_theory", "政治理论"),
    ("general_knowledge", "常识判断"),
    ("verbal_comprehension", "言语理解与表达"),
    ("quantitative_relations", "数量关系"),
    ("judgment_reasoning", "判断推理"),
    ("data_analysis", "资料分析"),
)
RELEASE_ID = "11111111-1111-4111-8111-111111111111"
EXPECTED_INDEXES = {
    "release_scope": "product_knowledge_map_binding_release_selection_idx",
    "module_scope": "product_knowledge_map_binding_module_selection_idx_v2",
    "targeted_node_scope": "product_knowledge_map_binding_node_selection_idx",
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an isolated local PostgreSQL fixture and capture real Xingce "
            "selection EXPLAIN ANALYZE plans over a private Unix socket. The "
            "command disables TCP and never accepts an external database URL."
        )
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 20:
        parser.error("--repetitions must be between 3 and 20")
    return args


@contextmanager
def _ephemeral_unix_socket_postgres() -> Iterator[tuple[URL, Path, Path]]:
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if initdb is None or pg_ctl is None:
        raise RuntimeError("local initdb and pg_ctl are required")

    temporary_root = Path(tempfile.mkdtemp(prefix="xingce-selection-explain-"))
    temporary_root.chmod(0o700)
    data_directory = temporary_root / "data"
    socket_directory = temporary_root / "socket"
    socket_directory.mkdir(mode=0o700)
    socket_directory.chmod(0o700)
    log_file = temporary_root / "postgres.log"
    started = False
    previous_pg_host = os.environ.get("PGHOST")
    previous_pg_port = os.environ.get("PGPORT")
    try:
        subprocess.run(
            [
                initdb,
                "-D",
                str(data_directory),
                "--auth-host=reject",
                "--auth-local=trust",
                "--username=postgres",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(data_directory),
                "-l",
                str(log_file),
                "-o",
                (
                    f"-h '' -k {socket_directory} "
                    "-c unix_socket_permissions=0700 "
                    "-c fsync=off -c synchronous_commit=off "
                    "-c full_page_writes=off"
                ),
                "start",
                "-w",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        started = True
        os.environ["PGHOST"] = str(socket_directory)
        os.environ["PGPORT"] = "5432"
        yield (
            URL.create(
                "postgresql+psycopg",
                username="postgres",
                database="postgres",
            ),
            log_file,
            socket_directory,
        )
    finally:
        if previous_pg_host is None:
            os.environ.pop("PGHOST", None)
        else:
            os.environ["PGHOST"] = previous_pg_host
        if previous_pg_port is None:
            os.environ.pop("PGPORT", None)
        else:
            os.environ["PGPORT"] = previous_pg_port
        if started:
            subprocess.run(
                [
                    pg_ctl,
                    "-D",
                    str(data_directory),
                    "stop",
                    "-m",
                    "fast",
                    "-w",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        shutil.rmtree(temporary_root, ignore_errors=True)


def _module_code_case(value_sql: str) -> str:
    clauses = " ".join(
        f"when {index} then '{module_code}'"
        for index, (module_code, _module_name) in enumerate(CANONICAL_MODULES)
    )
    return f"case mod({value_sql}, {len(CANONICAL_MODULES)}) {clauses} end"


def _module_name_case(value_sql: str) -> str:
    clauses = " ".join(
        f"when {index} then '{module_name}'"
        for index, (_module_code, module_name) in enumerate(CANONICAL_MODULES)
    )
    return f"case mod({value_sql}, {len(CANONICAL_MODULES)}) {clauses} end"


def _seed_fixture(engine: Engine) -> dict[str, Any]:
    node_module_name = _module_name_case("node_number - 1")
    question_module_code = _module_code_case("node_number - 1")
    question_module_name = _module_name_case("node_number - 1")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id, release_version, exam_scope, track,
                  source_data_version, vocab_version, generator_version,
                  source_fingerprint, generated_at, fresh_until, status,
                  node_count, activated_at, retired_at
                ) values (
                  cast(:release_id as uuid),
                  'knowledge-map@local-selection-explain-v1',
                  'guokao', 'xingce',
                  'synthetic-local-only@99409',
                  'synthetic-vocab@v1',
                  'explain_xingce_selection@v1',
                  repeat('1', 64),
                  transaction_timestamp() - interval '1 day',
                  transaction_timestamp() + interval '30 days',
                  'draft', 0, null, null
                )
                """
            ),
            {"release_id": RELEASE_ID},
        )
        connection.execute(
            text(
                f"""
                insert into product.knowledge_map_nodes (
                  release_id, node_id, exam_scope, track, module_name,
                  node_type, tag_type, tag_value, display_path,
                  support_status, near5_window_start_year,
                  near5_window_end_year, near5_weight,
                  near5_occurrence_count, stable_for_old_questions,
                  status
                )
                select
                  cast(:release_id as uuid),
                  'perf-node-' || lpad(node_number::text, 4, '0'),
                  'guokao',
                  'xingce',
                  {node_module_name},
                  'knowledge_point',
                  'knowledge_point',
                  'perf-tag-' || lpad(node_number::text, 4, '0'),
                  {node_module_name} || '/perf-tag-' ||
                    lpad(node_number::text, 4, '0'),
                  'text_supported',
                  2022,
                  2026,
                  0.5,
                  100,
                  true,
                  'published'
                from generate_series(1, :node_count) node_number
                """
            ),
            {"release_id": RELEASE_ID, "node_count": NODE_COUNT},
        )
        connection.execute(
            text(
                f"""
                with fixture_questions as materialized (
                  select
                    question_number,
                    mod(question_number - 1, :node_count) + 1 as node_number,
                    'perf-q-' || lpad(question_number::text, 6, '0') ||
                      '-' || substr(md5(question_number::text), 1, 8)
                      as question_id
                  from generate_series(1, :binding_count) question_number
                )
                insert into product.questions (
                  question_id, domain, status, module_name, question_type,
                  stem, answer, shared_context_required, visual_required,
                  needs_multimodal, occurrence_count, latest_year
                )
                select
                  question_id,
                  'objective',
                  'canonical',
                  {question_module_name},
                  'single_choice',
                  '本机隔离查询计划题 ' || question_number,
                  'A',
                  false,
                  false,
                  false,
                  1 + mod(question_number, 500),
                  2020 + mod(question_number, 6)
                from fixture_questions
                """
            ),
            {"node_count": NODE_COUNT, "binding_count": BINDING_COUNT},
        )
        connection.execute(
            text(
                f"""
                with fixture_questions as materialized (
                  select
                    question_number,
                    mod(question_number - 1, :node_count) + 1 as node_number,
                    'perf-q-' || lpad(question_number::text, 6, '0') ||
                      '-' || substr(md5(question_number::text), 1, 8)
                      as question_id
                  from generate_series(1, :binding_count) question_number
                )
                insert into product.question_content_versions (
                  content_version_id, question_id, track,
                  snapshot_payload, normalized_search_text, created_at
                )
                select
                  encode(
                    digest(
                      convert_to('perf-content-' || question_number, 'UTF8'),
                      'sha256'
                    ),
                    'hex'
                  ),
                  question_id,
                  'xingce',
                  jsonb_build_object(
                    'question_id', question_id,
                    'module_name', {question_module_name},
                    'stem', '本机隔离查询计划题 ' || question_number,
                    'answer', 'A',
                    'assets', '[]'::jsonb,
                    'options', jsonb_build_array(
                      jsonb_build_object('label', 'A', 'option_text', '选项 A'),
                      jsonb_build_object('label', 'B', 'option_text', '选项 B'),
                      jsonb_build_object('label', 'C', 'option_text', '选项 C'),
                      jsonb_build_object('label', 'D', 'option_text', '选项 D')
                    )
                  ),
                  '本机隔离查询计划题 ' || question_number,
                  transaction_timestamp()
                from fixture_questions
                """
            ),
            {"node_count": NODE_COUNT, "binding_count": BINDING_COUNT},
        )
        connection.exec_driver_sql(
            "alter table product.knowledge_map_question_bindings "
            "disable trigger knowledge_map_question_bindings_immutable"
        )
        try:
            connection.execute(
                text(
                    f"""
                    with fixture_questions as materialized (
                      select
                        question_number,
                        mod(question_number - 1, :node_count) + 1 as node_number,
                        'perf-q-' || lpad(question_number::text, 6, '0') ||
                          '-' || substr(md5(question_number::text), 1, 8)
                          as question_id
                      from generate_series(1, :binding_count) question_number
                    )
                    insert into product.knowledge_map_question_bindings (
                      release_id, node_id, question_id, content_version_id,
                      module_code, canonical_module, binding_kind,
                      source_tag_type, source_tag_value, delivery_eligible,
                      visual_required, shared_context_required,
                      latest_year, occurrence_count,
                      latest_recent_exam_year, recent_occurrence_count,
                      latest_old_exam_year, old_occurrence_count,
                      binding_fingerprint
                    )
                    select
                      cast(:release_id as uuid),
                      'perf-node-' || lpad(node_number::text, 4, '0'),
                      question_id,
                      encode(
                        digest(
                          convert_to(
                            'perf-content-' || question_number,
                            'UTF8'
                          ),
                          'sha256'
                        ),
                        'hex'
                      ),
                      {question_module_code},
                      {question_module_name},
                      'direct',
                      'knowledge_point',
                      'perf-tag-' || lpad(node_number::text, 4, '0'),
                      true,
                      false,
                      false,
                      2020 + mod(question_number, 6),
                      1 + mod(question_number, 500),
                      2020 + mod(question_number, 6),
                      1 + mod(question_number, 50),
                      null,
                      0,
                      encode(
                        digest(
                          convert_to(
                            'perf-binding-' || question_number,
                            'UTF8'
                          ),
                          'sha256'
                        ),
                        'hex'
                      )
                    from fixture_questions
                    """
                ),
                {
                    "release_id": RELEASE_ID,
                    "node_count": NODE_COUNT,
                    "binding_count": BINDING_COUNT,
                },
            )
        finally:
            connection.exec_driver_sql(
                "alter table product.knowledge_map_question_bindings "
                "enable trigger knowledge_map_question_bindings_immutable"
            )

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for relation in (
            "product.questions",
            "product.question_content_versions",
            "product.knowledge_map_question_bindings",
            "learning.xingce_assessments",
            "learning.xingce_question_exposures",
        ):
            connection.exec_driver_sql(f"vacuum (analyze, freeze) {relation}")

    with engine.connect() as connection:
        fixture = dict(
            connection.execute(
                text(
                    """
                    select
                      count(*)::integer as binding_rows,
                      count(distinct question_id)::integer as unique_questions,
                      count(distinct node_id)::integer as nodes,
                      count(distinct canonical_module)::integer as modules,
                      count(*) filter (
                        where delivery_eligible
                      )::integer as delivery_eligible_rows
                    from product.knowledge_map_question_bindings
                    where release_id = cast(:release_id as uuid)
                    """
                ),
                {"release_id": RELEASE_ID},
            )
            .mappings()
            .one()
        )
        module_counts = {
            str(row["canonical_module"]): int(row["binding_rows"])
            for row in connection.execute(
                text(
                    """
                    select canonical_module, count(*)::integer as binding_rows
                    from product.knowledge_map_question_bindings
                    where release_id = cast(:release_id as uuid)
                    group by canonical_module
                    order by canonical_module
                    """
                ),
                {"release_id": RELEASE_ID},
            ).mappings()
        }
        node_bounds = dict(
            connection.execute(
                text(
                    """
                    select
                      min(binding_rows)::integer as minimum,
                      max(binding_rows)::integer as maximum
                    from (
                      select node_id, count(*)::integer as binding_rows
                      from product.knowledge_map_question_bindings
                      where release_id = cast(:release_id as uuid)
                      group by node_id
                    ) node_counts
                    """
                ),
                {"release_id": RELEASE_ID},
            )
            .mappings()
            .one()
        )
    fixture["moduleCounts"] = module_counts
    fixture["nodeBindingBounds"] = node_bounds
    return fixture


def _fixture_assertions(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _assertion(
            "binding_count_exact",
            int(fixture["binding_rows"]) == BINDING_COUNT,
            expected=BINDING_COUNT,
            actual=int(fixture["binding_rows"]),
        ),
        _assertion(
            "unique_question_count_exact",
            int(fixture["unique_questions"]) == BINDING_COUNT,
            expected=BINDING_COUNT,
            actual=int(fixture["unique_questions"]),
        ),
        _assertion(
            "node_count_exact",
            int(fixture["nodes"]) == NODE_COUNT,
            expected=NODE_COUNT,
            actual=int(fixture["nodes"]),
        ),
        _assertion(
            "module_count_exact",
            int(fixture["modules"]) == len(CANONICAL_MODULES),
            expected=len(CANONICAL_MODULES),
            actual=int(fixture["modules"]),
        ),
        _assertion(
            "all_bindings_delivery_eligible",
            int(fixture["delivery_eligible_rows"]) == BINDING_COUNT,
            expected=BINDING_COUNT,
            actual=int(fixture["delivery_eligible_rows"]),
        ),
    ]


def _select_low_cursor_user_id() -> str:
    from app.domains.xingce_learning.real_attempt.repository import (
        _stable_question_selection_cursor,
    )

    for suffix in range(1, 10_000):
        user_id = f"22222222-2222-4222-8222-{suffix:012d}"
        cursor = _stable_question_selection_cursor(
            release_id=RELEASE_ID,
            user_id=user_id,
        )
        if 32 <= cursor[0] <= 160:
            return user_id
    raise AssertionError("failed to derive a bounded synthetic selection cursor")


def _capture_repository_query(
    engine: Engine,
    *,
    scenario_name: str,
    node_id: str | None,
    canonical_modules: tuple[str, ...] | None,
    user_id: str,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    from app.domains.xingce_learning.real_attempt.repository import (
        XingceAttemptRepository,
    )

    captured: list[tuple[str, dict[str, Any]]] = []

    def capture(
        _connection: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "candidate_ids as materialized" not in statement:
            return
        if not isinstance(parameters, dict):
            raise AssertionError(
                f"{scenario_name}: expected mapping query parameters"
            )
        captured.append((statement, dict(parameters)))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as session:
            selected = XingceAttemptRepository()._select_eligible_questions(
                session,
                release_id=RELEASE_ID,
                node_id=node_id,
                canonical_modules=canonical_modules,
                limit=3,
                user_id=user_id,
                excluded_question_ids=None,
            )
            session.rollback()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    if len(captured) != 1:
        raise AssertionError(
            f"{scenario_name}: expected one candidate query, captured {len(captured)}"
        )
    if len(selected) != 3:
        raise AssertionError(
            f"{scenario_name}: repository selected {len(selected)} questions"
        )
    return captured[0][0], captured[0][1], selected


def _run_explain(
    engine: Engine,
    statement: str,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_connection = engine.raw_connection()
    try:
        with raw_connection.cursor() as cursor:
            cursor.execute("set statement_timeout = '5s'")
            cursor.execute("set track_io_timing = on")
            cursor.execute("set jit = off")
            cursor.execute(
                "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL, "
                "TIMING OFF, SUMMARY ON, FORMAT JSON) " + statement,
                parameters,
            )
            value = cursor.fetchone()[0]
        raw_connection.rollback()
    finally:
        raw_connection.close()
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise AssertionError("EXPLAIN FORMAT JSON returned an unexpected payload")
    return value


def _walk_plan(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _walk_plan(child)


def _assertion(
    name: str,
    passed: bool,
    *,
    expected: Any = None,
    actual: Any = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
    }


def _plan_assertions(
    *,
    scenario_name: str,
    statement: str,
    parameters: dict[str, Any],
    explain: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = explain[0]["Plan"]
    nodes = list(_walk_plan(root))
    normalized_sql = " ".join(statement.split()).lower()
    expected_index = EXPECTED_INDEXES[scenario_name]
    index_names = sorted(
        {
            str(node["Index Name"])
            for node in nodes
            if node.get("Index Name") is not None
        }
    )
    binding_seq_scans = [
        node
        for node in nodes
        if node.get("Node Type") == "Seq Scan"
        and node.get("Relation Name") == "knowledge_map_question_bindings"
    ]
    binding_scans = [
        node
        for node in nodes
        if node.get("Relation Name") == "knowledge_map_question_bindings"
    ]
    large_sorts = [
        node
        for node in nodes
        if node.get("Node Type") == "Sort"
        and int(node.get("Actual Rows", 0)) > 96
    ]
    disk_sorts = [
        node
        for node in nodes
        if node.get("Node Type") == "Sort"
        and (
            "external" in str(node.get("Sort Method", "")).lower()
            or int(node.get("Temp Read Blocks", 0)) > 0
            or int(node.get("Temp Written Blocks", 0)) > 0
        )
    ]
    limits = [
        int(node.get("Actual Rows", 0))
        for node in nodes
        if node.get("Node Type") == "Limit"
    ]
    task_session_relations = [
        node
        for node in nodes
        if node.get("Relation Name") == "xingce_task_sessions"
    ]
    max_binding_scan_rows = max(
        (
            int(node.get("Actual Rows", 0))
            * max(int(node.get("Actual Loops", 1)), 1)
            for node in binding_scans
        ),
        default=0,
    )
    assertions = [
        _assertion(
            "expected_function_index_used",
            expected_index in index_names,
            expected=expected_index,
            actual=index_names,
        ),
        _assertion(
            "no_binding_seq_scan",
            not binding_seq_scans,
            expected=0,
            actual=len(binding_seq_scans),
        ),
        _assertion(
            "candidate_limit_bounded",
            bool(limits) and min(limits) <= int(parameters["candidate_batch_size"]),
            expected=f"one Limit <= {parameters['candidate_batch_size']}",
            actual=limits,
        ),
        _assertion(
            "no_large_sort_before_candidate_limit",
            not large_sorts,
            expected="no Sort with more than 96 actual rows",
            actual=[
                {
                    "actualRows": node.get("Actual Rows"),
                    "sortKey": node.get("Sort Key"),
                }
                for node in large_sorts
            ],
        ),
        _assertion(
            "no_disk_sort",
            not disk_sorts,
            expected=0,
            actual=len(disk_sorts),
        ),
        _assertion(
            "no_task_session_plan_relation",
            not task_session_relations,
            expected=0,
            actual=len(task_session_relations),
        ),
        _assertion(
            "no_historical_json_expansion_sql",
            "jsonb_array_elements" not in normalized_sql
            and "xingce_task_sessions" not in normalized_sql
            and "safe_bundle" not in normalized_sql,
            expected="no JSON task-session history scan",
            actual={
                "jsonbArrayElements": "jsonb_array_elements" in normalized_sql,
                "taskSessions": "xingce_task_sessions" in normalized_sql,
                "safeBundle": "safe_bundle" in normalized_sql,
            },
        ),
        _assertion(
            "stable_keyset_sql",
            "digest(binding.question_id, 'sha256')" in normalized_sql
            and "random(" not in normalized_sql
            and "offset" not in normalized_sql,
            expected="digest keyset without random/offset",
            actual={
                "digest": "digest(binding.question_id, 'sha256')" in normalized_sql,
                "random": "random(" in normalized_sql,
                "offset": "offset" in normalized_sql,
            },
        ),
        _assertion(
            "ordinary_binding_scan_is_bounded",
            max_binding_scan_rows <= 96,
            expected="<= 96 actual binding rows per scan including loops",
            actual=max_binding_scan_rows,
        ),
    ]
    metrics = {
        "planningTimeMs": float(explain[0].get("Planning Time", 0.0)),
        "executionTimeMs": float(explain[0].get("Execution Time", 0.0)),
        "indexNames": index_names,
        "limitActualRows": limits,
        "maxBindingScanRowsIncludingLoops": max_binding_scan_rows,
        "sharedHitBlocks": int(root.get("Shared Hit Blocks", 0)),
        "sharedReadBlocks": int(root.get("Shared Read Blocks", 0)),
        "tempReadBlocks": int(root.get("Temp Read Blocks", 0)),
        "tempWrittenBlocks": int(root.get("Temp Written Blocks", 0)),
    }
    return assertions, metrics


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _database_metadata(
    engine: Engine,
    *,
    expected_socket_directory: Path,
) -> dict[str, Any]:
    with engine.connect() as connection:
        network = dict(
            connection.execute(
                text(
                    """
                    select
                      host(inet_server_addr()) as server_address,
                      inet_server_port() as server_port,
                      current_setting('listen_addresses') as listen_addresses,
                      current_setting('unix_socket_directories')
                        as unix_socket_directories,
                      version() as postgres_version
                    """
                )
            )
            .mappings()
            .one()
        )
        if (
            network["server_address"] is not None
            or network["server_port"] is not None
            or network["listen_addresses"] != ""
            or network["unix_socket_directories"]
            != str(expected_socket_directory)
        ):
            raise AssertionError(f"PostgreSQL isolation check failed: {network}")
        directory_mode = stat.S_IMODE(expected_socket_directory.stat().st_mode)
        parent_mode = stat.S_IMODE(expected_socket_directory.parent.stat().st_mode)
        socket_paths = sorted(
            path
            for path in expected_socket_directory.glob(".s.PGSQL.*")
            if not path.name.endswith(".lock")
        )
        socket_modes = {
            path.name: stat.S_IMODE(path.stat().st_mode)
            for path in socket_paths
        }
        if (
            directory_mode != 0o700
            or parent_mode != 0o700
            or not socket_modes
            or any(mode != 0o700 for mode in socket_modes.values())
        ):
            raise AssertionError(
                "PostgreSQL private socket permissions failed: "
                f"parent={oct(parent_mode)}, directory={oct(directory_mode)}, "
                f"sockets={socket_modes}"
            )
        hba_rules = [
            {
                "lineNumber": int(row["line_number"]),
                "type": str(row["type"]),
                "database": list(row["database"] or ()),
                "userName": list(row["user_name"] or ()),
                "address": (
                    str(row["address"]) if row["address"] is not None else None
                ),
                "authMethod": str(row["auth_method"]),
                "error": str(row["error"]) if row["error"] is not None else None,
            }
            for row in connection.execute(
                text(
                    """
                    select
                      line_number, type, database, user_name,
                      address, auth_method, error
                    from pg_hba_file_rules
                    order by line_number
                    """
                )
            ).mappings()
        ]
        host_rules = [row for row in hba_rules if row["type"].startswith("host")]
        local_rules = [row for row in hba_rules if row["type"] == "local"]
        if (
            not host_rules
            or any(row["authMethod"] != "reject" for row in host_rules)
            or not local_rules
            or any(row["authMethod"] != "trust" for row in local_rules)
            or any(row["error"] is not None for row in hba_rules)
        ):
            raise AssertionError(
                f"PostgreSQL pg_hba isolation check failed: {hba_rules}"
            )
        heads = tuple(
            str(value)
            for value in connection.execute(
                text("select version_num from public.alembic_version order by version_num")
            ).scalars()
        )
        indexes = {
            str(row["indexname"]): str(row["indexdef"])
            for row in connection.execute(
                text(
                    """
                    select indexname, indexdef
                    from pg_indexes
                    where schemaname in ('product', 'learning')
                      and indexname in (
                        'product_knowledge_map_binding_release_selection_idx',
                        'product_knowledge_map_binding_node_selection_idx',
                        'product_knowledge_map_binding_module_selection_idx_v2',
                        'learning_xingce_assessments_user_question_idx',
                        'learning_xingce_exposures_user_question_idx'
                      )
                    order by indexname
                    """
                )
            ).mappings()
        }
        sizes = {
            str(row["relation"]): {
                "tableBytes": int(row["table_bytes"]),
                "indexesBytes": int(row["indexes_bytes"]),
                "totalBytes": int(row["total_bytes"]),
            }
            for row in connection.execute(
                text(
                    """
                    select
                      relation::text,
                      pg_relation_size(relation) as table_bytes,
                      pg_indexes_size(relation) as indexes_bytes,
                      pg_total_relation_size(relation) as total_bytes
                    from unnest(
                      array[
                        'product.knowledge_map_question_bindings'::regclass,
                        'product.question_content_versions'::regclass,
                        'learning.xingce_assessments'::regclass,
                        'learning.xingce_question_exposures'::regclass
                      ]
                    ) relation
                    """
                )
            ).mappings()
        }
        settings = {
            str(row["name"]): str(row["setting"])
            for row in connection.execute(
                text(
                    """
                    select name, setting
                    from pg_settings
                    where name in (
                      'block_size', 'effective_cache_size',
                      'max_parallel_workers_per_gather', 'random_page_cost',
                      'seq_page_cost', 'shared_buffers', 'work_mem'
                    )
                    order by name
                    """
                )
            ).mappings()
        }
    return {
        "network": network,
        "socketIsolation": {
            "parentDirectoryMode": oct(parent_mode),
            "socketDirectoryMode": oct(directory_mode),
            "socketModes": {
                name: oct(mode) for name, mode in socket_modes.items()
            },
            "hostRulesReject": True,
            "localRulesTrust": True,
            "hbaRules": hba_rules,
        },
        "alembicHeads": heads,
        "indexes": indexes,
        "relationSizes": sizes,
        "settings": settings,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytea", "hex": value.hex()}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_metadata() -> dict[str, str]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "--binary", "--", "."],
        cwd=REPOSITORY_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    return {
        "commit": commit,
        "workingTreeDiffSha256": sha256(diff).hexdigest(),
    }


def _run(repetitions: int) -> tuple[dict[str, Any], dict[str, str]]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from tests.conftest import _reset_test_schema

    repository_path = (
        BACKEND_ROOT
        / "app/domains/xingce_learning/real_attempt/repository.py"
    )
    migration_path = (
        BACKEND_ROOT
        / "migrations/versions/20260730_77_xingce_selection_projection.py"
    )
    artifact: dict[str, Any] = {
        "schemaVersion": "xingce-selection-explain@1",
        "generatedAt": datetime.now(UTC).isoformat(),
        "productionContacted": False,
        "databaseOrigin": "ephemeral-local-postgres-private-unix-socket",
        "syntheticFixture": True,
        "git": _git_metadata(),
        "sourceFiles": {
            str(repository_path.relative_to(REPOSITORY_ROOT)): _file_sha256(
                repository_path
            ),
            str(migration_path.relative_to(REPOSITORY_ROOT)): _file_sha256(
                migration_path
            ),
            str(Path(__file__).resolve().relative_to(REPOSITORY_ROOT)): _file_sha256(
                Path(__file__).resolve()
            ),
        },
        "performanceThresholdStatus": "hypothesis_only",
        "performanceThresholdHypothesis": {
            "warmP95Ms": 50,
            "warmMaximumMs": 100,
            "releaseGate": False,
            "reason": (
                "Local synthetic hardware and cache behavior do not establish "
                "an approved production SLO."
            ),
        },
        "assumptions": [
            (
                "Production reports 99,409 binding rows, but production unique "
                "question cardinality and per-node/module distribution were not "
                "read during this isolated run."
            ),
            (
                "The fixture uses 99,409 unique questions as a conservative "
                "grouping-cardinality assumption."
            ),
            (
                "User assessment/exposure tables are empty; high-history skip "
                "behavior is covered by functional tests, not this plan run."
            ),
            (
                "VACUUM ANALYZE plans represent a steady-state warm local "
                "database, not production cold-cache latency."
            ),
        ],
        "scenarios": [],
    }
    captured_sql: dict[str, str] = {}

    with _ephemeral_unix_socket_postgres() as (
        database_url,
        _log_file,
        socket_directory,
    ):
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        try:
            _reset_test_schema(engine)
            fixture = _seed_fixture(engine)
            artifact["database"] = _database_metadata(
                engine,
                expected_socket_directory=socket_directory,
            )
            artifact["fixture"] = fixture
            fixture_assertions = _fixture_assertions(fixture)
            artifact["fixtureAssertions"] = fixture_assertions
            if not all(item["passed"] for item in fixture_assertions):
                raise AssertionError(f"fixture assertions failed: {fixture_assertions}")

            user_id = _select_low_cursor_user_id()
            scenario_inputs = (
                ("release_scope", None, None),
                (
                    "module_scope",
                    None,
                    (CANONICAL_MODULES[2][1],),
                ),
                (
                    "targeted_node_scope",
                    "perf-node-0003",
                    (CANONICAL_MODULES[2][1],),
                ),
            )
            for scenario_name, node_id, canonical_modules in scenario_inputs:
                statement, parameters, selected = _capture_repository_query(
                    engine,
                    scenario_name=scenario_name,
                    node_id=node_id,
                    canonical_modules=canonical_modules,
                    user_id=user_id,
                )
                captured_sql[scenario_name] = statement
                _run_explain(engine, statement, parameters)
                plans: list[list[dict[str, Any]]] = []
                repetitions_metrics: list[dict[str, Any]] = []
                repetitions_assertions: list[list[dict[str, Any]]] = []
                for _index in range(repetitions):
                    explain = _run_explain(engine, statement, parameters)
                    assertions, metrics = _plan_assertions(
                        scenario_name=scenario_name,
                        statement=statement,
                        parameters=parameters,
                        explain=explain,
                    )
                    plans.append(explain)
                    repetitions_metrics.append(metrics)
                    repetitions_assertions.append(assertions)
                execution_times = [
                    float(item["executionTimeMs"])
                    for item in repetitions_metrics
                ]
                scenario_assertions = repetitions_assertions[-1]
                structure_passed = all(
                    assertion["passed"]
                    for assertions in repetitions_assertions
                    for assertion in assertions
                )
                artifact["scenarios"].append(
                    {
                        "name": scenario_name,
                        "scope": {
                            "releaseId": RELEASE_ID,
                            "nodeId": node_id,
                            "canonicalModules": canonical_modules,
                        },
                        "selectedQuestionIds": [
                            str(question["question_id"]) for question in selected
                        ],
                        "querySha256": sha256(statement.encode()).hexdigest(),
                        "parameters": _json_safe(parameters),
                        "expectedIndex": EXPECTED_INDEXES[scenario_name],
                        "warmupRuns": 1,
                        "measuredRuns": repetitions,
                        "structurePassed": structure_passed,
                        "assertions": scenario_assertions,
                        "repetitionAssertions": repetitions_assertions,
                        "metrics": repetitions_metrics,
                        "executionTimeSummaryMs": {
                            "minimum": min(execution_times),
                            "mean": statistics.fmean(execution_times),
                            "median": statistics.median(execution_times),
                            "p95": _percentile(execution_times, 0.95),
                            "maximum": max(execution_times),
                        },
                        "rawExplainPlans": plans,
                    }
                )
            artifact["overallPassed"] = all(
                item["passed"] for item in artifact["fixtureAssertions"]
            ) and all(
                bool(scenario["structurePassed"])
                for scenario in artifact["scenarios"]
            )
        finally:
            engine.dispose()
    return artifact, captured_sql


def _summary_markdown(artifact: dict[str, Any]) -> str:
    lines = [
        "# Xingce selection 99,409-binding query-plan evidence",
        "",
        f"- Generated: `{artifact['generatedAt']}`",
        f"- Production contacted: `{str(artifact['productionContacted']).lower()}`",
        f"- Database: `{artifact['databaseOrigin']}`",
        f"- Overall structural result: `{'PASS' if artifact.get('overallPassed') else 'FAIL'}`",
        "- Absolute latency thresholds: `hypothesis only; not a standalone release gate`",
        "",
    ]
    if artifact.get("error"):
        lines.extend(
            [
                "## Execution error",
                "",
                f"```text\n{artifact['error']['message']}\n```",
                "",
            ]
        )
        return "\n".join(lines)

    fixture = artifact["fixture"]
    lines.extend(
        [
            "## Fixture",
            "",
            "| Bindings | Unique questions | Nodes | Modules | Eligible |",
            "| ---: | ---: | ---: | ---: | ---: |",
            (
                f"| {fixture['binding_rows']:,} | "
                f"{fixture['unique_questions']:,} | "
                f"{fixture['nodes']:,} | {fixture['modules']:,} | "
                f"{fixture['delivery_eligible_rows']:,} |"
            ),
            "",
            "## Plans",
            "",
            "| Scenario | Expected index | Structural | Median ms | p95 ms | Max ms |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for scenario in artifact["scenarios"]:
        timing = scenario["executionTimeSummaryMs"]
        lines.append(
            f"| `{scenario['name']}` | `{scenario['expectedIndex']}` | "
            f"{'PASS' if scenario['structurePassed'] else 'FAIL'} | "
            f"{timing['median']:.3f} | {timing['p95']:.3f} | "
            f"{timing['maximum']:.3f} |"
        )
    lines.extend(["", "## Structural assertions", ""])
    for scenario in artifact["scenarios"]:
        lines.append(f"### {scenario['name']}")
        lines.append("")
        for assertion in scenario["assertions"]:
            lines.append(
                f"- `{'PASS' if assertion['passed'] else 'FAIL'}` "
                f"{assertion['name']}: actual `{json.dumps(assertion['actual'], ensure_ascii=False)}`"
            )
        lines.append("")
    lines.extend(
        [
            "## Assumptions",
            "",
            *[f"- {assumption}" for assumption in artifact["assumptions"]],
            "",
        ]
    )
    return "\n".join(lines)


def _sql_evidence(captured_sql: dict[str, str]) -> str:
    sections = []
    for scenario_name, statement in captured_sql.items():
        sections.extend(
            [
                f"-- scenario: {scenario_name}",
                f"-- sha256: {sha256(statement.encode()).hexdigest()}",
                statement.strip() + ";",
                "",
            ]
        )
    return "\n".join(sections)


def _write_artifacts(
    output_directory: Path,
    artifact: dict[str, Any],
    captured_sql: dict[str, str],
) -> tuple[Path, Path, Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"xingce-selection-explain-{run_id}"
    json_path = output_directory / f"{base_name}.json"
    sql_path = output_directory / f"{base_name}.sql"
    summary_path = output_directory / f"{base_name}-summary.md"
    checksum_path = output_directory / f"{base_name}.sha256"
    json_path.write_text(
        json.dumps(_json_safe(artifact), ensure_ascii=False, indent=2) + "\n"
    )
    sql_path.write_text(_sql_evidence(captured_sql))
    summary_path.write_text(_summary_markdown(artifact))
    checksum_lines = [
        f"{_file_sha256(path)}  {path.name}"
        for path in (json_path, sql_path, summary_path)
    ]
    checksum_path.write_text("\n".join(checksum_lines) + "\n")
    return json_path, sql_path, summary_path, checksum_path


def main() -> None:
    args = arguments()
    captured_sql: dict[str, str] = {}
    artifact: dict[str, Any]
    try:
        artifact, captured_sql = _run(args.repetitions)
    except Exception as exc:
        artifact = {
            "schemaVersion": "xingce-selection-explain@1",
            "generatedAt": datetime.now(UTC).isoformat(),
            "productionContacted": False,
            "databaseOrigin": "ephemeral-local-postgres-private-unix-socket",
            "syntheticFixture": True,
            "overallPassed": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        }
    paths = _write_artifacts(args.output_directory, artifact, captured_sql)
    print(
        json.dumps(
            {
                "overallPassed": bool(artifact.get("overallPassed")),
                "artifacts": [str(path) for path in paths],
                "error": artifact.get("error"),
            },
            ensure_ascii=False,
        )
    )
    if not artifact.get("overallPassed"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
