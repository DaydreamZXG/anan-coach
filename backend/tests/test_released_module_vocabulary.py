from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.guokao_xingce import GUOKAO_XINGCE_MODULES
from app.domains.knowledge.module_vocabulary import (
    _build_vocabulary,
    load_current_module_vocabulary,
)
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


def test_release_vocabulary_rejects_rotated_canonical_code_name_pairs() -> None:
    now = datetime.now(UTC)
    names = [module.display_name for module in GUOKAO_XINGCE_MODULES]
    rotated_names = names[1:] + names[:1]
    rows = [
        {
            "release_id": "00000000-0000-0000-0000-000000000068",
            "release_version": "knowledge-map@rotated-vocabulary",
            "source_data_version": "question-bank@rotated-vocabulary",
            "vocab_version": "vocab@rotated-vocabulary",
            "fresh_until": now + timedelta(days=1),
            "raw_module_name": raw_module_name,
            "action": "canonical",
            "canonical_code": module.code,
        }
        for module, raw_module_name in zip(
            GUOKAO_XINGCE_MODULES,
            rotated_names,
            strict=True,
        )
    ]

    assert _build_vocabulary(rows) is None


def test_release_vocabulary_maps_only_reviewed_names_without_crossing_modules(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    seed_released_module_vocabulary(
        db_session,
        version="vocab@runtime-test-v1",
        aliases={
            "言语理解": "verbal_comprehension",
            "逻辑判断": "judgment_reasoning",
            "资科分析": "data_analysis",
        },
    )
    db_session.execute(
        text(
            """
            insert into product.vocab_module_alias_map (
              version, raw_module_name, action, canonical_code,
              local_question_count, basis
            ) values
              (
                'vocab@runtime-test-v1', '常识应用能力',
                'map_needs_review', 'general_knowledge', 1, 'test fixture'
              ),
              (
                'vocab@runtime-test-v1', '公共基础知识',
                'quarantine', null, 1, 'test fixture'
              )
            """
        )
    )
    _insert_active_release(
        db_session,
        vocab_version="vocab@runtime-test-v1",
        now=now,
    )

    vocabulary = load_current_module_vocabulary(
        db_session,
        exam_scope="guokao",
        track="xingce",
        now=now,
    )

    assert vocabulary is not None
    assert vocabulary.canonicalize("逻辑判断") == "判断推理"
    assert vocabulary.canonicalize("资科分析") == "资料分析"
    assert vocabulary.canonicalize("常识应用能力") is None
    assert vocabulary.canonicalize("公共基础知识") is None
    assert set(vocabulary.equivalent_raw_names("逻辑判断")) == {
        "判断推理",
        "逻辑判断",
    }
    assert "资科分析" not in vocabulary.equivalent_raw_names("逻辑判断")


def test_release_vocabulary_fails_closed_when_the_six_canonical_rows_are_incomplete(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.execute(
        text(
            """
            insert into product.vocab_module_alias_map (
              version, raw_module_name, action, canonical_code,
              local_question_count, basis
            ) values (
              'vocab@runtime-incomplete', '资料分析', 'canonical',
              'data_analysis', 1, 'test fixture'
            )
            """
        )
    )
    _insert_active_release(
        db_session,
        vocab_version="vocab@runtime-incomplete",
        now=now,
    )

    assert (
        load_current_module_vocabulary(
            db_session,
            exam_scope="guokao",
            track="xingce",
            now=now,
        )
        is None
    )


def _insert_active_release(
    session: Session,
    *,
    vocab_version: str,
    now: datetime,
) -> None:
    release_id = str(uuid4())
    session.execute(
        text(
            """
            insert into product.knowledge_map_releases (
              release_id, release_version, exam_scope, track,
              source_data_version, vocab_version, generator_version,
              source_fingerprint, generated_at, fresh_until, status,
              node_count, activated_at
            ) values (
              cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
              'question-bank@runtime-test', :vocab_version,
              'knowledge-map-generator@runtime-test', :source_fingerprint,
              :generated_at, :fresh_until, 'draft', 0, null
            )
            """
        ),
        {
            "release_id": release_id,
            "release_version": f"knowledge-map@runtime-{release_id}",
            "vocab_version": vocab_version,
            "source_fingerprint": sha256(release_id.encode()).hexdigest(),
            "generated_at": now - timedelta(days=1),
            "fresh_until": now + timedelta(days=1),
        },
    )
    session.execute(
        text(
            """
            insert into product.knowledge_map_nodes (
              release_id, node_id, exam_scope, track, module_name,
              node_type, tag_type, tag_value, display_path,
              support_status, method_card_ids, status
            ) values (
              cast(:release_id as uuid), :node_id, 'guokao', 'xingce',
              '资料分析', 'knowledge_point', 'knowledge_point', '增长率',
              '行测/资料分析/增长率', 'text_supported', '[]'::jsonb, 'published'
            )
            """
        ),
        {
            "release_id": release_id,
            "node_id": f"km_runtime_{release_id}",
        },
    )
    seed_minimal_release_binding(
        session,
        release_id=release_id,
        node_id=f"km_runtime_{release_id}",
        question_id=f"q_runtime_vocab_{release_id}",
    )
    activate_knowledge_release(
        session,
        release_id=release_id,
        activated_at=now - timedelta(hours=23),
    )
