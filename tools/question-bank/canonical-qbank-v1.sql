-- Canonical question-bank v1 import.
--
-- This builds a local derived schema from the restored PeiPeiShua export.
-- It does not mutate source tables, exported files, or production servers.
--
-- Important reproducibility rule:
--   canonical v1 consumes the approved 2026-07-02 CSV artifacts under
--   reports/question-bank instead of recomputing duplicate candidate groups.
--   The original candidate discovery SQL is retained as research evidence, but
--   recomputing text signatures on a different PostgreSQL locale can change
--   candidate group ids and over-merge questions.
--
-- Accepted compaction policy:
--   - merge all compaction.candidate_groups_v1 auto_merge_plain_exact groups;
--   - merge only the joint-exam visual groups whose image sequences were
--     confirmed by SHA-256 evidence;
--   - keep taxonomy/material/other visual review groups as separate canonical
--     questions and place the groups in a review queue;
--   - keep essay questions one-to-one and do not create explanation rows;
--   - quarantine placeholder/non-candidate objective questions.
--
-- Run from repository root:
--   psql -h 127.0.0.1 -p 55432 -d qb_compaction_20260702 -v ON_ERROR_STOP=1 -f tools/question-bank/canonical-qbank-v1.sql

\set ON_ERROR_STOP on

drop schema if exists compaction cascade;
create schema compaction;

create table compaction.candidate_groups_v1 (
  candidate_group_id text primary key,
  candidate_identity_key text,
  identity_bucket text,
  member_count integer,
  removable_duplicate_count integer,
  answer_variants integer,
  module_variants integer,
  type_variants integer,
  knowledge_family_variants integer,
  ai_analysis_count integer,
  analysis_count integer,
  summed_existing_occurrences integer,
  has_visual_risk boolean,
  has_material_risk boolean,
  representative_question_id text,
  representative_uses_ai_analysis boolean,
  representative_analysis_source text,
  representative_analysis_status text,
  representative_analysis_length integer,
  merge_decision text
);

\copy compaction.candidate_groups_v1 from 'reports/question-bank/candidate-groups-v1-20260702.csv' with (format csv, header true)

create table compaction.candidate_duplicate_members_v1 (
  candidate_group_id text,
  merge_decision text,
  representative_question_id text,
  question_id text,
  "sourceQuestionId" text,
  "moduleName" text,
  "questionType" text,
  "knowledgeFamily" text,
  province text,
  year integer,
  "paperTitle" text,
  number integer,
  answer text,
  prefer_ai_analysis boolean,
  final_analysis_source text,
  final_analysis_status text,
  asset_occurrence_count integer,
  text_signature text,
  candidate_identity_key text
);

\copy compaction.candidate_duplicate_members_v1 from 'reports/question-bank/candidate-duplicate-members-v1-20260702.csv' with (format csv, header true)

create table compaction.candidate_members_v1 as
select
  d.candidate_group_id,
  d.merge_decision,
  d.representative_question_id,
  d.question_id,
  d."sourceQuestionId",
  d."moduleName",
  d."questionType",
  d."knowledgeFamily",
  d.province,
  d.year,
  d."paperTitle",
  d.number,
  d.answer,
  d.prefer_ai_analysis,
  d.final_analysis_source,
  d.final_analysis_status,
  d.asset_occurrence_count,
  d.text_signature,
  d.candidate_identity_key
from compaction.candidate_duplicate_members_v1 d
union all
select
  g.candidate_group_id,
  g.merge_decision,
  g.representative_question_id,
  q.id as question_id,
  q."sourceQuestionId",
  q."moduleName",
  q."questionType",
  q."knowledgeFamily",
  q.province,
  q.year,
  q."paperTitle",
  q.number,
  q.answer,
  coalesce(
    q."assetRefs"->'finalAnalysis'->>'source',
    q.classification->>'final_analysis_source',
    ''
  ) = 'ai_rewrite' as prefer_ai_analysis,
  coalesce(q."assetRefs"->'finalAnalysis'->>'source', q.classification->>'final_analysis_source', '') as final_analysis_source,
  coalesce(q."assetRefs"->'finalAnalysis'->>'status', q.classification->>'final_analysis_status', '') as final_analysis_status,
  coalesce((q."assetRefs"->>'occurrenceCount')::int, 1) as asset_occurrence_count,
  null::text as text_signature,
  g.candidate_identity_key
from compaction.candidate_groups_v1 g
join "QuestionBankQuestion" q on q.id = g.representative_question_id
where g.member_count = 1;

alter table compaction.candidate_members_v1
  add primary key (question_id);

create index candidate_members_v1_group_idx
  on compaction.candidate_members_v1(candidate_group_id);
create index candidate_groups_v1_decision_idx
  on compaction.candidate_groups_v1(merge_decision);

create table compaction.candidate_identity as
select
  q.id,
  q."moduleName" = '申论' as is_essay,
  case
    when q."moduleName" = '申论' then 'essay_excluded'
    when m.question_id is null then 'placeholder_excluded'
    else g.identity_bucket
  end as identity_bucket,
  case
    when q."moduleName" = '申论' then null::text
    when m.question_id is null then null::text
    else g.candidate_identity_key
  end as candidate_identity_key
from "QuestionBankQuestion" q
left join compaction.candidate_members_v1 m on m.question_id = q.id
left join compaction.candidate_groups_v1 g on g.candidate_group_id = m.candidate_group_id;

create index candidate_identity_question_idx
  on compaction.candidate_identity(id);

create table compaction.question_features as
select
  q.id,
  coalesce(q."assetRefs"->'finalAnalysis'->>'source', q.classification->>'final_analysis_source', '') as final_analysis_source,
  coalesce(q."assetRefs"->'finalAnalysis'->>'status', q.classification->>'final_analysis_status', '') as final_analysis_status,
  coalesce(
    (q."assetRefs"->'finalAnalysis'->>'needsReview')::boolean,
    (q.classification->>'final_analysis_needs_review')::boolean,
    false
  ) as final_analysis_needs_review,
  case
    when q."moduleName" = '申论' then false
    when coalesce(q.analysis, '') = '' then false
    when coalesce(q."assetRefs"->'finalAnalysis'->>'source', q.classification->>'final_analysis_source', '') = 'ai_rewrite'
      and coalesce(q."assetRefs"->'finalAnalysis'->>'status', q.classification->>'final_analysis_status', '') in ('ai_rewrite_accepted', 'accepted')
      and not coalesce(
        (q."assetRefs"->'finalAnalysis'->>'needsReview')::boolean,
        (q.classification->>'final_analysis_needs_review')::boolean,
        false
      )
      then true
    else false
  end as prefer_ai_analysis,
  case
    when q."moduleName" = '申论' then null
    when coalesce(q.analysis, '') = '' then null
    when coalesce(q."assetRefs"->'finalAnalysis'->>'source', q.classification->>'final_analysis_source', '') = 'ai_rewrite'
      and coalesce(q."assetRefs"->'finalAnalysis'->>'status', q.classification->>'final_analysis_status', '') in ('ai_rewrite_accepted', 'accepted')
      and not coalesce(
        (q."assetRefs"->'finalAnalysis'->>'needsReview')::boolean,
        (q.classification->>'final_analysis_needs_review')::boolean,
        false
      )
      then q.analysis
    when coalesce(q.analysis, '') <> '' then q.analysis
    else q."originalAnalysis"
  end as preferred_analysis
from "QuestionBankQuestion" q;

alter table compaction.question_features
  add primary key (id);

drop schema if exists canonical_qbank cascade;
create schema canonical_qbank;

create table canonical_qbank.import_policy (
  policy_key text primary key,
  policy_value jsonb not null,
  created_at timestamp(3) without time zone not null default current_timestamp
);

insert into canonical_qbank.import_policy(policy_key, policy_value)
values
  ('canonical_qbank_version', jsonb_build_object('version', 'v1', 'date', '2026-07-02')),
  ('merge_policy', jsonb_build_object(
    'auto_merge_plain_exact', 'accepted',
    'joint_visual_asset_exact_supported', 'accepted',
    'review_taxonomy_conflict', 'keep_separate_review_queue',
    'review_material_cluster', 'keep_separate_review_queue',
    'review_visual_cluster_without_asset_evidence', 'keep_separate_review_queue',
    'essay', 'keep_one_to_one_without_explanation_compaction',
    'placeholder', 'quarantine'
  )),
  ('non_semantic_merge_rule', jsonb_build_object(
    'light_rewrites_same_idea', 'keep_separate',
    'fuzzy_or_semantic_dedupe', 'disabled'
  ));

create table canonical_qbank.accepted_visual_asset_merge_group (
  candidate_group_id text primary key,
  evidence_decision text not null,
  evidence_source text not null
);

insert into canonical_qbank.accepted_visual_asset_merge_group
  (candidate_group_id, evidence_decision, evidence_source)
values
  (
    'cg_20f265b0abacbed8982a',
    'asset_exact_match_supported',
    'reports/question-bank/joint-exam-visual-asset-hash-summary-v1-20260702.csv'
  ),
  (
    'cg_62bd97fb40054f6af698',
    'asset_exact_match_supported',
    'reports/question-bank/joint-exam-visual-asset-hash-summary-v1-20260702.csv'
  );

create table canonical_qbank.accepted_merge_group as
select
  g.candidate_group_id,
  g.candidate_identity_key,
  g.identity_bucket,
  g.merge_decision,
  'auto_plain_exact'::text as accepted_merge_policy,
  'tools/question-bank/compaction-v1.sql'::text as evidence_source,
  g.member_count,
  g.removable_duplicate_count,
  g.representative_question_id
from compaction.candidate_groups_v1 g
where g.merge_decision = 'auto_merge_plain_exact'
union all
select
  g.candidate_group_id,
  g.candidate_identity_key,
  g.identity_bucket,
  g.merge_decision,
  'visual_asset_exact_supported'::text as accepted_merge_policy,
  v.evidence_source,
  g.member_count,
  g.removable_duplicate_count,
  g.representative_question_id
from compaction.candidate_groups_v1 g
join canonical_qbank.accepted_visual_asset_merge_group v
  on v.candidate_group_id = g.candidate_group_id;

alter table canonical_qbank.accepted_merge_group
  add primary key (candidate_group_id);

create table canonical_qbank.source_question_map as
with candidate_sources as (
  select
    case
      when amg.candidate_group_id is not null then
        'cq_' || substr(md5('accepted_group:' || g.candidate_group_id), 1, 24)
      else
        'cq_' || substr(md5('source_question:' || m.question_id), 1, 24)
    end as canonical_question_id,
    m.question_id as source_question_id,
    q."datasetId" as source_dataset_id,
    q."sourceQuestionId" as source_question_key,
    m.candidate_group_id,
    g.candidate_identity_key,
    g.identity_bucket,
    g.merge_decision as candidate_merge_decision,
    coalesce(amg.accepted_merge_policy, 'keep_separate_' || g.merge_decision) as merge_policy,
    coalesce(amg.evidence_source, 'not_accepted_for_merge') as merge_evidence_source,
    case
      when amg.candidate_group_id is not null then amg.representative_question_id
      else m.question_id
    end as representative_source_question_id,
    case
      when amg.candidate_group_id is not null and m.question_id = amg.representative_question_id then 'representative'
      when amg.candidate_group_id is not null then 'merged_member'
      else 'kept_separate'
    end as source_role,
    (amg.candidate_group_id is not null) as is_merged,
    q."moduleName" as module_name,
    q."questionType" as question_type,
    q.province,
    q.year,
    q."paperTitle" as paper_title,
    q.number
  from compaction.candidate_members_v1 m
  join compaction.candidate_groups_v1 g using (candidate_group_id)
  join "QuestionBankQuestion" q on q.id = m.question_id
  left join canonical_qbank.accepted_merge_group amg
    on amg.candidate_group_id = g.candidate_group_id
), essay_sources as (
  select
    'cq_' || substr(md5('source_question:' || q.id), 1, 24) as canonical_question_id,
    q.id as source_question_id,
    q."datasetId" as source_dataset_id,
    q."sourceQuestionId" as source_question_key,
    null::text as candidate_group_id,
    null::text as candidate_identity_key,
    'essay_excluded'::text as identity_bucket,
    'essay_keep_one_to_one'::text as candidate_merge_decision,
    'essay_keep_one_to_one'::text as merge_policy,
    'essay_questions_are_not_explanation_compacted'::text as merge_evidence_source,
    q.id as representative_source_question_id,
    'essay_source'::text as source_role,
    false as is_merged,
    q."moduleName" as module_name,
    q."questionType" as question_type,
    q.province,
    q.year,
    q."paperTitle" as paper_title,
    q.number
  from "QuestionBankQuestion" q
  where q."moduleName" = '申论'
), all_sources as (
  select * from candidate_sources
  union all
  select * from essay_sources
)
select
  *,
  (source_question_id = representative_source_question_id) as is_representative,
  row_number() over (
    partition by canonical_question_id
    order by
      (source_question_id = representative_source_question_id) desc,
      year desc nulls last,
      province,
      paper_title,
      number nulls last,
      source_question_id
  ) as source_member_rank
from all_sources;

alter table canonical_qbank.source_question_map
  add primary key (source_question_id);

create index source_question_map_canonical_idx
  on canonical_qbank.source_question_map(canonical_question_id);
create index source_question_map_group_idx
  on canonical_qbank.source_question_map(candidate_group_id);
create index source_question_map_policy_idx
  on canonical_qbank.source_question_map(merge_policy);

create table canonical_qbank.rejected_source_question as
select
  ci.id as source_question_id,
  q."datasetId" as source_dataset_id,
  q."sourceQuestionId" as source_question_key,
  ci.identity_bucket as rejection_reason,
  q."moduleName" as module_name,
  q."questionType" as question_type,
  q.province,
  q.year,
  q."paperTitle" as paper_title,
  q.number,
  count(o.id) as occurrence_count
from compaction.candidate_identity ci
join "QuestionBankQuestion" q on q.id = ci.id
left join "QuestionBankQuestionOccurrence" o on o."questionId" = q.id
where ci.candidate_identity_key is null
  and ci.is_essay = false
group by
  ci.id,
  q."datasetId",
  q."sourceQuestionId",
  ci.identity_bucket,
  q."moduleName",
  q."questionType",
  q.province,
  q.year,
  q."paperTitle",
  q.number;

alter table canonical_qbank.rejected_source_question
  add primary key (source_question_id);

create table canonical_qbank.canonical_question as
with source_stats as (
  select
    m.canonical_question_id,
    count(*) as source_member_count,
    count(*) filter (where m.is_merged) as merged_source_member_count,
    string_agg(m.source_question_id, ' | ' order by m.source_member_rank) as source_question_ids,
    string_agg(m.source_question_key, ' | ' order by m.source_member_rank) as source_question_keys
  from canonical_qbank.source_question_map m
  group by m.canonical_question_id
), occurrence_stats as (
  select
    m.canonical_question_id,
    count(o.id) as occurrence_count,
    count(distinct o.province) as occurrence_province_count,
    count(distinct o."paperId") as occurrence_paper_count,
    min(coalesce(o.year, p.year)) as first_year,
    max(coalesce(o.year, p.year)) as latest_year,
    string_agg(distinct o.province, '、') filter (where o.province <> '') as occurrence_provinces
  from canonical_qbank.source_question_map m
  left join "QuestionBankQuestionOccurrence" o on o."questionId" = m.source_question_id
  left join "QuestionBankPaper" p on p.id = o."paperId"
  group by m.canonical_question_id
), tag_stats as (
  select
    m.canonical_question_id,
    count(qt.*) as source_tag_link_count,
    count(distinct qt."tagId") as distinct_tag_count
  from canonical_qbank.source_question_map m
  left join "QuestionBankQuestionTag" qt on qt."questionId" = m.source_question_id
  group by m.canonical_question_id
), chunk_stats as (
  select
    m.canonical_question_id,
    count(k.*) as knowledge_chunk_count
  from canonical_qbank.source_question_map m
  left join "QuestionBankKnowledgeChunk" k on k."questionId" = m.source_question_id
  group by m.canonical_question_id
)
select
  m.canonical_question_id,
  case when q."moduleName" = '申论' then 'essay' else 'objective' end as question_domain,
  case
    when m.merge_policy in ('auto_plain_exact', 'visual_asset_exact_supported') then 'merged'
    when m.merge_policy like 'keep_separate_review_%' then 'needs_identity_review'
    else 'active'
  end as canonical_status,
  m.merge_policy,
  m.merge_evidence_source,
  m.candidate_group_id,
  m.identity_bucket,
  m.candidate_merge_decision,
  m.representative_source_question_id,
  q."datasetId" as source_dataset_id,
  q.cls,
  q."moduleName" as module_name,
  q."questionType" as question_type,
  q."displayLevel1" as display_level1,
  q."displayLevel2" as display_level2,
  q."displayLevel3" as display_level3,
  q."knowledgeFamily" as knowledge_family,
  q."teachingPoint" as teaching_point,
  q."microSkill" as micro_skill,
  q.stem,
  q."stemHtml" as stem_html,
  q.options as option_texts,
  coalesce(q."assetRefs"->'fenbiFinalDisplay'->'optionHtmls', '[]'::jsonb) as option_htmls,
  q.answer,
  q."sharedContextRequired" as shared_context_required,
  q."visualRequired" as visual_required,
  q."needsMultimodal" as needs_multimodal,
  q."groupRange" as group_range,
  q."groupReason" as group_reason,
  q."assetRefs"->'fenbiFinalDisplay'->>'materialId' as material_id,
  q."assetRefs"->'fenbiFinalDisplay'->>'materialHtml' as material_html,
  q."assetRefs"->'fenbiFinalDisplay'->>'materialText' as material_text,
  q.difficulty,
  q.rate,
  q."correctRatio" as representative_correct_ratio,
  q."totalCount" as representative_total_count,
  q."easyWrong" as representative_easy_wrong,
  q."mostWrongAnswer" as representative_most_wrong_answer,
  coalesce(ss.source_member_count, 0) as source_member_count,
  coalesce(ss.merged_source_member_count, 0) as merged_source_member_count,
  coalesce(os.occurrence_count, 0) as occurrence_count,
  coalesce(os.occurrence_province_count, 0) as occurrence_province_count,
  coalesce(os.occurrence_paper_count, 0) as occurrence_paper_count,
  os.first_year,
  os.latest_year,
  os.occurrence_provinces,
  coalesce(ts.source_tag_link_count, 0) as source_tag_link_count,
  coalesce(ts.distinct_tag_count, 0) as distinct_tag_count,
  coalesce(cs.knowledge_chunk_count, 0) as knowledge_chunk_count,
  ss.source_question_ids,
  ss.source_question_keys,
  q.classification as representative_classification,
  q."assetRefs" as representative_asset_refs,
  current_timestamp as built_at
from canonical_qbank.source_question_map m
join "QuestionBankQuestion" q on q.id = m.source_question_id
left join source_stats ss on ss.canonical_question_id = m.canonical_question_id
left join occurrence_stats os on os.canonical_question_id = m.canonical_question_id
left join tag_stats ts on ts.canonical_question_id = m.canonical_question_id
left join chunk_stats cs on cs.canonical_question_id = m.canonical_question_id
where m.is_representative;

alter table canonical_qbank.canonical_question
  add primary key (canonical_question_id);

alter table canonical_qbank.source_question_map
  add constraint source_question_map_canonical_fk
  foreign key (canonical_question_id)
  references canonical_qbank.canonical_question(canonical_question_id);

create index canonical_question_domain_idx
  on canonical_qbank.canonical_question(question_domain);
create index canonical_question_module_type_idx
  on canonical_qbank.canonical_question(module_name, question_type);
create index canonical_question_display_path_idx
  on canonical_qbank.canonical_question(display_level1, display_level2, display_level3);
create index canonical_question_status_idx
  on canonical_qbank.canonical_question(canonical_status);

create table canonical_qbank.exam_paper as
select distinct
  p.id as source_paper_row_id,
  p."datasetId" as source_dataset_id,
  p."sourcePaperId" as source_paper_key,
  p.cls,
  p.province,
  p.title,
  p.year,
  p."sourcePath" as source_path,
  p.raw,
  p."assetRefs" as asset_refs
from "QuestionBankPaper" p
join "QuestionBankQuestionOccurrence" o on o."paperId" = p.id
join canonical_qbank.source_question_map m on m.source_question_id = o."questionId";

alter table canonical_qbank.exam_paper
  add primary key (source_paper_row_id);

create index exam_paper_year_province_idx
  on canonical_qbank.exam_paper(year desc nulls last, province, title);

create table canonical_qbank.question_occurrence as
select
  o.id as source_occurrence_id,
  m.canonical_question_id,
  m.source_question_id,
  o."datasetId" as source_dataset_id,
  o."paperId" as source_paper_row_id,
  p."sourcePaperId" as source_paper_key,
  o."sourceQuestionId" as source_occurrence_question_key,
  o."sourcePaperId" as source_occurrence_paper_key,
  o."sourcePath" as source_path,
  o.cls,
  o.province,
  o."paperTitle" as occurrence_paper_title,
  p.title as paper_title,
  coalesce(o.year, p.year) as year,
  o.number,
  o.section,
  o.answer as occurrence_answer,
  o.rate,
  o."correctRatio" as occurrence_correct_ratio,
  o."totalCount" as occurrence_total_count,
  o."easyWrong" as occurrence_easy_wrong,
  o."mostWrongAnswer" as occurrence_most_wrong_answer,
  o.raw,
  row_number() over (
    partition by m.canonical_question_id
    order by coalesce(o.year, p.year) desc nulls last, o.province, p.title, o.number nulls last, o.id
  ) as canonical_occurrence_rank
from "QuestionBankQuestionOccurrence" o
join canonical_qbank.source_question_map m on m.source_question_id = o."questionId"
join "QuestionBankPaper" p on p.id = o."paperId";

alter table canonical_qbank.question_occurrence
  add primary key (source_occurrence_id);
alter table canonical_qbank.question_occurrence
  add constraint question_occurrence_canonical_fk
  foreign key (canonical_question_id)
  references canonical_qbank.canonical_question(canonical_question_id);
alter table canonical_qbank.question_occurrence
  add constraint question_occurrence_paper_fk
  foreign key (source_paper_row_id)
  references canonical_qbank.exam_paper(source_paper_row_id);

create index question_occurrence_canonical_idx
  on canonical_qbank.question_occurrence(canonical_question_id, canonical_occurrence_rank);
create index question_occurrence_paper_order_idx
  on canonical_qbank.question_occurrence(source_paper_row_id, number nulls last, source_occurrence_id);
create index question_occurrence_year_province_idx
  on canonical_qbank.question_occurrence(year desc nulls last, province);

create table canonical_qbank.canonical_explanation as
select
  m.canonical_question_id,
  m.representative_source_question_id,
  f.preferred_analysis,
  q."originalAnalysis" as original_analysis,
  q.analysis as stored_analysis,
  f.final_analysis_source as analysis_source,
  f.final_analysis_status as analysis_status,
  f.final_analysis_needs_review as analysis_needs_review,
  f.prefer_ai_analysis,
  length(coalesce(f.preferred_analysis, '')) as preferred_analysis_length,
  q."assetRefs"->'finalAnalysis' as final_analysis_asset_refs,
  current_timestamp as built_at
from canonical_qbank.source_question_map m
join "QuestionBankQuestion" q on q.id = m.source_question_id
join compaction.question_features f on f.id = m.source_question_id
where m.is_representative
  and q."moduleName" <> '申论'
  and f.preferred_analysis is not null;

alter table canonical_qbank.canonical_explanation
  add primary key (canonical_question_id);
alter table canonical_qbank.canonical_explanation
  add constraint canonical_explanation_question_fk
  foreign key (canonical_question_id)
  references canonical_qbank.canonical_question(canonical_question_id);

create index canonical_explanation_source_idx
  on canonical_qbank.canonical_explanation(analysis_source, analysis_status);

create table canonical_qbank.question_asset as
with display_parts as (
  select
    m.canonical_question_id,
    m.source_question_id,
    m.is_representative,
    coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'assetRoot', '') as remote_asset_root,
    'stem_html'::text as part_kind,
    0::int as part_index,
    coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'stemHtml', q."stemHtml", '') as html
  from canonical_qbank.source_question_map m
  join "QuestionBankQuestion" q on q.id = m.source_question_id
  union all
  select
    m.canonical_question_id,
    m.source_question_id,
    m.is_representative,
    coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'assetRoot', '') as remote_asset_root,
    'option_html'::text as part_kind,
    x.ord::int as part_index,
    x.html
  from canonical_qbank.source_question_map m
  join "QuestionBankQuestion" q on q.id = m.source_question_id
  cross join lateral jsonb_array_elements_text(
    coalesce(q."assetRefs"->'fenbiFinalDisplay'->'optionHtmls', '[]'::jsonb)
  ) with ordinality as x(html, ord)
  union all
  select
    m.canonical_question_id,
    m.source_question_id,
    m.is_representative,
    coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'assetRoot', '') as remote_asset_root,
    'material_html'::text as part_kind,
    0::int as part_index,
    coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'materialHtml', '') as html
  from canonical_qbank.source_question_map m
  join "QuestionBankQuestion" q on q.id = m.source_question_id
  union all
  select
    m.canonical_question_id,
    m.source_question_id,
    m.is_representative,
    coalesce(q."assetRefs"->'fenbiFinalDisplay'->>'assetRoot', '') as remote_asset_root,
    'original_html'::text as part_kind,
    0::int as part_index,
    coalesce(q."assetRefs"->>'originalHtml', '') as html
  from canonical_qbank.source_question_map m
  join "QuestionBankQuestion" q on q.id = m.source_question_id
), image_refs as (
  select
    p.*,
    r.image_ordinal::int,
    r.match[1] as image_src
  from display_parts p
  cross join lateral regexp_matches(p.html, 'src=["'']([^"'']+)["'']', 'g')
    with ordinality as r(match, image_ordinal)
)
select
  'qa_' || substr(md5(source_question_id || ':' || part_kind || ':' || part_index::text || ':' || image_ordinal::text || ':' || image_src), 1, 28) as question_asset_id,
  canonical_question_id,
  source_question_id,
  is_representative as is_representative_asset,
  part_kind,
  part_index,
  image_ordinal,
  remote_asset_root,
  image_src,
  case
    when image_src like 'http://%' or image_src like 'https://%' then image_src
    when remote_asset_root like '/opt/peipeishua/data/source-question-bank/%' then
      regexp_replace(remote_asset_root, '^/opt/peipeishua/data/source-question-bank/?', 'data/question-bank-export/files/source-question-bank/') || '/' || image_src
    else null
  end as local_or_remote_path,
  html as source_html
from image_refs
where image_src is not null
  and image_src <> '';

alter table canonical_qbank.question_asset
  add primary key (question_asset_id);
alter table canonical_qbank.question_asset
  add constraint question_asset_canonical_fk
  foreign key (canonical_question_id)
  references canonical_qbank.canonical_question(canonical_question_id);

create index question_asset_canonical_idx
  on canonical_qbank.question_asset(canonical_question_id);
create index question_asset_source_question_idx
  on canonical_qbank.question_asset(source_question_id);
create index question_asset_path_idx
  on canonical_qbank.question_asset(local_or_remote_path);

create table canonical_qbank.question_tag_evidence as
select
  'qte_' || substr(md5(m.source_question_id || ':' || qt."tagId"), 1, 28) as question_tag_evidence_id,
  m.canonical_question_id,
  m.source_question_id,
  qt."tagId" as source_tag_id,
  qt.type as source_link_type,
  qt.value as source_link_value,
  qt.source as source_link_source,
  qt."sortOrder" as source_sort_order,
  t.type as tag_type,
  t.value as tag_value,
  qt."createdAt" as source_created_at
from canonical_qbank.source_question_map m
join "QuestionBankQuestionTag" qt on qt."questionId" = m.source_question_id
join "QuestionBankTag" t on t.id = qt."tagId";

alter table canonical_qbank.question_tag_evidence
  add primary key (question_tag_evidence_id);
alter table canonical_qbank.question_tag_evidence
  add constraint question_tag_evidence_canonical_fk
  foreign key (canonical_question_id)
  references canonical_qbank.canonical_question(canonical_question_id);

create index question_tag_evidence_canonical_idx
  on canonical_qbank.question_tag_evidence(canonical_question_id);
create index question_tag_evidence_tag_idx
  on canonical_qbank.question_tag_evidence(tag_type, tag_value);

create table canonical_qbank.canonical_question_tag as
select
  'cqt_' || substr(md5(canonical_question_id || ':' || source_tag_id), 1, 28) as canonical_question_tag_id,
  canonical_question_id,
  source_tag_id,
  tag_type,
  tag_value,
  min(source_sort_order) as sort_order,
  count(*) as evidence_link_count,
  count(distinct source_question_id) as source_question_count,
  string_agg(distinct source_link_source, ' | ') as evidence_sources
from canonical_qbank.question_tag_evidence
group by
  canonical_question_id,
  source_tag_id,
  tag_type,
  tag_value;

alter table canonical_qbank.canonical_question_tag
  add primary key (canonical_question_tag_id);
alter table canonical_qbank.canonical_question_tag
  add constraint canonical_question_tag_question_fk
  foreign key (canonical_question_id)
  references canonical_qbank.canonical_question(canonical_question_id);

create index canonical_question_tag_question_idx
  on canonical_qbank.canonical_question_tag(canonical_question_id);
create index canonical_question_tag_type_value_idx
  on canonical_qbank.canonical_question_tag(tag_type, tag_value);

create table canonical_qbank.knowledge_chunk_link as
select
  k.id as source_knowledge_chunk_id,
  m.canonical_question_id,
  m.source_question_id,
  k."datasetId" as source_dataset_id,
  k."paperId" as source_paper_row_id,
  k."sourceQuestionId" as source_question_key,
  k."sourcePaperId" as source_paper_key,
  k."chunkKind" as chunk_kind,
  k."chunkIndex" as chunk_index,
  k."materialOrder" as material_order,
  k.title,
  k.content,
  k."contentSha256" as content_sha256,
  k."tokenEstimate" as token_estimate,
  k."displayPath" as display_path,
  k."questionType" as question_type,
  k.province,
  k.year,
  k.tags,
  k.metadata,
  k.embedding,
  k."embeddingProvider" as embedding_provider,
  k."embeddingModel" as embedding_model,
  k."embeddingDimension" as embedding_dimension,
  k."embeddedAt" as embedded_at,
  k."createdAt" as source_created_at,
  k."updatedAt" as source_updated_at
from canonical_qbank.source_question_map m
join "QuestionBankKnowledgeChunk" k on k."questionId" = m.source_question_id;

alter table canonical_qbank.knowledge_chunk_link
  add primary key (source_knowledge_chunk_id);
alter table canonical_qbank.knowledge_chunk_link
  add constraint knowledge_chunk_link_canonical_fk
  foreign key (canonical_question_id)
  references canonical_qbank.canonical_question(canonical_question_id);

create index knowledge_chunk_link_canonical_idx
  on canonical_qbank.knowledge_chunk_link(canonical_question_id, chunk_kind, chunk_index);
create index knowledge_chunk_link_sha_idx
  on canonical_qbank.knowledge_chunk_link(source_dataset_id, content_sha256);

create table canonical_qbank.merge_review_queue as
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
  g.has_visual_risk,
  g.has_material_risk,
  g.representative_question_id,
  case
    when g.merge_decision = 'review_taxonomy_conflict' then 'review taxonomy differences before merge'
    when g.merge_decision = 'review_material_cluster' then 'material-aware identity check required'
    when g.merge_decision = 'review_visual_cluster' then 'image hash evidence missing or not accepted'
    when g.merge_decision = 'must_review_answer_conflict' then 'answer conflict blocks merge'
    else 'manual review required'
  end as review_reason,
  string_agg(m.question_id, ' | ' order by m.question_id) as member_question_ids,
  string_agg(distinct coalesce(m."paperTitle", ''), ' | ') as member_paper_titles
from compaction.candidate_groups_v1 g
join compaction.candidate_members_v1 m using (candidate_group_id)
left join canonical_qbank.accepted_merge_group amg
  on amg.candidate_group_id = g.candidate_group_id
where g.member_count > 1
  and amg.candidate_group_id is null
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
  g.has_visual_risk,
  g.has_material_risk,
  g.representative_question_id;

alter table canonical_qbank.merge_review_queue
  add primary key (candidate_group_id);

create index merge_review_queue_decision_idx
  on canonical_qbank.merge_review_queue(merge_decision, identity_bucket);

create table canonical_qbank.import_summary (
  metric text primary key,
  value bigint not null,
  note text not null
);

insert into canonical_qbank.import_summary(metric, value, note)
select 'source_questions_total', count(*), 'all rows in old QuestionBankQuestion'
from "QuestionBankQuestion"
union all
select 'source_questions_mapped', count(*), 'source questions mapped into canonical_qbank'
from canonical_qbank.source_question_map
union all
select 'source_questions_quarantined', count(*), 'placeholder/non-candidate objective source questions excluded from canonical_question'
from canonical_qbank.rejected_source_question
union all
select 'canonical_questions_total', count(*), 'final canonical question count for v1 accepted merge policy'
from canonical_qbank.canonical_question
union all
select 'canonical_questions_objective', count(*), 'objective canonical questions'
from canonical_qbank.canonical_question
where question_domain = 'objective'
union all
select 'canonical_questions_essay', count(*), 'essay canonical questions kept one-to-one'
from canonical_qbank.canonical_question
where question_domain = 'essay'
union all
select 'accepted_merge_groups', count(*), 'accepted duplicate groups'
from canonical_qbank.accepted_merge_group
union all
select 'accepted_merge_source_members', coalesce(sum(member_count), 0), 'source question members in accepted duplicate groups'
from canonical_qbank.accepted_merge_group
union all
select 'accepted_merge_removed_duplicates', coalesce(sum(removable_duplicate_count), 0), 'source question rows compacted by accepted duplicate groups'
from canonical_qbank.accepted_merge_group
union all
select 'question_occurrences_imported', count(*), 'occurrence rows attached to canonical questions'
from canonical_qbank.question_occurrence
union all
select 'question_occurrences_quarantined', count(*), 'occurrence rows attached only to quarantined source questions'
from "QuestionBankQuestionOccurrence" o
join canonical_qbank.rejected_source_question r on r.source_question_id = o."questionId"
union all
select 'exam_papers_imported', count(*), 'papers referenced by imported occurrences'
from canonical_qbank.exam_paper
union all
select 'canonical_explanations', count(*), 'non-essay explanation rows, AI accepted preferred when available'
from canonical_qbank.canonical_explanation
union all
select 'question_asset_refs', count(*), 'image refs extracted from source question HTML'
from canonical_qbank.question_asset
union all
select 'question_tag_evidence_rows', count(*), 'source tag links preserved with canonical IDs'
from canonical_qbank.question_tag_evidence
union all
select 'canonical_question_tag_rows', count(*), 'deduped canonical tag rows'
from canonical_qbank.canonical_question_tag
union all
select 'knowledge_chunk_links', count(*), 'source knowledge chunks preserved with canonical IDs'
from canonical_qbank.knowledge_chunk_link
union all
select 'merge_review_queue_groups', count(*), 'candidate duplicate groups intentionally not merged in v1'
from canonical_qbank.merge_review_queue;
