from __future__ import annotations

from pathlib import Path
import importlib.util
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "backend/scripts/finalize_question_bank.py"


def _load_finalizer():
    spec = importlib.util.spec_from_file_location("qbank_finalizer_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_question(
    session: Session,
    *,
    question_id: str,
    source_question_id: str,
    module_tag: str | None,
) -> None:
    session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, representative_source_question_id,
              module_name, question_type, stem, answer
            ) values (
              :question_id, 'objective', 'active', :source_question_id,
              '2024 年试卷', 'single', '下列说法正确的是', 'A'
            )
            """
        ),
        {"question_id": question_id, "source_question_id": source_question_id},
    )
    session.execute(
        text(
            """
            insert into product.question_options (
              question_id, option_index, label, option_text
            ) values (:question_id, 1, 'A', '正确选项')
            """
        ),
        {"question_id": question_id},
    )
    if module_tag is not None:
        session.execute(
            text(
                """
                insert into product.question_tags (
                  question_tag_id, question_id, tag_type, tag_value
                ) values (:tag_id, :question_id, 'module', :tag_value)
                """
            ),
            {
                "tag_id": f"tag-{question_id}",
                "question_id": question_id,
                "tag_value": module_tag,
            },
        )


def test_staging_name_is_required() -> None:
    finalizer = _load_finalizer()

    with pytest.raises(finalizer.QuestionBankFinalizationError, match="_staging"):
        finalizer.require_staging_database_name("gongkao_dazi")


def test_apply_backfills_provenance_normalizes_module_and_publishes_current_content(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    finalizer = _load_finalizer()
    monkeypatch.setattr(finalizer, "_database_name", lambda _session: "gongkao_dazi_staging")
    _seed_question(
        db_session,
        question_id="pp-fenbi",
        source_question_id="an:1001",
        module_tag="reasoning",
    )
    _seed_question(
        db_session,
        question_id="pp-quarantined",
        source_question_id="sydw-1002",
        module_tag=None,
    )
    db_session.commit()

    report = finalizer.apply_finalization(db_session, batch_size=1)

    assert report.imported_questions == 2
    assert report.eligible == 1
    assert report.published == 1
    assert report.quarantined == 1
    assert report.missing_source == 0
    assert report.missing_content_version == 0
    assert db_session.execute(
        text("select module_name from product.questions where question_id = 'pp-fenbi'")
    ).scalar_one() == "判断推理"
    assert db_session.execute(
        text(
            """
            select source_dataset_id
            from product.question_occurrences
            where question_id = 'pp-fenbi'
            """
        )
    ).scalar_one() == "fenbi-import-20260801"
    assert db_session.execute(
        text(
            """
            select count(*)
            from product.question_content_heads
            where question_id = 'pp-fenbi'
            """
        )
    ).scalar_one() == 1

    replay = finalizer.apply_finalization(db_session, batch_size=1)
    assert replay.inserted_occurrences == 0
    assert replay.published == 1
    assert finalizer.verify_finalization(db_session).missing_content_version == 0
