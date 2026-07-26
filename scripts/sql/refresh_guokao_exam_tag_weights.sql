create table if not exists product.exam_tag_weights (
  exam_scope text not null,
  track text not null,
  module_name text not null,
  tag_type text not null,
  tag_value text not null,
  window_start_year integer not null,
  window_end_year integer not null,
  question_count integer not null,
  occurrence_count integer not null,
  weight numeric(12,8) not null,
  source_query jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (
    exam_scope,
    track,
    module_name,
    tag_type,
    tag_value,
    window_start_year,
    window_end_year
  )
);

create index if not exists product_exam_tag_weights_scope_rank_idx
  on product.exam_tag_weights(
    exam_scope,
    track,
    tag_type,
    weight desc,
    occurrence_count desc
  );

delete from product.exam_tag_weights
where exam_scope = 'guokao'
  and track = 'xingce';

insert into product.exam_tag_weights (
  exam_scope,
  track,
  module_name,
  tag_type,
  tag_value,
  window_start_year,
  window_end_year,
  question_count,
  occurrence_count,
  weight,
  source_query,
  created_at,
  updated_at
)
with guokao_occurrences as (
  select
    qo.occurrence_id,
    qo.question_id,
    coalesce(qo.paper_title, qo.occurrence_paper_title, '') as source_title,
    qo.source_path,
    coalesce(
      nullif(
        substring(coalesce(qo.paper_title, qo.occurrence_paper_title, '') from '([12][0-9]{3})年'),
        ''
      )::integer,
      qo.year
    ) as exam_year
  from product.question_occurrences qo
  where coalesce(qo.province, '') = '国考'
     or coalesce(qo.paper_title, qo.occurrence_paper_title, '') ~
        '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
     or coalesce(qo.source_path, '') ~
        '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
), bounds as (
  select
    max(exam_year) - 4 as window_start_year,
    max(exam_year) as window_end_year
  from guokao_occurrences
  where exam_year is not null
), tagged_occurrences as (
  select distinct
    'guokao'::text as exam_scope,
    'xingce'::text as track,
    q.module_name,
    'module'::text as tag_type,
    q.module_name as tag_value,
    go.occurrence_id,
    q.question_id,
    go.exam_year
  from guokao_occurrences go
  join product.questions q on q.question_id = go.question_id
  where q.status = 'active'
    and q.domain = 'objective'
    and q.module_name is not null
    and q.module_name <> ''
    and go.exam_year is not null

  union all

  select distinct
    'guokao'::text as exam_scope,
    'xingce'::text as track,
    q.module_name,
    qt.tag_type,
    qt.tag_value,
    go.occurrence_id,
    q.question_id,
    go.exam_year
  from guokao_occurrences go
  join product.questions q on q.question_id = go.question_id
  join product.question_tags qt on qt.question_id = q.question_id
  where q.status = 'active'
    and q.domain = 'objective'
    and q.module_name is not null
    and q.module_name <> ''
    and qt.tag_type in (
      'question_type',
      'taxonomy_path',
      'knowledge_family',
      'knowledge_point',
      -- v2 四级知识树（refresh_knowledge_map_nodes.sql）需要 micro_skill 层的
      -- near5 权重；权重按 tag_type 内归一，新增该层不改变既有各层的权重分布。
      'micro_skill'
    )
    and qt.tag_value is not null
    and qt.tag_value <> ''
    and go.exam_year is not null
), weighted as (
  select
    t.exam_scope,
    t.track,
    t.module_name,
    t.tag_type,
    t.tag_value,
    b.window_start_year,
    b.window_end_year,
    count(distinct t.question_id)::integer as question_count,
    count(distinct t.occurrence_id)::integer as occurrence_count
  from tagged_occurrences t
  cross join bounds b
  where b.window_start_year is not null
    and t.exam_year between b.window_start_year and b.window_end_year
  group by
    t.exam_scope,
    t.track,
    t.module_name,
    t.tag_type,
    t.tag_value,
    b.window_start_year,
    b.window_end_year
), totals as (
  select
    exam_scope,
    track,
    tag_type,
    sum(occurrence_count) as total_occurrences
  from weighted
  group by exam_scope, track, tag_type
)
select
  w.exam_scope,
  w.track,
  w.module_name,
  w.tag_type,
  w.tag_value,
  w.window_start_year,
  w.window_end_year,
  w.question_count,
  w.occurrence_count,
  round(w.occurrence_count::numeric / nullif(t.total_occurrences, 0), 8) as weight,
  jsonb_build_object(
    'exam_scope', w.exam_scope,
    'track', w.track,
    'window', 'latest_five_exam_years',
    'year_source', 'first YYYY年 parsed from question_occurrences paper title, fallback to question_occurrences.year',
    'source_fields', jsonb_build_array(
      'product.question_occurrences.province',
      'product.question_occurrences.paper_title',
      'product.question_occurrences.occurrence_paper_title',
      'product.question_occurrences.source_path'
    ),
    'guokao_detection', jsonb_build_array(
      'province = 国考',
      'paper title/source path contains 国考/国家公务员/中央机关'
    )
  ) as source_query,
  now(),
  now()
from weighted w
join totals t
  on t.exam_scope = w.exam_scope
 and t.track = w.track
 and t.tag_type = w.tag_type
where w.occurrence_count > 0
on conflict (
  exam_scope,
  track,
  module_name,
  tag_type,
  tag_value,
  window_start_year,
  window_end_year
) do update set
  question_count = excluded.question_count,
  occurrence_count = excluded.occurrence_count,
  weight = excluded.weight,
  source_query = excluded.source_query,
  updated_at = now();
