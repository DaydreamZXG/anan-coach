from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from app.domains.external_ingestion.matching.fingerprint import normalize_text
from app.domains.xingce_learning.content_version import question_content_version_id


_CANONICAL_MODULES = {
    "political_theory": "政治理论",
    "general_knowledge": "常识判断",
    "verbal_comprehension": "言语理解与表达",
    "quantitative_relations": "数量关系",
    "judgment_reasoning": "判断推理",
    "data_analysis": "资料分析",
}


def seed_released_module_vocabulary(
    session: Session,
    *,
    version: str,
    aliases: Mapping[str, str] | None = None,
) -> None:
    rows = [
        {
            "raw_module_name": raw_name,
            "action": "canonical",
            "canonical_code": canonical_code,
        }
        for canonical_code, raw_name in _CANONICAL_MODULES.items()
    ]
    for raw_name, canonical_code in (aliases or {}).items():
        rows.append(
            {
                "raw_module_name": raw_name,
                "action": "map",
                "canonical_code": canonical_code,
            }
        )
    for row in rows:
        session.execute(
            text(
                """
                insert into product.vocab_module_alias_map (
                  version, raw_module_name, action, canonical_code,
                  local_question_count, basis
                )
                select
                  :version, :raw_module_name, :action, :canonical_code,
                  0, 'test fixture'
                where not exists (
                  select 1
                  from product.vocab_module_alias_map existing
                  where existing.version = :version
                    and existing.raw_module_name = :raw_module_name
                )
                on conflict (version, raw_module_name) do nothing
                """
            ),
            {"version": version, **row},
        )
    stored = {
        str(row["raw_module_name"]): (
            str(row["action"]),
            str(row["canonical_code"]),
        )
        for row in session.execute(
            text(
                """
                select raw_module_name, action, canonical_code
                from product.vocab_module_alias_map
                where version = :version
                """
            ),
            {"version": version},
        ).mappings()
    }
    expected = {
        str(row["raw_module_name"]): (
            str(row["action"]),
            str(row["canonical_code"]),
        )
        for row in rows
    }
    if any(stored.get(raw_name) != value for raw_name, value in expected.items()):
        raise AssertionError("test vocabulary replay changed same-version semantics")


def bind_question_to_release_node(
    session: Session,
    *,
    release_id: str,
    node_id: str,
    question_id: str,
    latest_recent_exam_year: int | None = None,
    recent_occurrence_count: int = 0,
    latest_old_exam_year: int | None = None,
    old_occurrence_count: int = 0,
    content_version_id: str | None = None,
) -> str:
    """Freeze one existing question into one draft release/node binding."""

    resolved_content_version_id = content_version_id
    if resolved_content_version_id is None:
        resolved_content_version_id = SQLQuestionContentCatalog(session).publish_current(
            question_id=question_id
        ).content_version_id
    authority = (
        session.execute(
            text(
                """
                select
                  canonical.canonical_code as module_code,
                  node.module_name as canonical_module,
                  coalesce(node.tag_type, node.node_type) as source_tag_type,
                  node.tag_value as source_tag_value,
                  question.visual_required,
                  question.shared_context_required,
                  question.latest_year,
                  coalesce(question.occurrence_count, 0)::integer
                    as occurrence_count
                from product.knowledge_map_releases release
                join product.knowledge_map_nodes node
                  on node.release_id = release.release_id
                 and node.node_id = :node_id
                join product.vocab_module_alias_map canonical
                  on canonical.version = release.vocab_version
                 and canonical.action = 'canonical'
                 and canonical.raw_module_name = node.module_name
                join product.questions question
                  on question.question_id = :question_id
                where release.release_id = cast(:release_id as uuid)
                  and release.status = 'draft'
                """
            ),
            {
                "release_id": release_id,
                "node_id": node_id,
                "question_id": question_id,
            },
        )
        .mappings()
        .one()
    )
    session.execute(
        text(
            """
            insert into product.question_tags (
              question_tag_id, question_id, tag_type, tag_value, sort_order
            )
            select
              'qt_binding_' || md5(
                cast(:release_id as text) || '|' || :node_id || '|' ||
                :question_id || '|' || :tag_type || '|' || :tag_value
              ),
              :question_id,
              :tag_type,
              :tag_value,
              1
            where not exists (
              select 1
              from product.question_tags existing
              where existing.question_id = :question_id
                and existing.tag_type = :tag_type
                and existing.tag_value = :tag_value
            )
            """
        ),
        {
            "release_id": release_id,
            "node_id": node_id,
            "question_id": question_id,
            "tag_type": authority["source_tag_type"],
            "tag_value": authority["source_tag_value"],
        },
    )
    session.execute(
        text(
            """
            with candidate as (
              select
                cast(:release_id as uuid) as release_id,
                cast(:node_id as text) as node_id,
                cast(:question_id as text) as question_id,
                cast(:content_version_id as text) as content_version_id,
                cast(:module_code as text) as module_code,
                cast(:canonical_module as text) as canonical_module,
                'direct'::text as binding_kind,
                cast(:source_tag_type as text) as source_tag_type,
                cast(:source_tag_value as text) as source_tag_value,
                true as delivery_eligible,
                cast(:visual_required as boolean) as visual_required,
                cast(:shared_context_required as boolean)
                  as shared_context_required,
                cast(:latest_year as integer) as latest_year,
                cast(:occurrence_count as integer) as occurrence_count,
                cast(:latest_recent_exam_year as integer)
                  as latest_recent_exam_year,
                cast(:recent_occurrence_count as integer)
                  as recent_occurrence_count,
                cast(:latest_old_exam_year as integer)
                  as latest_old_exam_year,
                cast(:old_occurrence_count as integer) as old_occurrence_count,
                transaction_timestamp() as created_at
            )
            insert into product.knowledge_map_question_bindings (
              release_id, node_id, question_id, content_version_id,
              module_code, canonical_module, binding_kind,
              source_tag_type, source_tag_value, delivery_eligible,
              visual_required, shared_context_required, latest_year,
              occurrence_count, latest_recent_exam_year,
              recent_occurrence_count, latest_old_exam_year,
              old_occurrence_count, binding_fingerprint, created_at
            )
            select
              candidate.release_id,
              candidate.node_id,
              candidate.question_id,
              candidate.content_version_id,
              candidate.module_code,
              candidate.canonical_module,
              candidate.binding_kind,
              candidate.source_tag_type,
              candidate.source_tag_value,
              candidate.delivery_eligible,
              candidate.visual_required,
              candidate.shared_context_required,
              candidate.latest_year,
              candidate.occurrence_count,
              candidate.latest_recent_exam_year,
              candidate.recent_occurrence_count,
              candidate.latest_old_exam_year,
              candidate.old_occurrence_count,
              product.knowledge_map_question_binding_row_fingerprint(
                row(
                  candidate.release_id,
                  candidate.node_id,
                  candidate.question_id,
                  candidate.content_version_id,
                  candidate.module_code,
                  candidate.canonical_module,
                  candidate.binding_kind,
                  candidate.source_tag_type,
                  candidate.source_tag_value,
                  candidate.delivery_eligible,
                  candidate.visual_required,
                  candidate.shared_context_required,
                  candidate.latest_year,
                  candidate.occurrence_count,
                  candidate.latest_recent_exam_year,
                  candidate.recent_occurrence_count,
                  candidate.latest_old_exam_year,
                  candidate.old_occurrence_count,
                  repeat('0', 64),
                  candidate.created_at
                )::product.knowledge_map_question_bindings
              ),
              candidate.created_at
            from candidate
            """
        ),
        {
            "release_id": release_id,
            "node_id": node_id,
            "question_id": question_id,
            "content_version_id": resolved_content_version_id,
            "module_code": authority["module_code"],
            "canonical_module": authority["canonical_module"],
            "source_tag_type": authority["source_tag_type"],
            "source_tag_value": authority["source_tag_value"],
            "visual_required": authority["visual_required"],
            "shared_context_required": authority["shared_context_required"],
            "latest_year": authority["latest_year"],
            "occurrence_count": authority["occurrence_count"],
            "latest_recent_exam_year": latest_recent_exam_year,
            "recent_occurrence_count": recent_occurrence_count,
            "latest_old_exam_year": latest_old_exam_year,
            "old_occurrence_count": old_occurrence_count,
        },
    )
    return resolved_content_version_id


def seed_minimal_release_binding(
    session: Session,
    *,
    release_id: str,
    node_id: str,
    question_id: str,
    legacy_visual_schema: bool = False,
) -> str:
    node = session.execute(
        text(
            """
            select module_name, tag_value
            from product.knowledge_map_nodes
            where release_id = cast(:release_id as uuid)
              and node_id = :node_id
            """
        ),
        {"release_id": release_id, "node_id": node_id},
    ).mappings().one()
    session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer, visual_required, shared_context_required,
              occurrence_count, latest_year
            ) values (
              :question_id, 'objective', 'active', :module_name, :module_name,
              :stem, 'A', false, false, 1, 2026
            )
            on conflict (question_id) do nothing
            """
        ),
        {
            "question_id": question_id,
            "module_name": node["module_name"],
            "stem": f"{node['tag_value']} fixture question",
        },
    )
    session.execute(
        text(
            """
            insert into product.question_options (
              question_id, option_index, label, option_text
            ) values
              (:question_id, 1, 'A', '正确'),
              (:question_id, 2, 'B', '错误')
            on conflict (question_id, option_index) do nothing
            """
        ),
        {"question_id": question_id},
    )
    content_version_id = None
    if legacy_visual_schema:
        content_version_id = publish_legacy_question_content(
            session,
            question_id=question_id,
        )
    return bind_question_to_release_node(
        session,
        release_id=release_id,
        node_id=node_id,
        question_id=question_id,
        latest_recent_exam_year=2026,
        recent_occurrence_count=1,
        content_version_id=content_version_id,
    )


def publish_legacy_question_content(
    session: Session,
    *,
    question_id: str,
) -> str:
    """Publish fixture content without reading post-revision-90 visual columns."""

    source = SQLQuestionContentCatalog(session)._canonical_question(  # noqa: SLF001
        question_id=question_id
    )
    if source is None:
        raise AssertionError("legacy fixture question is missing canonical content")
    source.pop("domain", None)
    snapshot = {
        field: source.get(field)
        for field in (
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
    content_version_id = question_content_version_id(snapshot)
    options = snapshot["options"] if isinstance(snapshot["options"], list) else []
    normalized_search_text = normalize_text(
        "\n".join(
            (
                str(snapshot.get("stem") or ""),
                *(str(item.get("option_text") or "") for item in options),
                str(snapshot.get("material_text") or ""),
            )
        )
    )
    session.execute(
        text(
            """
            insert into product.question_content_versions (
              content_version_id, question_id, track, snapshot_payload,
              normalized_search_text, created_at
            ) values (
              :content_version_id, :question_id, 'xingce',
              cast(:snapshot_payload as jsonb), :normalized_search_text,
              transaction_timestamp()
            )
            on conflict (content_version_id) do nothing
            """
        ),
        {
            "content_version_id": content_version_id,
            "question_id": question_id,
            "snapshot_payload": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            "normalized_search_text": normalized_search_text,
        },
    )
    session.execute(
        text(
            """
            insert into product.question_content_heads (
              question_id, content_version_id, updated_at
            ) values (
              :question_id, :content_version_id, transaction_timestamp()
            )
            on conflict (question_id) do update
            set content_version_id = excluded.content_version_id,
                updated_at = excluded.updated_at
            """
        ),
        {"question_id": question_id, "content_version_id": content_version_id},
    )
    return content_version_id


def activate_knowledge_release(
    session: Session,
    *,
    release_id: str,
    activated_at: datetime | None = None,
) -> None:
    session.execute(
        text(
            """
            with manifest as (
              select *
              from product.knowledge_map_question_binding_manifest(
                cast(:release_id as uuid)
              )
            )
            update product.knowledge_map_releases release
            set
              status = 'active',
              node_count = (
                select count(*)::integer
                from product.knowledge_map_nodes node
                where node.release_id = release.release_id
              ),
              question_binding_count = manifest.binding_count,
              question_binding_fingerprint = manifest.binding_fingerprint,
              activated_at = cast(:activated_at as timestamptz)
            from manifest
            where release.release_id = cast(:release_id as uuid)
              and release.status = 'draft'
            """
        ),
        {
            "release_id": release_id,
            "activated_at": activated_at or datetime.now(UTC),
        },
    )
