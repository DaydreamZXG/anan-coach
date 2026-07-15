create table if not exists product.knowledge_map_nodes (
  node_id text primary key,
  exam_scope text not null,
  track text not null check (track in ('xingce', 'shenlun')),
  module_name text not null,
  node_type text not null check (
    node_type in (
      'module',
      'question_type',
      'knowledge_family',
      'knowledge_point',
      'micro_skill'
    )
  ),
  tag_type text,
  tag_value text not null,
  display_path text not null,
  parent_node_id text,
  aliases jsonb not null default '[]'::jsonb,
  support_status text not null check (
    support_status in ('text_supported', 'image_unsupported', 'draft', 'retired')
  ),
  near5_window_start_year integer,
  near5_window_end_year integer,
  near5_weight numeric(12,8),
  near5_occurrence_count integer,
  stable_for_old_questions boolean not null default false,
  method_card_ids jsonb not null default '[]'::jsonb,
  evidence jsonb not null default '{}'::jsonb,
  status text not null check (status in ('draft', 'published')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (exam_scope, track, module_name, node_type, tag_type, tag_value)
);

create index if not exists product_knowledge_map_nodes_rank_idx
  on product.knowledge_map_nodes(
    exam_scope,
    track,
    status,
    support_status,
    near5_weight desc nulls last,
    near5_occurrence_count desc nulls last
  );

create index if not exists product_knowledge_map_nodes_tag_idx
  on product.knowledge_map_nodes(exam_scope, track, module_name, tag_type, tag_value);

delete from product.knowledge_map_nodes
where evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1';

insert into product.knowledge_map_nodes (
  node_id,
  exam_scope,
  track,
  module_name,
  node_type,
  tag_type,
  tag_value,
  display_path,
  parent_node_id,
  aliases,
  support_status,
  near5_window_start_year,
  near5_window_end_year,
  near5_weight,
  near5_occurrence_count,
  stable_for_old_questions,
  method_card_ids,
  evidence,
  status,
  created_at,
  updated_at
)
with module_nodes as (
  select
    q.module_name,
    count(distinct q.question_id)::integer as question_count,
    count(distinct q.question_id) filter (where q.visual_required)::integer as visual_required_count
  from product.questions q
  where q.status = 'active'
    and q.domain = 'objective'
    and q.module_name is not null
    and q.module_name <> ''
  group by q.module_name
), tag_nodes as (
  select
    q.module_name,
    case
      when qt.tag_type = 'question_type' then 'question_type'
      when qt.tag_type = 'knowledge_family' then 'knowledge_family'
      when qt.tag_type = 'knowledge_point' then 'knowledge_point'
      else 'knowledge_point'
    end as node_type,
    qt.tag_type,
    qt.tag_value,
    count(distinct q.question_id)::integer as question_count,
    count(distinct q.question_id) filter (where q.visual_required)::integer as visual_required_count
  from product.questions q
  join product.question_tags qt on qt.question_id = q.question_id
  where q.status = 'active'
    and q.domain = 'objective'
    and q.module_name is not null
    and q.module_name <> ''
    and qt.tag_type in (
      'question_type',
      'taxonomy_path',
      'knowledge_family',
      'knowledge_point'
    )
    and qt.tag_value is not null
    and qt.tag_value <> ''
    and qt.tag_value <> q.module_name
  group by q.module_name, qt.tag_type, qt.tag_value
), unified as (
  select
    'guokao'::text as exam_scope,
    'xingce'::text as track,
    module_name,
    'module'::text as node_type,
    null::text as tag_type,
    module_name as tag_value,
    '行测/' || module_name as display_path,
    null::text as parent_node_id,
    question_count,
    visual_required_count
  from module_nodes

  union all

  select
    'guokao'::text as exam_scope,
    'xingce'::text as track,
    module_name,
    node_type,
    tag_type,
    tag_value,
    '行测/' || module_name || '/' || tag_value as display_path,
    'km_' || substr(
      md5('guokao|xingce|' || module_name || '|module|module|' || module_name),
      1,
      24
    ) as parent_node_id,
    question_count,
    visual_required_count
  from tag_nodes
), enriched as (
  select
    'km_' || substr(
      md5(
        u.exam_scope || '|' ||
        u.track || '|' ||
        u.module_name || '|' ||
        u.node_type || '|' ||
        coalesce(u.tag_type, 'module') || '|' ||
        u.tag_value
      ),
      1,
      24
    ) as node_id,
    u.exam_scope,
    u.track,
    u.module_name,
    u.node_type,
    u.tag_type,
    u.tag_value,
    u.display_path,
    u.parent_node_id,
    u.question_count,
    u.visual_required_count,
    w.weight as near5_weight,
    w.occurrence_count as near5_occurrence_count,
    w.window_start_year as near5_window_start_year,
    w.window_end_year as near5_window_end_year
  from unified u
  left join product.exam_tag_weights w
    on w.exam_scope = u.exam_scope
   and w.track = u.track
   and w.module_name = u.module_name
   and w.tag_type = coalesce(u.tag_type, 'module')
   and w.tag_value = u.tag_value
)
select
  e.node_id,
  e.exam_scope,
  e.track,
  e.module_name,
  e.node_type,
  e.tag_type,
  e.tag_value,
  e.display_path,
  e.parent_node_id,
  '[]'::jsonb as aliases,
  case
    when e.tag_value ~ '(图形|图形推理|空间|立体|折纸|位置规律|样式规律|数量规律)'
      or (e.question_count > 0 and e.visual_required_count = e.question_count)
    then 'image_unsupported'
    else 'text_supported'
  end as support_status,
  e.near5_window_start_year,
  e.near5_window_end_year,
  e.near5_weight,
  e.near5_occurrence_count,
  coalesce(e.near5_occurrence_count, 0) > 0 as stable_for_old_questions,
  coalesce(
    (
      select jsonb_agg(mc.id order by mc.priority desc, mc.id)
      from product.method_cards mc
      where mc.status = 'published'
        and mc.exam_scope = e.exam_scope
        and mc.track = e.track
        and mc.module_name = e.module_name
        and (
          mc.tag_path = e.tag_value
          or mc.tag_path like e.tag_value || '/%'
          or mc.tag_path like '%/' || e.tag_value
          or mc.tag_path like '%/' || e.tag_value || '/%'
        )
    ),
    '[]'::jsonb
  ) as method_card_ids,
  jsonb_build_object(
    'generated_by', 'refresh_knowledge_map_nodes_v1',
    'source_tables', jsonb_build_array(
      'product.questions',
      'product.question_tags',
      'product.exam_tag_weights',
      'product.method_cards'
    ),
    'question_count', e.question_count,
    'visual_required_count', e.visual_required_count,
    'manual_confirmed', false
  ) as evidence,
  'published'::text as status,
  now(),
  now()
from enriched e
where e.question_count > 0
on conflict (node_id) do update set
  exam_scope = excluded.exam_scope,
  track = excluded.track,
  module_name = excluded.module_name,
  node_type = excluded.node_type,
  tag_type = excluded.tag_type,
  tag_value = excluded.tag_value,
  display_path = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1'
    then excluded.display_path
    else product.knowledge_map_nodes.display_path
  end,
  parent_node_id = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1'
    then excluded.parent_node_id
    else product.knowledge_map_nodes.parent_node_id
  end,
  aliases = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1'
    then excluded.aliases
    else product.knowledge_map_nodes.aliases
  end,
  support_status = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1'
    then excluded.support_status
    else product.knowledge_map_nodes.support_status
  end,
  near5_window_start_year = excluded.near5_window_start_year,
  near5_window_end_year = excluded.near5_window_end_year,
  near5_weight = excluded.near5_weight,
  near5_occurrence_count = excluded.near5_occurrence_count,
  stable_for_old_questions = excluded.stable_for_old_questions,
  method_card_ids = excluded.method_card_ids,
  evidence = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1'
    then excluded.evidence
    else product.knowledge_map_nodes.evidence || jsonb_build_object(
      'last_generated_refresh',
      excluded.evidence - 'manual_confirmed'
    )
  end,
  status = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1'
    then excluded.status
    else product.knowledge_map_nodes.status
  end,
  updated_at = now();
