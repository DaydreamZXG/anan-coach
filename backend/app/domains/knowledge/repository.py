from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ProductStatsRecord:
    canonical_questions: int
    objective_questions: int
    essay_questions: int
    explanations: int
    knowledge_chunks: int
    position_records: int


@dataclass(frozen=True)
class KnowledgeMapNodeRecord:
    node_id: str
    exam_scope: str
    track: str
    module_name: str
    node_type: str
    tag_type: str
    tag_value: str
    display_path: str
    support_status: str
    near5_window_start_year: int
    near5_window_end_year: int
    near5_weight: float
    near5_occurrence_count: int


class KnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_product_stats(self) -> ProductStatsRecord:
        row = self._session.execute(
            text(
                """
                select
                  (
                    select count(*)
                    from product.questions
                    where status = 'active'
                  ) as canonical_questions,
                  (
                    select count(*)
                    from product.questions
                    where status = 'active'
                      and domain = 'objective'
                  ) as objective_questions,
                  (
                    select count(*)
                    from product.questions
                    where status = 'active'
                      and domain = 'essay'
                  ) as essay_questions,
                  (
                    select count(*)
                    from product.question_explanations
                  ) as explanations,
                  (
                    select count(*)
                    from product.knowledge_chunks
                  ) as knowledge_chunks,
                  0 as position_records
                """
            )
        ).mappings().one()
        return ProductStatsRecord(
            canonical_questions=int(row["canonical_questions"]),
            objective_questions=int(row["objective_questions"]),
            essay_questions=int(row["essay_questions"]),
            explanations=int(row["explanations"]),
            knowledge_chunks=int(row["knowledge_chunks"]),
            position_records=self._count_optional_table(schema="product", table="positions"),
        )

    def get_question_row(self, question_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select
                  question_id,
                  domain,
                  status,
                  module_name,
                  question_type,
                  display_level1,
                  display_level2,
                  display_level3,
                  knowledge_family,
                  teaching_point,
                  micro_skill,
                  stem,
                  stem_html,
                  answer,
                  material_text,
                  material_html,
                  difficulty,
                  shared_context_required,
                  visual_required,
                  needs_multimodal,
                  occurrence_count,
                  occurrence_province_count,
                  first_year,
                  latest_year,
                  source_member_count
                from product.questions
                where question_id = :question_id
                """
            ),
            {"question_id": question_id},
        ).mappings().first()
        return dict(row) if row else None

    def get_options(self, question_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            select
              option_index,
              label,
              option_text,
              option_html
            from product.question_options
            where question_id = :question_id
            order by option_index
            """,
            question_id=question_id,
        )

    def get_explanation(self, question_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select
                  preferred_analysis,
                  original_analysis,
                  stored_analysis,
                  analysis_source,
                  analysis_status,
                  prefer_ai_analysis,
                  analysis_needs_review
                from product.question_explanations
                where question_id = :question_id
                """
            ),
            {"question_id": question_id},
        ).mappings().first()
        return dict(row) if row else None

    def get_occurrences(self, question_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            select
              occurrence_id,
              source_question_id,
              source_dataset_id,
              province,
              year,
              number,
              section,
              coalesce(paper_title, occurrence_paper_title) as paper_title,
              source_path,
              canonical_occurrence_rank as rank
            from product.question_occurrences
            where question_id = :question_id
            order by canonical_occurrence_rank nulls last, year desc nulls last, province nulls last
            """,
            question_id=question_id,
        )

    def get_tags(self, question_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            select
              tag_type,
              tag_value,
              sort_order,
              evidence_link_count
            from product.question_tags
            where question_id = :question_id
            order by sort_order nulls last, tag_type, tag_value
            """,
            question_id=question_id,
        )

    def get_knowledge_chunks(self, question_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            select
              knowledge_chunk_id,
              chunk_kind,
              chunk_index,
              title,
              content,
              display_path,
              token_estimate,
              metadata
            from product.knowledge_chunks
            where question_id = :question_id
            order by chunk_index nulls last, knowledge_chunk_id
            """,
            question_id=question_id,
        )

    def get_assets(self, question_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            select
              asset_id,
              part_kind,
              part_index,
              image_ordinal,
              image_src,
              object_key,
              is_representative_asset
            from product.question_assets
            where question_id = :question_id
            order by part_kind nulls last, part_index nulls last, image_ordinal nulls last, asset_id
            """,
            question_id=question_id,
        )

    def search_questions(
        self,
        *,
        domain: str | None,
        module_name: str | None,
        question_type: str | None,
        tag: str | None,
        province: str | None,
        year: int | None,
        requires_images: bool | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        filters = ["q.status = 'active'"]
        params: dict[str, Any] = {"limit": limit}

        if domain:
            filters.append("q.domain = :domain")
            params["domain"] = domain
        if module_name:
            filters.append("q.module_name = :module_name")
            params["module_name"] = module_name
        if question_type:
            filters.append("q.question_type = :question_type")
            params["question_type"] = question_type
        if requires_images is not None:
            filters.append("q.visual_required = :requires_images")
            params["requires_images"] = requires_images
        if tag:
            filters.append(
                """
                exists (
                  select 1
                  from product.question_tags qt
                  where qt.question_id = q.question_id
                    and qt.tag_value = :tag
                )
                """
            )
            params["tag"] = tag
        if province:
            filters.append(
                """
                exists (
                  select 1
                  from product.question_occurrences qo
                  where qo.question_id = q.question_id
                    and qo.province = :province
                )
                """
            )
            params["province"] = province
        if year:
            filters.append(
                """
                exists (
                  select 1
                  from product.question_occurrences qo
                  where qo.question_id = q.question_id
                    and qo.year = :year
                )
                """
            )
            params["year"] = year

        where_sql = "\n and ".join(filters)
        return self._fetch_all(
            f"""
            select
              q.question_id,
              q.domain,
              q.module_name,
              q.question_type,
              q.stem,
              q.answer,
              q.visual_required,
              (qe.question_id is not null) as has_explanation,
              q.occurrence_count,
              q.latest_year
            from product.questions q
            left join product.question_explanations qe on qe.question_id = q.question_id
            where {where_sql}
            order by q.latest_year desc nulls last, q.occurrence_count desc nulls last, q.question_id
            limit :limit
            """,
            **params,
        )

    def get_top_knowledge_map_node(
        self,
        *,
        exam_scope: str,
        track: str,
        node_type: str,
        module_name: str | None = None,
    ) -> KnowledgeMapNodeRecord | None:
        filters = [
            "exam_scope = :exam_scope",
            "track = :track",
            "node_type = :node_type",
            "status = 'published'",
            "support_status = 'text_supported'",
            "near5_occurrence_count > 0",
            "tag_type is not null",
        ]
        params: dict[str, Any] = {
            "exam_scope": exam_scope,
            "track": track,
            "node_type": node_type,
        }
        if module_name:
            filters.append("module_name = :module_name")
            params["module_name"] = module_name
        row = self._session.execute(
            text(
                f"""
                select
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count
                from product.knowledge_map_nodes
                where {" and ".join(filters)}
                order by
                  near5_weight desc nulls last,
                  near5_occurrence_count desc nulls last,
                  module_name,
                  tag_value
                limit 1
                """
            ),
            params,
        ).mappings().first()
        if row is None:
            return None
        return KnowledgeMapNodeRecord(
            node_id=str(row["node_id"]),
            exam_scope=str(row["exam_scope"]),
            track=str(row["track"]),
            module_name=str(row["module_name"]),
            node_type=str(row["node_type"]),
            tag_type=str(row["tag_type"]),
            tag_value=str(row["tag_value"]),
            display_path=str(row["display_path"]),
            support_status=str(row["support_status"]),
            near5_window_start_year=int(row["near5_window_start_year"]),
            near5_window_end_year=int(row["near5_window_end_year"]),
            near5_weight=float(row["near5_weight"]),
            near5_occurrence_count=int(row["near5_occurrence_count"]),
        )

    def get_knowledge_map_node_by_tag(
        self,
        *,
        exam_scope: str,
        track: str,
        module_name: str,
        tag_type: str,
        tag_value: str,
    ) -> KnowledgeMapNodeRecord | None:
        row = self._session.execute(
            text(
                """
                select
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  support_status,
                  coalesce(near5_window_start_year, 0) as near5_window_start_year,
                  coalesce(near5_window_end_year, 0) as near5_window_end_year,
                  coalesce(near5_weight, 0) as near5_weight,
                  coalesce(near5_occurrence_count, 0) as near5_occurrence_count
                from product.knowledge_map_nodes
                where exam_scope = :exam_scope
                  and track = :track
                  and module_name = :module_name
                  and tag_type = :tag_type
                  and tag_value = :tag_value
                  and status = 'published'
                order by
                  near5_weight desc nulls last,
                  near5_occurrence_count desc nulls last,
                  node_id
                limit 1
                """
            ),
            {
                "exam_scope": exam_scope,
                "track": track,
                "module_name": module_name,
                "tag_type": tag_type,
                "tag_value": tag_value,
            },
        ).mappings().first()
        if row is None:
            return None
        return KnowledgeMapNodeRecord(
            node_id=str(row["node_id"]),
            exam_scope=str(row["exam_scope"]),
            track=str(row["track"]),
            module_name=str(row["module_name"]),
            node_type=str(row["node_type"]),
            tag_type=str(row["tag_type"]),
            tag_value=str(row["tag_value"]),
            display_path=str(row["display_path"]),
            support_status=str(row["support_status"]),
            near5_window_start_year=int(row["near5_window_start_year"]),
            near5_window_end_year=int(row["near5_window_end_year"]),
            near5_weight=float(row["near5_weight"]),
            near5_occurrence_count=int(row["near5_occurrence_count"]),
        )

    def select_weighted_practice_questions(
        self,
        *,
        module_name: str,
        tag_type: str,
        tag_value: str,
        window_start_year: int,
        window_end_year: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            with params as (
              select
                cast(:module_name as text) as module_name,
                cast(:tag_type as text) as tag_type,
                cast(:tag_value as text) as tag_value,
                cast(:window_start_year as integer) as window_start_year,
                cast(:window_end_year as integer) as window_end_year,
                cast(:limit as integer) as total_limit,
                greatest(1, cast(ceil(cast(:limit as numeric) * 0.70) as integer)) as recent_limit
            ), matching_questions as (
              select
                q.question_id,
                q.domain,
                q.module_name,
                q.question_type,
                q.stem,
                q.answer,
                q.visual_required,
                (qe.question_id is not null) as has_explanation,
                q.occurrence_count,
                q.latest_year,
                max(go.exam_year) filter (
                  where go.exam_year between p.window_start_year and p.window_end_year
                ) as latest_recent_exam_year,
                count(distinct go.occurrence_id) filter (
                  where go.exam_year between p.window_start_year and p.window_end_year
                ) as recent_occurrence_count,
                max(go.exam_year) filter (
                  where go.exam_year < p.window_start_year
                ) as latest_old_exam_year,
                count(distinct go.occurrence_id) filter (
                  where go.exam_year < p.window_start_year
                ) as old_occurrence_count
              from params p
              join product.questions q
                on q.status = 'active'
               and q.domain = 'objective'
               and q.module_name = p.module_name
               and q.visual_required is false
              join product.question_tags qt
                on qt.question_id = q.question_id
               and qt.tag_type = p.tag_type
               and qt.tag_value = p.tag_value
              left join product.question_explanations qe
                on qe.question_id = q.question_id
              join lateral (
                select
                  qo.occurrence_id,
                  coalesce(
                    nullif(
                      substring(
                        coalesce(qo.paper_title, qo.occurrence_paper_title, '')
                        from '([12][0-9]{3})年'
                      ),
                      ''
                    )::integer,
                    qo.year
                  ) as exam_year
                from product.question_occurrences qo
                where qo.question_id = q.question_id
                  and (
                    coalesce(qo.province, '') = '国考'
                    or coalesce(qo.paper_title, qo.occurrence_paper_title, '') ~
                       '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
                    or coalesce(qo.source_path, '') ~
                       '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
                  )
              ) go on true
              group by
                q.question_id,
                q.domain,
                q.module_name,
                q.question_type,
                q.stem,
                q.answer,
                q.visual_required,
                qe.question_id,
                q.occurrence_count,
                q.latest_year
            ), recent_ranked as (
              select
                mq.*,
                row_number() over (
                  order by
                    mq.latest_recent_exam_year desc nulls last,
                    mq.recent_occurrence_count desc,
                    mq.occurrence_count desc nulls last,
                    mq.question_id
                ) as rank
              from matching_questions mq
              where mq.recent_occurrence_count > 0
            ), selected_recent as (
              select
                0 as bucket,
                rr.*
              from recent_ranked rr
              cross join params p
              where rr.rank <= p.recent_limit
            ), old_ranked as (
              select
                mq.*,
                row_number() over (
                  order by
                    mq.latest_old_exam_year desc nulls last,
                    mq.old_occurrence_count desc,
                    mq.occurrence_count desc nulls last,
                    mq.question_id
                ) as rank
              from matching_questions mq
              where mq.recent_occurrence_count = 0
                and mq.old_occurrence_count > 0
            ), selected_old as (
              select
                1 as bucket,
                old_ranked.*
              from old_ranked
              cross join params p
              where old_ranked.rank <= greatest(
                0,
                p.total_limit - (select count(*) from selected_recent)
              )
            )
            select
              question_id,
              domain,
              module_name,
              question_type,
              stem,
              answer,
              visual_required,
              has_explanation,
              occurrence_count,
              latest_year
            from (
              select * from selected_recent
              union all
              select * from selected_old
            ) selected
            order by bucket, rank
            limit :limit
            """,
            module_name=module_name,
            tag_type=tag_type,
            tag_value=tag_value,
            window_start_year=window_start_year,
            window_end_year=window_end_year,
            limit=limit,
        )

    def _fetch_all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        rows = self._session.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]

    def _count_optional_table(self, *, schema: str, table: str) -> int:
        qualified_name = f"{schema}.{table}"
        relation = self._session.execute(
            text("select to_regclass(:qualified_name)"),
            {"qualified_name": qualified_name},
        ).scalar_one()
        if relation is None:
            return 0
        row = self._session.execute(
            text(f"select count(*) from {schema}.{table}")
        ).scalar_one()
        return int(row)
