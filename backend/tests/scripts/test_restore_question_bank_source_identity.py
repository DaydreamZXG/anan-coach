from __future__ import annotations

import csv
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "restore_question_bank_source_identity.py"
SPEC = importlib.util.spec_from_file_location("restore_question_bank_source_identity", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture(autouse=True)
def no_surplus_by_default(monkeypatch):
    monkeypatch.setattr(MODULE, "EXPECTED_SURPLUS_OCCURRENCES", 0)


def source(dataset: str, source_id: str, occurrence_key: str, number: int = 1):
    return MODULE.SourceOccurrence(
        source_dataset_id=dataset,
        source_question_id=source_id,
        source_paper_row_id="paper-row",
        source_paper_key="paper",
        source_occurrence_question_key=occurrence_key,
        source_occurrence_paper_key="source-paper",
        source_path="paper.json",
        cls="行测" if dataset == MODULE.QBANK_DATASET else "公共基础知识",
        province="国考" if dataset == MODULE.QBANK_DATASET else "山东",
        occurrence_paper_title="测试卷",
        paper_title="测试卷",
        year=2026,
        number=number,
        section="资料分析" if dataset == MODULE.QBANK_DATASET else "单项选择题",
        occurrence_answer="A",
        occurrence_correct_ratio=50.0 if dataset == MODULE.QBANK_DATASET else None,
        occurrence_total_count=100 if dataset == MODULE.QBANK_DATASET else None,
        occurrence_easy_wrong="B" if dataset == MODULE.QBANK_DATASET else None,
        occurrence_most_wrong_answer='{"choice":"1"}' if dataset == MODULE.QBANK_DATASET else None,
        occurrence_sample_size=100 if dataset == MODULE.QBANK_DATASET else None,
    )


def runtime(item, *, occurrence_id=None, question_id="question-1", has_head=True):
    if occurrence_id is None:
        occurrence_id = MODULE._stable_id("existing", item.source_dataset_id, item.source_occurrence_question_key)
    values = MODULE._plan_values(item, occurrence_id, question_id, 1)
    return MODULE.RuntimeOccurrence(
        **values,
        question_status="active",
        has_content_head=has_head,
    )


def write_sources(path: Path, items: list) -> None:
    connection = MODULE._connect(path)
    connection.execute(
        "create table expected_occurrences ("
        "source_dataset_id text, source_question_id text, source_paper_row_id text, "
        "source_paper_key text, source_occurrence_question_key text, "
        "source_occurrence_paper_key text, source_path text, cls text, province text, "
        "occurrence_paper_title text, paper_title text, year integer, number integer, "
        "section text, occurrence_answer text, occurrence_correct_ratio real, "
        "occurrence_total_count integer, occurrence_easy_wrong text, "
        "occurrence_most_wrong_answer text, occurrence_sample_size integer)"
    )
    connection.execute("create table metadata (key text primary key, value text not null)")
    for item in items:
        connection.execute(
            "insert into expected_occurrences values (" + ",".join("?" for _ in range(20)) + ")",
            tuple(asdict(item).values()),
        )
    connection.execute(
        "insert into metadata values (?, ?)",
        ("source", json.dumps({"sourceManifestFingerprint": "sha256:test"})),
    )
    connection.commit()
    connection.close()


def write_runtime(path: Path, rows: list) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=MODULE.RUNTIME_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def test_qbank_section_and_scope_come_from_source_sheet() -> None:
    payload = {
        "sheet": {"chapters": [
            {"name": "言语理解与表达", "questionCount": 2},
            {"name": "资料分析", "questionCount": 2},
        ]}
    }
    ranges = MODULE._qbank_sections(payload)
    assert MODULE._section_for(1, ranges) == "言语理解与表达"
    assert MODULE._section_for(3, ranges) == "资料分析"
    assert MODULE._province("2025年中央机关及其直属机构考试《行测》题") == "国考"
    assert MODULE._province("2025年新疆公务员考试《行测》题") == "新疆"


def test_sydw_manifest_hash_mismatch_stops(tmp_path: Path) -> None:
    root = tmp_path / "sydw"
    root.mkdir()
    (root / "papers.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "occurrences.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({
            "counts": {"papers": 1630, "occurrences": 123812},
            "outputs": {
                "papers": {"path": "papers.jsonl", "sha256": "bad"},
                "occurrences": {"path": "occurrences.jsonl", "sha256": "bad"},
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(MODULE.RecoveryError, match="hash mismatch"):
        MODULE._verified_sydw_files(root)


def test_plan_preserves_exact_occurrence_mapping_and_adds_missing(monkeypatch, tmp_path: Path) -> None:
    q1 = source(MODULE.QBANK_DATASET, "an:1", "an:o:paper:1:1", 1)
    q2 = replace(q1, source_occurrence_question_key="an:o:paper:2:1", number=2)
    s1 = source(MODULE.SYDW_DATASET, "sydw:q:1", "sydw:o:paper:1", 1)
    sources = tmp_path / "sources.sqlite"
    write_sources(sources, [q1, q2, s1])
    old_q2 = runtime(q2, occurrence_id="old-q2", question_id="physical-duplicate")
    old_q1 = runtime(q1, occurrence_id="old-q1", question_id="canonical")
    old_s1 = runtime(s1, occurrence_id="old-s1", question_id="sydw-question")
    runtime_path = tmp_path / "runtime.csv"
    write_runtime(runtime_path, [old_q2, old_q1, old_s1])
    monkeypatch.setattr(MODULE, "EXPECTED_COUNTS", {MODULE.QBANK_DATASET: 2, MODULE.SYDW_DATASET: 1})
    plan = tmp_path / "plan.sqlite"
    first = MODULE.build_plan(sources, runtime_path, plan)
    assert first["datasets"][MODULE.QBANK_DATASET]["inserted"] == 0
    connection = MODULE._connect(plan)
    mappings = connection.execute(
        "select source_occurrence_question_key, occurrence_id, question_id from plan order by source_dataset_id, number"
    ).fetchall()
    connection.close()
    assert [tuple(row) for row in mappings] == [
        ("an:o:paper:1:1", "old-q1", "canonical"),
        ("an:o:paper:2:1", "old-q2", "physical-duplicate"),
        ("sydw:o:paper:1", "old-s1", "sydw-question"),
    ]

    # Removing q2 from runtime adds exactly one stable occurrence against the
    # usable representative without deleting or merging the existing question.
    write_runtime(runtime_path, [old_q1, old_s1])
    second_plan = tmp_path / "plan-2.sqlite"
    second = MODULE.build_plan(sources, runtime_path, second_plan)
    assert second["datasets"][MODULE.QBANK_DATASET]["inserted"] == 1
    connection = MODULE._connect(second_plan)
    added = connection.execute("select occurrence_id, question_id from plan where action='insert'").fetchone()
    connection.close()
    assert added[1] == "canonical"
    assert added[0] == MODULE._stable_id("qbo", MODULE.QBANK_DATASET, q2.source_occurrence_question_key)


def test_unmapped_source_and_sydw_missing_head_stop(monkeypatch, tmp_path: Path) -> None:
    qbank = source(MODULE.QBANK_DATASET, "an:1", "an:o:1")
    sydw = source(MODULE.SYDW_DATASET, "sydw:q:1", "sydw:o:1")
    sources = tmp_path / "sources.sqlite"
    write_sources(sources, [qbank, sydw])
    monkeypatch.setattr(MODULE, "EXPECTED_COUNTS", {MODULE.QBANK_DATASET: 1, MODULE.SYDW_DATASET: 1})
    runtime_path = tmp_path / "runtime.csv"
    write_runtime(runtime_path, [runtime(qbank)])
    with pytest.raises(MODULE.RecoveryError, match="SOURCE_QUESTION_UNMAPPED"):
        MODULE.build_plan(sources, runtime_path, tmp_path / "unmapped.sqlite")

    write_runtime(runtime_path, [runtime(qbank), runtime(sydw, has_head=False)])
    with pytest.raises(MODULE.RecoveryError, match="sydw contains delivery-ineligible"):
        MODULE.build_plan(sources, runtime_path, tmp_path / "sydw-head.sqlite")


def test_qbank_missing_head_is_counted_not_hidden(monkeypatch, tmp_path: Path) -> None:
    qbank = source(MODULE.QBANK_DATASET, "an:1", "an:o:1")
    sydw = source(MODULE.SYDW_DATASET, "sydw:q:1", "sydw:o:1")
    sources = tmp_path / "sources.sqlite"
    write_sources(sources, [qbank, sydw])
    runtime_path = tmp_path / "runtime.csv"
    write_runtime(runtime_path, [runtime(qbank, has_head=False), runtime(sydw)])
    monkeypatch.setattr(MODULE, "EXPECTED_COUNTS", {MODULE.QBANK_DATASET: 1, MODULE.SYDW_DATASET: 1})
    report = MODULE.build_plan(sources, runtime_path, tmp_path / "plan.sqlite")
    assert report["datasets"][MODULE.QBANK_DATASET]["deliveryIneligibleSourceQuestions"] == 1
    assert report["datasets"][MODULE.SYDW_DATASET]["deliveryIneligibleSourceQuestions"] == 0


def test_export_is_deterministic(monkeypatch, tmp_path: Path) -> None:
    qbank = source(MODULE.QBANK_DATASET, "an:1", "an:o:1")
    sydw = source(MODULE.SYDW_DATASET, "sydw:q:1", "sydw:o:1")
    sources = tmp_path / "sources.sqlite"
    write_sources(sources, [qbank, sydw])
    runtime_path = tmp_path / "runtime.csv"
    write_runtime(runtime_path, [runtime(qbank), runtime(sydw)])
    monkeypatch.setattr(MODULE, "EXPECTED_COUNTS", {MODULE.QBANK_DATASET: 1, MODULE.SYDW_DATASET: 1})
    plan = tmp_path / "plan.sqlite"
    MODULE.build_plan(sources, runtime_path, plan)
    first = MODULE.export_plan(plan, tmp_path / "one.csv.gz")
    second = MODULE.export_plan(plan, tmp_path / "two.csv.gz")
    # gzip headers may differ by filename, while the normalized dry-run plan is
    # stable and is the command precondition that matters.
    assert first["dryRunFingerprint"] == second["dryRunFingerprint"]


def test_exact_five_sydw_surplus_rows_are_retained_in_quarantine(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_SURPLUS_OCCURRENCES", 5)
    qbank = source(MODULE.QBANK_DATASET, "an:1", "an:o:1")
    sydw_items = [
        source(MODULE.SYDW_DATASET, f"sydw:q:{index}", f"sydw:o:{index}")
        for index in range(5)
    ]
    sources = tmp_path / "sources.sqlite"
    write_sources(sources, [qbank, *sydw_items])
    rows = [runtime(qbank)]
    for index, item in enumerate(sydw_items):
        rows.extend([
            runtime(item, occurrence_id=f"kept-{index}", question_id=f"question-{index}"),
            replace(
                runtime(item, occurrence_id=f"surplus-{index}", question_id=f"duplicate-{index}"),
                source_occurrence_question_key=None,
            ),
        ])
    runtime_path = tmp_path / "runtime.csv"
    write_runtime(runtime_path, rows)
    monkeypatch.setattr(MODULE, "EXPECTED_COUNTS", {MODULE.QBANK_DATASET: 1, MODULE.SYDW_DATASET: 5})
    plan = tmp_path / "plan.sqlite"
    report = MODULE.build_plan(sources, runtime_path, plan)
    assert report["quarantinedSurplusOccurrences"] == 5
    connection = MODULE._connect(plan)
    quarantine = connection.execute(
        "select occurrence_id, question_id, source_question_id, quarantine_dataset_id from quarantine order by occurrence_id"
    ).fetchall()
    connection.close()
    assert len(quarantine) == 5
    assert {row[3] for row in quarantine} == {MODULE.QUARANTINE_DATASET}
    assert {row[0] for row in quarantine} == {f"surplus-{index}" for index in range(5)}

    # A post-apply snapshot already contains the same five rows in quarantine.
    # Replanning must keep the same desired-state fingerprint and produce no
    # active-source insertion/update.
    preapply_fingerprint = report["dryRunFingerprint"]
    post_rows = [runtime(qbank)]
    for index, item in enumerate(sydw_items):
        post_rows.extend([
            runtime(item, occurrence_id=f"kept-{index}", question_id=f"question-{index}"),
            replace(
                runtime(item, occurrence_id=f"surplus-{index}", question_id=f"duplicate-{index}"),
                source_dataset_id=MODULE.QUARANTINE_DATASET,
                source_occurrence_question_key=None,
            ),
        ])
    write_runtime(runtime_path, post_rows)
    replay_plan = tmp_path / "replay.sqlite"
    replay = MODULE.build_plan(sources, runtime_path, replay_plan)
    assert replay["dryRunFingerprint"] == preapply_fingerprint
    assert replay["datasets"][MODULE.QBANK_DATASET]["inserted"] == 0
    assert replay["datasets"][MODULE.QBANK_DATASET]["updated"] == 0
    assert replay["datasets"][MODULE.SYDW_DATASET]["inserted"] == 0
    assert replay["datasets"][MODULE.SYDW_DATASET]["updated"] == 0
