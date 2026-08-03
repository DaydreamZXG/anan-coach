from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
SCRIPT_ROOT = REPOSITORY_ROOT / "ops" / "scripts"


def _load_script(name: str, path: Path):
    sys.path.insert(0, str(SCRIPT_ROOT))
    try:
        specification = importlib.util.spec_from_file_location(name, path)
        assert specification is not None
        assert specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT_ROOT))


authority = _load_script(
    "bootstrap_data_release_authority",
    SCRIPT_ROOT / "data_release_authority.py",
)
publication = _load_script(
    "bootstrap_commercial_data_publication",
    SCRIPT_ROOT / "commercial_data_publication.py",
)

CANDIDATE_COMMIT = "1" * 40
ROLLBACK_COMMIT = "7" * 40


def _seed_bootstrap_authority(engine: Engine) -> None:
    now = datetime.now(UTC)
    position_id = str(uuid4())
    position_release_id = str(uuid4())
    knowledge_release_id = str(uuid4())
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
                  cast(:position_id as uuid), 'bootstrap-position', 2026,
                  '3001102026999', '一级行政执法员', '首发桥接测试局',
                  'tax', '江西', '南昌市', 'administrativeLaw', 1,
                  '本科', '不限', '不限', 0, '不限', '无',
                  '{"positionDataVersion":"positions@bootstrap-v1"}'::jsonb
                )
                """
            ),
            {"position_id": position_id},
        )
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  cast(:release_id as uuid), 'guokao', 2026,
                  'rules@bootstrap-v1', 'positions@bootstrap-v1',
                  'active', :activated_at
                )
                """
            ),
            {
                "release_id": position_release_id,
                "activated_at": now - timedelta(hours=2),
            },
        )
        session.execute(
            text(
                """
                insert into product.position_rule_compilations (
                  compilation_id, position_id, rule_set_version,
                  position_data_version, compiler_version,
                  source_fingerprint, status, requirements_count,
                  manual_review_count, warnings, compiled_at
                ) values (
                  cast(:compilation_id as uuid), cast(:position_id as uuid),
                  'rules@bootstrap-v1', 'positions@bootstrap-v1',
                  'bootstrap-fixture@v1', repeat('c', 64), 'no_conditions',
                  0, 0, '[]'::jsonb, :compiled_at
                )
                """
            ),
            {
                "compilation_id": str(uuid4()),
                "position_id": position_id,
                "compiled_at": now - timedelta(hours=3),
            },
        )

        seed_released_module_vocabulary(
            session,
            version="vocab@bootstrap-v1",
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
                  cast(:release_id as uuid), 'knowledge@bootstrap-v1',
                  'guokao', 'xingce', 'questions@bootstrap-v1',
                  'vocab@bootstrap-v1', 'generator@bootstrap-v1',
                  repeat('d', 64), :generated_at, :fresh_until, 'draft', 0
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
                  cast(:release_id as uuid), 'bootstrap-node',
                  'guokao', 'xingce', '资料分析', 'knowledge_point',
                  'knowledge_point', '增长率', '行测/资料分析/增长率',
                  'text_supported', 'published'
                )
                """
            ),
            {"release_id": knowledge_release_id},
        )
        seed_minimal_release_binding(
            session,
            release_id=knowledge_release_id,
            node_id="bootstrap-node",
            question_id="bootstrap-question",
            legacy_visual_schema=True,
        )
        activate_knowledge_release(
            session,
            release_id=knowledge_release_id,
            activated_at=now - timedelta(hours=1),
        )


def _libpq_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg:",
        "postgresql:",
        1,
    )


def _sign_receipt(receipt: Path, directory: Path) -> tuple[Path, Path]:
    private_key = directory / "bootstrap-private.pem"
    public_key = directory / "bootstrap-public.pem"
    signature = directory / "bootstrap-receipt.sig"
    subprocess.run(
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
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-out",
            str(public_key),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(signature),
            str(receipt),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(public_key),
            "-signature",
            str(signature),
            str(receipt),
        ],
        check=True,
        capture_output=True,
    )
    return public_key, signature


def test_revision_76_external_transaction_bootstrap_is_atomic_and_resumable(
    db_engine_at_revision_76: Engine,
    tmp_path: Path,
) -> None:
    db_engine = db_engine_at_revision_76
    _seed_bootstrap_authority(db_engine)
    database_url = _libpq_url(db_engine)
    receipt_payload = authority.capture_bootstrap_authority(
        database_url=database_url,
        environment="preproduction",
        expected_revision="20260729_76",
        application_commit=ROLLBACK_COMMIT,
        position_exam_code="guokao",
        position_exam_year=2026,
        knowledge_exam_scope="guokao",
        knowledge_track="xingce",
    )
    receipt = tmp_path / "bootstrap-receipt.json"
    authority._write_new_json(receipt, receipt_payload)
    _sign_receipt(receipt, tmp_path)
    receipt_sha256 = publication.sha256(receipt.read_bytes()).hexdigest()

    request = publication.build_request(
        candidate_commit=CANDIDATE_COMMIT,
        rollback_commit=ROLLBACK_COMMIT,
        environment="preproduction",
        expected_revision="20260802_94",
        position_exam_code="guokao",
        position_exam_year=2026,
        position_rule_set_version="rules@candidate-v2",
        position_data_version="positions@candidate-v2",
        position_window_manifest_sha256="e" * 64,
        knowledge_exam_scope="guokao",
        knowledge_track="xingce",
        knowledge_release_version="knowledge@candidate-v2",
        knowledge_source_data_version="questions@candidate-v2",
        knowledge_fresh_until=(datetime.now(UTC) + timedelta(days=60)).isoformat(),
    )
    request_path = tmp_path / "publication-request.json"
    publication._write_json(request_path, request)
    ready = tmp_path / "after-upgrade.ready"
    subprocess_source = """
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import commercial_data_publication as publication

def stop_after_upgrade():
    Path(sys.argv[7]).write_text("ready", encoding="utf-8")
    while True:
        time.sleep(1)

publication.bootstrap_upgrade_and_begin(
    database_url=sys.argv[2],
    request=publication.load_request(Path(sys.argv[3])),
    bootstrap_receipt_path=Path(sys.argv[4]),
    bootstrap_receipt_sha256=sys.argv[5],
    backend_root=Path(sys.argv[6]),
    after_upgrade_hook=stop_after_upgrade,
)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            subprocess_source,
            str(SCRIPT_ROOT),
            database_url,
            str(request_path),
            str(receipt),
            receipt_sha256,
            str(BACKEND_ROOT),
            str(ready),
        ],
        env={name: value for name, value in os.environ.items() if not name.startswith("PG")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not ready.exists() and process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
            raise AssertionError(f"bootstrap bridge did not reach hook: {stdout} {stderr}")
        time.sleep(0.05)
    if process.poll() is not None:
        stdout, stderr = process.communicate(timeout=10)
        raise AssertionError(f"bootstrap bridge exited before hook: {stdout} {stderr}")
    process.kill()
    process.wait(timeout=10)

    with db_engine.connect() as connection:
        assert (
            connection.execute(text("select version_num from public.alembic_version")).scalar_one()
            == "20260729_76"
        )
        assert (
            connection.execute(
                text("select to_regclass('platform.commercial_data_publication_state')")
            ).scalar_one()
            is None
        )

    result = publication.bootstrap_upgrade_and_begin(
        database_url=database_url,
        request=request,
        bootstrap_receipt_path=receipt,
        bootstrap_receipt_sha256=receipt_sha256,
        backend_root=BACKEND_ROOT,
    )
    assert result["action"] == "bootstrap_started"
    assert result["state"] == "publishing"
    assert result["previousReceiptSha256"] == receipt_sha256
    with db_engine.connect() as connection:
        assert (
            connection.execute(text("select version_num from public.alembic_version")).scalar_one()
            == "20260802_94"
        )
        assert (
            connection.execute(
                text("select count(*) from platform.commercial_data_publication_state")
            ).scalar_one()
            == 1
        )
