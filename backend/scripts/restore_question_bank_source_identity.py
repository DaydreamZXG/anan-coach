#!/usr/bin/env python3
"""Build and verify an occurrence-only question-bank source identity recovery.

The tool deliberately separates immutable source parsing from runtime planning:

* ``build-sources`` reads the verified qbank paper JSON tree and the sydw v2
  papers/occurrences manifests into a local SQLite inventory;
* ``build-plan`` pairs existing runtime occurrences with source occurrences,
  preserves existing question/occurrence IDs, and creates only the missing
  occurrence IDs against a deterministic existing representative;
* ``export-plan`` emits the complete desired occurrence set as CSV for a
  transactional PostgreSQL staging-table apply.

It never creates, deletes, merges, or rewrites ``product.questions`` and it has
no publication, object-store, or service-control operation.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import gzip
from hashlib import sha256
from itertools import chain
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Iterator, TextIO


QBANK_DATASET = "qbank-import-20260801"
SYDW_DATASET = "sydw-public-import-20260801"
EXPECTED_COUNTS = {QBANK_DATASET: 104_762, SYDW_DATASET: 123_812}
EXPECTED_PAPERS = {QBANK_DATASET: 902, SYDW_DATASET: 1_630}
QBANK_MISSING_HEAD_CEILING = 119
EXPECTED_SURPLUS_OCCURRENCES = 5
REGIONS = (
    "内蒙古", "黑龙江", "广西", "宁夏", "新疆", "西藏", "北京", "天津",
    "上海", "重庆", "河北", "山西", "辽宁", "吉林", "江苏", "浙江",
    "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东",
    "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "香港", "澳门",
)
PLAN_COLUMNS = (
    "occurrence_id", "question_id", "source_question_id", "source_dataset_id",
    "source_paper_row_id", "source_paper_key", "source_occurrence_question_key",
    "source_occurrence_paper_key", "source_path", "cls", "province",
    "occurrence_paper_title", "paper_title", "year", "number", "section",
    "occurrence_answer", "occurrence_correct_ratio", "occurrence_total_count",
    "occurrence_easy_wrong", "occurrence_most_wrong_answer",
    "canonical_occurrence_rank", "occurrence_sample_size",
)
QUARANTINE_DATASET = f"{SYDW_DATASET}-duplicate-quarantine"
RUNTIME_COLUMNS = PLAN_COLUMNS + ("question_status", "has_content_head")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)")


RUNTIME_EXPORT_SQL = r"""\copy (
select
  occurrence.occurrence_id,
  occurrence.question_id,
  occurrence.source_question_id,
  occurrence.source_dataset_id,
  occurrence.source_paper_row_id,
  occurrence.source_paper_key,
  occurrence.source_occurrence_question_key,
  occurrence.source_occurrence_paper_key,
  occurrence.source_path,
  occurrence.cls,
  occurrence.province,
  occurrence.occurrence_paper_title,
  occurrence.paper_title,
  occurrence.year,
  occurrence.number,
  occurrence.section,
  occurrence.occurrence_answer,
  occurrence.occurrence_correct_ratio,
  occurrence.occurrence_total_count,
  occurrence.occurrence_easy_wrong,
  occurrence.occurrence_most_wrong_answer,
  occurrence.canonical_occurrence_rank,
  occurrence.occurrence_sample_size,
  question.status as question_status,
  (head.question_id is not null) as has_content_head
from product.question_occurrences occurrence
join product.questions question on question.question_id = occurrence.question_id
left join product.question_content_heads head on head.question_id = question.question_id
where occurrence.source_dataset_id in (
  'qbank-import-20260801',
  'sydw-public-import-20260801',
  'sydw-public-import-20260801-duplicate-quarantine'
)
order by occurrence.source_dataset_id, occurrence.source_question_id, occurrence.occurrence_id
) to stdout with (format csv, header true)"""


class RecoveryError(RuntimeError):
    """Raised when a source or runtime state cannot support a safe recovery."""


@dataclass(frozen=True)
class SourceOccurrence:
    source_dataset_id: str
    source_question_id: str
    source_paper_row_id: str
    source_paper_key: str
    source_occurrence_question_key: str
    source_occurrence_paper_key: str
    source_path: str
    cls: str
    province: str
    occurrence_paper_title: str
    paper_title: str
    year: int | None
    number: int
    section: str
    occurrence_answer: str | None
    occurrence_correct_ratio: float | None
    occurrence_total_count: int | None
    occurrence_easy_wrong: str | None
    occurrence_most_wrong_answer: str | None
    occurrence_sample_size: int | None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (
            self.source_dataset_id,
            self.source_question_id,
            self.source_occurrence_question_key,
        )


@dataclass(frozen=True)
class RuntimeOccurrence:
    occurrence_id: str
    question_id: str
    source_question_id: str
    source_dataset_id: str
    source_paper_row_id: str | None
    source_paper_key: str | None
    source_occurrence_question_key: str | None
    source_occurrence_paper_key: str | None
    source_path: str | None
    cls: str | None
    province: str | None
    occurrence_paper_title: str | None
    paper_title: str | None
    year: int | None
    number: int | None
    section: str | None
    occurrence_answer: str | None
    occurrence_correct_ratio: float | None
    occurrence_total_count: int | None
    occurrence_easy_wrong: str | None
    occurrence_most_wrong_answer: str | None
    canonical_occurrence_rank: int | None
    occurrence_sample_size: int | None
    question_status: str
    has_content_head: bool

    @property
    def usable(self) -> bool:
        return self.question_status == "active" and self.has_content_head


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:28]}"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_json_text(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return _canonical_json(json.loads(text))
    except json.JSONDecodeError as exc:
        raise RecoveryError("runtime occurrence contains invalid JSON statistics") from exc


def _year(title: str, explicit: object = None) -> int | None:
    if explicit not in (None, ""):
        value = int(explicit)
        if 1900 <= value <= 2100:
            return value
    match = _YEAR_RE.search(title)
    return int(match.group(1)) if match else None


def _province(title: str, fallback: object = None) -> str:
    if "国家公务员" in title or re.search(r"(^|非)国考|中央机关及其直属机构", title):
        return "国考"
    fallback_text = _optional_text(fallback)
    if fallback_text and fallback_text not in {"公务员", "行测", "事业单位"}:
        return fallback_text
    for region in REGIONS:
        if region in title:
            return region
    return "全国"


def _qbank_sections(payload: dict[str, object]) -> list[tuple[int, int, str]]:
    chapters = ((payload.get("sheet") or {}).get("chapters") or []) if isinstance(payload.get("sheet"), dict) else []
    result: list[tuple[int, int, str]] = []
    start = 1
    for raw in chapters:
        if not isinstance(raw, dict):
            continue
        count = int(raw.get("questionCount") or 0)
        if count <= 0:
            continue
        name = str(raw.get("name") or "行测").strip() or "行测"
        result.append((start, start + count - 1, name))
        start += count
    return result


def _section_for(number: int, ranges: list[tuple[int, int, str]]) -> str:
    for start, end, name in ranges:
        if start <= number <= end:
            return name
    return "行测"


def iter_qbank_occurrences(root: Path) -> Iterator[SourceOccurrence]:
    files = sorted(root.rglob("*.json"), key=lambda path: path.relative_to(root).as_posix())
    if len(files) != EXPECTED_PAPERS[QBANK_DATASET]:
        raise RecoveryError(f"qbank paper count {len(files)} != {EXPECTED_PAPERS[QBANK_DATASET]}")
    seen_occurrences: set[str] = set()
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecoveryError(f"cannot read qbank paper {path}: {exc}") from exc
        paper = payload.get("paper") if isinstance(payload.get("paper"), dict) else {}
        title = str(paper.get("name") or payload.get("name") or path.stem).strip()
        paper_id = str(paper.get("paper_id") or paper.get("id") or payload.get("paper_id") or "").strip()
        if not paper_id:
            raise RecoveryError(f"qbank paper has no stable paper id: {path}")
        relative = path.relative_to(root).as_posix()
        paper_row_id = _stable_id("qbp", QBANK_DATASET, relative)
        paper_instance_key = _stable_id("anp", QBANK_DATASET, relative)
        ranges = _qbank_sections(payload)
        paper_year = _year(title, str(paper.get("date") or "")[:4] or None)
        source_region = path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else None
        province = _province(
            title,
            source_region
            or paper.get("label_name")
            or paper.get("label")
            or payload.get("label"),
        )
        questions = payload.get("questions") or []
        if not isinstance(questions, list) or not questions:
            raise RecoveryError(f"qbank paper has no questions: {relative}")
        for raw in questions:
            if not isinstance(raw, dict):
                raise RecoveryError(f"invalid qbank question in {relative}")
            key = str(raw.get("key") or raw.get("id") or "").strip()
            number = int(raw.get("idx") or 0)
            if not key or number <= 0:
                raise RecoveryError(f"qbank question lacks key/number in {relative}")
            source_question_id = f"an:{key}"
            occurrence_key = _stable_id("ano", QBANK_DATASET, relative, str(number), key)
            if occurrence_key in seen_occurrences:
                raise RecoveryError(f"duplicate qbank occurrence {occurrence_key}")
            seen_occurrences.add(occurrence_key)
            question_meta = raw.get("question_meta") if isinstance(raw.get("question_meta"), dict) else {}
            total = _optional_int(raw.get("total_count") or question_meta.get("totalCount"))
            ratio = _optional_float(raw.get("correct_ratio"))
            most_wrong = raw.get("most_wrong_answer")
            yield SourceOccurrence(
                source_dataset_id=QBANK_DATASET,
                source_question_id=source_question_id,
                source_paper_row_id=paper_row_id,
                source_paper_key=paper_id,
                source_occurrence_question_key=occurrence_key,
                source_occurrence_paper_key=paper_instance_key,
                source_path=relative,
                cls="行测",
                province=province,
                occurrence_paper_title=title,
                paper_title=title,
                year=paper_year,
                number=number,
                section=_section_for(number, ranges),
                occurrence_answer=_optional_text(raw.get("answer")),
                occurrence_correct_ratio=ratio,
                occurrence_total_count=total,
                occurrence_easy_wrong=_optional_text(raw.get("easy_wrong")),
                occurrence_most_wrong_answer=_canonical_json(most_wrong) if most_wrong else None,
                occurrence_sample_size=total if total is not None and total > 0 else None,
            )


def _verified_sydw_files(root: Path) -> tuple[Path, Path, dict[str, object]]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest.get("outputs") or {}
    for name in ("papers", "occurrences"):
        entry = outputs.get(name) if isinstance(outputs, dict) else None
        if not isinstance(entry, dict):
            raise RecoveryError(f"sydw manifest missing output {name}")
        path = root / str(entry.get("path") or "")
        expected_hash = str(entry.get("sha256") or "")
        if not path.is_file() or _sha256_file(path) != expected_hash:
            raise RecoveryError(f"sydw source hash mismatch: {name}")
    counts = manifest.get("counts") or {}
    if int(counts.get("papers") or 0) != EXPECTED_PAPERS[SYDW_DATASET]:
        raise RecoveryError("sydw manifest paper count mismatch")
    if int(counts.get("occurrences") or 0) != EXPECTED_COUNTS[SYDW_DATASET]:
        raise RecoveryError("sydw manifest occurrence count mismatch")
    return root / outputs["papers"]["path"], root / outputs["occurrences"]["path"], manifest


def iter_sydw_occurrences(root: Path) -> Iterator[SourceOccurrence]:
    papers_path, occurrences_path, _manifest = _verified_sydw_files(root)
    papers: dict[str, dict[str, object]] = {}
    with papers_path.open(encoding="utf-8") as source:
        for line in source:
            paper = json.loads(line)
            paper_id = str(paper.get("sourcePaperId") or "")
            if not paper_id or paper_id in papers:
                raise RecoveryError(f"invalid/duplicate sydw paper id {paper_id!r}")
            papers[paper_id] = paper
    if len(papers) != EXPECTED_PAPERS[SYDW_DATASET]:
        raise RecoveryError("sydw papers.jsonl count mismatch")
    seen: set[str] = set()
    with occurrences_path.open(encoding="utf-8") as source:
        for line in source:
            raw = json.loads(line)
            source_occurrence_id = str(raw.get("sourceQuestionId") or "")
            source_question_id = str(raw.get("canonicalSourceQuestionId") or "")
            paper_id = str(raw.get("sourcePaperId") or "")
            number = int(raw.get("number") or 0)
            if not source_occurrence_id or source_occurrence_id in seen:
                raise RecoveryError(f"invalid/duplicate sydw occurrence {source_occurrence_id!r}")
            if not source_question_id or paper_id not in papers or number <= 0:
                raise RecoveryError(f"unresolvable sydw occurrence {source_occurrence_id}")
            seen.add(source_occurrence_id)
            paper = papers[paper_id]
            title = str(paper.get("title") or "").strip()
            question = raw.get("question") if isinstance(raw.get("question"), dict) else {}
            original_paper_id = str(paper.get("originalPaperId") or paper_id)
            yield SourceOccurrence(
                source_dataset_id=SYDW_DATASET,
                source_question_id=source_question_id,
                source_paper_row_id=paper_id,
                source_paper_key=original_paper_id,
                source_occurrence_question_key=source_occurrence_id,
                source_occurrence_paper_key=paper_id,
                source_path=str(paper.get("sourcePath") or ""),
                cls="公共基础知识",
                province=_province(title),
                occurrence_paper_title=title,
                paper_title=title,
                year=_year(title, paper.get("year")),
                number=number,
                section=str(raw.get("section") or "公共基础知识"),
                occurrence_answer=_optional_text(question.get("answer")),
                occurrence_correct_ratio=None,
                occurrence_total_count=None,
                occurrence_easy_wrong=None,
                occurrence_most_wrong_answer=None,
                occurrence_sample_size=None,
            )


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma journal_mode=wal")
    return connection


def build_sources(qbank_root: Path, sydw_root: Path, output: Path) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    connection = _connect(output)
    connection.executescript(
        """
        create table expected_occurrences (
          source_dataset_id text not null, source_question_id text not null,
          source_paper_row_id text not null, source_paper_key text not null,
          source_occurrence_question_key text not null,
          source_occurrence_paper_key text not null, source_path text not null,
          cls text not null, province text not null,
          occurrence_paper_title text not null, paper_title text not null,
          year integer, number integer not null, section text not null,
          occurrence_answer text, occurrence_correct_ratio real,
          occurrence_total_count integer, occurrence_easy_wrong text,
          occurrence_most_wrong_answer text, occurrence_sample_size integer,
          primary key(source_dataset_id, source_occurrence_question_key)
        );
        create index expected_source_question_idx
          on expected_occurrences(source_dataset_id, source_question_id, source_occurrence_question_key);
        create table metadata (key text primary key, value text not null);
        """
    )
    digest = sha256()
    counts = {QBANK_DATASET: 0, SYDW_DATASET: 0}
    papers: dict[str, set[str]] = {QBANK_DATASET: set(), SYDW_DATASET: set()}
    insert_sql = "insert into expected_occurrences values (" + ",".join("?" for _ in range(20)) + ")"
    for occurrence in chain(iter_qbank_occurrences(qbank_root), iter_sydw_occurrences(sydw_root)):
        row = asdict(occurrence)
        connection.execute(insert_sql, tuple(row.values()))
        digest.update((_canonical_json(row) + "\n").encode("utf-8"))
        counts[occurrence.source_dataset_id] += 1
        papers[occurrence.source_dataset_id].add(occurrence.source_paper_row_id)
    for dataset, expected in EXPECTED_COUNTS.items():
        if counts[dataset] != expected:
            raise RecoveryError(f"{dataset} occurrence count {counts[dataset]} != {expected}")
        if len(papers[dataset]) != EXPECTED_PAPERS[dataset]:
            raise RecoveryError(f"{dataset} paper count mismatch")
    fingerprint = f"sha256:{digest.hexdigest()}"
    metadata = {
        "schemaVersion": 1,
        "sourceManifestFingerprint": fingerprint,
        "occurrenceCounts": counts,
        "paperCounts": {key: len(value) for key, value in papers.items()},
    }
    connection.execute("insert into metadata values (?, ?)", ("source", _canonical_json(metadata)))
    connection.commit()
    connection.close()
    return metadata


def _open_text(path: Path, mode: str) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, mode + "t", encoding="utf-8", newline="")
    return path.open(mode, encoding="utf-8", newline="")


def read_runtime_csv(path: Path) -> Iterator[RuntimeOccurrence]:
    with _open_text(path, "r") as source:
        reader = csv.DictReader(source)
        missing = set(RUNTIME_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise RecoveryError(f"runtime CSV missing columns: {sorted(missing)}")
        for raw in reader:
            dataset = raw["source_dataset_id"]
            if dataset not in {*EXPECTED_COUNTS, QUARANTINE_DATASET}:
                raise RecoveryError(f"runtime CSV contains out-of-scope dataset {dataset}")
            yield RuntimeOccurrence(
                occurrence_id=raw["occurrence_id"], question_id=raw["question_id"],
                source_question_id=raw["source_question_id"], source_dataset_id=dataset,
                source_paper_row_id=_optional_text(raw["source_paper_row_id"]),
                source_paper_key=_optional_text(raw["source_paper_key"]),
                source_occurrence_question_key=_optional_text(raw["source_occurrence_question_key"]),
                source_occurrence_paper_key=_optional_text(raw["source_occurrence_paper_key"]),
                source_path=_optional_text(raw["source_path"]), cls=_optional_text(raw["cls"]),
                province=_optional_text(raw["province"]),
                occurrence_paper_title=_optional_text(raw["occurrence_paper_title"]),
                paper_title=_optional_text(raw["paper_title"]), year=_optional_int(raw["year"]),
                number=_optional_int(raw["number"]), section=_optional_text(raw["section"]),
                occurrence_answer=_optional_text(raw["occurrence_answer"]),
                occurrence_correct_ratio=_optional_float(raw["occurrence_correct_ratio"]),
                occurrence_total_count=_optional_int(raw["occurrence_total_count"]),
                occurrence_easy_wrong=_optional_text(raw["occurrence_easy_wrong"]),
                occurrence_most_wrong_answer=_optional_json_text(raw["occurrence_most_wrong_answer"]),
                canonical_occurrence_rank=_optional_int(raw["canonical_occurrence_rank"]),
                occurrence_sample_size=_optional_int(raw["occurrence_sample_size"]),
                question_status=raw["question_status"],
                has_content_head=str(raw["has_content_head"]).lower() in {"1", "t", "true", "yes"},
            )


def _source_from_row(row: sqlite3.Row) -> SourceOccurrence:
    return SourceOccurrence(**dict(row))


def _expected_groups(connection: sqlite3.Connection) -> dict[tuple[str, str], list[SourceOccurrence]]:
    result: dict[tuple[str, str], list[SourceOccurrence]] = {}
    rows = connection.execute(
        "select * from expected_occurrences order by source_dataset_id, source_question_id, source_occurrence_question_key"
    )
    for row in rows:
        item = _source_from_row(row)
        result.setdefault((item.source_dataset_id, item.source_question_id), []).append(item)
    return result


def _runtime_groups(rows: Iterable[RuntimeOccurrence]) -> dict[tuple[str, str], list[RuntimeOccurrence]]:
    result: dict[tuple[str, str], list[RuntimeOccurrence]] = {}
    for row in rows:
        result.setdefault((row.source_dataset_id, row.source_question_id), []).append(row)
    for group in result.values():
        group.sort(key=lambda item: item.occurrence_id)
    return result


def _plan_values(source: SourceOccurrence, occurrence_id: str, question_id: str, rank: int) -> dict[str, object]:
    return {
        "occurrence_id": occurrence_id, "question_id": question_id,
        "source_question_id": source.source_question_id,
        "source_dataset_id": source.source_dataset_id,
        "source_paper_row_id": source.source_paper_row_id,
        "source_paper_key": source.source_paper_key,
        "source_occurrence_question_key": source.source_occurrence_question_key,
        "source_occurrence_paper_key": source.source_occurrence_paper_key,
        "source_path": source.source_path, "cls": source.cls,
        "province": source.province,
        "occurrence_paper_title": source.occurrence_paper_title,
        "paper_title": source.paper_title, "year": source.year,
        "number": source.number, "section": source.section,
        "occurrence_answer": source.occurrence_answer,
        "occurrence_correct_ratio": source.occurrence_correct_ratio,
        "occurrence_total_count": source.occurrence_total_count,
        "occurrence_easy_wrong": source.occurrence_easy_wrong,
        "occurrence_most_wrong_answer": source.occurrence_most_wrong_answer,
        "canonical_occurrence_rank": rank,
        "occurrence_sample_size": source.occurrence_sample_size,
    }


def _runtime_plan_values(row: RuntimeOccurrence) -> dict[str, object]:
    values = {key: getattr(row, key) for key in PLAN_COLUMNS}
    if values["occurrence_most_wrong_answer"] is not None:
        values["occurrence_most_wrong_answer"] = _optional_json_text(
            values["occurrence_most_wrong_answer"]
        )
    return values


def _normalized_plan_values(values: dict[str, object]) -> dict[str, object]:
    normalized = dict(values)
    for key in ("year", "number", "occurrence_total_count", "canonical_occurrence_rank", "occurrence_sample_size"):
        normalized[key] = _optional_int(normalized[key])
    ratio = _optional_float(normalized["occurrence_correct_ratio"])
    normalized["occurrence_correct_ratio"] = (
        format(ratio, ".10g") if ratio is not None else None
    )
    normalized["occurrence_most_wrong_answer"] = _optional_json_text(
        normalized["occurrence_most_wrong_answer"]
    )
    for key in PLAN_COLUMNS:
        if key not in {
            "year", "number", "occurrence_correct_ratio", "occurrence_total_count",
            "canonical_occurrence_rank", "occurrence_sample_size",
            "occurrence_most_wrong_answer",
        }:
            normalized[key] = _optional_text(normalized[key])
    return normalized


def build_plan(sources: Path, runtime_csv: Path, output: Path) -> dict[str, object]:
    source_connection = _connect(sources)
    expected = _expected_groups(source_connection)
    source_meta = json.loads(source_connection.execute("select value from metadata where key='source'").fetchone()[0])
    runtime_rows = list(read_runtime_csv(runtime_csv))
    existing_quarantine = [
        row for row in runtime_rows if row.source_dataset_id == QUARANTINE_DATASET
    ]
    active_runtime_rows = [
        row for row in runtime_rows if row.source_dataset_id in EXPECTED_COUNTS
    ]
    runtime = _runtime_groups(active_runtime_rows)
    if set(runtime) - set(expected):
        raise RecoveryError(f"runtime contains source IDs absent from source: {len(set(runtime) - set(expected))}")
    missing_source_ids = sorted(set(expected) - set(runtime))
    if missing_source_ids:
        raise RecoveryError(f"SOURCE_QUESTION_UNMAPPED: {len(missing_source_ids)} source IDs")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    connection = _connect(output)
    connection.execute(
        "create table plan (" + ",".join(f"{name} text" for name in PLAN_COLUMNS) + ", action text not null, primary key(occurrence_id))"
    )
    connection.execute(
        "create table quarantine (occurrence_id text primary key, question_id text not null, "
        "source_question_id text not null, source_dataset_id text not null, "
        "quarantine_dataset_id text not null, reason text not null)"
    )
    connection.execute("create table metadata (key text primary key, value text not null)")
    counts = {
        dataset: {"expected": 0, "existing": 0, "updated": 0, "inserted": 0, "deliveryIneligibleSourceQuestions": 0}
        for dataset in EXPECTED_COUNTS
    }
    digest = sha256()
    existing_occurrence_ids = {row.occurrence_id for row in runtime_rows}
    surplus_rows: list[RuntimeOccurrence] = list(existing_quarantine)
    for row in existing_quarantine:
        if (SYDW_DATASET, row.source_question_id) not in expected:
            raise RecoveryError(
                f"quarantine contains a source ID absent from sydw source: {row.source_question_id}"
            )
    for key in sorted(expected):
        source_group = expected[key]
        runtime_group = runtime[key]
        expected_by_occurrence_key = {
            row.source_occurrence_question_key: row for row in source_group
        }
        exact_matches: dict[str, RuntimeOccurrence] = {}
        unmatched_runtime: list[RuntimeOccurrence] = []
        for row in runtime_group:
            occurrence_key = row.source_occurrence_question_key
            if occurrence_key in expected_by_occurrence_key:
                if occurrence_key in exact_matches:
                    raise RecoveryError(f"duplicate runtime source occurrence key {occurrence_key}")
                exact_matches[str(occurrence_key)] = row
            else:
                unmatched_runtime.append(row)
        unmatched_source = [
            row for row in source_group
            if row.source_occurrence_question_key not in exact_matches
        ]
        if len(unmatched_runtime) > len(unmatched_source):
            if key[0] != SYDW_DATASET:
                raise RecoveryError(f"runtime has {len(unmatched_runtime) - len(unmatched_source)} surplus occurrences for {key}")
            # Pair a deterministic prefix with the source; retain every excess
            # row by moving only its dataset identity into duplicate quarantine.
            unmatched_runtime.sort(key=lambda item: item.occurrence_id)
            surplus_rows.extend(unmatched_runtime[len(unmatched_source):])
            unmatched_runtime = unmatched_runtime[:len(unmatched_source)]
        fallback_matches = {
            source.source_occurrence_question_key: row
            for source, row in zip(unmatched_source, sorted(unmatched_runtime, key=lambda item: item.occurrence_id))
        }
        active_rows = [row for row in runtime_group if row.question_status == "active"]
        if not active_rows:
            raise RecoveryError(f"SOURCE_QUESTION_UNMAPPED: no active question for {key}")
        usable_rows = [row for row in active_rows if row.usable]
        representative = sorted(usable_rows or active_rows, key=lambda row: row.question_id)[0]
        if not usable_rows:
            counts[key[0]]["deliveryIneligibleSourceQuestions"] += 1
        for index, source in enumerate(source_group):
            counts[key[0]]["expected"] += 1
            old = exact_matches.get(source.source_occurrence_question_key) or fallback_matches.get(source.source_occurrence_question_key)
            if old is not None:
                occurrence_id, question_id, action = old.occurrence_id, old.question_id, "unchanged"
                counts[key[0]]["existing"] += 1
            else:
                occurrence_id = _stable_id("qbo", source.source_dataset_id, source.source_occurrence_question_key)
                if occurrence_id in existing_occurrence_ids:
                    raise RecoveryError(f"new occurrence id collision: {occurrence_id}")
                question_id, action = representative.question_id, "insert"
                existing_occurrence_ids.add(occurrence_id)
            values = _plan_values(source, occurrence_id, question_id, index + 1)
            if (
                action == "unchanged"
                and old is not None
                and _normalized_plan_values(values)
                != _normalized_plan_values(_runtime_plan_values(old))
            ):
                action = "update"
            counts[key[0]]["inserted" if action == "insert" else "updated" if action == "update" else "existing"] += 1 if action != "unchanged" else 0
            row = ["" if values[name] is None else values[name] for name in PLAN_COLUMNS]
            connection.execute(
                "insert into plan values (" + ",".join("?" for _ in range(len(PLAN_COLUMNS) + 1)) + ")",
                (*row, action),
            )
            # The dry-run identity binds the desired final state, not whether
            # this particular replay reaches it via insert, update, or no-op.
            digest.update((_canonical_json(values) + "\n").encode("utf-8"))
    for dataset, expected_count in EXPECTED_COUNTS.items():
        if counts[dataset]["expected"] != expected_count:
            raise RecoveryError(f"plan count mismatch for {dataset}")
    if counts[QBANK_DATASET]["deliveryIneligibleSourceQuestions"] > QBANK_MISSING_HEAD_CEILING:
        raise RecoveryError("qbank delivery-ineligible source questions exceed verified ceiling")
    if counts[SYDW_DATASET]["deliveryIneligibleSourceQuestions"] != 0:
        raise RecoveryError("sydw contains delivery-ineligible source questions")
    if len(surplus_rows) != EXPECTED_SURPLUS_OCCURRENCES:
        raise RecoveryError(
            f"verified surplus occurrence set changed: {len(surplus_rows)} "
            f"!= {EXPECTED_SURPLUS_OCCURRENCES}"
        )
    for row in sorted(surplus_rows, key=lambda item: item.occurrence_id):
        connection.execute(
            "insert into quarantine values (?, ?, ?, ?, ?, ?)",
            (
                row.occurrence_id,
                row.question_id,
                row.source_question_id,
                SYDW_DATASET,
                QUARANTINE_DATASET,
                "source-occurrence-surplus",
            ),
        )
        digest.update(
            (_canonical_json({
                "occurrence_id": row.occurrence_id,
                "question_id": row.question_id,
                "source_question_id": row.source_question_id,
                "source_dataset_id": QUARANTINE_DATASET,
            }) + "\n").encode("utf-8")
        )
    runtime_digest = sha256()
    for row in sorted(runtime_rows, key=lambda item: (item.source_dataset_id, item.source_question_id, item.occurrence_id)):
        runtime_digest.update((_canonical_json(asdict(row)) + "\n").encode("utf-8"))
    report = {
        "schemaVersion": 1,
        "sourceManifestFingerprint": source_meta["sourceManifestFingerprint"],
        "runtimeSnapshotFingerprint": f"sha256:{runtime_digest.hexdigest()}",
        "dryRunFingerprint": f"sha256:{digest.hexdigest()}",
        "datasets": counts,
        "unmappedSourceQuestions": 0,
        "crossExamConflicts": 0,
        "quarantinedSurplusOccurrences": len(surplus_rows),
    }
    connection.execute("insert into metadata values (?, ?)", ("plan", _canonical_json(report)))
    connection.commit()
    connection.close()
    source_connection.close()
    return report


def export_plan(plan: Path, output: Path) -> dict[str, object]:
    connection = _connect(plan)
    report = json.loads(connection.execute("select value from metadata where key='plan'").fetchone()[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    with _open_text(output, "w") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(PLAN_COLUMNS)
        for row in connection.execute("select " + ",".join(PLAN_COLUMNS) + " from plan order by source_dataset_id, source_question_id, source_occurrence_question_key"):
            writer.writerow(["" if value is None else value for value in row])
    connection.close()
    return {**report, "planCsvSha256": f"sha256:{_sha256_file(output)}"}


def export_quarantine(plan: Path, output: Path) -> dict[str, object]:
    connection = _connect(plan)
    report = json.loads(connection.execute("select value from metadata where key='plan'").fetchone()[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    with _open_text(output, "w") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(("occurrence_id", "question_id", "source_question_id", "source_dataset_id", "quarantine_dataset_id", "reason"))
        for row in connection.execute("select * from quarantine order by occurrence_id"):
            writer.writerow(row)
    connection.close()
    return {**report, "quarantineCsvSha256": f"sha256:{_sha256_file(output)}"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-sources")
    build.add_argument("--qbank-root", required=True, type=Path)
    build.add_argument("--sydw-root", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    plan = sub.add_parser("build-plan")
    plan.add_argument("--sources", required=True, type=Path)
    plan.add_argument("--runtime-csv", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    export = sub.add_parser("export-plan")
    export.add_argument("--plan", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    quarantine = sub.add_parser("export-quarantine")
    quarantine.add_argument("--plan", required=True, type=Path)
    quarantine.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "build-sources":
        result = build_sources(args.qbank_root.resolve(), args.sydw_root.resolve(), args.output.resolve())
    elif args.command == "build-plan":
        result = build_plan(args.sources.resolve(), args.runtime_csv.resolve(), args.output.resolve())
    elif args.command == "export-plan":
        result = export_plan(args.plan.resolve(), args.output.resolve())
    else:
        result = export_quarantine(args.plan.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
