from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.domains.qualification.rule_releases import (
    PositionReleaseWindowInput,
    PositionRuleReleaseService,
)
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPO_ROOT / "ops" / "scripts"
SCRIPT_PATH = SCRIPT_ROOT / "data_release_authority.py"
sys.path.insert(0, str(SCRIPT_ROOT))
try:
    SPEC = importlib.util.spec_from_file_location(
        "data_release_authority",
        SCRIPT_PATH,
    )
    assert SPEC is not None and SPEC.loader is not None
    data_release_authority = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(data_release_authority)
finally:
    sys.path.remove(str(SCRIPT_ROOT))


CANDIDATE_COMMIT = "1" * 40
ROLLBACK_COMMIT = "7" * 40
DATABASE_URL = "postgresql://owner@127.0.0.1:5432/gongkao"


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(
        self,
        *,
        current_windows: list[dict[str, Any]] | None = None,
        source_fingerprint: str = "b" * 64,
    ) -> None:
        self.executed: list[str] = []
        self.rollback_count = 0
        self.current_windows = current_windows or []
        self.source_fingerprint = source_fingerprint

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def rollback(self) -> None:
        self.rollback_count += 1

    def execute(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> _Result:
        normalized = " ".join(query.split())
        self.executed.append(normalized)
        if normalized.startswith("begin transaction isolation level"):
            return _Result([])
        if "pg_control_system()" in normalized:
            return _Result(
                [
                    {
                        "database_name": "gongkao",
                        "system_identifier": "7440000000000000000",
                        "observed_at": datetime(
                            2026,
                            7,
                            30,
                            0,
                            0,
                            tzinfo=UTC,
                        ),
                    }
                ]
            )
        if "public.alembic_version" in normalized:
            return _Result([{"version_num": "20260730_79"}])
        if "from product.position_rule_releases" in normalized:
            assert parameters == {"exam_code": "guokao", "exam_year": 2026}
            return _Result(
                [
                    {
                        "release_id": ("00000000-0000-4000-8000-000000000001"),
                        "exam_code": "guokao",
                        "exam_year": 2026,
                        "rule_set_version": "rules@test",
                        "position_data_version": "positions@test",
                        "release_content_sha256": "a" * 64,
                        "recomputed_content_sha256": "a" * 64,
                        "source_snapshot_sha256": "9" * 64,
                    }
                ]
            )
        if "from product.position_release_windows" in normalized:
            return _Result(self.current_windows)
        if "from product.knowledge_map_releases" in normalized:
            return _Result(
                [
                    {
                        "release_id": ("00000000-0000-4000-8000-000000000002"),
                        "release_version": "knowledge@test",
                        "source_data_version": "questions@test",
                        "source_fingerprint": self.source_fingerprint,
                        "fresh_until": datetime(
                            2026,
                            8,
                            30,
                            0,
                            0,
                            tzinfo=UTC,
                        ),
                        "node_count": 6,
                        "question_binding_count": 120,
                        "question_binding_fingerprint": "d" * 64,
                        "actual_node_count": 6,
                        "invalid_node_count": 0,
                        "actual_binding_count": 120,
                        "actual_binding_fingerprint": "d" * 64,
                        "release_content_sha256": "c" * 64,
                    }
                ]
            )
        raise AssertionError(f"unexpected SQL: {normalized}")


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    connection: _Connection,
) -> dict[str, Any]:
    for name in tuple(data_release_authority.os.environ):
        if name.startswith("PG"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        data_release_authority.psycopg,
        "connect",
        lambda *_args, **_kwargs: connection,
    )
    return data_release_authority.capture_authority(
        database_url=DATABASE_URL,
        environment="preproduction",
        expected_revision="20260730_79",
        compatible_commits=(CANDIDATE_COMMIT, ROLLBACK_COMMIT),
        position_exam_code="guokao",
        position_exam_year=2026,
        knowledge_exam_scope="guokao",
        knowledge_track="xingce",
    )


def _seed_real_authority(engine: Engine) -> None:
    now = datetime.now(UTC)
    position_id = str(uuid4())
    knowledge_release_id = str(uuid4())
    knowledge_node_id = "data-authority-node"
    service = PositionRuleReleaseService()
    with Session(engine) as session, session.begin():
        session.execute(
            text(
                """
                insert into product.positions (
                  position_id, source_id, year, position_code, name,
                  department_full, system_code, region, city, paper_type,
                  headcount, education_min, major_required, political_req,
                  service_min_yrs, hukou_req, remark, raw_json
                ) values (
                  cast(:position_id as uuid), 'authority-position', 2026,
                  '3001102026001', '一级行政执法员', '数据权威测试局',
                  'tax', '江西', '南昌市', 'administrativeLaw', 1,
                  '本科', '不限', '不限', 0, '不限', '无',
                  '{"positionDataVersion":"positions@authority-v1"}'::jsonb
                )
                """
            ),
            {"position_id": position_id},
        )
        release_id = service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="rules@authority-v1",
            position_data_version="positions@authority-v1",
        )
        service.configure_windows(
            session,
            release_id=release_id,
            windows=[
                PositionReleaseWindowInput(
                    kind="primary",
                    sequence_no=1,
                    opens_at=now - timedelta(days=1),
                    closes_at=now + timedelta(days=30),
                    official_source_url=("https://example.gov.cn/authority-window"),
                    official_source_title="数据权威测试公告",
                    official_source_published_at=now - timedelta(days=3),
                    official_source_received_at=now - timedelta(days=2),
                    official_source_sha256="8" * 64,
                    official_source_receipt="authority-window-receipt",
                )
            ],
        )
        service.compile_draft(
            session,
            release_id=release_id,
            compiled_at=now - timedelta(hours=3),
        )
        session.execute(
            text(
                """
                insert into product.position_import_batches (
                  position_data_version, source_sha256, position_count
                ) values ('positions@authority-v1', repeat('9', 64), 1)
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.position_snapshots (
                  position_id, position_data_version, source_id, year,
                  position_code, name, department_full, system_code, region,
                  city, paper_type, headcount, education_min, major_required,
                  political_req, hukou_req, service_min_yrs, has_extra_exam,
                  remark, raw_json, source_fingerprint
                )
                select
                  position_id, 'positions@authority-v1', source_id, year,
                  position_code, name, department_full, system_code, region,
                  city, paper_type, headcount, education_min, major_required,
                  political_req, hukou_req, service_min_yrs, has_extra_exam,
                  remark, raw_json, repeat('a', 64)
                from product.positions
                where position_id = cast(:position_id as uuid)
                """
            ),
            {"position_id": position_id},
        )
        service.activate(
            session,
            release_id=release_id,
            activated_at=now - timedelta(hours=1),
        )

        seed_released_module_vocabulary(
            session,
            version="vocab@authority-v1",
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id, release_version, exam_scope, track,
                  source_data_version, vocab_version, generator_version,
                  source_fingerprint, generated_at, fresh_until, status,
                  node_count
                ) values (
                  cast(:release_id as uuid), 'knowledge@authority-v1',
                  'guokao', 'xingce', 'questions@authority-v1',
                  'vocab@authority-v1', 'generator@authority-v1',
                  repeat('b', 64), :generated_at, :fresh_until, 'draft', 0
                )
                """
            ),
            {
                "release_id": knowledge_release_id,
                "generated_at": now - timedelta(hours=2),
                "fresh_until": now + timedelta(days=30),
            },
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id, node_id, exam_scope, track, module_name,
                  node_type, tag_type, tag_value, display_path,
                  support_status, status
                ) values (
                  cast(:release_id as uuid), :node_id, 'guokao', 'xingce',
                  '资料分析', 'knowledge_point', 'knowledge_point', '增长率',
                  '行测/资料分析/增长率', 'text_supported', 'published'
                )
                """
            ),
            {
                "release_id": knowledge_release_id,
                "node_id": knowledge_node_id,
            },
        )
        seed_minimal_release_binding(
            session,
            release_id=knowledge_release_id,
            node_id=knowledge_node_id,
            question_id="data-authority-question",
        )
        activate_knowledge_release(
            session,
            release_id=knowledge_release_id,
            activated_at=now - timedelta(minutes=30),
        )


def test_capture_proves_a_closed_cycle_in_one_read_only_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()

    authority = _capture(monkeypatch, connection)

    assert authority["position"]["lifecycleState"] == "closed"
    assert authority["position"]["currentBusinessWindow"] is None
    assert authority["database"]["databaseName"] == "gongkao"
    assert connection.rollback_count == 1
    assert connection.executed[0] == ("begin transaction isolation level repeatable read read only")
    assert any("public.alembic_version" in sql for sql in connection.executed)
    assert any("knowledge_map_question_binding_manifest" in sql for sql in connection.executed)


def test_real_postgres_capture_and_verify_cli(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    _seed_real_authority(db_engine)
    database_url = db_engine.url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg:", "postgresql:", 1
    )
    with db_engine.connect() as connection:
        revision = str(
            connection.execute(text("select version_num from public.alembic_version")).scalar_one()
        )
    environment = {name: value for name, value in os.environ.items() if not name.startswith("PG")}
    environment["MIGRATION_DATABASE_URL"] = database_url
    receipt = tmp_path / "real-authority.json"

    capture = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--environment",
            "preproduction",
            "--expected-revision",
            revision,
            "--compatible-commit",
            CANDIDATE_COMMIT,
            "--compatible-commit",
            ROLLBACK_COMMIT,
            "capture",
            "--output",
            str(receipt),
            "--position-exam-year",
            "2026",
        ],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert capture.returncode == 0, capture.stderr
    assert receipt.is_file()

    verify = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--environment",
            "preproduction",
            "--expected-revision",
            revision,
            "--compatible-commit",
            CANDIDATE_COMMIT,
            "--compatible-commit",
            ROLLBACK_COMMIT,
            "verify",
            "--receipt",
            str(receipt),
        ],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    payload = json.loads(verify.stdout)
    assert payload["database"]["alembicRevision"] == revision


def test_verify_rejects_live_content_changed_after_signed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _capture(monkeypatch, _Connection())
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(authority), encoding="utf-8")
    changed = _Connection(source_fingerprint="e" * 64)
    monkeypatch.setattr(
        data_release_authority.psycopg,
        "connect",
        lambda *_args, **_kwargs: changed,
    )

    with pytest.raises(
        data_release_authority.DataReleaseAuthorityError,
        match="active knowledge authority does not match",
    ):
        data_release_authority.verify_authority(
            database_url=DATABASE_URL,
            receipt_path=receipt,
            environment="preproduction",
            expected_revision="20260730_79",
            expected_compatible_commits=(
                CANDIDATE_COMMIT,
                ROLLBACK_COMMIT,
            ),
        )


def test_database_authority_rejects_search_path_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(data_release_authority.os.environ):
        if name.startswith("PG"):
            monkeypatch.delenv(name, raising=False)
    with pytest.raises(
        data_release_authority.DataReleaseAuthorityError,
        match="out-of-band authority parameter: options",
    ):
        data_release_authority._validate_database_url(
            DATABASE_URL + "?options=-csearch_path%3Devil"
        )


def test_validate_connection_cli_rejects_unsafe_url_without_connecting() -> None:
    environment = {name: value for name, value in os.environ.items() if not name.startswith("PG")}
    environment["MIGRATION_DATABASE_URL"] = DATABASE_URL + "?options=-csearch_path%3Devil"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--environment",
            "preproduction",
            "--expected-revision",
            "20260730_79",
            "--compatible-commit",
            CANDIDATE_COMMIT,
            "--compatible-commit",
            ROLLBACK_COMMIT,
            "validate-connection",
        ],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "out-of-band authority parameter: options" in result.stderr
