#!/usr/bin/env python3
"""收口 staging 题库为可重跑的 qbank-v1。

此脚本只处理既有 ``pp_`` 导入题：补来源 occurrence、把已有模块标签写回
正式模块名，并为合格题创建当前内容版本。它不删除题目、不覆盖历史内容版本，
也拒绝连接到非 ``*_staging`` 数据库。
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Literal
from urllib.parse import unquote, urlparse

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.external_ingestion.matching.fingerprint import normalize_text  # noqa: E402
from app.domains.xingce_learning.content_version import (  # noqa: E402
    question_content_version_id,
)


SOURCE_DATASET_BY_PREFIX = {
    "an:": "fenbi-import-20260801",
    "fenbi:": "fenbi-import-20260801",
}
DEFAULT_SOURCE_DATASET = "sydw-public-import-20260801"
CANONICAL_MODULE_BY_VALUE = {
    "political_theory": "政治理论",
    "政治理论": "政治理论",
    "general_knowledge": "常识判断",
    "常识判断": "常识判断",
    "verbal_comprehension": "言语理解与表达",
    "言语理解": "言语理解与表达",
    "言语理解与表达": "言语理解与表达",
    "quantitative": "数量关系",
    "quantitative_relations": "数量关系",
    "数量关系": "数量关系",
    "reasoning": "判断推理",
    "judgment_reasoning": "判断推理",
    "判断推理": "判断推理",
    "data_analysis": "资料分析",
    "资料分析": "资料分析",
    "gongji": "公共基础知识",
    "公共基础知识": "公共基础知识",
}


class QuestionBankFinalizationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FinalizationReport:
    database: str
    imported_questions: int
    eligible: int
    published: int
    quarantined: int
    missing_source: int
    missing_module: int
    ambiguous_module: int
    missing_content_version: int
    inserted_occurrences: int = 0
    normalized_modules: int = 0
    published_added: int = 0
    publication_failures: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "database": self.database,
            "counts": {
                "eligible": self.eligible,
                "published": self.published,
                "quarantined": self.quarantined,
                "missingSource": self.missing_source,
            },
            "detail": {
                "importedQuestions": self.imported_questions,
                "missingModule": self.missing_module,
                "ambiguousModule": self.ambiguous_module,
                "missingContentVersion": self.missing_content_version,
                "insertedOccurrences": self.inserted_occurrences,
                "normalizedModules": self.normalized_modules,
                "publishedAdded": self.published_added,
                "publicationFailures": self.publication_failures,
            },
        }


def database_name_from_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    path = unquote(parsed.path).strip("/")
    if path:
        return path.rsplit("/", 1)[-1]
    raise QuestionBankFinalizationError("database URL must include a database name")


def require_staging_database_name(database_name: str) -> None:
    if not database_name.endswith("_staging"):
        raise QuestionBankFinalizationError(
            f"qbank finalization only accepts a *_staging database, got {database_name!r}"
        )


def stable_identifier(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def source_dataset_id(source_question_id: str) -> str:
    for prefix, dataset_id in SOURCE_DATASET_BY_PREFIX.items():
        if source_question_id.startswith(prefix):
            return dataset_id
    return DEFAULT_SOURCE_DATASET


def canonical_module_name(value: str | None) -> str | None:
    if value is None:
        return None
    return CANONICAL_MODULE_BY_VALUE.get(value.strip())


def _resolved_module(
    current_module: str | None, module_tags: Iterable[str]
) -> tuple[str | None, Literal["existing", "tag", "missing", "ambiguous"]]:
    current = canonical_module_name(current_module)
    if current is not None:
        return current, "existing"
    candidates = {canonical_module_name(value) for value in module_tags}
    candidates.discard(None)
    if len(candidates) == 1:
        return next(iter(candidates)), "tag"
    if not candidates:
        return None, "missing"
    return None, "ambiguous"


def _database_name(session: Session) -> str:
    name = session.execute(text("select current_database()")).scalar_one()
    return str(name)


def _question_rows(session: Session) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in session.execute(
            text(
                """
                select question.question_id,
                       question.representative_source_question_id,
                       question.module_name,
                       question.stem,
                       question.answer,
                       coalesce(
                         array_agg(distinct tag.tag_value)
                           filter (where tag.tag_type = 'module'),
                         '{}'::text[]
                       ) as module_tags
                from product.questions question
                left join product.question_tags tag
                  on tag.question_id = question.question_id
                where question.question_id like 'pp_%'
                group by question.question_id,
                         question.representative_source_question_id,
                         question.module_name,
                         question.stem,
                         question.answer
                order by question.question_id
                """
            )
        ).mappings()
    ]


def _sourced_question_ids(session: Session) -> set[str]:
    return {
        str(question_id)
        for question_id in session.execute(
            text(
                """
                select distinct occurrence.question_id
                from product.question_occurrences occurrence
                where occurrence.question_id like 'pp_%'
                  and nullif(trim(occurrence.source_dataset_id), '') is not null
                """
            )
        ).scalars()
    }


def _published_question_ids(session: Session) -> set[str]:
    return {
        str(question_id)
        for question_id in session.execute(
            text(
                """
                select question_id
                from product.question_content_heads
                where question_id like 'pp_%'
                """
            )
        ).scalars()
    }


def audit_question_bank(session: Session) -> tuple[FinalizationReport, tuple[str, ...]]:
    database = _database_name(session)
    require_staging_database_name(database)
    sourced_ids = _sourced_question_ids(session)
    published_ids = _published_question_ids(session)
    rows = _question_rows(session)

    eligible_ids: list[str] = []
    missing_source = 0
    missing_module = 0
    ambiguous_module = 0
    quarantined = 0
    for row in rows:
        question_id = str(row["question_id"])
        _module, module_status = _resolved_module(
            str(row["module_name"]) if row["module_name"] is not None else None,
            tuple(str(value) for value in row["module_tags"]),
        )
        has_source = question_id in sourced_ids
        has_stem = bool(str(row["stem"] or "").strip())
        has_answer = bool(str(row["answer"] or "").strip())
        if not has_source:
            missing_source += 1
        if module_status == "missing":
            missing_module += 1
        elif module_status == "ambiguous":
            ambiguous_module += 1
        if has_source and _module is not None and has_stem and has_answer:
            eligible_ids.append(question_id)
        else:
            quarantined += 1

    report = FinalizationReport(
        database=database,
        imported_questions=len(rows),
        eligible=len(eligible_ids),
        published=len(set(eligible_ids) & published_ids),
        quarantined=quarantined,
        missing_source=missing_source,
        missing_module=missing_module,
        ambiguous_module=ambiguous_module,
        missing_content_version=len(set(eligible_ids) - published_ids),
    )
    return report, tuple(eligible_ids)


def _chunks(values: Iterable[dict[str, str]], size: int = 1_000) -> Iterable[list[dict[str, str]]]:
    chunk: list[dict[str, str]] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _backfill_occurrences(session: Session) -> int:
    existing = {
        (str(row["question_id"]), str(row["source_question_id"]))
        for row in session.execute(
            text(
                """
                select question_id, source_question_id
                from product.question_occurrences
                where question_id like 'pp_%'
                  and source_question_id is not null
                """
            )
        ).mappings()
    }
    values: list[dict[str, str]] = []
    for row in session.execute(
        text(
            """
            select question_id, representative_source_question_id
            from product.questions
            where question_id like 'pp_%'
              and nullif(trim(representative_source_question_id), '') is not null
            """
        )
    ).mappings():
        question_id = str(row["question_id"])
        source_question_id = str(row["representative_source_question_id"])
        if (question_id, source_question_id) in existing:
            continue
        values.append(
            {
                "occurrence_id": stable_identifier(
                    "occ", question_id, source_question_id
                ),
                "question_id": question_id,
                "source_question_id": source_question_id,
                "source_dataset_id": source_dataset_id(source_question_id),
            }
        )
    for chunk in _chunks(values):
        session.execute(
            text(
                """
                insert into product.question_occurrences (
                  occurrence_id, question_id, source_question_id, source_dataset_id
                ) values (
                  :occurrence_id, :question_id, :source_question_id, :source_dataset_id
                ) on conflict (occurrence_id) do nothing
                """
            ),
            chunk,
        )
    session.execute(
        text(
            """
            update product.question_occurrences occurrence
            set source_dataset_id = case
              when occurrence.source_question_id like 'an:%'
                or occurrence.source_question_id like 'fenbi:%'
                then :fenbi_dataset
              else :sydw_dataset
            end
            where occurrence.question_id like 'pp_%'
              and nullif(trim(occurrence.source_question_id), '') is not null
              and nullif(trim(occurrence.source_dataset_id), '') is null
            """
        ),
        {
            "fenbi_dataset": SOURCE_DATASET_BY_PREFIX["an:"],
            "sydw_dataset": DEFAULT_SOURCE_DATASET,
        },
    )
    return len(values)


def _normalize_modules(session: Session) -> int:
    result = session.execute(
        text(
            """
            with module_candidates as (
              select question.question_id,
                     count(distinct case
                       when tag.tag_type = 'module' then case tag.tag_value
                         when 'political_theory' then '政治理论'
                         when '政治理论' then '政治理论'
                         when 'general_knowledge' then '常识判断'
                         when '常识判断' then '常识判断'
                         when 'verbal_comprehension' then '言语理解与表达'
                         when '言语理解' then '言语理解与表达'
                         when '言语理解与表达' then '言语理解与表达'
                         when 'quantitative' then '数量关系'
                         when 'quantitative_relations' then '数量关系'
                         when '数量关系' then '数量关系'
                         when 'reasoning' then '判断推理'
                         when 'judgment_reasoning' then '判断推理'
                         when '判断推理' then '判断推理'
                         when 'data_analysis' then '资料分析'
                         when '资料分析' then '资料分析'
                         when 'gongji' then '公共基础知识'
                         when '公共基础知识' then '公共基础知识'
                       end
                     end) as module_count,
                     min(case
                       when tag.tag_type = 'module' then case tag.tag_value
                         when 'political_theory' then '政治理论'
                         when '政治理论' then '政治理论'
                         when 'general_knowledge' then '常识判断'
                         when '常识判断' then '常识判断'
                         when 'verbal_comprehension' then '言语理解与表达'
                         when '言语理解' then '言语理解与表达'
                         when '言语理解与表达' then '言语理解与表达'
                         when 'quantitative' then '数量关系'
                         when 'quantitative_relations' then '数量关系'
                         when '数量关系' then '数量关系'
                         when 'reasoning' then '判断推理'
                         when 'judgment_reasoning' then '判断推理'
                         when '判断推理' then '判断推理'
                         when 'data_analysis' then '资料分析'
                         when '资料分析' then '资料分析'
                         when 'gongji' then '公共基础知识'
                         when '公共基础知识' then '公共基础知识'
                       end
                     end) as module_name
              from product.questions question
              left join product.question_tags tag
                on tag.question_id = question.question_id
              where question.question_id like 'pp_%'
              group by question.question_id
            ), resolved as (
              select question.question_id,
                     case question.module_name
                       when 'political_theory' then '政治理论'
                       when '政治理论' then '政治理论'
                       when 'general_knowledge' then '常识判断'
                       when '常识判断' then '常识判断'
                       when 'verbal_comprehension' then '言语理解与表达'
                       when '言语理解' then '言语理解与表达'
                       when '言语理解与表达' then '言语理解与表达'
                       when 'quantitative' then '数量关系'
                       when 'quantitative_relations' then '数量关系'
                       when '数量关系' then '数量关系'
                       when 'reasoning' then '判断推理'
                       when 'judgment_reasoning' then '判断推理'
                       when '判断推理' then '判断推理'
                       when 'data_analysis' then '资料分析'
                       when '资料分析' then '资料分析'
                       when 'gongji' then '公共基础知识'
                       when '公共基础知识' then '公共基础知识'
                     end as current_module,
                     candidates.module_name as tagged_module,
                     candidates.module_count
              from product.questions question
              join module_candidates candidates using (question_id)
              where question.question_id like 'pp_%'
            )
            update product.questions question
            set module_name = case
              when resolved.current_module is not null then resolved.current_module
              else resolved.tagged_module
            end
            from resolved
            where question.question_id = resolved.question_id
              and (resolved.current_module is not null
                   or resolved.module_count = 1)
              and nullif(trim(coalesce(
                case when resolved.current_module is not null
                  then resolved.current_module else resolved.tagged_module end,
                '')), '') is not null
              and question.module_name is distinct from case
                when resolved.current_module is not null then resolved.current_module
                else resolved.tagged_module
              end
            """
        )
    )
    updated = result.rowcount
    session.execute(
        text(
            """
            update product.questions
            set domain = 'objective'
            where question_id like 'pp_%'
              and nullif(trim(domain), '') is null
            """
        )
    )
    session.execute(
        text(
            """
            update product.questions
            set status = 'active'
            where question_id like 'pp_%'
              and nullif(trim(status), '') is null
            """
        )
    )
    return updated


def _publication_rows(
    session: Session, eligible_ids: tuple[str, ...]
) -> list[dict[str, object]]:
    if not eligible_ids:
        return []
    statement = text(
        """
        select question.question_id, question.domain, question.module_name,
               question.question_type, question.stem, question.stem_html,
               question.answer, question.material_text, question.material_html,
               explanation.preferred_analysis, explanation.analysis_source,
               explanation.analysis_status, explanation.prefer_ai_analysis,
               explanation.analysis_needs_review,
               coalesce((
                 select jsonb_agg(
                   jsonb_build_object(
                     'label', option.label,
                     'option_text', option.option_text,
                     'option_html', option.option_html
                   ) order by option.option_index
                 )
                 from product.question_options option
                 where option.question_id = question.question_id
               ), '[]'::jsonb) as options,
               coalesce((
                 select jsonb_agg(
                   jsonb_build_object(
                     'asset_id', asset.asset_id,
                     'part_kind', asset.part_kind,
                     'part_index', asset.part_index,
                     'image_ordinal', asset.image_ordinal,
                     'image_src', asset.image_src,
                     'object_key', asset.object_key,
                     'is_representative_asset', asset.is_representative_asset
                   ) order by asset.part_kind nulls last,
                              asset.part_index nulls last,
                              asset.image_ordinal nulls last,
                              asset.asset_id
                 )
                 from product.question_assets asset
                 where asset.question_id = question.question_id
               ), '[]'::jsonb) as assets
        from product.questions question
        left join product.question_explanations explanation
          on explanation.question_id = question.question_id
        where question.question_id in :question_ids
          and question.status in ('active', 'canonical')
        order by question.question_id
        """
    ).bindparams(bindparam("question_ids", expanding=True))
    rows: list[dict[str, object]] = []
    for id_chunk in _chunks(
        ({"question_id": question_id} for question_id in eligible_ids), size=10_000
    ):
        rows.extend(
            dict(row)
            for row in session.execute(
                statement,
                {"question_ids": [item["question_id"] for item in id_chunk]},
            ).mappings()
        )
    return rows


def _bulk_publish_current(
    session: Session, eligible_ids: tuple[str, ...], *, batch_size: int
) -> tuple[int, int]:
    rows = _publication_rows(session, eligible_ids)
    values: list[dict[str, object]] = []
    for row in rows:
        domain = str(row["domain"] or "")
        track = {"objective": "xingce", "xingce": "xingce", "essay": "essay", "shenlun": "essay"}.get(domain)
        if track is None:
            continue
        snapshot = {
            key: row.get(key)
            for key in (
                "question_id",
                "module_name",
                "question_type",
                "stem",
                "stem_html",
                "answer",
                "material_text",
                "material_html",
                "preferred_analysis",
                "analysis_source",
                "analysis_status",
                "prefer_ai_analysis",
                "analysis_needs_review",
                "options",
                "assets",
            )
        }
        if not str(snapshot.get("stem") or "").strip():
            continue
        normalized_search_text = normalize_text(
            "\n".join(
                [
                    str(snapshot.get("stem") or ""),
                    *[
                        str(item.get("option_text") or "")
                        for item in snapshot["options"]
                        if isinstance(item, dict)
                    ],
                    str(snapshot.get("material_text") or ""),
                ]
            )
        )
        if not normalized_search_text:
            continue
        values.append(
            {
                "content_version_id": question_content_version_id(snapshot),
                "question_id": str(row["question_id"]),
                "track": track,
                "snapshot_payload": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                "normalized_search_text": normalized_search_text,
                "created_at": datetime.now(UTC),
            }
        )

    for chunk in _chunks(values, size=batch_size):
        session.execute(
            text(
                """
                insert into product.question_content_versions (
                  content_version_id, question_id, track, snapshot_payload,
                  normalized_search_text, created_at
                ) values (
                  :content_version_id, :question_id, :track,
                  cast(:snapshot_payload as jsonb), :normalized_search_text,
                  :created_at
                ) on conflict (content_version_id) do nothing
                """
            ),
            chunk,
        )
        session.execute(
            text(
                """
                insert into product.question_content_heads (
                  question_id, content_version_id, updated_at
                ) values (
                  :question_id, :content_version_id, :updated_at
                ) on conflict (question_id) do update
                set content_version_id = excluded.content_version_id,
                    updated_at = excluded.updated_at
                where product.question_content_heads.content_version_id
                      is distinct from excluded.content_version_id
                """
            ),
            [
                {
                    "question_id": value["question_id"],
                    "content_version_id": value["content_version_id"],
                    "updated_at": value["created_at"],
                }
                for value in chunk
            ],
        )
        session.commit()
    return len(values), 0


def apply_finalization(session: Session, *, batch_size: int = 500) -> FinalizationReport:
    database = _database_name(session)
    require_staging_database_name(database)
    inserted_occurrences = _backfill_occurrences(session)
    normalized_modules = _normalize_modules(session)
    session.commit()

    before, eligible_ids = audit_question_bank(session)
    _published_count, publication_failures = _bulk_publish_current(
        session, eligible_ids, batch_size=batch_size
    )
    session.commit()

    after, _ = audit_question_bank(session)
    return replace(
        after,
        inserted_occurrences=inserted_occurrences,
        normalized_modules=normalized_modules,
        published_added=max(0, after.published - before.published),
        publication_failures=publication_failures,
    )


def verify_finalization(session: Session) -> FinalizationReport:
    report, _ = audit_question_bank(session)
    if report.missing_content_version:
        raise QuestionBankFinalizationError(
            f"{report.missing_content_version} eligible questions still lack a content version"
        )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "apply", "verify"))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    return parser


def main() -> None:
    args = _parser().parse_args()
    require_staging_database_name(database_name_from_url(args.database_url))
    engine = create_engine(args.database_url, future=True)
    try:
        with Session(engine) as session:
            if args.mode == "audit":
                report, _ = audit_question_bank(session)
            elif args.mode == "apply":
                report = apply_finalization(session, batch_size=args.batch_size)
            else:
                report = verify_finalization(session)
            print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
