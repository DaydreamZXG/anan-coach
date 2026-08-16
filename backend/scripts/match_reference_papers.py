#!/usr/bin/env python3
"""Match reference-paper questions to an existing product question bank.

This tool is deliberately read-only.  It never inserts or updates database
rows.  The workflow has two small steps so a large question bank can be
scanned next to PostgreSQL without uploading a full reference archive:

1. ``manifest`` extracts selected papers into a compact gzip JSONL manifest.
2. ``match-psql`` streams existing questions through ``psql`` and writes exact
   normalized-stem matches plus an audit summary.
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import gzip
import html
import json
import os
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[\s\u00a0]+")
_PUNCTUATION_RE = re.compile(
    r"[“”‘’\"'，。！？；：、（）()【】\[\]<>《》…—_·,.!?;:\-]"
)


def normalize_stem(value: object) -> str:
    """Return a conservative comparison key for a question stem."""

    text = html.unescape(_HTML_TAG_RE.sub("", str(value or "")))
    text = unicodedata.normalize("NFKC", text)
    text = _SPACE_RE.sub("", text)
    return _PUNCTUATION_RE.sub("", text)


def normalize_options(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    normalized: list[str] = []
    for option in value:
        if isinstance(option, dict):
            option = option.get("content") or option.get("text") or ""
        normalized.append(normalize_stem(option))
    return tuple(item for item in normalized if item)


def candidate_evidence(
    reference: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    source_answer = normalize_stem(reference.get("sourceAnswer"))
    existing_answer = normalize_stem(candidate.get("answer"))
    source_options = normalize_options(reference.get("sourceOptions"))
    existing_options = normalize_options(candidate.get("optionTexts"))
    source_answer_content = _answer_content(source_answer, source_options)
    existing_answer_content = _answer_content(existing_answer, existing_options)
    return {
        "answerComparable": bool(source_answer and existing_answer),
        "answerAgrees": bool(
            source_answer and existing_answer and source_answer == existing_answer
        ),
        "optionsComparable": bool(source_options and existing_options),
        "optionsAgree": bool(
            source_options and existing_options and source_options == existing_options
        ),
        "optionSetAgrees": bool(
            source_options
            and existing_options
            and sorted(source_options) == sorted(existing_options)
        ),
        "answerContentComparable": bool(
            source_answer_content and existing_answer_content
        ),
        "answerContentAgrees": bool(
            source_answer_content
            and existing_answer_content
            and source_answer_content == existing_answer_content
        ),
    }


def _answer_content(answer: str, options: tuple[str, ...]) -> str:
    if len(answer) != 1 or not answer.isalpha():
        return ""
    index = ord(answer.upper()) - ord("A")
    return options[index] if 0 <= index < len(options) else ""


def decode_zip_name(value: str) -> str:
    """Repair UTF-8 ZIP names whose general-purpose UTF-8 flag was omitted."""

    try:
        return value.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _open_jsonl(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8")
    return path.open(mode, encoding="utf-8")


def build_manifest(
    *, archive: Path, output: Path, categories: set[str]
) -> dict[str, Any]:
    paper_count = 0
    question_count = 0
    with zipfile.ZipFile(archive) as source, _open_jsonl(output, "wt") as target:
        for info in source.infolist():
            decoded_name = decode_zip_name(info.filename)
            if not decoded_name.startswith("papers/") or not decoded_name.endswith(
                ".json"
            ):
                continue
            paper = json.loads(source.read(info))
            category = str(paper.get("category") or "")
            if category not in categories:
                continue
            paper_count += 1
            for question in paper.get("questions") or []:
                record = {
                    "sourcePaperFile": decoded_name,
                    "sourcePaperId": paper.get("paperId"),
                    "sourcePaperName": paper.get("paperName"),
                    "sourcePaperKind": paper.get("source"),
                    "category": category,
                    "sourceNumber": question.get("number"),
                    "sourceQuestionType": question.get("type"),
                    "sourceStem": question.get("content"),
                    "sourceOptions": question.get("options") or [],
                    "sourceAnswer": question.get("answer"),
                    "sourceExplanation": question.get("explanation"),
                    "sourceExplanationNew": question.get("explanationNew"),
                    "sourceKnowledgePoints": question.get("knowledgePoints"),
                    "sourceImages": question.get("images") or [],
                    "sourceMaterial": question.get("material"),
                    "sourceCorrectRate": question.get("correctRate"),
                    "sourceWrongOption": question.get("wrongOption"),
                }
                target.write(json.dumps(record, ensure_ascii=False) + "\n")
                question_count += 1
    return {
        "archive": str(archive),
        "output": str(output),
        "categories": sorted(categories),
        "paperCount": paper_count,
        "questionCount": question_count,
    }


def iter_manifest(path: Path) -> Iterable[dict[str, Any]]:
    with _open_jsonl(path, "rt") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def iter_psql_questions(
    database: str, database_url: str | None
) -> Iterable[dict[str, Any]]:
    query = """
        select json_build_object(
          'questionId', question_id,
          'sourceQuestionId', representative_source_question_id,
          'moduleName', module_name,
          'questionType', question_type,
          'stem', stem,
          'optionTexts', coalesce(
            (
              select array_agg(
                coalesce(nullif(option.option_text, ''), option.option_html)
                order by option.option_index
              )
              from product.question_options option
              where option.question_id = product.questions.question_id
            ),
            option_texts,
            '{}'::text[]
          ),
          'answer', answer,
          'materialId', material_id,
          'visualRequired', visual_required,
          'firstYear', first_year,
          'latestYear', latest_year,
          'occurrenceCount', occurrence_count,
          'hasExplanation', exists (
            select 1
            from product.question_explanations explanation
            where explanation.question_id = product.questions.question_id
              and coalesce(
                nullif(explanation.stored_analysis, ''),
                nullif(explanation.original_analysis, '')
              ) is not null
          )
        )::text
        from product.questions
    """
    if database_url:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "MATCH_DATABASE_URL requires psycopg; run with the backend virtualenv"
            ) from exc
        with psycopg.connect(database_url) as connection:
            connection.execute("set transaction read only")
            with connection.cursor(name="reference_paper_question_scan") as cursor:
                cursor.execute(query)
                while rows := cursor.fetchmany(1000):
                    for row in rows:
                        yield json.loads(row[0])
        return

    process = subprocess.Popen(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database, "-At", "-c", query],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            if line.strip():
                yield json.loads(line)
    finally:
        process.stdout.close()
    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"psql exited with {return_code}: {stderr.strip()}")


def match_psql(
    *,
    manifest: Path,
    database: str,
    output: Path,
    summary_output: Path,
    fuzzy_threshold: float | None,
    fuzzy_limit: int,
    database_url: str | None,
) -> dict[str, Any]:
    references = list(iter_manifest(manifest))
    reference_by_stem: dict[str, list[int]] = defaultdict(list)
    for index, reference in enumerate(references):
        key = normalize_stem(reference.get("sourceStem"))
        if key:
            reference_by_stem[key].append(index)

    matches_by_reference: dict[int, list[dict[str, Any]]] = defaultdict(list)
    scanned = 0
    for existing in iter_psql_questions(database, database_url):
        scanned += 1
        key = normalize_stem(existing.get("stem"))
        for reference_index in reference_by_stem.get(key, ()):
            matches_by_reference[reference_index].append(existing)

    try:
        import psycopg
    except ImportError:
        psycopg = None
    candidate_by_id = {
        str(candidate["questionId"]): candidate
        for candidates in matches_by_reference.values()
        for candidate in candidates
    }
    if candidate_by_id and psycopg is not None:
        question_ids = sorted(candidate_by_id)
        with psycopg.connect(database_url or f"dbname={database}") as connection:
            connection.execute("set transaction read only")
            for offset in range(0, len(question_ids), 1000):
                chunk = question_ids[offset : offset + 1000]
                option_rows = connection.execute(
                    """
                    select question_id,
                           array_agg(
                             coalesce(nullif(option_text, ''), option_html)
                             order by option_index
                           )
                    from product.question_options
                    where question_id = any(%s)
                    group by question_id
                    """,
                    (chunk,),
                ).fetchall()
                for question_id, option_texts in option_rows:
                    candidate_by_id[str(question_id)]["optionTexts"] = option_texts or []
                explanation_rows = connection.execute(
                    """
                    select distinct question_id
                    from product.question_explanations
                    where question_id = any(%s)
                      and coalesce(nullif(stored_analysis, ''),
                                   nullif(original_analysis, '')) is not null
                    """,
                    (chunk,),
                ).fetchall()
                for (question_id,) in explanation_rows:
                    candidate_by_id[str(question_id)]["hasExplanation"] = True

    fuzzy_candidates: dict[int, list[dict[str, Any]]] = {}
    if fuzzy_threshold is not None:
        if psycopg is None:
            raise RuntimeError(
                "fuzzy matching requires psycopg; run with the backend virtualenv"
            )
        unmatched_indices = [
            index for index in range(len(references)) if index not in matches_by_reference
        ]
        if fuzzy_limit:
            unmatched_indices = unmatched_indices[:fuzzy_limit]
        with psycopg.connect(database_url or f"dbname={database}") as connection:
            connection.execute("set transaction read only")
            connection.execute("set local pg_trgm.similarity_threshold = 0.45")
            for index in unmatched_indices:
                reference = references[index]
                source_stem = str(reference.get("sourceStem") or "")
                rows = connection.execute(
                    """
                    select question_id, representative_source_question_id,
                           module_name, question_type, stem, option_texts, answer,
                           material_id, visual_required, first_year, latest_year,
                           occurrence_count, similarity(stem, %s) as raw_similarity
                    from product.questions
                    where stem %% %s
                    order by raw_similarity desc
                    limit 5
                    """,
                    (source_stem, source_stem),
                ).fetchall()
                candidates: list[dict[str, Any]] = []
                source_key = normalize_stem(source_stem)
                for row in rows:
                    candidate = {
                        "questionId": row[0],
                        "sourceQuestionId": row[1],
                        "moduleName": row[2],
                        "questionType": row[3],
                        "stem": row[4],
                        "optionTexts": row[5] or [],
                        "answer": row[6],
                        "materialId": row[7],
                        "visualRequired": row[8],
                        "firstYear": row[9],
                        "latestYear": row[10],
                        "occurrenceCount": row[11],
                        "rawSimilarity": round(float(row[12]), 6),
                    }
                    candidate["normalizedSimilarity"] = round(
                        SequenceMatcher(
                            None, source_key, normalize_stem(candidate["stem"])
                        ).ratio(),
                        6,
                    )
                    candidate["evidence"] = candidate_evidence(reference, candidate)
                    candidates.append(candidate)
                accepted = [
                    candidate
                    for candidate in candidates
                    if candidate["normalizedSimilarity"] >= fuzzy_threshold
                    and not (
                        candidate["evidence"]["answerContentComparable"]
                        and not candidate["evidence"]["answerContentAgrees"]
                    )
                    and not (
                        candidate["evidence"]["optionsComparable"]
                        and not candidate["evidence"]["optionSetAgrees"]
                    )
                ]
                if accepted:
                    fuzzy_candidates[index] = candidates

    counts: Counter[str] = Counter()
    paper_counts: dict[str, Counter[str]] = defaultdict(Counter)
    with _open_jsonl(output, "wt") as target:
        for index, reference in enumerate(references):
            existing_matches = matches_by_reference.get(index, [])
            for candidate in existing_matches:
                candidate["evidence"] = candidate_evidence(reference, candidate)
            if len(existing_matches) == 1:
                evidence = existing_matches[0]["evidence"]
                option_conflict = evidence["optionsComparable"] and not evidence[
                    "optionSetAgrees"
                ]
                answer_conflict = (
                    evidence["answerContentComparable"]
                    and not evidence["answerContentAgrees"]
                ) or (
                    not evidence["answerContentComparable"]
                    and evidence["answerComparable"]
                    and not evidence["answerAgrees"]
                )
                status = (
                    "exact_conflict"
                    if option_conflict or answer_conflict
                    else "exact_unique"
                )
            elif existing_matches:
                consistent = [
                    candidate
                    for candidate in existing_matches
                    if (
                        candidate["evidence"]["optionsAgree"]
                        or (
                            candidate["evidence"]["optionSetAgrees"]
                            and candidate["evidence"]["answerContentAgrees"]
                        )
                    )
                    and not (
                        candidate["evidence"]["answerContentComparable"]
                        and not candidate["evidence"]["answerContentAgrees"]
                    )
                ]
                status = (
                    "exact_disambiguated" if len(consistent) == 1 else "exact_multiple"
                )
                if len(consistent) == 1:
                    existing_matches = consistent
            elif index in fuzzy_candidates:
                status = "fuzzy_review"
                existing_matches = fuzzy_candidates[index]
            else:
                status = "unmatched"
            counts[status] += 1
            paper_counts[str(reference.get("sourcePaperFile") or "")][status] += 1
            target.write(
                json.dumps(
                    {
                        **reference,
                        "matchStatus": status,
                        "matches": existing_matches,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    matched = counts["exact_unique"] + counts["exact_disambiguated"]
    summary = {
        "mode": "read_only_exact_normalized_stem",
        "database": database,
        "existingQuestionsScanned": scanned,
        "referenceQuestions": len(references),
        "exactUnique": counts["exact_unique"],
        "exactDisambiguated": counts["exact_disambiguated"],
        "exactConflict": counts["exact_conflict"],
        "exactMultiple": counts["exact_multiple"],
        "fuzzyReview": counts["fuzzy_review"],
        "unmatched": counts["unmatched"],
        "trustedMatched": matched,
        "trustedMatchRate": round(matched / len(references), 6) if references else 0,
        "byPaper": {key: dict(value) for key, value in paper_counts.items()},
        "output": str(output),
    }
    summary_output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    manifest = commands.add_parser("manifest")
    manifest.add_argument("--archive", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument(
        "--category", action="append", default=["联考A类", "联考B类"]
    )

    match = commands.add_parser("match-psql")
    match.add_argument("--manifest", type=Path, required=True)
    match.add_argument("--database", required=True)
    match.add_argument(
        "--database-url-env",
        default="MATCH_DATABASE_URL",
        help="Environment variable containing a libpq URL; the value is never logged.",
    )
    match.add_argument("--output", type=Path, required=True)
    match.add_argument("--summary-output", type=Path, required=True)
    match.add_argument("--fuzzy-threshold", type=float)
    match.add_argument(
        "--fuzzy-limit",
        type=int,
        default=0,
        help="Only inspect this many unmatched references; 0 means all.",
    )
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "manifest":
        result = build_manifest(
            archive=args.archive,
            output=args.output,
            categories=set(args.category),
        )
    else:
        result = match_psql(
            manifest=args.manifest,
            database=args.database,
            output=args.output,
            summary_output=args.summary_output,
            fuzzy_threshold=args.fuzzy_threshold,
            fuzzy_limit=args.fuzzy_limit,
            database_url=os.getenv(args.database_url_env),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
