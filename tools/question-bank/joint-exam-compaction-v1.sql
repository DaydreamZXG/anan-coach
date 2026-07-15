-- Province joint-exam compaction v1.
--
-- Scope:
--   - Local restored question-bank copy only.
--   - Joint-exam means an occurrence or its paper title contains "联考".
--   - A joint-exam duplicate group requires at least two candidate members that
--     both have joint-exam occurrences.
--   - Visual/material/question rewrites are not semantic-merged here.
--
-- Prerequisite:
--   tools/question-bank/compaction-v1.sql has created compaction.*_v1 views.
--
-- Run:
--   psql -h 127.0.0.1 -p 55432 -d qb_compaction_20260702 -v ON_ERROR_STOP=1 -f tools/question-bank/joint-exam-compaction-v1.sql

create schema if not exists compaction;

drop materialized view if exists compaction.joint_exam_visual_asset_refs_v1;
drop materialized view if exists compaction.joint_exam_candidate_members_v1;
drop materialized view if exists compaction.joint_exam_candidate_groups_v1;
drop materialized view if exists compaction.joint_exam_question_summary_v1;
drop materialized view if exists compaction.joint_exam_occurrences_v1;

create materialized view compaction.joint_exam_occurrences_v1 as
select
  o.id as occurrence_id,
  o."datasetId",
  o."questionId" as question_id,
  o."paperId" as paper_id,
  o."sourceQuestionId",
  o."sourcePaperId",
  o."sourcePath" as occurrence_source_path,
  o.cls,
  o.province,
  o."paperTitle" as occurrence_paper_title,
  p.title as paper_title,
  p."sourcePath" as paper_source_path,
  coalesce(o.year, p.year) as year,
  o.number,
  o.section,
  o.answer as occurrence_answer,
  o."correctRatio",
  o."totalCount"
from "QuestionBankQuestionOccurrence" o
join "QuestionBankPaper" p on p.id = o."paperId"
where o."paperTitle" like '%联考%'
   or p.title like '%联考%';

create index joint_exam_occurrences_question_idx on compaction.joint_exam_occurrences_v1(question_id);
create index joint_exam_occurrences_paper_idx on compaction.joint_exam_occurrences_v1(paper_id);

create materialized view compaction.joint_exam_question_summary_v1 as
select
  question_id,
  count(*) as joint_occurrence_count,
  count(distinct province) as joint_province_count,
  count(distinct paper_id) as joint_paper_count,
  count(distinct coalesce(year::text, '')) filter (where year is not null) as joint_year_count,
  string_agg(distinct province, '、') as joint_provinces,
  string_agg(distinct coalesce(year::text, ''), '、') filter (where year is not null) as joint_years,
  string_agg(distinct coalesce(occurrence_paper_title, paper_title), ' | ') as joint_paper_titles,
  string_agg(distinct "sourceQuestionId", ' | ') as joint_source_question_ids,
  string_agg(distinct occurrence_source_path, ' | ') as joint_source_paths
from compaction.joint_exam_occurrences_v1
group by question_id;

create unique index joint_exam_question_summary_question_idx
  on compaction.joint_exam_question_summary_v1(question_id);

create materialized view compaction.joint_exam_candidate_groups_v1 as
with grouped as (
  select
    g.candidate_group_id,
    g.candidate_identity_key,
    g.identity_bucket,
    g.merge_decision,
    g.member_count,
    g.removable_duplicate_count,
    g.answer_variants,
    g.module_variants,
    g.type_variants,
    g.knowledge_family_variants,
    g.ai_analysis_count,
    g.analysis_count,
    g.summed_existing_occurrences,
    g.has_visual_risk,
    g.has_material_risk,
    g.representative_question_id,
    g.representative_uses_ai_analysis,
    g.representative_analysis_source,
    g.representative_analysis_status,
    g.representative_analysis_length,
    count(distinct m.question_id) filter (where j.question_id is not null) as joint_member_count,
    count(distinct m.question_id) filter (where j.question_id is null) as non_joint_member_count,
    coalesce(sum(j.joint_occurrence_count), 0) as joint_occurrence_count,
    count(distinct nullif(j.joint_provinces, '')) as joint_member_province_sets,
    string_agg(distinct j.joint_provinces, ' || ') filter (where j.joint_provinces is not null) as joint_provinces_by_member,
    string_agg(distinct j.joint_years, ' || ') filter (where j.joint_years is not null) as joint_years_by_member,
    string_agg(distinct j.joint_paper_titles, ' || ') filter (where j.joint_paper_titles is not null) as joint_paper_titles
  from compaction.candidate_groups_v1 g
  join compaction.candidate_members_v1 m using (candidate_group_id)
  left join compaction.joint_exam_question_summary_v1 j on j.question_id = m.question_id
  group by
    g.candidate_group_id,
    g.candidate_identity_key,
    g.identity_bucket,
    g.merge_decision,
    g.member_count,
    g.removable_duplicate_count,
    g.answer_variants,
    g.module_variants,
    g.type_variants,
    g.knowledge_family_variants,
    g.ai_analysis_count,
    g.analysis_count,
    g.summed_existing_occurrences,
    g.has_visual_risk,
    g.has_material_risk,
    g.representative_question_id,
    g.representative_uses_ai_analysis,
    g.representative_analysis_source,
    g.representative_analysis_status,
    g.representative_analysis_length
)
select
  *,
  case
    when joint_member_count > 1 and merge_decision = 'auto_merge_plain_exact'
      then 'joint_auto_merge_candidate'
    when joint_member_count > 1 and merge_decision = 'review_visual_cluster'
      then 'joint_visual_review'
    when joint_member_count > 1 and merge_decision = 'review_taxonomy_conflict'
      then 'joint_taxonomy_review'
    when joint_member_count = 1 and member_count > 1
      then 'partial_overlap_not_joint_duplicate'
    when joint_member_count > 0
      then 'joint_singleton_or_nonduplicate'
    else 'not_joint_exam'
  end as joint_scope_decision
from grouped
where joint_member_count > 0;

create unique index joint_exam_candidate_groups_id_idx
  on compaction.joint_exam_candidate_groups_v1(candidate_group_id);
create index joint_exam_candidate_groups_scope_idx
  on compaction.joint_exam_candidate_groups_v1(joint_scope_decision);

create materialized view compaction.joint_exam_candidate_members_v1 as
select
  g.joint_scope_decision,
  g.candidate_group_id,
  g.merge_decision,
  g.identity_bucket,
  g.member_count,
  g.joint_member_count,
  g.non_joint_member_count,
  g.joint_occurrence_count as group_joint_occurrence_count,
  m.representative_question_id,
  m.question_id,
  m."sourceQuestionId",
  m."moduleName",
  m."questionType",
  m."knowledgeFamily",
  m.province as representative_province,
  m.year as representative_year,
  m."paperTitle" as representative_paper_title,
  m.number as representative_number,
  m.answer,
  m.prefer_ai_analysis,
  m.final_analysis_source,
  m.final_analysis_status,
  m.asset_occurrence_count,
  s.joint_occurrence_count as member_joint_occurrence_count,
  s.joint_province_count as member_joint_province_count,
  s.joint_paper_count as member_joint_paper_count,
  s.joint_provinces as member_joint_provinces,
  s.joint_years as member_joint_years,
  s.joint_paper_titles as member_joint_paper_titles,
  s.joint_source_question_ids as member_joint_source_question_ids,
  f.stem,
  f.best_stem_html,
  f.asset_root
from compaction.joint_exam_candidate_groups_v1 g
join compaction.candidate_members_v1 m using (candidate_group_id)
left join compaction.joint_exam_question_summary_v1 s on s.question_id = m.question_id
join compaction.question_features f on f.id = m.question_id
where g.joint_scope_decision in (
  'joint_auto_merge_candidate',
  'joint_visual_review',
  'joint_taxonomy_review',
  'partial_overlap_not_joint_duplicate'
);

create index joint_exam_candidate_members_group_idx
  on compaction.joint_exam_candidate_members_v1(candidate_group_id);
create index joint_exam_candidate_members_question_idx
  on compaction.joint_exam_candidate_members_v1(question_id);
create index joint_exam_candidate_members_scope_idx
  on compaction.joint_exam_candidate_members_v1(joint_scope_decision);

create materialized view compaction.joint_exam_visual_asset_refs_v1 as
with visual_members as (
  select
    m.joint_scope_decision,
    m.candidate_group_id,
    m.question_id,
    m."sourceQuestionId",
    f.asset_root,
    f."assetRefs"
  from compaction.joint_exam_candidate_members_v1 m
  join compaction.question_features f on f.id = m.question_id
  where m.joint_scope_decision = 'joint_visual_review'
), html_parts as (
  select
    joint_scope_decision,
    candidate_group_id,
    question_id,
    "sourceQuestionId",
    asset_root,
    'stem_html'::text as part_kind,
    0::int as part_index,
    coalesce("assetRefs"->'fenbiFinalDisplay'->>'stemHtml', '') as html
  from visual_members
  union all
  select
    v.joint_scope_decision,
    v.candidate_group_id,
    v.question_id,
    v."sourceQuestionId",
    v.asset_root,
    'option_html'::text as part_kind,
    x.ord::int as part_index,
    x.html
  from visual_members v
  cross join lateral jsonb_array_elements_text(
    coalesce(v."assetRefs"->'fenbiFinalDisplay'->'optionHtmls', '[]'::jsonb)
  ) with ordinality as x(html, ord)
  union all
  select
    joint_scope_decision,
    candidate_group_id,
    question_id,
    "sourceQuestionId",
    asset_root,
    'material_html'::text as part_kind,
    0::int as part_index,
    coalesce("assetRefs"->'fenbiFinalDisplay'->>'materialHtml', '') as html
  from visual_members
), image_refs as (
  select
    h.*,
    r.image_ordinal::int,
    r.match[1] as image_src
  from html_parts h
  cross join lateral regexp_matches(h.html, 'src=["'']([^"'']+)["'']', 'g')
    with ordinality as r(match, image_ordinal)
)
select
  joint_scope_decision,
  candidate_group_id,
  question_id,
  "sourceQuestionId",
  part_kind,
  part_index,
  image_ordinal,
  asset_root as remote_asset_root,
  image_src,
  case
    when asset_root like '/opt/peipeishua/data/source-question-bank/%' then
      regexp_replace(asset_root, '^/opt/peipeishua/data/source-question-bank/?', 'data/question-bank-export/files/source-question-bank/') || '/' || image_src
    else null
  end as local_relative_path
from image_refs
where image_src is not null
  and image_src <> '';

create index joint_exam_visual_asset_refs_group_idx
  on compaction.joint_exam_visual_asset_refs_v1(candidate_group_id);
create index joint_exam_visual_asset_refs_question_idx
  on compaction.joint_exam_visual_asset_refs_v1(question_id);
