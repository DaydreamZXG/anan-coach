#!/usr/bin/env python3
"""Read-only reconciliation for repository question-bank sources and runtime data.

The command has three deliberately separate operations:

* ``build-local`` derives immutable identities from the repository export, the
  A/B 职测 reference archive and the private 综应 source tree;
* ``collect-remote`` resolves the database used by a remote service and streams
  a credential-free catalog from PostgreSQL inside a read-only transaction;
* ``compare`` compares two catalogs and emits the v1 reconciliation contract.

There is no apply mode.  The module contains no database or object-store write
statement, publication switch, upload, restart or service mutation.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sqlite3
import subprocess
import sys
from typing import Iterable, Iterator
from urllib.parse import unquote, urlparse
import zipfile


EXAM_SCOPES = ("guokao", "shengkao", "sydw")
ENTITY_KINDS = ("question", "occurrence", "content_head", "asset")
LOCAL_QBANK_DATASET = "fenbi-final-analysis-dedup-v2-20260613"
RUNTIME_QBANK_DATASET = "qbank-final-analysis-dedup-v2-20260613"
SYDW_AB_DATASET = "sydw-ab-zhice-reference-20260811"
SYDW_PRIVATE_DATASET = "sydw-zongying-ab-private-20260811"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
_IMAGE_SRC_RE = re.compile(r"\bsrc=(?:\\{0,2})[\"']([^\"']+?)(?:\\{0,2})[\"']", re.I)


class ReconciliationError(RuntimeError):
    """Raised when a catalog cannot be trusted for deterministic comparison."""


@dataclass(frozen=True, slots=True)
class Entity:
    examScope: str
    kind: str
    identity: str
    version: str | None = None
    status: str | None = None
    datasetId: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.examScope, self.kind, self.identity)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _version(attributes: dict[str, object]) -> str:
    return f"sha256:{_sha256_json(attributes)}"


def _scope_for_province(province: object) -> str:
    return "guokao" if str(province or "").strip() == "国考" else "shengkao"


def credential_free_database_identity(database_url: str) -> dict[str, object]:
    """Return a database identity that cannot contain a user name or password."""

    parsed = urlparse(database_url)
    database = unquote(parsed.path).strip("/").rsplit("/", 1)[-1]
    if not database:
        raise ReconciliationError("RUNTIME_TARGET_UNKNOWN: database name is missing")
    return {
        "scheme": parsed.scheme.split("+", 1)[0] or "postgresql",
        "host": parsed.hostname or "local-socket",
        "port": parsed.port or 5432,
        "database": database,
    }


def database_fingerprint(identity: dict[str, object]) -> str:
    return f"sha256:{_sha256_json(identity)}"


def database_url_from_env_text(env_text: str) -> str:
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DATABASE_URL":
            return value.strip().strip("\"'")
    raise ReconciliationError("RUNTIME_TARGET_UNKNOWN: DATABASE_URL is missing")


def _load_catalog(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"CATALOG_READ_FAILED: {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("entities"), list):
        raise ReconciliationError(f"CATALOG_READ_FAILED: invalid catalog {path}")
    return value


def _entities(catalog: dict[str, object]) -> dict[tuple[str, str, str], Entity]:
    result: dict[tuple[str, str, str], Entity] = {}
    for raw in catalog.get("entities") or []:
        if not isinstance(raw, dict):
            raise ReconciliationError("MANIFEST_IDENTITY_MISSING: invalid entity")
        entity = Entity(
            examScope=str(raw.get("examScope") or ""),
            kind=str(raw.get("kind") or ""),
            identity=str(raw.get("identity") or ""),
            version=str(raw["version"]) if raw.get("version") is not None else None,
            status=str(raw["status"]) if raw.get("status") is not None else None,
            datasetId=(
                str(raw["datasetId"]) if raw.get("datasetId") is not None else None
            ),
        )
        if entity.examScope not in EXAM_SCOPES or entity.kind not in ENTITY_KINDS:
            raise ReconciliationError(
                f"MANIFEST_IDENTITY_MISSING: invalid scope/kind for {entity.identity!r}"
            )
        if not entity.identity:
            raise ReconciliationError("MANIFEST_IDENTITY_MISSING: empty identity")
        previous = result.get(entity.key)
        if previous is not None and previous != entity:
            raise ReconciliationError(
                f"MANIFEST_IDENTITY_MISSING: conflicting duplicate {entity.key!r}"
            )
        result[entity.key] = entity
    return result


def reconcile_catalogs(
    local_catalog: dict[str, object], runtime_catalog: dict[str, object]
) -> dict[str, object]:
    """Return deterministic details plus the exact v1 output contract."""

    local = _entities(local_catalog)
    runtime = _entities(runtime_catalog)
    local_scopes = set(local_catalog.get("examScopes") or [])
    runtime_scopes = set(runtime_catalog.get("examScopes") or [])
    if local_scopes != set(EXAM_SCOPES) or runtime_scopes != set(EXAM_SCOPES):
        raise ReconciliationError("MANIFEST_IDENTITY_MISSING: all three scopes are required")

    missing_keys = sorted(local.keys() - runtime.keys())
    extra_keys = sorted(runtime.keys() - local.keys())
    mismatch_keys: list[tuple[str, str, str]] = []
    for key in sorted(local.keys() & runtime.keys()):
        expected = local[key]
        actual = runtime[key]
        version_differs = (
            expected.version is not None
            and actual.version is not None
            and expected.version != actual.version
        )
        status_differs = (
            expected.status is not None
            and actual.status is not None
            and expected.status != actual.status
        )
        if version_differs or status_differs:
            mismatch_keys.append(key)

    runtime_questions = {
        entity.key: entity
        for entity in runtime.values()
        if entity.kind == "question"
    }
    quarantined = sum(
        entity.status == "quarantined" for entity in runtime_questions.values()
    )
    withdrawn = sum(entity.status == "withdrawn" for entity in runtime_questions.values())

    def breakdown(keys: Iterable[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for scope, kind, _identity in keys:
            counts.setdefault(scope, {}).setdefault(kind, 0)
            counts[scope][kind] += 1
        return {scope: dict(sorted(kinds.items())) for scope, kinds in sorted(counts.items())}

    local_refs = sorted(str(value) for value in local_catalog.get("manifestRefs") or [])
    active_refs = sorted(str(value) for value in runtime_catalog.get("activeReleaseRefs") or [])
    unclassified = sorted(
        runtime_catalog.get("unclassifiedRuntimeDatasets") or [],
        key=lambda value: str(value.get("datasetId") or ""),
    )
    runtime_db_fingerprint = str(runtime_catalog.get("runtimeDatabaseFingerprint") or "")
    if not runtime_db_fingerprint:
        raise ReconciliationError("RUNTIME_TARGET_UNKNOWN: runtime fingerprint is missing")

    normalized = {
        "runtimeDatabaseFingerprint": runtime_db_fingerprint,
        "activeReleaseRefs": active_refs,
        "unclassifiedRuntimeDatasets": unclassified,
        "localManifestRefs": local_refs,
        "examScopes": list(EXAM_SCOPES),
        "missing": missing_keys,
        "extra": extra_keys,
        "mismatched": mismatch_keys,
        "quarantinedCount": quarantined,
        "withdrawnCount": withdrawn,
    }
    contract = {
        "reconciliationFingerprint": f"sha256:{_sha256_json(normalized)}",
        "missingCount": len(missing_keys),
        "extraCount": len(extra_keys),
        "mismatchCount": len(mismatch_keys),
        "quarantinedCount": quarantined,
        "withdrawnCount": withdrawn,
        "mutationCount": 0,
    }
    return {
        "contract": contract,
        "input": {
            "runtimeDatabaseFingerprint": runtime_db_fingerprint,
            "activeReleaseRefs": active_refs,
            "unclassifiedRuntimeDatasets": unclassified,
            "localManifestRefs": local_refs,
            "examScopes": list(EXAM_SCOPES),
        },
        "breakdown": {
            "missing": breakdown(missing_keys),
            "extra": breakdown(extra_keys),
            "mismatched": breakdown(mismatch_keys),
        },
        "samples": {
            "missing": [list(key) for key in missing_keys[:25]],
            "extra": [list(key) for key in extra_keys[:25]],
            "mismatched": [list(key) for key in mismatch_keys[:25]],
        },
    }


def _pg_unescape(value: str) -> str | None:
    if value == r"\N":
        return None
    result: list[str] = []
    index = 0
    escapes = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
    while index < len(value):
        if value[index] != "\\":
            result.append(value[index])
            index += 1
            continue
        index += 1
        if index >= len(value):
            result.append("\\")
            break
        marker = value[index]
        if marker in escapes:
            result.append(escapes[marker])
            index += 1
        elif marker.isdigit():
            end = index
            while end < min(index + 3, len(value)) and value[end].isdigit():
                end += 1
            result.append(chr(int(value[index:end], 8)))
            index = end
        else:
            result.append(marker)
            index += 1
    return "".join(result)


def iter_dump_rows(dump: Path, table: str, pg_restore: str = "pg_restore") -> Iterator[dict[str, str | None]]:
    """Stream a table from a custom pg_dump without restoring a database."""

    process = subprocess.Popen(
        [pg_restore, "-a", "-t", table, "-f", "-", str(dump)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdout is not None
    columns: list[str] | None = None
    try:
        for line in process.stdout:
            if columns is None:
                if not line.startswith("COPY "):
                    continue
                raw_columns = line.split("(", 1)[1].split(")", 1)[0]
                columns = next(csv.reader(io.StringIO(raw_columns), skipinitialspace=True))
                columns = [column.strip().strip('"') for column in columns]
                continue
            if line == "\\.\n":
                break
            values = [_pg_unescape(value) for value in line.rstrip("\n").split("\t")]
            if len(values) != len(columns):
                raise ReconciliationError(
                    f"CATALOG_READ_FAILED: malformed COPY row in {table}"
                )
            yield dict(zip(columns, values, strict=True))
    finally:
        process.stdout.close()
    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code:
        raise ReconciliationError(
            f"CATALOG_READ_FAILED: pg_restore exited {return_code}: {stderr.strip()}"
        )


def _qbank_assets(asset_refs: str | None) -> set[str]:
    candidates: set[str] = set()
    for source in _IMAGE_SRC_RE.findall(asset_refs or ""):
        source = source.replace(r"\/", "/").replace("\\\\", "\\")
        path = PurePosixPath(source.replace("\\", "/"))
        if path.suffix.casefold() in IMAGE_SUFFIXES and not urlparse(source).scheme:
            candidates.add(path.as_posix().lstrip("/"))
    prefix = (
        "data/question-bank-export/files/source-question-bank/"
        "fenbi-final-analysis-v1-20260613-assets/"
    )
    return {prefix + candidate for candidate in candidates}


def _entity(
    scope: str,
    kind: str,
    identity: str,
    *,
    dataset: str,
    version: str | None = None,
    status: str | None = None,
) -> Entity:
    return Entity(scope, kind, identity, version, status, dataset)


def build_qbank_entities(dump: Path, pg_restore: str = "pg_restore") -> list[Entity]:
    question_meta: dict[str, dict[str, object]] = {}
    for row in iter_dump_rows(dump, "QuestionBankQuestion", pg_restore):
        if row.get("datasetId") != LOCAL_QBANK_DATASET:
            continue
        raw = row.get("raw") or ""
        fingerprint_match = re.search(
            r'"exact_fingerprint"\s*:\s*"([0-9a-f]{64})"', raw
        )
        question_meta[str(row["id"])] = {
            "fingerprint": fingerprint_match.group(1) if fingerprint_match else None,
            "status": "active",
            "assets": _qbank_assets(row.get("assetRefs")),
        }

    result: dict[tuple[str, str, str], Entity] = {}
    for row in iter_dump_rows(dump, "QuestionBankQuestionOccurrence", pg_restore):
        if row.get("datasetId") != LOCAL_QBANK_DATASET:
            continue
        scope = _scope_for_province(row.get("province"))
        source_question_id = str(row["questionId"])
        question_identity = f"{RUNTIME_QBANK_DATASET}:{source_question_id}"
        meta = question_meta.get(source_question_id, {})
        question = _entity(
            scope,
            "question",
            question_identity,
            dataset=RUNTIME_QBANK_DATASET,
            version=(
                f"source:{meta['fingerprint']}" if meta.get("fingerprint") else None
            ),
            status="active",
        )
        result[question.key] = question
        occurrence_attributes = {
            "sourceQuestionId": source_question_id,
            "sourcePaperId": row.get("sourcePaperId"),
            "cls": row.get("cls"),
            "province": row.get("province"),
            "paperTitle": row.get("paperTitle"),
            "year": int(row["year"]) if row.get("year") else None,
            "number": int(row["number"]) if row.get("number") else None,
            "section": row.get("section"),
            "answer": row.get("answer"),
        }
        occurrence = _entity(
            scope,
            "occurrence",
            f"{RUNTIME_QBANK_DATASET}:{row['id']}",
            dataset=RUNTIME_QBANK_DATASET,
            version=_version(occurrence_attributes),
        )
        result[occurrence.key] = occurrence
        head = _entity(
            scope,
            "content_head",
            question_identity,
            dataset=RUNTIME_QBANK_DATASET,
        )
        result[head.key] = head
        for object_key in meta.get("assets") or set():
            asset = _entity(
                scope,
                "asset",
                f"{RUNTIME_QBANK_DATASET}:{object_key}",
                dataset=RUNTIME_QBANK_DATASET,
            )
            result[asset.key] = asset
    return list(result.values())


def _decode_zip_name(value: str) -> str:
    try:
        return value.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _safe_image_ref(value: object) -> str:
    candidate = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.casefold() not in IMAGE_SUFFIXES
    ):
        raise ReconciliationError(f"MANIFEST_IDENTITY_MISSING: unsafe asset {value!r}")
    return path.as_posix()


def _material_image_refs(record: dict[str, object]) -> list[str]:
    material = str(record.get("sourceMaterial") or "")
    return [_safe_image_ref(value) for value in _IMAGE_SRC_RE.findall(material)]


def _ab_section(category: str, number: int) -> str:
    if category == "联考A类":
        if number <= 20:
            return "常识判断"
        if number <= 45:
            return "言语理解与表达"
        if number <= 50:
            return "数量关系"
        if number <= 85:
            return "判断推理"
        return "资料分析"
    if number <= 25:
        return "常识判断"
    if number <= 40:
        return "言语运用"
    if number <= 45:
        return "数学运算"
    if number <= 50:
        return "资料分析"
    if number <= 75:
        return "判断推理"
    return "综合分析"


def _ab_completeness(record: dict[str, object]) -> tuple[str, ...]:
    issues: list[str] = []
    stem = str(record.get("sourceStem") or "").strip()
    options = record.get("sourceOptions") or []
    answer = str(record.get("sourceAnswer") or "").strip()
    category = str(record.get("category") or "")
    number = int(record.get("sourceNumber") or 0)
    section = _ab_section(category, number)
    images = list(record.get("sourceImages") or []) + _material_image_refs(record)
    if not stem:
        issues.append("missing_stem")
    if not options:
        issues.append("missing_options")
    if not answer:
        issues.append("missing_answer")
    if section in {"资料分析", "综合分析"} and not record.get("sourceMaterial"):
        issues.append("missing_shared_material")
    visual_hint = "图" in stem or "图形" in str(record.get("sourceKnowledgePoints") or "")
    if visual_hint and not images:
        issues.append("missing_required_image")
    return tuple(sorted(set(issues)))


def build_sydw_ab_entities(archive: Path) -> list[Entity]:
    try:
        from scripts.import_sydw_ab_reference import (  # type: ignore[import-not-found]
            _group_issues,
            _source_question_id,
            build_plan,
            native_section,
        )
    except ModuleNotFoundError:
        from import_sydw_ab_reference import (  # type: ignore[no-redef]
            _group_issues,
            _source_question_id,
            build_plan,
            native_section,
        )

    records: list[dict[str, object]] = []
    payload_by_ref: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as source:
        names = {_decode_zip_name(info.filename): info for info in source.infolist()}
        for decoded_name, info in sorted(names.items()):
            if not decoded_name.startswith("papers/") or not decoded_name.endswith(".json"):
                continue
            paper = json.loads(source.read(info))
            category = str(paper.get("category") or "")
            if category not in {"联考A类", "联考B类"}:
                continue
            for question in paper.get("questions") or []:
                record = {
                    "sourcePaperFile": decoded_name,
                    "sourcePaperId": paper.get("paperId"),
                    "sourcePaperName": paper.get("paperName"),
                    "category": category,
                    "sourceNumber": question.get("number"),
                    "sourceStem": question.get("content"),
                    "sourceOptions": question.get("options") or [],
                    "sourceAnswer": question.get("answer"),
                    "sourceKnowledgePoints": question.get("knowledgePoints"),
                    "sourceImages": question.get("images") or [],
                    "sourceMaterial": question.get("material"),
                }
                records.append(record)
                for reference in [
                    *record["sourceImages"],
                    *_material_image_refs(record),
                ]:
                    safe = _safe_image_ref(reference)
                    info_for_asset = names.get(safe)
                    if info_for_asset is None:
                        raise ReconciliationError(
                            f"MANIFEST_IDENTITY_MISSING: archive lacks {safe}"
                        )
                    payload_by_ref[safe] = source.read(info_for_asset)

    plan = build_plan(records, payload_by_ref)
    group_issues = {
        question_id: _group_issues(group)
        for question_id, group in plan.new_groups.items()
    }
    result: dict[tuple[str, str, str], Entity] = {}
    for record in records:
        paper_id = str(record.get("sourcePaperId") or "")
        number = int(record.get("sourceNumber") or 0)
        source_id = _source_question_id(record)
        identity = f"{SYDW_AB_DATASET}:{source_id}"
        target_id = plan.target_by_source[source_id]
        status = "quarantined" if group_issues.get(target_id, ()) else "active"
        question = _entity(
            "sydw", "question", identity, dataset=SYDW_AB_DATASET, status=status
        )
        result[question.key] = question
        occurrence_attributes = {
            "sourceQuestionId": source_id,
            "sourcePaperId": paper_id,
            "cls": record.get("category"),
            "province": "全国联考",
            "paperTitle": record.get("sourcePaperName"),
            "year": (
                int(match.group(1))
                if (match := re.search(r"(20\d{2})", str(record.get("sourcePaperName") or "")))
                else None
            ),
            "number": number,
            "section": native_section(str(record.get("category") or ""), number),
            "answer": record.get("sourceAnswer"),
        }
        occurrence_id = _stable_id("occ", SYDW_AB_DATASET, paper_id, str(number))
        occurrence = _entity(
            "sydw",
            "occurrence",
            f"{SYDW_AB_DATASET}:{occurrence_id}",
            dataset=SYDW_AB_DATASET,
            version=_version(occurrence_attributes),
        )
        result[occurrence.key] = occurrence
        if status == "active":
            head = _entity(
                "sydw", "content_head", identity, dataset=SYDW_AB_DATASET
            )
            result[head.key] = head
        for reference in [
            *list(record.get("sourceImages") or []),
            *_material_image_refs(record),
        ]:
            safe = _safe_image_ref(reference)
            digest = sha256(payload_by_ref[safe]).hexdigest()
            suffix = PurePosixPath(safe).suffix.casefold()
            object_key = (
                f"data/question-bank-export/files/{SYDW_AB_DATASET}/"
                f"{digest[:2]}/{digest}{suffix}"
            )
            asset = _entity(
                "sydw",
                "asset",
                f"{SYDW_AB_DATASET}:{object_key}",
                dataset=SYDW_AB_DATASET,
            )
            result[asset.key] = asset
    return list(result.values())


def build_sydw_private_entities(source_dir: Path) -> list[Entity]:
    # Reuse the importer as the single parser/identity authority for this source.
    try:
        from scripts.import_sydw_zongying_private import (  # type: ignore[import-not-found]
            _iter_assets,
            _source_question_id,
            build_plan,
        )
    except ModuleNotFoundError:
        from import_sydw_zongying_private import (  # type: ignore[no-redef]
            _iter_assets,
            _source_question_id,
            build_plan,
        )

    plan = build_plan(source_dir)
    result: dict[tuple[str, str, str], Entity] = {}
    for paper, task in plan.tasks:
        source_id = _source_question_id(paper, task)
        question = _entity(
            "sydw",
            "question",
            f"{SYDW_PRIVATE_DATASET}:{source_id}",
            dataset=SYDW_PRIVATE_DATASET,
            status="quarantined",
        )
        result[question.key] = question
        occurrence_id = _stable_id(
            "sydwzyocc", SYDW_PRIVATE_DATASET, paper.paper_id, str(task.order)
        )
        year_match = re.match(r"(20\d{2})", paper.exam)
        occurrence_attributes = {
            "sourceQuestionId": source_id,
            "sourcePaperId": paper.paper_id,
            "cls": f"联考{paper.category}类综应",
            "province": "全国联考",
            "paperTitle": paper.title,
            "year": int(year_match.group(1)) if year_match else None,
            "number": task.order,
            "section": "综合应用能力",
            "answer": None,
        }
        occurrence = _entity(
            "sydw",
            "occurrence",
            f"{SYDW_PRIVATE_DATASET}:{occurrence_id}",
            dataset=SYDW_PRIVATE_DATASET,
            version=_version(occurrence_attributes),
        )
        result[occurrence.key] = occurrence
        for asset_row in _iter_assets(paper, task, plan):
            object_key = str(asset_row["object_key"])
            asset = _entity(
                "sydw",
                "asset",
                f"{SYDW_PRIVATE_DATASET}:{object_key}",
                dataset=SYDW_PRIVATE_DATASET,
            )
            result[asset.key] = asset
    return list(result.values())


def build_local_catalog(
    *, repository_root: Path, sydw_ab_archive: Path, pg_restore: str = "pg_restore"
) -> dict[str, object]:
    dump = repository_root / "data/question-bank-export/db/question-bank-subset-20260702.dump"
    private_source = repository_root / "data/sydw-liankao-zongying"
    if not dump.is_file() or not sydw_ab_archive.is_file() or not private_source.is_dir():
        raise ReconciliationError("MANIFEST_IDENTITY_MISSING: a local source is absent")
    entities = [
        *build_qbank_entities(dump, pg_restore),
        *build_sydw_ab_entities(sydw_ab_archive),
        *build_sydw_private_entities(private_source),
    ]
    private_files = sorted(
        path for path in private_source.rglob("*") if path.is_file()
    )
    private_digest = sha256()
    for path in private_files:
        private_digest.update(path.relative_to(private_source).as_posix().encode("utf-8"))
        private_digest.update(bytes.fromhex(_sha256_file(path)))
    return {
        "schemaVersion": 1,
        "catalogType": "local-manifest",
        "examScopes": list(EXAM_SCOPES),
        "manifestRefs": [
            f"question-bank-subset-20260702.dump@sha256:{_sha256_file(dump)}",
            f"sydw-ab-reference@sha256:{_sha256_file(sydw_ab_archive)}",
            f"sydw-zongying-private@sha256:{private_digest.hexdigest()}",
        ],
        "entities": [asdict(entity) for entity in sorted(entities, key=lambda item: item.key)],
    }


def _open_catalog_database(path: Path, *, replace: bool) -> sqlite3.Connection:
    if replace:
        for candidate in (
            path,
            Path(str(path) + "-wal"),
            Path(str(path) + "-shm"),
        ):
            if candidate.exists():
                candidate.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("pragma journal_mode = wal")
    connection.execute("pragma synchronous = normal")
    connection.execute(
        """
        create table if not exists catalog_metadata (
          key text primary key,
          value text not null
        )
        """
    )
    connection.execute(
        """
        create table if not exists entities (
          exam_scope text not null,
          kind text not null,
          identity text not null,
          version text,
          status text,
          dataset_id text,
          primary key (exam_scope, kind, identity)
        ) without rowid
        """
    )
    return connection


def _set_metadata(connection: sqlite3.Connection, key: str, value: object) -> None:
    connection.execute(
        "insert or replace into catalog_metadata(key, value) values (?, ?)",
        (key, _canonical_json(value)),
    )


def _get_metadata(connection: sqlite3.Connection, key: str) -> object:
    row = connection.execute(
        "select value from catalog_metadata where key = ?", (key,)
    ).fetchone()
    if row is None:
        raise ReconciliationError(f"CATALOG_READ_FAILED: metadata {key!r} is missing")
    return json.loads(str(row[0]))


def _insert_entity(connection: sqlite3.Connection, entity: Entity) -> None:
    previous = connection.execute(
        """
        select version, status, dataset_id from entities
        where exam_scope = ? and kind = ? and identity = ?
        """,
        entity.key,
    ).fetchone()
    values = (entity.version, entity.status, entity.datasetId)
    if previous is not None:
        if tuple(previous) != values:
            raise ReconciliationError(
                f"MANIFEST_IDENTITY_MISSING: conflicting duplicate {entity.key!r}"
            )
        return
    connection.execute(
        """
        insert into entities(
          exam_scope, kind, identity, version, status, dataset_id
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (*entity.key, *values),
    )


def _insert_entity_batch(
    connection: sqlite3.Connection, entities: list[Entity]
) -> None:
    connection.executemany(
        """
        insert or ignore into entities(
          exam_scope, kind, identity, version, status, dataset_id
        ) values (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                entity.examScope,
                entity.kind,
                entity.identity,
                entity.version,
                entity.status,
                entity.datasetId,
            )
            for entity in entities
        ],
    )


def _build_qbank_sqlite(
    connection: sqlite3.Connection, dump: Path, pg_restore: str
) -> None:
    connection.execute(
        "create temp table qbank_meta(question_id text primary key, fingerprint text)"
    )
    connection.execute(
        """
        create temp table qbank_assets(
          question_id text not null,
          object_key text not null,
          primary key(question_id, object_key)
        ) without rowid
        """
    )
    for row in iter_dump_rows(dump, "QuestionBankQuestion", pg_restore):
        if row.get("datasetId") != LOCAL_QBANK_DATASET:
            continue
        raw = row.get("raw") or ""
        fingerprint_match = re.search(
            r'"exact_fingerprint"\s*:\s*"([0-9a-f]{64})"', raw
        )
        question_id = str(row["id"])
        connection.execute(
            "insert into qbank_meta(question_id, fingerprint) values (?, ?)",
            (question_id, fingerprint_match.group(1) if fingerprint_match else None),
        )
        connection.executemany(
            "insert or ignore into qbank_assets(question_id, object_key) values (?, ?)",
            [(question_id, key) for key in sorted(_qbank_assets(row.get("assetRefs")))],
        )

    for index, row in enumerate(
        iter_dump_rows(dump, "QuestionBankQuestionOccurrence", pg_restore), start=1
    ):
        if row.get("datasetId") != LOCAL_QBANK_DATASET:
            continue
        scope = _scope_for_province(row.get("province"))
        source_question_id = str(row["questionId"])
        question_identity = f"{RUNTIME_QBANK_DATASET}:{source_question_id}"
        fingerprint_row = connection.execute(
            "select fingerprint from qbank_meta where question_id = ?",
            (source_question_id,),
        ).fetchone()
        fingerprint = fingerprint_row[0] if fingerprint_row else None
        _insert_entity(
            connection,
            _entity(
                scope,
                "question",
                question_identity,
                dataset=RUNTIME_QBANK_DATASET,
                version=f"source:{fingerprint}" if fingerprint else None,
                status="active",
            ),
        )
        occurrence_attributes = {
            "sourceQuestionId": source_question_id,
            "sourcePaperId": row.get("sourcePaperId"),
            "cls": row.get("cls"),
            "province": row.get("province"),
            "paperTitle": row.get("paperTitle"),
            "year": int(row["year"]) if row.get("year") else None,
            "number": int(row["number"]) if row.get("number") else None,
            "section": row.get("section"),
            "answer": row.get("answer"),
        }
        _insert_entity(
            connection,
            _entity(
                scope,
                "occurrence",
                f"{RUNTIME_QBANK_DATASET}:{row['id']}",
                dataset=RUNTIME_QBANK_DATASET,
                version=_version(occurrence_attributes),
            ),
        )
        _insert_entity(
            connection,
            _entity(
                scope,
                "content_head",
                question_identity,
                dataset=RUNTIME_QBANK_DATASET,
            ),
        )
        for (object_key,) in connection.execute(
            "select object_key from qbank_assets where question_id = ?",
            (source_question_id,),
        ):
            _insert_entity(
                connection,
                _entity(
                    scope,
                    "asset",
                    f"{RUNTIME_QBANK_DATASET}:{object_key}",
                    dataset=RUNTIME_QBANK_DATASET,
                ),
            )
        if index % 2_000 == 0:
            connection.commit()
    connection.execute("drop table qbank_meta")
    connection.execute("drop table qbank_assets")


def build_local_catalog_database(
    *,
    repository_root: Path,
    sydw_ab_archive: Path,
    output: Path,
    pg_restore: str = "pg_restore",
) -> None:
    dump = repository_root / "data/question-bank-export/db/question-bank-subset-20260702.dump"
    private_source = repository_root / "data/sydw-liankao-zongying"
    if not dump.is_file() or not sydw_ab_archive.is_file() or not private_source.is_dir():
        raise ReconciliationError("MANIFEST_IDENTITY_MISSING: a local source is absent")
    connection = _open_catalog_database(output, replace=False)
    try:
        _set_metadata(connection, "schemaVersion", 1)
        _set_metadata(connection, "catalogType", "local-manifest")
        _set_metadata(connection, "examScopes", list(EXAM_SCOPES))
        phase = connection.execute(
            "select value from catalog_metadata where key='qbankPhase'"
        ).fetchone()
        if phase is None or json.loads(str(phase[0])) != "complete":
            connection.execute(
                "delete from entities where dataset_id=?",
                (RUNTIME_QBANK_DATASET,),
            )
            connection.commit()
            _build_qbank_sqlite(connection, dump, pg_restore)
            _set_metadata(connection, "qbankPhase", "complete")
            connection.commit()
        connection.execute(
            "delete from entities where dataset_id in (?, ?)",
            (SYDW_AB_DATASET, SYDW_PRIVATE_DATASET),
        )
        for entity in build_sydw_ab_entities(sydw_ab_archive):
            _insert_entity(connection, entity)
        for entity in build_sydw_private_entities(private_source):
            _insert_entity(connection, entity)
        private_files = sorted(path for path in private_source.rglob("*") if path.is_file())
        private_digest = sha256()
        for path in private_files:
            private_digest.update(
                path.relative_to(private_source).as_posix().encode("utf-8")
            )
            private_digest.update(bytes.fromhex(_sha256_file(path)))
        _set_metadata(
            connection,
            "manifestRefs",
            [
                f"question-bank-subset-20260702.dump@sha256:{_sha256_file(dump)}",
                f"sydw-ab-reference@sha256:{_sha256_file(sydw_ab_archive)}",
                f"sydw-zongying-private@sha256:{private_digest.hexdigest()}",
            ],
        )
        _set_metadata(connection, "mutationCount", 0)
        connection.commit()
    finally:
        connection.close()


RUNTIME_READONLY_SQL = r"""
\set QUIET on
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '10min';
COPY (
WITH scoped_occurrences AS MATERIALIZED (
  SELECT occurrence.*,
    CASE
      WHEN occurrence.source_dataset_id LIKE 'sydw-%' THEN 'sydw'
      WHEN occurrence.source_dataset_id = 'qbank-final-analysis-dedup-v2-20260613'
        THEN CASE WHEN occurrence.province = '国考' THEN 'guokao' ELSE 'shengkao' END
      ELSE NULL
    END AS exam_scope
  FROM product.question_occurrences occurrence
),
question_entities AS (
  SELECT DISTINCT occurrence.exam_scope, 'question'::text AS kind,
    occurrence.source_dataset_id || ':' || occurrence.source_question_id AS identity,
    CASE
      WHEN question.representative_classification->>'exact_fingerprint' IS NOT NULL
        THEN 'source:' || (question.representative_classification->>'exact_fingerprint')
      ELSE NULL
    END AS version,
    question.status, occurrence.source_dataset_id
  FROM scoped_occurrences occurrence
  JOIN product.questions question USING (question_id)
  WHERE occurrence.source_dataset_id IS NOT NULL
    AND occurrence.source_question_id IS NOT NULL
    AND occurrence.exam_scope IS NOT NULL
),
occurrence_entities AS (
  SELECT occurrence.exam_scope, 'occurrence'::text AS kind,
    occurrence.source_dataset_id || ':' || occurrence.occurrence_id AS identity,
    NULL::text AS version, NULL::text AS status, occurrence.source_dataset_id,
    jsonb_build_object(
      'sourceQuestionId', occurrence.source_question_id,
      'sourcePaperId', occurrence.source_paper_key,
      'cls', occurrence.cls,
      'province', occurrence.province,
      'paperTitle', occurrence.paper_title,
      'year', occurrence.year,
      'number', occurrence.number,
      'section', occurrence.section,
      'answer', occurrence.occurrence_answer
    ) AS attributes
  FROM scoped_occurrences occurrence
  WHERE occurrence.source_dataset_id IS NOT NULL
    AND occurrence.exam_scope IS NOT NULL
),
head_entities AS (
  SELECT DISTINCT occurrence.exam_scope, 'content_head'::text AS kind,
    occurrence.source_dataset_id || ':' || occurrence.source_question_id AS identity,
    NULL::text AS version, NULL::text AS status, occurrence.source_dataset_id
  FROM scoped_occurrences occurrence
  JOIN product.question_content_heads head USING (question_id)
  WHERE occurrence.source_dataset_id IS NOT NULL
    AND occurrence.source_question_id IS NOT NULL
    AND occurrence.exam_scope IS NOT NULL
),
asset_entities AS (
  SELECT DISTINCT occurrence.exam_scope, 'asset'::text AS kind,
    occurrence.source_dataset_id || ':' || asset.object_key AS identity,
    NULL::text AS version, NULL::text AS status, occurrence.source_dataset_id
  FROM scoped_occurrences occurrence
  JOIN product.question_assets asset USING (question_id)
  WHERE occurrence.source_dataset_id IS NOT NULL
    AND asset.object_key IS NOT NULL
    AND occurrence.exam_scope IS NOT NULL
),
all_entities AS (
  SELECT exam_scope, kind, identity, version, status, source_dataset_id, NULL::jsonb attributes
    FROM question_entities
  UNION ALL
  SELECT exam_scope, kind, identity, version, status, source_dataset_id, attributes
    FROM occurrence_entities
  UNION ALL
  SELECT exam_scope, kind, identity, version, status, source_dataset_id, NULL::jsonb
    FROM head_entities
  UNION ALL
  SELECT exam_scope, kind, identity, version, status, source_dataset_id, NULL::jsonb
    FROM asset_entities
)
SELECT jsonb_build_object(
  'examScope', exam_scope,
  'kind', kind,
  'identity', identity,
  'version', version,
  'attributes', attributes,
  'status', status,
  'datasetId', source_dataset_id
)::text
FROM all_entities
) TO STDOUT;
ROLLBACK;
"""


RUNTIME_RELEASE_SQL = r"""
\set QUIET on
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';
WITH unclassified AS (
  SELECT occurrence.source_dataset_id,
    count(*) AS occurrence_count,
    count(DISTINCT occurrence.question_id) AS question_count
  FROM product.question_occurrences occurrence
  WHERE occurrence.source_dataset_id NOT LIKE 'sydw-%'
    AND occurrence.source_dataset_id <> 'qbank-final-analysis-dedup-v2-20260613'
  GROUP BY occurrence.source_dataset_id
)
SELECT jsonb_build_object(
  'commercial', coalesce((
    SELECT jsonb_agg(
      'commercial:' || publication_id::text || ':' || publication_digest
      ORDER BY publication_id::text
    ) FROM platform.commercial_data_publication_state WHERE state = 'active'
  ), '[]'::jsonb),
  'knowledge', coalesce((
    SELECT jsonb_agg(
      exam_scope || ':' || track || ':' || release_version || ':' || source_data_version
      || ':' || vocab_version || ':' || source_fingerprint
      ORDER BY exam_scope, track, release_version
    ) FROM product.knowledge_map_releases WHERE status = 'active'
  ), '[]'::jsonb),
  'unclassified', coalesce((
    SELECT jsonb_agg(
      jsonb_build_object(
        'datasetId', source_dataset_id,
        'occurrenceCount', occurrence_count,
        'questionCount', question_count
      ) ORDER BY source_dataset_id
    ) FROM unclassified
  ), '[]'::jsonb)
)::text;
ROLLBACK;
"""


def _run_ssh(
    *, host: str, key: Path | None, command: str, input_text: str | None = None
) -> str:
    argv = ["ssh", "-o", "BatchMode=yes"]
    if key is not None:
        argv.extend(["-i", str(key)])
    argv.extend([host, command])
    completed = subprocess.run(
        argv,
        input=input_text,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise ReconciliationError(
            f"CATALOG_READ_FAILED: ssh exited {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def resolve_remote_database(
    *, host: str, key: Path | None, env_path: str
) -> tuple[str, dict[str, object]]:
    remote_program = (
        "from pathlib import Path\n"
        "from urllib.parse import urlparse,unquote\n"
        "import json,sys\n"
        "value=''\n"
        "for raw in Path(sys.argv[1]).read_text().splitlines():\n"
        " line=raw.strip()\n"
        " if line and not line.startswith('#') and '=' in line:\n"
        "  key,item=line.split('=',1)\n"
        "  if key.strip()=='DATABASE_URL': value=item.strip().strip(chr(34)+chr(39))\n"
        "parsed=urlparse(value)\n"
        "database=unquote(parsed.path).strip('/').rsplit('/',1)[-1]\n"
        "assert database\n"
        "print(json.dumps({'database':database,'scheme':parsed.scheme.split('+',1)[0] or 'postgresql','host':parsed.hostname or 'local-socket','port':parsed.port or 5432},sort_keys=True))\n"
    )
    command = f"python3 -c {shlex.quote(remote_program)} {shlex.quote(env_path)}"
    rendered = _run_ssh(host=host, key=key, command=command)
    try:
        identity = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ReconciliationError("RUNTIME_TARGET_UNKNOWN: invalid remote identity") from exc
    # Host/user/password from DATABASE_URL never leave the remote command.  The
    # externally observed SSH target is the database host identity we retain.
    safe_identity = {
        "serviceHost": host.split("@")[-1],
        "database": identity["database"],
        "transport": "remote-local-socket",
    }
    return str(identity["database"]), safe_identity


def collect_remote_catalog(
    *, host: str, key: Path | None, env_path: str
) -> dict[str, object]:
    database, identity = resolve_remote_database(host=host, key=key, env_path=env_path)
    psql = (
        f"sudo -u postgres psql -X -q -d {shlex.quote(database)} "
        "-v ON_ERROR_STOP=1 -At"
    )
    release_output = _run_ssh(
        host=host, key=key, command=psql, input_text=RUNTIME_RELEASE_SQL
    )
    release_lines = [line for line in release_output.splitlines() if line.startswith("{")]
    if len(release_lines) != 1:
        raise ReconciliationError("CATALOG_READ_FAILED: release catalog is invalid")
    release_payload = json.loads(release_lines[0])
    active_refs = sorted(
        [*release_payload.get("commercial", []), *release_payload.get("knowledge", [])]
    )
    unclassified = sorted(
        release_payload.get("unclassified", []),
        key=lambda value: str(value.get("datasetId") or ""),
    )

    entity_output = _run_ssh(
        host=host, key=key, command=psql, input_text=RUNTIME_READONLY_SQL
    )
    entities: list[dict[str, object]] = []
    for line in entity_output.splitlines():
        if not line.startswith("{"):
            continue
        raw = json.loads(line)
        attributes = raw.pop("attributes", None)
        if raw.get("kind") == "occurrence" and isinstance(attributes, dict):
            raw["version"] = _version(attributes)
        entities.append(raw)
    if not entities:
        raise ReconciliationError("CATALOG_READ_FAILED: runtime catalog is empty")
    return {
        "schemaVersion": 1,
        "catalogType": "runtime-readonly",
        "examScopes": list(EXAM_SCOPES),
        "runtimeDatabaseFingerprint": database_fingerprint(identity),
        "activeReleaseRefs": active_refs,
        "unclassifiedRuntimeDatasets": unclassified,
        "entities": entities,
        "mutationCount": 0,
    }


def collect_remote_catalog_database(
    *, host: str, key: Path | None, env_path: str, output: Path
) -> None:
    database, identity = resolve_remote_database(host=host, key=key, env_path=env_path)
    psql = (
        f"sudo -u postgres psql -X -q -d {shlex.quote(database)} "
        "-v ON_ERROR_STOP=1 -At"
    )
    release_output = _run_ssh(
        host=host, key=key, command=psql, input_text=RUNTIME_RELEASE_SQL
    )
    release_lines = [line for line in release_output.splitlines() if line.startswith("{")]
    if len(release_lines) != 1:
        raise ReconciliationError("CATALOG_READ_FAILED: release catalog is invalid")
    release_payload = json.loads(release_lines[0])
    active_refs = sorted(
        [*release_payload.get("commercial", []), *release_payload.get("knowledge", [])]
    )
    unclassified = sorted(
        release_payload.get("unclassified", []),
        key=lambda value: str(value.get("datasetId") or ""),
    )

    connection = _open_catalog_database(output, replace=True)
    try:
        _set_metadata(connection, "schemaVersion", 1)
        _set_metadata(connection, "catalogType", "runtime-readonly")
        _set_metadata(connection, "examScopes", list(EXAM_SCOPES))
        _set_metadata(
            connection, "runtimeDatabaseFingerprint", database_fingerprint(identity)
        )
        _set_metadata(connection, "activeReleaseRefs", active_refs)
        _set_metadata(connection, "unclassifiedRuntimeDatasets", unclassified)
        _set_metadata(connection, "mutationCount", 0)

        argv = ["ssh", "-o", "BatchMode=yes"]
        if key is not None:
            argv.extend(["-i", str(key)])
        pipeline = f"{psql} | gzip -1 -c"
        argv.extend([host, f"bash -o pipefail -c {shlex.quote(pipeline)}"])
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(RUNTIME_READONLY_SQL.encode("utf-8"))
        process.stdin.close()
        count = 0
        pending: list[Entity] = []
        compressed = gzip.GzipFile(fileobj=process.stdout, mode="rb")
        decoded = io.TextIOWrapper(compressed, encoding="utf-8")
        for line in decoded:
            if not line.startswith("{"):
                continue
            raw = json.loads(line)
            attributes = raw.pop("attributes", None)
            if raw.get("kind") == "occurrence" and isinstance(attributes, dict):
                raw["version"] = _version(attributes)
            pending.append(
                Entity(
                    examScope=str(raw.get("examScope") or ""),
                    kind=str(raw.get("kind") or ""),
                    identity=str(raw.get("identity") or ""),
                    version=(
                        str(raw["version"]) if raw.get("version") is not None else None
                    ),
                    status=str(raw["status"]) if raw.get("status") is not None else None,
                    datasetId=(
                        str(raw["datasetId"])
                        if raw.get("datasetId") is not None
                        else None
                    ),
                )
            )
            count += 1
            if len(pending) == 5_000:
                _insert_entity_batch(connection, pending)
                pending.clear()
                connection.commit()
        decoded.close()
        stderr = (
            process.stderr.read().decode("utf-8", errors="replace")
            if process.stderr is not None
            else ""
        )
        return_code = process.wait()
        if return_code:
            raise ReconciliationError(
                f"CATALOG_READ_FAILED: ssh exited {return_code}: {stderr.strip()}"
            )
        if not count:
            raise ReconciliationError("CATALOG_READ_FAILED: runtime catalog is empty")
        _insert_entity_batch(connection, pending)
        connection.commit()
    finally:
        connection.close()


def _sqlite_keys(connection: sqlite3.Connection) -> Iterator[tuple[str, str, str]]:
    for row in connection.execute(
        "select exam_scope, kind, identity from entities order by 1,2,3"
    ):
        yield (str(row[0]), str(row[1]), str(row[2]))


def _diff_table(
    output: sqlite3.Connection,
    table: str,
    left: sqlite3.Connection,
    right: sqlite3.Connection,
) -> int:
    output.execute(
        f"create table {table}(exam_scope text, kind text, identity text, "
        "primary key(exam_scope, kind, identity)) without rowid"
    )
    count = 0
    left_iter = _sqlite_keys(left)
    right_iter = _sqlite_keys(right)
    left_key = next(left_iter, None)
    right_key = next(right_iter, None)
    while left_key is not None:
        if right_key is None or left_key < right_key:
            output.execute(f"insert into {table} values (?, ?, ?)", left_key)
            count += 1
            left_key = next(left_iter, None)
        elif left_key == right_key:
            left_key = next(left_iter, None)
            right_key = next(right_iter, None)
        else:
            right_key = next(right_iter, None)
    return count


def reconcile_catalog_databases(
    local_path: Path, runtime_path: Path, detail_path: Path
) -> dict[str, object]:
    local = _open_catalog_database(local_path, replace=False)
    runtime = _open_catalog_database(runtime_path, replace=False)
    if detail_path.exists():
        detail_path.unlink()
    detail = sqlite3.connect(detail_path)
    try:
        if set(_get_metadata(local, "examScopes")) != set(EXAM_SCOPES):
            raise ReconciliationError("MANIFEST_IDENTITY_MISSING: all three scopes are required")
        if set(_get_metadata(runtime, "examScopes")) != set(EXAM_SCOPES):
            raise ReconciliationError("MANIFEST_IDENTITY_MISSING: all three scopes are required")
        missing_count = _diff_table(detail, "missing", local, runtime)
        extra_count = _diff_table(detail, "extra", runtime, local)
        detail.execute(
            """
            create table mismatched(
              exam_scope text, kind text, identity text,
              expected_version text, actual_version text,
              expected_status text, actual_status text,
              primary key(exam_scope, kind, identity)
            ) without rowid
            """
        )
        mismatch_count = 0
        for row in local.execute(
            "select exam_scope, kind, identity, version, status from entities order by 1,2,3"
        ):
            actual = runtime.execute(
                """
                select version, status from entities
                where exam_scope=? and kind=? and identity=?
                """,
                row[:3],
            ).fetchone()
            if actual is None:
                continue
            version_differs = row[3] is not None and actual[0] is not None and row[3] != actual[0]
            status_differs = row[4] is not None and actual[1] is not None and row[4] != actual[1]
            if version_differs or status_differs:
                detail.execute(
                    "insert into mismatched values (?, ?, ?, ?, ?, ?, ?)",
                    (*row[:3], row[3], actual[0], row[4], actual[1]),
                )
                mismatch_count += 1
        quarantined_count = int(
            runtime.execute(
                "select count(*) from entities where kind='question' and status='quarantined'"
            ).fetchone()[0]
        )
        withdrawn_count = int(
            runtime.execute(
                "select count(*) from entities where kind='question' and status='withdrawn'"
            ).fetchone()[0]
        )
        detail.commit()

        runtime_fingerprint = str(_get_metadata(runtime, "runtimeDatabaseFingerprint"))
        active_refs = sorted(str(value) for value in _get_metadata(runtime, "activeReleaseRefs"))
        unclassified = sorted(
            _get_metadata(runtime, "unclassifiedRuntimeDatasets"),
            key=lambda value: str(value.get("datasetId") or ""),
        )
        manifest_refs = sorted(str(value) for value in _get_metadata(local, "manifestRefs"))
        diff_digest = sha256()
        for table in ("missing", "extra", "mismatched"):
            for row in detail.execute(f"select * from {table} order by 1,2,3"):
                diff_digest.update(_canonical_json(list(row)).encode("utf-8"))
                diff_digest.update(b"\n")
        normalized = {
            "runtimeDatabaseFingerprint": runtime_fingerprint,
            "activeReleaseRefs": active_refs,
            "unclassifiedRuntimeDatasets": unclassified,
            "localManifestRefs": manifest_refs,
            "examScopes": list(EXAM_SCOPES),
            "diffDigest": diff_digest.hexdigest(),
            "missingCount": missing_count,
            "extraCount": extra_count,
            "mismatchCount": mismatch_count,
            "quarantinedCount": quarantined_count,
            "withdrawnCount": withdrawn_count,
        }

        def breakdown(table: str) -> dict[str, dict[str, int]]:
            rows = detail.execute(
                f"select exam_scope,kind,count(*) from {table} group by 1,2 order by 1,2"
            )
            result: dict[str, dict[str, int]] = {}
            for scope, kind, count in rows:
                result.setdefault(str(scope), {})[str(kind)] = int(count)
            return result

        return {
            "contract": {
                "reconciliationFingerprint": f"sha256:{_sha256_json(normalized)}",
                "missingCount": missing_count,
                "extraCount": extra_count,
                "mismatchCount": mismatch_count,
                "quarantinedCount": quarantined_count,
                "withdrawnCount": withdrawn_count,
                "mutationCount": 0,
            },
            "input": {
                "runtimeDatabaseFingerprint": runtime_fingerprint,
                "activeReleaseRefs": active_refs,
                "unclassifiedRuntimeDatasets": unclassified,
                "localManifestRefs": manifest_refs,
                "examScopes": list(EXAM_SCOPES),
            },
            "breakdown": {
                "missing": breakdown("missing"),
                "extra": breakdown("extra"),
                "mismatched": breakdown("mismatched"),
            },
            "detailDatabase": str(detail_path),
        }
    finally:
        local.close()
        runtime.close()
        detail.close()


def _write_json(value: object, output: Path | None) -> None:
    if output is None:
        json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
        sys.stdout.write("\n")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False, sort_keys=True, indent=2)
            target.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    local = subparsers.add_parser("build-local")
    local.add_argument("--repository-root", type=Path, required=True)
    local.add_argument("--sydw-ab-archive", type=Path, required=True)
    local.add_argument("--pg-restore", default="pg_restore")
    local.add_argument("--output", type=Path, required=True)

    remote = subparsers.add_parser("collect-remote")
    remote.add_argument("--ssh-host", required=True)
    remote.add_argument("--ssh-key", type=Path)
    remote.add_argument("--database-env", default="/etc/gongkao-dazi/db.env")
    remote.add_argument("--output", type=Path, required=True)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--local-manifest", type=Path, required=True)
    compare.add_argument("--runtime-catalog", type=Path, required=True)
    compare.add_argument("--detail-database", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "build-local":
        build_local_catalog_database(
            repository_root=args.repository_root.resolve(),
            sydw_ab_archive=args.sydw_ab_archive.resolve(),
            output=args.output.resolve(),
            pg_restore=args.pg_restore,
        )
        return
    elif args.command == "collect-remote":
        collect_remote_catalog_database(
            host=args.ssh_host,
            key=args.ssh_key.resolve() if args.ssh_key else None,
            env_path=args.database_env,
            output=args.output.resolve(),
        )
        return
    else:
        value = reconcile_catalog_databases(
            args.local_manifest.resolve(),
            args.runtime_catalog.resolve(),
            args.detail_database.resolve(),
        )
    _write_json(value, args.output)


if __name__ == "__main__":
    main()
