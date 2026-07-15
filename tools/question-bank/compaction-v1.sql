-- Question bank compaction v1.
--
-- Expected database:
--   A restored copy of data/question-bank-export/db/question-bank-subset-20260702.dump
--
-- Scope:
--   - Objective / non-essay questions are compacted.
--   - Essay questions are excluded from explanation compaction.
--   - Placeholder questions are quarantined.
--   - AI-cleaned explanations are preferred when accepted and not marked for review.
--
-- Run:
--   psql -h 127.0.0.1 -p 55432 -d qb_compaction_20260702 -v ON_ERROR_STOP=1 -f tools/question-bank/compaction-v1.sql

create schema if not exists compaction;

drop materialized view if exists compaction.candidate_members_v1 cascade;
drop materialized view if exists compaction.candidate_groups_v1 cascade;
drop materialized view if exists compaction.candidate_identity cascade;
drop materialized view if exists compaction.question_features cascade;

create materialized view compaction.question_features as
select
  q.id,
  q."datasetId",
  q."sourceQuestionId",
  q."paperId",
  q.cls,
  q.province,
  q."paperTitle",
  q.year,
  q.number,
  q.section,
  q."moduleName",
  q."questionType",
  q."displayLevel1",
  q."displayLevel2",
  q."displayLevel3",
  q."knowledgeFamily",
  q."teachingPoint",
  q."microSkill",
  q.stem,
  q."stemHtml",
  q.options,
  q.answer,
  q.analysis,
  q."originalAnalysis",
  q."easyWrong",
  q."correctRatio",
  q."totalCount",
  q."assetRefs",
  q.classification,
  coalesce(q."assetRefs"->>'canonicalDedupeKey', q.classification->>'canonical_dedupe_key') as old_canonical_key,
  coalesce(q."assetRefs"->'finalAnalysis'->>'source', q.classification->>'final_analysis_source', '') as final_analysis_source,
  coalesce(q."assetRefs"->'finalAnalysis'->>'status', q.classification->>'final_analysis_status', '') as final_analysis_status,
  coalesce((q."assetRefs"->'finalAnalysis'->>'needsReview')::boolean, (q.classification->>'final_analysis_needs_review')::boolean, false) as final_analysis_needs_review,
  coalesce((q."assetRefs"->>'occurrenceCount')::int, null) as asset_occurrence_count,
  coalesce(q."visualRequired", false) as visual_required,
  coalesce(q."needsMultimodal", false) as needs_multimodal,
  coalesce(q."sharedContextRequired", false) as shared_context_required,
  coalesce((q.classification->>'is_graphic_reasoning')::boolean, false) as is_graphic_reasoning,
  coalesce(q.classification->>'graphic_reasoning_image_signature', '') as graphic_image_signature,
  coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'materialHtml', '') as material_html,
  coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'materialText', '') as material_text,
  coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'stemHtml', q."stemHtml", '') as best_stem_html,
  coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'assetRoot', '') as asset_root,
  regexp_replace(lower(coalesce(q.stem, '')), '[[:space:][:punct:]]+', '', 'g') as norm_stem,
  regexp_replace(lower(array_to_string(coalesce(q.options, array[]::text[]), '|')), '[[:space:][:punct:]]+', '', 'g') as norm_options,
  regexp_replace(lower(coalesce(q.answer, '')), '[[:space:][:punct:]]+', '', 'g') as norm_answer,
  regexp_replace(lower(coalesce(q.stem, '') || '|' || array_to_string(coalesce(q.options, array[]::text[]), '|') || '|' || coalesce(q.answer, '')), '[[:space:][:punct:]]+', '', 'g') as norm_text,
  md5(regexp_replace(lower(coalesce(q.stem, '') || '|' || array_to_string(coalesce(q.options, array[]::text[]), '|') || '|' || coalesce(q.answer, '')), '[[:space:][:punct:]]+', '', 'g')) as text_signature,
  md5(regexp_replace(lower(coalesce(q.stem, '') || '|' || array_to_string(coalesce(q.options, array[]::text[]), '|')), '[[:space:][:punct:]]+', '', 'g')) as stem_options_signature,
  case
    when q."moduleName" = '申论' then false
    when coalesce(q.analysis, '') = '' then false
    when coalesce(q."assetRefs"->'finalAnalysis'->>'source', q.classification->>'final_analysis_source', '') = 'ai_rewrite'
      and coalesce(q."assetRefs"->'finalAnalysis'->>'status', q.classification->>'final_analysis_status', '') in ('ai_rewrite_accepted', 'accepted')
      and not coalesce((q."assetRefs"->'finalAnalysis'->>'needsReview')::boolean, (q.classification->>'final_analysis_needs_review')::boolean, false)
      then true
    else false
  end as prefer_ai_analysis,
  case
    when q."moduleName" = '申论' then null
    when coalesce(q.analysis, '') = '' then null
    when coalesce(q."assetRefs"->'finalAnalysis'->>'source', q.classification->>'final_analysis_source', '') = 'ai_rewrite'
      and coalesce(q."assetRefs"->'finalAnalysis'->>'status', q.classification->>'final_analysis_status', '') in ('ai_rewrite_accepted', 'accepted')
      and not coalesce((q."assetRefs"->'finalAnalysis'->>'needsReview')::boolean, (q.classification->>'final_analysis_needs_review')::boolean, false)
      then q.analysis
    when coalesce(q.analysis, '') <> '' then q.analysis
    else q."originalAnalysis"
  end as preferred_analysis
from "QuestionBankQuestion" q;

create index question_features_id_idx on compaction.question_features(id);
create index question_features_text_signature_idx on compaction.question_features(text_signature);
create index question_features_stem_options_signature_idx on compaction.question_features(stem_options_signature);
create index question_features_module_idx on compaction.question_features("moduleName", "questionType");
create index question_features_old_key_idx on compaction.question_features(old_canonical_key);

create materialized view compaction.candidate_identity as
select
  f.*,
  (f."moduleName" = '申论') as is_essay,
  (f.stem ~ '题目正在全力以赴征集|该题正在征集|题目暂缺|暂无题目|正确答案默认设置|该题选择A项默认得分') as is_placeholder,
  case
    when f."moduleName" = '申论' then 'essay_excluded'
    when f.stem ~ '题目正在全力以赴征集|该题正在征集|题目暂缺|暂无题目|正确答案默认设置|该题选择A项默认得分' then 'placeholder_excluded'
    when f.is_graphic_reasoning or f."questionType"='图形推理' then
      case when f.graphic_image_signature <> '' or f.best_stem_html like '%<img%' then 'graphic_asset_signature' else 'graphic_needs_review' end
    when f.shared_context_required or f.material_html <> '' or f.material_text <> '' then 'material_context_signature'
    when f.visual_required or f.needs_multimodal or f.best_stem_html like '%<img%' then 'visual_html_signature'
    else 'plain_text_signature'
  end as identity_bucket,
  case
    when f."moduleName" = '申论' then null
    when f.stem ~ '题目正在全力以赴征集|该题正在征集|题目暂缺|暂无题目|正确答案默认设置|该题选择A项默认得分' then null
    when f.is_graphic_reasoning or f."questionType"='图形推理' then
      'graphic:' || md5(coalesce(nullif(f.graphic_image_signature,''), f.best_stem_html, '') || '|' || f.norm_answer)
    when f.shared_context_required or f.material_html <> '' or f.material_text <> '' then
      'material:' || md5(regexp_replace(lower(coalesce(f.material_html,'') || '|' || coalesce(f.material_text,'') || '|' || f.norm_text), '[[:space:][:punct:]]+', '', 'g'))
    when f.visual_required or f.needs_multimodal or f.best_stem_html like '%<img%' then
      'visual:' || md5(regexp_replace(lower(coalesce(f.best_stem_html,'') || '|' || f.norm_text), '[[:space:][:punct:]]+', '', 'g'))
    else 'plain:' || f.text_signature
  end as candidate_identity_key
from compaction.question_features f;

create index candidate_identity_key_idx on compaction.candidate_identity(candidate_identity_key);
create index candidate_identity_bucket_idx on compaction.candidate_identity(identity_bucket);
create index candidate_identity_question_idx on compaction.candidate_identity(id);

create materialized view compaction.candidate_groups_v1 as
with active as (
  select *
  from compaction.candidate_identity
  where candidate_identity_key is not null
), grouped as (
  select
    candidate_identity_key,
    min(identity_bucket) as identity_bucket,
    count(*) as member_count,
    count(distinct coalesce(answer,'')) as answer_variants,
    count(distinct coalesce("moduleName",'')) as module_variants,
    count(distinct coalesce("questionType",'')) as type_variants,
    count(distinct coalesce("knowledgeFamily",'')) as knowledge_family_variants,
    sum(case when prefer_ai_analysis then 1 else 0 end) as ai_analysis_count,
    sum(case when preferred_analysis is not null then 1 else 0 end) as analysis_count,
    sum(coalesce(asset_occurrence_count, 1)) as summed_existing_occurrences,
    bool_or(visual_required or needs_multimodal or is_graphic_reasoning) as has_visual_risk,
    bool_or(shared_context_required or material_html <> '' or material_text <> '') as has_material_risk
  from active
  group by candidate_identity_key
), reps as (
  select distinct on (candidate_identity_key)
    candidate_identity_key,
    id as representative_question_id,
    prefer_ai_analysis as representative_uses_ai_analysis,
    final_analysis_source as representative_analysis_source,
    final_analysis_status as representative_analysis_status,
    length(coalesce(preferred_analysis,'')) as representative_analysis_length
  from active
  order by candidate_identity_key,
    prefer_ai_analysis desc,
    length(coalesce(preferred_analysis,'')) desc,
    coalesce(asset_occurrence_count, 1) desc,
    year desc nulls last,
    id
)
select
  'cg_' || substr(md5(g.candidate_identity_key), 1, 20) as candidate_group_id,
  g.candidate_identity_key,
  g.identity_bucket,
  g.member_count,
  g.member_count - 1 as removable_duplicate_count,
  g.answer_variants,
  g.module_variants,
  g.type_variants,
  g.knowledge_family_variants,
  g.ai_analysis_count,
  g.analysis_count,
  g.summed_existing_occurrences,
  g.has_visual_risk,
  g.has_material_risk,
  r.representative_question_id,
  r.representative_uses_ai_analysis,
  r.representative_analysis_source,
  r.representative_analysis_status,
  r.representative_analysis_length,
  case
    when g.member_count = 1 then 'singleton_keep'
    when g.answer_variants > 1 then 'must_review_answer_conflict'
    when g.identity_bucket in ('graphic_asset_signature', 'visual_html_signature') then 'review_visual_cluster'
    when g.identity_bucket = 'material_context_signature' then 'review_material_cluster'
    when g.module_variants > 1 or g.type_variants > 1 or g.knowledge_family_variants > 2 then 'review_taxonomy_conflict'
    else 'auto_merge_plain_exact'
  end as merge_decision
from grouped g
join reps r using (candidate_identity_key);

create unique index candidate_groups_v1_id_idx on compaction.candidate_groups_v1(candidate_group_id);
create index candidate_groups_v1_key_idx on compaction.candidate_groups_v1(candidate_identity_key);
create index candidate_groups_v1_decision_idx on compaction.candidate_groups_v1(merge_decision);

create materialized view compaction.candidate_members_v1 as
select
  g.candidate_group_id,
  g.merge_decision,
  g.representative_question_id,
  c.id as question_id,
  c."sourceQuestionId",
  c."moduleName",
  c."questionType",
  c."knowledgeFamily",
  c.province,
  c.year,
  c."paperTitle",
  c.number,
  c.answer,
  c.prefer_ai_analysis,
  c.final_analysis_source,
  c.final_analysis_status,
  c.asset_occurrence_count,
  c.text_signature,
  c.candidate_identity_key
from compaction.candidate_identity c
join compaction.candidate_groups_v1 g using (candidate_identity_key)
where c.candidate_identity_key is not null;

create index candidate_members_v1_group_idx on compaction.candidate_members_v1(candidate_group_id);
create index candidate_members_v1_question_idx on compaction.candidate_members_v1(question_id);
