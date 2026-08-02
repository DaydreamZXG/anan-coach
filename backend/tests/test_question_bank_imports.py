from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.engine import Engine


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
IMPORT_SCRIPT = REPOSITORY_ROOT / "backend/scripts/import_pp_questions.py"
KNOWLEDGE_IMPORT_SCRIPT = REPOSITORY_ROOT / "backend/scripts/import_fenbi_knowledge.py"


def _database_url(engine: Engine) -> str:
    return str(engine.url).replace("+psycopg", "")


def _run_import(engine: Engine, source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(IMPORT_SCRIPT), _database_url(engine), str(source)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _record(source_question_id: str, *, option: str = "选项 A") -> dict[str, object]:
    return {
        "sourceQuestionId": source_question_id,
        "section": "判断推理",
        "question": {
            "type": "单选题",
            "stem": f"题干 {source_question_id}",
            "options": [option, "选项 B"],
            "answer": "A",
        },
    }


def test_import_replay_is_idempotent_and_keeps_source_occurrence(
    db_engine: Engine, tmp_path: Path
) -> None:
    source = tmp_path / "questions.jsonl"
    source.write_text(json.dumps(_record("sydw-1")) + "\n", encoding="utf-8")

    first = _run_import(db_engine, source)
    second = _run_import(db_engine, source)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "inserted=1" in first.stdout
    assert "inserted=0 skipped=1" in second.stdout
    with db_engine.connect() as connection:
        assert connection.execute(
            text("select count(*) from product.questions where question_id like 'pp_%'")
        ).scalar_one() == 1
        assert connection.execute(
            text(
                """
                select source_dataset_id
                from product.question_occurrences
                where source_question_id = 'sydw-1'
                """
            )
        ).scalar_one() == "sydw-public-import-20260801"


def test_import_uses_a_savepoint_for_each_question(db_engine: Engine, tmp_path: Path) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                alter table product.question_options
                add constraint question_option_not_bad
                check (option_text <> '坏选项')
                """
            )
        )
    source = tmp_path / "questions.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in (
                _record("sydw-good-before"),
                _record("sydw-bad", option="坏选项"),
                _record("sydw-good-after"),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_import(db_engine, source)

    assert result.returncode == 0, result.stderr
    assert "inserted=2 skipped=1" in result.stdout
    with db_engine.connect() as connection:
        identifiers = connection.execute(
            text(
                """
                select representative_source_question_id
                from product.questions
                order by representative_source_question_id
                """
            )
        ).scalars().all()
    assert identifiers == ["sydw-good-after", "sydw-good-before"]


def test_knowledge_import_supports_an_source_and_is_idempotent(
    db_engine: Engine, tmp_path: Path
) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into product.questions (
                  question_id, domain, status, representative_source_question_id, stem, answer
                ) values (
                  'pp_knowledge', 'objective', 'active', 'an:knowledge-1', '题干', 'A'
                )
                """
            )
        )
    source = tmp_path / "fenbi"
    source.mkdir()
    (source / "paper.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "key": "knowledge-1",
                        "knowledge_points": [{"name": "削弱论证"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(KNOWLEDGE_IMPORT_SCRIPT),
        _database_url(db_engine),
        str(source),
    ]

    first = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False, capture_output=True, text=True)
    second = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False, capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "tagged=1" in first.stdout
    assert "tagged=0" in second.stdout
    with db_engine.connect() as connection:
        assert connection.execute(
            text(
                """
                select count(*)
                from product.question_tags
                where question_id = 'pp_knowledge'
                  and tag_type = 'knowledge_point'
                  and tag_value = '削弱论证'
                """
            )
        ).scalar_one() == 1
