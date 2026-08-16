#!/usr/bin/env python3
"""Plan and apply a reversible quarantine for incomplete runtime questions.

The command never edits content, answers, occurrences, assets, releases or user
facts.  ``prepare`` writes an exact ID/status/reason snapshot.  ``apply`` locks
and rechecks the same IDs, then changes only ``product.questions.status`` from
``active`` to ``quarantined``.  A replay is a verified no-op.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = "question-delivery-quarantine-plan-v1"
BATCH_ID = "question-delivery-quarantine-20260813"
DATASETS = ("qbank-import-20260801", "sydw-public-import-20260801")
EXPECTED_TOTAL = 273
EXPECTED_MISSING_HEAD = 153
EXPECTED_MISSING_ANSWER = 120
ADVISORY_LOCK_KEY = 0x5144515541520009  # "QDQUAR" + release 009


class QuarantineError(RuntimeError):
    """Raised when the frozen quarantine preconditions do not hold."""


@dataclass(frozen=True, slots=True)
class QuestionFact:
    question_id: str
    status: str
    missing_content_head: bool
    missing_answer: bool
    source_dataset_ids: tuple[str, ...]
    has_assessment: bool = False
    has_exposure: bool = False
    has_favorite: bool = False
    has_eligible_binding: bool = False

    @property
    def reason_code(self) -> str:
        reasons: list[str] = []
        if self.missing_content_head:
            reasons.append("missing_content_head")
        if self.missing_answer:
            reasons.append("missing_answer")
        return "+".join(reasons)

    @property
    def user_reference_count(self) -> int:
        return sum((self.has_assessment, self.has_exposure, self.has_favorite))


@dataclass(frozen=True, slots=True)
class QuarantinePlan:
    rows: tuple[QuestionFact, ...]
    fingerprint: str

    @property
    def counts(self) -> dict[str, int]:
        return {
            "questions": len(self.rows),
            "missingContentHead": sum(row.missing_content_head for row in self.rows),
            "missingAnswer": sum(row.missing_answer for row in self.rows),
            "overlap": sum(
                row.missing_content_head and row.missing_answer for row in self.rows
            ),
            "userReferencedQuestions": sum(
                row.user_reference_count > 0 for row in self.rows
            ),
            "eligibleBindingQuestions": sum(
                row.has_eligible_binding for row in self.rows
            ),
        }


PLAN_CANDIDATE_SQL = """
with scoped as (
  select
    question.question_id,
    question.status,
    question.answer,
    array_agg(distinct occurrence.source_dataset_id
              order by occurrence.source_dataset_id) as source_dataset_ids
  from product.questions question
  join product.question_occurrences occurrence
    on occurrence.question_id = question.question_id
   and occurrence.source_dataset_id = any(%s)
  where question.status = 'active'
  group by question.question_id, question.status, question.answer
), facts as (
  select
    scoped.*,
    not exists (
      select 1 from product.question_content_heads head
      where head.question_id = scoped.question_id
    ) as missing_content_head,
    nullif(btrim(scoped.answer), '') is null as missing_answer
  from scoped
)
select
  facts.question_id,
  facts.status,
  facts.missing_content_head,
  facts.missing_answer,
  facts.source_dataset_ids,
  exists (
    select 1 from learning.xingce_assessments assessment
    where assessment.question_id = facts.question_id
  ) as has_assessment,
  exists (
    select 1 from learning.xingce_question_exposures exposure
    where exposure.question_id = facts.question_id
  ) as has_exposure,
  exists (
    select 1 from learning.xingce_question_favorite_states favorite
    where favorite.question_id = facts.question_id
  ) as has_favorite,
  exists (
    select 1
    from product.knowledge_map_question_bindings binding
    join product.knowledge_map_releases release
      on release.release_id = binding.release_id
     and release.status = 'active'
    where binding.question_id = facts.question_id
      and binding.delivery_eligible
  ) as has_eligible_binding
from facts
where facts.missing_content_head or facts.missing_answer
order by facts.question_id
"""


PLANNED_QUESTION_SQL = """
select
  question.question_id,
  question.status,
  not exists (
    select 1 from product.question_content_heads head
    where head.question_id = question.question_id
  ) as missing_content_head,
  nullif(btrim(question.answer), '') is null as missing_answer,
  coalesce((
    select array_agg(distinct occurrence.source_dataset_id
                     order by occurrence.source_dataset_id)
    from product.question_occurrences occurrence
    where occurrence.question_id = question.question_id
      and occurrence.source_dataset_id = any(%s)
  ), array[]::text[]) as source_dataset_ids,
  exists (
    select 1 from learning.xingce_assessments assessment
    where assessment.question_id = question.question_id
  ) as has_assessment,
  exists (
    select 1 from learning.xingce_question_exposures exposure
    where exposure.question_id = question.question_id
  ) as has_exposure,
  exists (
    select 1 from learning.xingce_question_favorite_states favorite
    where favorite.question_id = question.question_id
  ) as has_favorite,
  exists (
    select 1
    from product.knowledge_map_question_bindings binding
    join product.knowledge_map_releases release
      on release.release_id = binding.release_id
     and release.status = 'active'
    where binding.question_id = question.question_id
      and binding.delivery_eligible
  ) as has_eligible_binding
from product.questions question
where question.question_id = any(%s)
order by question.question_id
"""


UPDATE_SQL = """
update product.questions
set status = 'quarantined'
where question_id = any(%s)
  and status = 'active'
"""


RUNTIME_IDENTITY_SQL = """
select
  current_database() as database_name,
  current_setting('server_version_num') as server_version_num,
  (select version_num from alembic_version limit 1) as alembic_version,
  (select system_identifier::text from pg_control_system()) as system_identifier
"""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_payload(row: QuestionFact) -> dict[str, Any]:
    return {
        "questionId": row.question_id,
        "status": row.status,
        "missingContentHead": row.missing_content_head,
        "missingAnswer": row.missing_answer,
        "sourceDatasetIds": list(row.source_dataset_ids),
        "hasAssessment": row.has_assessment,
        "hasExposure": row.has_exposure,
        "hasFavorite": row.has_favorite,
        "hasEligibleBinding": row.has_eligible_binding,
    }


def plan_fingerprint(rows: Sequence[QuestionFact]) -> str:
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "datasets": list(DATASETS),
        "expected": {
            "questions": EXPECTED_TOTAL,
            "missingContentHead": EXPECTED_MISSING_HEAD,
            "missingAnswer": EXPECTED_MISSING_ANSWER,
        },
        "rows": [_row_payload(row) for row in sorted(rows, key=lambda item: item.question_id)],
    }
    return sha256(_canonical_json(payload)).hexdigest()


def build_plan(rows: Iterable[QuestionFact], *, allowed_statuses: set[str]) -> QuarantinePlan:
    ordered = tuple(sorted(rows, key=lambda row: row.question_id))
    if len({row.question_id for row in ordered}) != len(ordered):
        raise QuarantineError("duplicate question_id in quarantine candidate set")
    if len(ordered) != EXPECTED_TOTAL:
        raise QuarantineError(
            f"expected {EXPECTED_TOTAL} questions, observed {len(ordered)}"
        )
    invalid_statuses = sorted({row.status for row in ordered} - allowed_statuses)
    if invalid_statuses:
        raise QuarantineError(f"unexpected question statuses: {invalid_statuses}")
    if any(not row.reason_code for row in ordered):
        raise QuarantineError("planned question without a quarantine reason")
    if any(not row.source_dataset_ids for row in ordered):
        raise QuarantineError("planned question without an authorized source dataset")
    if any(set(row.source_dataset_ids) - set(DATASETS) for row in ordered):
        raise QuarantineError("planned question belongs to an unauthorized dataset")

    plan = QuarantinePlan(rows=ordered, fingerprint=plan_fingerprint(ordered))
    counts = plan.counts
    expected = {
        "questions": EXPECTED_TOTAL,
        "missingContentHead": EXPECTED_MISSING_HEAD,
        "missingAnswer": EXPECTED_MISSING_ANSWER,
        "overlap": 0,
        "userReferencedQuestions": 0,
        "eligibleBindingQuestions": 0,
    }
    if counts != expected:
        raise QuarantineError(f"quarantine baseline drift: expected {expected}, got {counts}")
    return plan


def replay_state(rows: Sequence[QuestionFact]) -> str:
    statuses = {row.status for row in rows}
    if statuses == {"active"}:
        return "applied"
    if statuses == {"quarantined"}:
        return "unchanged"
    raise QuarantineError(f"partial/mixed quarantine state: {sorted(statuses)}")


def _fact_from_record(record: Sequence[Any]) -> QuestionFact:
    return QuestionFact(
        question_id=str(record[0]),
        status=str(record[1]),
        missing_content_head=bool(record[2]),
        missing_answer=bool(record[3]),
        source_dataset_ids=tuple(str(value) for value in (record[4] or ())),
        has_assessment=bool(record[5]),
        has_exposure=bool(record[6]),
        has_favorite=bool(record[7]),
        has_eligible_binding=bool(record[8]),
    )


def _connect(dsn: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - operational environment check
        raise QuarantineError("psycopg is required for database commands") from exc
    return psycopg.connect(dsn)


def _runtime_fingerprint(connection: Any) -> str:
    with connection.cursor() as cursor:
        cursor.execute(RUNTIME_IDENTITY_SQL)
        row = cursor.fetchone()
    if row is None:
        raise QuarantineError("runtime identity query returned no row")
    return sha256(
        _canonical_json(
            {
                "database": str(row[0]),
                "serverVersion": str(row[1]),
                "alembicVersion": str(row[2]),
                "systemIdentifier": str(row[3]),
            }
        )
    ).hexdigest()


def _load_active_candidates(connection: Any) -> tuple[QuestionFact, ...]:
    with connection.cursor() as cursor:
        cursor.execute(PLAN_CANDIDATE_SQL, (list(DATASETS),))
        return tuple(_fact_from_record(row) for row in cursor.fetchall())


def _load_planned_questions(
    connection: Any, question_ids: Sequence[str], *, lock: bool = False
) -> tuple[QuestionFact, ...]:
    with connection.cursor() as cursor:
        statement = PLANNED_QUESTION_SQL
        if lock:
            statement += "\nfor update of question"
        cursor.execute(statement, (list(DATASETS), list(question_ids)))
        return tuple(_fact_from_record(row) for row in cursor.fetchall())


def _snapshot_csv(path: Path, rows: Sequence[QuestionFact]) -> None:
    if path.exists():
        raise QuarantineError(f"refusing to overwrite snapshot: {path}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "question_id",
                "original_status",
                "reason_code",
                "missing_content_head",
                "missing_answer",
                "source_dataset_ids",
            ),
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "question_id": row.question_id,
                    "original_status": row.status,
                    "reason_code": row.reason_code,
                    "missing_content_head": str(row.missing_content_head).lower(),
                    "missing_answer": str(row.missing_answer).lower(),
                    "source_dataset_ids": "|".join(row.source_dataset_ids),
                }
            )


def _plan_document(
    *, plan: QuarantinePlan, runtime_fingerprint: str, snapshot_sha256: str
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "batchId": BATCH_ID,
        "generatedAt": datetime.now(UTC).isoformat(),
        "runtimeFingerprint": runtime_fingerprint,
        "planFingerprint": plan.fingerprint,
        "snapshotCsvSha256": snapshot_sha256,
        "counts": plan.counts,
        "rows": [_row_payload(row) for row in plan.rows],
    }


def _load_plan_document(path: Path) -> tuple[dict[str, Any], QuarantinePlan]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != SCHEMA_VERSION:
        raise QuarantineError("unsupported quarantine plan schema")
    if document.get("batchId") != BATCH_ID:
        raise QuarantineError("unexpected quarantine batch identity")
    rows = tuple(
        QuestionFact(
            question_id=str(item["questionId"]),
            status=str(item["status"]),
            missing_content_head=bool(item["missingContentHead"]),
            missing_answer=bool(item["missingAnswer"]),
            source_dataset_ids=tuple(str(value) for value in item["sourceDatasetIds"]),
            has_assessment=bool(item["hasAssessment"]),
            has_exposure=bool(item["hasExposure"]),
            has_favorite=bool(item["hasFavorite"]),
            has_eligible_binding=bool(item["hasEligibleBinding"]),
        )
        for item in document.get("rows", ())
    )
    plan = build_plan(rows, allowed_statuses={"active"})
    if plan.fingerprint != document.get("planFingerprint"):
        raise QuarantineError("quarantine plan fingerprint mismatch")
    return document, plan


def prepare(*, dsn: str, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "pre-question-statuses.csv"
    plan_path = output_dir / "question-delivery-quarantine-plan.json"
    if plan_path.exists():
        raise QuarantineError(f"refusing to overwrite plan: {plan_path}")

    with _connect(dsn) as connection:
        connection.execute("set transaction read only")
        runtime_fingerprint = _runtime_fingerprint(connection)
        plan = build_plan(
            _load_active_candidates(connection), allowed_statuses={"active"}
        )
    _snapshot_csv(snapshot_path, plan.rows)
    document = _plan_document(
        plan=plan,
        runtime_fingerprint=runtime_fingerprint,
        snapshot_sha256=_file_sha256(snapshot_path),
    )
    plan_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "state": "prepared",
        "plan": str(plan_path),
        "snapshot": str(snapshot_path),
        "runtimeFingerprint": runtime_fingerprint,
        "planFingerprint": plan.fingerprint,
        "snapshotCsvSha256": document["snapshotCsvSha256"],
        "counts": plan.counts,
    }


def apply(*, dsn: str, plan_path: Path, snapshot_path: Path) -> dict[str, Any]:
    document, frozen_plan = _load_plan_document(plan_path)
    if _file_sha256(snapshot_path) != document.get("snapshotCsvSha256"):
        raise QuarantineError("status snapshot SHA-256 mismatch")
    question_ids = [row.question_id for row in frozen_plan.rows]

    connection = _connect(dsn)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("select pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            if _runtime_fingerprint(connection) != document.get("runtimeFingerprint"):
                raise QuarantineError("runtime database fingerprint changed")

            current_rows = _load_planned_questions(connection, question_ids, lock=True)
            current_plan = build_plan(
                current_rows, allowed_statuses={"active", "quarantined"}
            )
            normalized_current = tuple(
                QuestionFact(
                    **{
                        **asdict(row),
                        "status": "active",
                    }
                )
                for row in current_plan.rows
            )
            if plan_fingerprint(normalized_current) != frozen_plan.fingerprint:
                raise QuarantineError("planned question reasons or references changed")

            active_candidates = _load_active_candidates(connection)
            unexpected_active = sorted(
                {row.question_id for row in active_candidates} - set(question_ids)
            )
            if unexpected_active:
                raise QuarantineError(
                    f"new active incomplete questions appeared: {unexpected_active[:5]}"
                )

            state = replay_state(current_plan.rows)
            updated = 0
            if state == "applied":
                with connection.cursor() as cursor:
                    cursor.execute(UPDATE_SQL, (question_ids,))
                    updated = int(cursor.rowcount)
                if updated != EXPECTED_TOTAL:
                    raise QuarantineError(
                        f"expected to update {EXPECTED_TOTAL} rows, updated {updated}"
                    )

            post_rows = _load_planned_questions(connection, question_ids)
            post_plan = build_plan(post_rows, allowed_statuses={"quarantined"})
            if replay_state(post_plan.rows) != "unchanged":
                raise QuarantineError("postcondition did not reach quarantined state")
            if _load_active_candidates(connection):
                raise QuarantineError("active incomplete questions remain after apply")

            mutation_fingerprint = sha256(
                _canonical_json(
                    [
                        {"questionId": row.question_id, "status": row.status}
                        for row in post_plan.rows
                    ]
                )
            ).hexdigest()
        return {
            "batchId": BATCH_ID,
            "state": state,
            "matchedQuestions": EXPECTED_TOTAL,
            "updatedQuestions": updated,
            "eligibleBindings": post_plan.counts["eligibleBindingQuestions"],
            "userEvidenceReferences": post_plan.counts["userReferencedQuestions"],
            "mutationFingerprint": mutation_fingerprint,
        }
    finally:
        connection.close()


def verify(*, dsn: str, plan_path: Path, snapshot_path: Path) -> dict[str, Any]:
    document, frozen_plan = _load_plan_document(plan_path)
    if _file_sha256(snapshot_path) != document.get("snapshotCsvSha256"):
        raise QuarantineError("status snapshot SHA-256 mismatch")
    question_ids = [row.question_id for row in frozen_plan.rows]
    with _connect(dsn) as connection:
        connection.execute("set transaction read only")
        if _runtime_fingerprint(connection) != document.get("runtimeFingerprint"):
            raise QuarantineError("runtime database fingerprint changed")
        current = build_plan(
            _load_planned_questions(connection, question_ids),
            allowed_statuses={"quarantined"},
        )
        if replay_state(current.rows) != "unchanged":
            raise QuarantineError("planned questions are not fully quarantined")
        active_candidates = _load_active_candidates(connection)
        if active_candidates:
            raise QuarantineError("active incomplete questions remain")
    return {
        "state": "verified",
        "matchedQuestions": len(current.rows),
        "activeIncompleteQuestions": 0,
        "eligibleBindings": current.counts["eligibleBindingQuestions"],
        "userEvidenceReferences": current.counts["userReferencedQuestions"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "apply", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--dsn", required=True, help="libpq DSN; prefer peer auth")
        if command == "prepare":
            child.add_argument("--output-dir", type=Path, required=True)
        else:
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--snapshot", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(dsn=args.dsn, output_dir=args.output_dir)
        elif args.command == "apply":
            result = apply(dsn=args.dsn, plan_path=args.plan, snapshot_path=args.snapshot)
        else:
            result = verify(dsn=args.dsn, plan_path=args.plan, snapshot_path=args.snapshot)
    except QuarantineError as exc:
        print(json.dumps({"state": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
