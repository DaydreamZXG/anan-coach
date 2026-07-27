from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.method_cards.repository import MethodCardRecord, MethodCardRepository
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
VOCAB_LOADER = REPO_ROOT / "scripts" / "sql" / "vocabulary" / "load_vocabulary_seed.sql"


def test_vocab_loader_declares_complete_staging_contract() -> None:
    sql = VOCAB_LOADER.read_text(encoding="utf-8")

    required_contracts = (
        "exact six canonical module code-name pairs",
        "maps to a missing canonical code",
        "facet vocabulary staging must not be empty",
        "facet vocabulary staging contains an unexpected version",
    )
    for contract in required_contracts:
        assert contract in sql
    assert sql.index(required_contracts[0]) < sql.index(
        "insert into product.vocab_module_alias_map"
    )
    assert sql.index(required_contracts[2]) < sql.index(
        "insert into product.vocab_facet_rules"
    )


def test_vocab_loader_accepts_only_semantically_identical_replay(
    db_engine: Engine,
) -> None:
    first = _run_vocab_loader(db_engine)
    replay = _run_vocab_loader(db_engine)

    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert "replay is semantically identical" in replay.stderr
    with db_engine.connect() as connection:
        module_count = connection.execute(
            text(
                "select count(*) from product.vocab_module_alias_map "
                "where version = 'vocab@v1'"
            )
        ).scalar_one()
        facet_count = connection.execute(
            text(
                "select count(*) from product.vocab_facet_rules "
                "where version = 'vocab@v1'"
            )
        ).scalar_one()
    assert module_count > 6
    assert facet_count > 0


def test_vocab_loader_rejects_same_version_content_drift(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into product.vocab_module_alias_map (
                  version, raw_module_name, action, canonical_code,
                  local_question_count, basis
                ) values (
                  'vocab@v1', '政治理论', 'canonical', 'political_theory',
                  999999, 'drifted fixture'
                )
                """
            )
        )

    result = _run_vocab_loader(db_engine)

    assert result.returncode != 0
    assert "vocab_module_alias_map same-version content drift" in result.stderr
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                select local_question_count, basis
                from product.vocab_module_alias_map
                where version = 'vocab@v1'
                  and raw_module_name = '政治理论'
                """
            )
        ).one()
    assert row.local_question_count == 999999
    assert row.basis == "drifted fixture"


def test_vocab_loader_rejects_malformed_staging_before_target_writes(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    module_source = REPO_ROOT / "scripts/sql/vocabulary/module_alias_map.csv"
    facet_source = REPO_ROOT / "scripts/sql/vocabulary/facet_rules.csv"
    malformed_module = tmp_path / "module_alias_map.csv"
    copied_facet = tmp_path / "facet_rules.csv"
    loader = tmp_path / "load_vocabulary_seed.sql"

    module_lines = module_source.read_text(encoding="utf-8").splitlines()
    malformed_module.write_text(
        "\n".join(
            line
            for line in module_lines
            if not line.startswith("政治理论,canonical,political_theory,")
        )
        + "\n",
        encoding="utf-8",
    )
    copied_facet.write_text(
        facet_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    loader.write_text(
        VOCAB_LOADER.read_text(encoding="utf-8")
        .replace(
            "scripts/sql/vocabulary/module_alias_map.csv",
            str(malformed_module),
        )
        .replace(
            "scripts/sql/vocabulary/facet_rules.csv",
            str(copied_facet),
        ),
        encoding="utf-8",
    )

    with db_engine.connect() as connection:
        before = (
            connection.execute(
                text(
                    "select count(*) from product.vocab_module_alias_map "
                    "where version = 'vocab@v1'"
                )
            ).scalar_one(),
            connection.execute(
                text(
                    "select count(*) from product.vocab_facet_rules "
                    "where version = 'vocab@v1'"
                )
            ).scalar_one(),
        )

    result = _run_vocab_loader(db_engine, loader=loader)

    assert result.returncode != 0
    assert "exact six canonical module code-name pairs" in result.stderr
    with db_engine.connect() as connection:
        after = (
            connection.execute(
                text(
                    "select count(*) from product.vocab_module_alias_map "
                    "where version = 'vocab@v1'"
                )
            ).scalar_one(),
            connection.execute(
                text(
                    "select count(*) from product.vocab_facet_rules "
                    "where version = 'vocab@v1'"
                )
            ).scalar_one(),
        )
    assert after == before


def test_release_rows_must_enter_the_lifecycle_as_draft(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    with pytest.raises(IntegrityError, match="must be created as draft"):
        db_session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id, release_version, exam_scope, track,
                  source_data_version, vocab_version, generator_version,
                  source_fingerprint, generated_at, fresh_until, status,
                  node_count, activated_at
                ) values (
                  cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
                  'question-bank@invalid-insert', 'vocab@invalid-insert',
                  'generator@invalid-insert', repeat('a', 64),
                  :generated_at, :fresh_until, 'active', 1, :activated_at
                )
                """
            ),
            {
                "release_id": str(uuid4()),
                "release_version": f"knowledge-map@invalid-insert-{uuid4()}",
                "generated_at": now - timedelta(hours=1),
                "fresh_until": now + timedelta(days=1),
                "activated_at": now,
            },
        )


def test_release_activation_freezes_identity_nodes_and_vocabulary(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    release_id = str(uuid4())
    vocab_version = f"vocab@immutability-{release_id}"
    seed_released_module_vocabulary(
        db_session,
        version=vocab_version,
        aliases={"资科分析": "data_analysis"},
    )
    db_session.execute(
        text(
            """
            insert into product.vocab_facet_rules (
              version, term, facet, tag_levels_observed,
              local_occurrences, needs_review, basis
            ) values (
              :version, '增长率', 'skill', 'knowledge_point',
              1, false, 'immutability fixture'
            )
            """
        ),
        {"version": vocab_version},
    )
    db_session.execute(
        text(
            """
            insert into product.knowledge_map_releases (
              release_id, release_version, exam_scope, track,
              source_data_version, vocab_version, generator_version,
              source_fingerprint, generated_at, fresh_until, status,
              node_count, activated_at
            ) values (
              cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
              'question-bank@immutability', :version, 'generator@immutability',
              :fingerprint, :generated_at, :fresh_until, 'draft', 0, null
            )
            """
        ),
        {
            "release_id": release_id,
            "release_version": f"knowledge-map@immutability-{release_id}",
            "version": vocab_version,
            "fingerprint": release_id.replace("-", "") * 2,
            "generated_at": now - timedelta(hours=1),
            "fresh_until": now + timedelta(days=30),
        },
    )
    db_session.execute(
        text(
            """
            insert into product.knowledge_map_nodes (
              release_id, node_id, exam_scope, track, module_name,
              node_type, tag_type, tag_value, display_path,
              support_status, method_card_ids, status
            ) values (
              cast(:release_id as uuid), 'km_immutable', 'guokao', 'xingce',
              '资料分析', 'knowledge_point', 'knowledge_point', '增长率',
              '行测/资料分析/增长率', 'text_supported', '[]'::jsonb, 'draft'
            )
            """
        ),
        {"release_id": release_id},
    )

    with pytest.raises(
        IntegrityError,
        match=r"activation.*(invalid|outside)",
    ):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.knowledge_map_releases
                    set status = 'active', node_count = 1,
                        activated_at = :invalid_activated_at
                    where release_id = cast(:release_id as uuid)
                    """
                ),
                {
                    "release_id": release_id,
                    "invalid_activated_at": now - timedelta(hours=2),
                },
            )

    with pytest.raises(IntegrityError, match="node count does not match"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.knowledge_map_releases
                    set status = 'active', node_count = 2, activated_at = :now
                    where release_id = cast(:release_id as uuid)
                    """
                ),
                {"release_id": release_id, "now": now},
            )

    with pytest.raises(IntegrityError, match="unpublished or out-of-scope"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.knowledge_map_releases
                    set status = 'active', node_count = 1, activated_at = :now
                    where release_id = cast(:release_id as uuid)
                    """
                ),
                {"release_id": release_id, "now": now},
            )

    db_session.execute(
        text(
            """
            update product.knowledge_map_nodes
            set status = 'published'
            where release_id = cast(:release_id as uuid)
              and node_id = 'km_immutable'
            """
        ),
        {"release_id": release_id},
    )
    seed_minimal_release_binding(
        db_session,
        release_id=release_id,
        node_id="km_immutable",
        question_id=f"q_immutable_{release_id}",
    )
    activate_knowledge_release(
        db_session,
        release_id=release_id,
        activated_at=now,
    )

    blocked_mutations = (
        (
            """
            insert into product.knowledge_map_nodes (
              release_id, node_id, exam_scope, track, module_name,
              node_type, tag_type, tag_value, display_path,
              support_status, method_card_ids, status
            ) values (
              cast(:release_id as uuid), 'km_late_insert', 'guokao', 'xingce',
              '资料分析', 'knowledge_point', 'knowledge_point', '比重',
              '行测/资料分析/比重', 'text_supported', '[]'::jsonb, 'published'
            )
            """,
            {"release_id": release_id},
        ),
        (
            """
            update product.knowledge_map_nodes
            set display_path = '漂移路径'
            where release_id = cast(:release_id as uuid)
              and node_id = 'km_immutable'
            """,
            {"release_id": release_id},
        ),
        (
            """
            delete from product.knowledge_map_nodes
            where release_id = cast(:release_id as uuid)
              and node_id = 'km_immutable'
            """,
            {"release_id": release_id},
        ),
        (
            """
            insert into product.vocab_module_alias_map (
              version, raw_module_name, action, canonical_code,
              local_question_count, basis
            ) values (
              :version, '资料计算', 'map', 'data_analysis', 1, 'late alias'
            )
            """,
            {"version": vocab_version},
        ),
        (
            """
            update product.vocab_facet_rules
            set basis = 'drifted'
            where version = :version and term = '增长率'
            """,
            {"version": vocab_version},
        ),
        (
            """
            update product.knowledge_map_releases
            set source_data_version = 'question-bank@drifted'
            where release_id = cast(:release_id as uuid)
            """,
            {"release_id": release_id},
        ),
    )
    for statement, params in blocked_mutations:
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.execute(text(statement), params)

    db_session.execute(
        text(
            """
            update product.knowledge_map_releases
            set status = 'retired', retired_at = :retired_at
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": release_id, "retired_at": now + timedelta(minutes=1)},
    )
    with pytest.raises(IntegrityError, match="retired knowledge-map releases"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.knowledge_map_releases
                    set fresh_until = fresh_until + interval '1 day'
                    where release_id = cast(:release_id as uuid)
                    """
                ),
                {"release_id": release_id},
            )


def test_released_method_card_allows_exact_replay_and_rejects_content_drift(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    card = _method_card("mc_immutable")
    repository = MethodCardRepository(db_session)
    assert repository.upsert_cards([card]) == 1
    original_updated_at = db_session.execute(
        text("select updated_at from product.method_cards where id = :card_id"),
        {"card_id": card.id},
    ).scalar_one()

    release_id = str(uuid4())
    vocab_version = f"vocab@method-card-{release_id}"
    seed_released_module_vocabulary(db_session, version=vocab_version)
    db_session.execute(
        text(
            """
            insert into product.knowledge_map_releases (
              release_id, release_version, exam_scope, track,
              source_data_version, vocab_version, generator_version,
              source_fingerprint, generated_at, fresh_until, status, node_count
            ) values (
              cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
              'question-bank@method-card', :vocab_version, 'generator@method-card',
              :fingerprint, :generated_at, :fresh_until, 'draft', 0
            )
            """
        ),
        {
            "release_id": release_id,
            "release_version": f"knowledge-map@method-card-{release_id}",
            "vocab_version": vocab_version,
            "fingerprint": release_id.replace("-", "") * 2,
            "generated_at": now - timedelta(hours=1),
            "fresh_until": now + timedelta(days=30),
        },
    )
    db_session.execute(
        text(
            """
            insert into product.knowledge_map_nodes (
              release_id, node_id, exam_scope, track, module_name,
              node_type, tag_type, tag_value, display_path, support_status,
              method_card_ids, status
            ) values (
              cast(:release_id as uuid), 'km_method_card_immutable',
                  'guokao', 'xingce', '资料分析', 'knowledge_point',
                  'knowledge_point', '增长率', '行测/资料分析/增长率',
                  'text_supported', jsonb_build_array(cast(:card_id as text)),
                  'published'
            )
            """
        ),
        {"release_id": release_id, "card_id": card.id},
    )
    seed_minimal_release_binding(
        db_session,
        release_id=release_id,
        node_id="km_method_card_immutable",
        question_id=f"q_method_card_{release_id}",
    )
    activate_knowledge_release(
        db_session,
        release_id=release_id,
        activated_at=now,
    )
    db_session.commit()

    assert repository.upsert_cards([card]) == 1
    replayed_updated_at = db_session.execute(
        text("select updated_at from product.method_cards where id = :card_id"),
        {"card_id": card.id},
    ).scalar_one()
    assert replayed_updated_at == original_updated_at

    with pytest.raises(IntegrityError, match="referenced by published"):
        repository.upsert_cards(
            [replace(card, fix_action="内容已漂移，必须使用新方法卡 ID")]
        )
    db_session.rollback()

    stored = repository.get_published_card(card.id)
    assert stored is not None
    assert stored["fix_action"] == card.fix_action


def _run_vocab_loader(
    db_engine: Engine,
    *,
    loader: Path = VOCAB_LOADER,
) -> subprocess.CompletedProcess[str]:
    dsn = db_engine.url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    return subprocess.run(
        [
            "psql",
            dsn,
            "--set",
            "ON_ERROR_STOP=1",
            "--file",
            str(loader),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _method_card(card_id: str) -> MethodCardRecord:
    return MethodCardRecord(
        id=card_id,
        exam_scope="guokao",
        track="xingce",
        module_name="资料分析",
        tag_path="增长率",
        title="增长率识别",
        recognition="看到增长与基期关系时使用。",
        applicability={"modules": ["资料分析"]},
        method_steps=["识别口径", "代入计算"],
        error_signatures=[{"code": "base_period", "description": "基期混淆"}],
        fix_action="先标出现期与基期，再代入公式。",
        time_budget_sec=90,
        accuracy_rule="单位和时期均一致。",
        pitfall="不要混用同比与环比。",
        author_note="测试方法卡",
        validation={"status": "verified"},
        priority=3,
        status="published",
    )
