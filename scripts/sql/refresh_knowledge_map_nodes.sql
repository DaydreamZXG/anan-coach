-- =============================================================================
-- refresh_knowledge_map_nodes v2 —— 国考行测四级知识树生成器
--   module → knowledge_family → knowledge_point → micro_skill
-- =============================================================================
-- 治理依据（可审计，逐条对应 docs/engineering/2026-07-26-knowledge-vocabulary-governance.md）：
--   R0  module_name 未命中 product.vocab_module_alias_map(version='vocab@v1')
--       的题一律不进图（缺省隔离）。
--   R1  只有 action in ('canonical','map') 的模块行可进图；
--       'map_needs_review' 在人工复核通过前等同 quarantine；
--       'quarantine' 模块不产任何节点。
--   R2  product.vocab_facet_rules 中 facet='material_form'（综合资料/文字资料/
--       统计表/统计图）不建技能节点——本生成器把材料形态降为节点属性，写入
--       evidence->'material_form_breakdown'（题目在该节点内的材料形态分布），
--       不再像 v1 那样与考点混挂为兄弟节点；facet='exclude'（待分类/其他）为
--       占位符，不建任何节点；未命中词表的 tag 默认按 skill 面处理。
--   R3  词表版本在本文件内固定为 'vocab@v1'（下方 guard 与 params 两处，升版
--       必须同步修改并换生成器版本号）。
--
-- 结构决策（相对 v1 的行为变化，见治理文档 §9.G）：
--   1. 四级父链：knowledge_family 的父=module；knowledge_point 的父=与其共现
--      题数最多的 family（无共现族则直挂 module）；micro_skill 的父=共现最多
--      的 knowledge_point，其族沿父点继承保证链一致性（无父点时回退共现最多
--      的 family，再退 module）。共现证据可由 question_tags 重算复核。
--   2. 自适应折叠：叶子(micro_skill)题量 < 折叠阈值时上收合并进父
--      knowledge_point；knowledge_point < 阈值同理上收 family。module 不作为
--      吸收目标（把叶子并进模块根会丢失全部区分度），因此直挂 module 的稀疏
--      节点保留不折叠——宁保留细节，不向根误并。被折叠的词记入吸收节点的
--      aliases 与 evidence->'collapsed_from'（含题量，可审计）。
--      折叠阈值是 SQL 变量：默认 30，可在执行前用
--        set app.knowledge_map_collapse_threshold = <n>
--      覆盖（见 params CTE）。
--   3. question_count 每层聚合：节点题量 = 命中该节点 tag 或其任一后代节点
--      tag 的去重题数（15% 缺 knowledge_point 标注的题也能计入上层）。
--   4. image_unsupported 沿父链向下继承：节点不支持 = 自身命中图形类词面 /
--      全部题 visual_required / 任一祖先不支持。
--   5. near5_weight 仍取 product.exam_tag_weights 按 (module, tag_type,
--      tag_value) 直连（micro_skill 权重需先跑扩展后的
--      refresh_guokao_exam_tag_weights.sql）。
--   6. v1 消费的 question_type / taxonomy_path 标签不再进树主链：生产审计证实
--      taxonomy_path 为平面标签（121714 条 depth=1），与三级词表重复。
--
-- 前置条件：product.vocab_module_alias_map / product.vocab_facet_rules 已按
-- scripts/sql/vocabulary/load_vocabulary_seed.sql 装载 vocab@v1。
-- 本文件不连接生产库；生产执行由发布流程人工触发。
-- =============================================================================

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

-- R0/R3 前置守卫：vocab@v1 的 6 条 canonical 模块行必须已装载。
-- 未装载时词表 join 会静默隔离全部题目，因此这里用除零强制中止
-- （报错 division by zero 即代表词表未装载，先执行 load_vocabulary_seed.sql）。
select
  1 / (
    select (count(*) = 6)::int
    from product.vocab_module_alias_map
    where version = 'vocab@v1'
      and action = 'canonical'
  ) as vocab_v1_canonical_guard;

-- 清除生成器产出后重建：
--   * refresh_knowledge_map_nodes_v2 —— 本生成器自身的幂等重跑；
--   * refresh_knowledge_map_nodes_v1 —— 旧平面生成器（所有 tag 平挂模块根）的
--     产出，四级树上线时一次性清除（依据：治理文档 §1 对 v1 平挂结构的审计）。
-- 人工治理节点（evidence->>'generated_by' 非上述两值）不受影响。
delete from product.knowledge_map_nodes
where evidence->>'generated_by' in (
  'refresh_knowledge_map_nodes_v1',
  'refresh_knowledge_map_nodes_v2'
);

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
with params as (
  -- 折叠阈值 = SQL 变量：默认 30，可用
  --   set app.knowledge_map_collapse_threshold = <n>
  -- 在会话级覆盖；vocab_version 按 R3 固定在脚本内。
  select
    'vocab@v1'::text as vocab_version,
    coalesce(
      nullif(current_setting('app.knowledge_map_collapse_threshold', true), '')::integer,
      30
    ) as collapse_threshold,
    '(图形|图形推理|空间|立体|折纸|位置规律|样式规律|数量规律)'::text as image_pattern
), module_map as (
  -- R0/R1：只放行 canonical/map 行；canonical_code 自连接取 canonical 显示名，
  -- 使别名模块（言语理解、资科分析…）的题并入规范模块节点。
  select
    am.raw_module_name,
    am.canonical_code,
    canon.raw_module_name as module_name
  from params p
  join product.vocab_module_alias_map am
    on am.version = p.vocab_version
  join product.vocab_module_alias_map canon
    on canon.version = am.version
   and canon.action = 'canonical'
   and canon.canonical_code = am.canonical_code
  where am.action in ('canonical', 'map')
), governed_questions as (
  select
    q.question_id,
    mm.module_name,
    q.visual_required
  from product.questions q
  join module_map mm on mm.raw_module_name = q.module_name
  where q.status = 'active'
    and q.domain = 'objective'
), skill_tags as (
  -- R2：材料形态与占位符不进技能树主链；未命中词表默认 skill 面。
  select distinct
    gq.question_id,
    gq.module_name,
    gq.visual_required,
    qt.tag_type,
    qt.tag_value
  from governed_questions gq
  join product.question_tags qt on qt.question_id = gq.question_id
  cross join params p
  left join product.vocab_facet_rules fr
    on fr.version = p.vocab_version
   and fr.term = qt.tag_value
  where qt.tag_type in ('knowledge_family', 'knowledge_point', 'micro_skill')
    and qt.tag_value is not null
    and qt.tag_value <> ''
    and qt.tag_value <> gq.module_name
    and coalesce(fr.facet, 'skill') = 'skill'
), material_tags as (
  -- R2：材料形态面单列，稍后聚合为节点属性 evidence->'material_form_breakdown'。
  select distinct
    gq.question_id,
    gq.module_name,
    qt.tag_value as term
  from governed_questions gq
  join product.question_tags qt on qt.question_id = gq.question_id
  cross join params p
  join product.vocab_facet_rules fr
    on fr.version = p.vocab_version
   and fr.term = qt.tag_value
   and fr.facet = 'material_form'
  where qt.tag_type in ('knowledge_family', 'knowledge_point', 'micro_skill')
), point_family_pairs as (
  -- 知识点 × 知识族 共现题数（父链证据，可由 question_tags 重算复核）
  select
    pt.module_name,
    pt.tag_value as point_term,
    fa.tag_value as family_term,
    count(distinct pt.question_id) as pair_question_count
  from skill_tags pt
  join skill_tags fa
    on fa.question_id = pt.question_id
   and fa.module_name = pt.module_name
   and fa.tag_type = 'knowledge_family'
  where pt.tag_type = 'knowledge_point'
  group by pt.module_name, pt.tag_value, fa.tag_value
), point_parent as (
  -- 每个知识点取共现最多的族为父（并列时取共现题数高者，再按族名升序定序）
  select distinct on (module_name, point_term)
    module_name,
    point_term,
    family_term,
    pair_question_count
  from point_family_pairs
  order by module_name, point_term, pair_question_count desc, family_term
), skill_point_pairs as (
  select
    sk.module_name,
    sk.tag_value as skill_term,
    pt.tag_value as point_term,
    count(distinct sk.question_id) as pair_question_count
  from skill_tags sk
  join skill_tags pt
    on pt.question_id = sk.question_id
   and pt.module_name = sk.module_name
   and pt.tag_type = 'knowledge_point'
  where sk.tag_type = 'micro_skill'
  group by sk.module_name, sk.tag_value, pt.tag_value
), skill_point_parent as (
  select distinct on (module_name, skill_term)
    module_name,
    skill_term,
    point_term,
    pair_question_count
  from skill_point_pairs
  order by module_name, skill_term, pair_question_count desc, point_term
), skill_family_pairs as (
  select
    sk.module_name,
    sk.tag_value as skill_term,
    fa.tag_value as family_term,
    count(distinct sk.question_id) as pair_question_count
  from skill_tags sk
  join skill_tags fa
    on fa.question_id = sk.question_id
   and fa.module_name = sk.module_name
   and fa.tag_type = 'knowledge_family'
  where sk.tag_type = 'micro_skill'
  group by sk.module_name, sk.tag_value, fa.tag_value
), skill_family_parent as (
  select distinct on (module_name, skill_term)
    module_name,
    skill_term,
    family_term,
    pair_question_count
  from skill_family_pairs
  order by module_name, skill_term, pair_question_count desc, family_term
), point_chain as (
  -- 知识点父链：族存在则挂族，否则直挂模块
  select
    pt.module_name,
    pt.tag_value as point_term,
    pp.family_term
  from (
    select distinct module_name, tag_value
    from skill_tags
    where tag_type = 'knowledge_point'
  ) pt
  left join point_parent pp
    on pp.module_name = pt.module_name
   and pp.point_term = pt.tag_value
), skill_chain as (
  -- 微技能父链：有父点时族严格沿父点继承（保证树形，不允许
  -- 「技能的族 ≠ 父点的族」的交叉边）；无父点时才回退自身共现族。
  select
    sk.module_name,
    sk.tag_value as skill_term,
    spp.point_term as parent_point_term,
    case
      when spp.point_term is not null then pc.family_term
      else sfp.family_term
    end as chain_family_term
  from (
    select distinct module_name, tag_value
    from skill_tags
    where tag_type = 'micro_skill'
  ) sk
  left join skill_point_parent spp
    on spp.module_name = sk.module_name
   and spp.skill_term = sk.tag_value
  left join point_chain pc
    on pc.module_name = sk.module_name
   and pc.point_term = spp.point_term
  left join skill_family_parent sfp
    on sfp.module_name = sk.module_name
   and sfp.skill_term = sk.tag_value
), membership as (
  -- 逐题展开到所属节点及其全部祖先：question_count 每层聚合的依据
  select
    module_name,
    'module'::text as node_level,
    module_name as node_term,
    question_id,
    visual_required
  from governed_questions

  union

  select st.module_name, 'knowledge_family', st.tag_value, st.question_id, st.visual_required
  from skill_tags st
  where st.tag_type = 'knowledge_family'

  union

  select st.module_name, 'knowledge_family', pc.family_term, st.question_id, st.visual_required
  from skill_tags st
  join point_chain pc
    on pc.module_name = st.module_name
   and pc.point_term = st.tag_value
  where st.tag_type = 'knowledge_point'
    and pc.family_term is not null

  union

  select st.module_name, 'knowledge_family', sc.chain_family_term, st.question_id, st.visual_required
  from skill_tags st
  join skill_chain sc
    on sc.module_name = st.module_name
   and sc.skill_term = st.tag_value
  where st.tag_type = 'micro_skill'
    and sc.chain_family_term is not null

  union

  select st.module_name, 'knowledge_point', st.tag_value, st.question_id, st.visual_required
  from skill_tags st
  where st.tag_type = 'knowledge_point'

  union

  select st.module_name, 'knowledge_point', sc.parent_point_term, st.question_id, st.visual_required
  from skill_tags st
  join skill_chain sc
    on sc.module_name = st.module_name
   and sc.skill_term = st.tag_value
  where st.tag_type = 'micro_skill'
    and sc.parent_point_term is not null

  union

  select st.module_name, 'micro_skill', st.tag_value, st.question_id, st.visual_required
  from skill_tags st
  where st.tag_type = 'micro_skill'
), node_counts as (
  select
    module_name,
    node_level,
    node_term,
    count(distinct question_id)::integer as question_count,
    count(distinct question_id) filter (where visual_required)::integer as visual_required_count
  from membership
  group by module_name, node_level, node_term
), module_stats as (
  select
    nc.module_name,
    nc.question_count,
    nc.visual_required_count,
    (
      nc.node_term ~ p.image_pattern
      or (nc.question_count > 0 and nc.visual_required_count = nc.question_count)
    ) as image_unsupported
  from node_counts nc
  cross join params p
  where nc.node_level = 'module'
), family_stats as (
  select
    nc.module_name,
    nc.node_term as family_term,
    nc.question_count,
    nc.visual_required_count,
    (
      nc.node_term ~ p.image_pattern
      or (nc.question_count > 0 and nc.visual_required_count = nc.question_count)
      or ms.image_unsupported
    ) as image_unsupported
  from node_counts nc
  join module_stats ms on ms.module_name = nc.module_name
  cross join params p
  where nc.node_level = 'knowledge_family'
), point_stats as (
  select
    nc.module_name,
    nc.node_term as point_term,
    pc.family_term,
    nc.question_count,
    nc.visual_required_count,
    (
      nc.node_term ~ p.image_pattern
      or (nc.question_count > 0 and nc.visual_required_count = nc.question_count)
      or coalesce(fs.image_unsupported, ms.image_unsupported)
    ) as image_unsupported,
    -- 折叠规则：题量 < 阈值时上收 family；无族可收（直挂模块）时保留，
    -- module 不作为吸收目标（宁保留细节，不向根误并）。
    (
      nc.question_count >= p.collapse_threshold
      or pc.family_term is null
    ) as survives
  from node_counts nc
  join point_chain pc
    on pc.module_name = nc.module_name
   and pc.point_term = nc.node_term
  join module_stats ms on ms.module_name = nc.module_name
  left join family_stats fs
    on fs.module_name = nc.module_name
   and fs.family_term = pc.family_term
  cross join params p
  where nc.node_level = 'knowledge_point'
), skill_stats as (
  select
    nc.module_name,
    nc.node_term as skill_term,
    sc.parent_point_term,
    sc.chain_family_term,
    nc.question_count,
    nc.visual_required_count,
    -- 有效父节点：存活父点 > 链上族（父点被折叠时改挂祖父族）> 模块
    case
      when sc.parent_point_term is not null and coalesce(ps.survives, false)
        then 'knowledge_point'
      when sc.chain_family_term is not null
        then 'knowledge_family'
      else 'module'
    end as parent_level,
    case
      when sc.parent_point_term is not null and coalesce(ps.survives, false)
        then sc.parent_point_term
      when sc.chain_family_term is not null
        then sc.chain_family_term
      else nc.module_name
    end as parent_term,
    (
      nc.node_term ~ p.image_pattern
      or (nc.question_count > 0 and nc.visual_required_count = nc.question_count)
    ) as self_image_unsupported
  from node_counts nc
  join skill_chain sc
    on sc.module_name = nc.module_name
   and sc.skill_term = nc.node_term
  left join point_stats ps
    on ps.module_name = nc.module_name
   and ps.point_term = sc.parent_point_term
  cross join params p
  where nc.node_level = 'micro_skill'
), skill_final as (
  select
    ss.module_name,
    ss.skill_term,
    ss.parent_point_term,
    ss.chain_family_term,
    ss.question_count,
    ss.visual_required_count,
    ss.parent_level,
    ss.parent_term,
    -- 叶子折叠：题量 < 阈值时并入有效父节点；直挂模块的叶子保留（同上）。
    (
      ss.question_count >= p.collapse_threshold
      or ss.parent_level = 'module'
    ) as survives,
    (
      ss.self_image_unsupported
      or coalesce(
        case ss.parent_level
          when 'knowledge_point' then ps.image_unsupported
          when 'knowledge_family' then fs.image_unsupported
          else ms.image_unsupported
        end,
        false
      )
    ) as image_unsupported
  from skill_stats ss
  cross join params p
  join module_stats ms on ms.module_name = ss.module_name
  left join point_stats ps
    on ps.module_name = ss.module_name
   and ps.point_term = ss.parent_term
   and ss.parent_level = 'knowledge_point'
  left join family_stats fs
    on fs.module_name = ss.module_name
   and fs.family_term = ss.parent_term
   and ss.parent_level = 'knowledge_family'
), collapsed_points as (
  select
    module_name,
    'knowledge_family'::text as absorber_level,
    family_term as absorber_term,
    'knowledge_point'::text as collapsed_level,
    point_term as collapsed_term,
    question_count
  from point_stats
  where not survives
), collapsed_skills as (
  select
    module_name,
    parent_level as absorber_level,
    parent_term as absorber_term,
    'micro_skill'::text as collapsed_level,
    skill_term as collapsed_term,
    question_count
  from skill_final
  where not survives
), collapsed_agg as (
  -- 吸收节点记录被折叠词：aliases 供检索命中，collapsed_from 供审计追溯
  select
    module_name,
    absorber_level,
    absorber_term,
    jsonb_agg(distinct collapsed_term) as collapsed_aliases,
    jsonb_agg(
      jsonb_build_object(
        'node_type', collapsed_level,
        'tag_value', collapsed_term,
        'question_count', question_count
      )
      order by collapsed_level, collapsed_term
    ) as collapsed_detail
  from (
    select * from collapsed_points
    union all
    select * from collapsed_skills
  ) c
  group by module_name, absorber_level, absorber_term
), material_counts as (
  select
    m.module_name,
    m.node_level,
    m.node_term,
    mt.term,
    count(distinct m.question_id)::integer as material_question_count
  from membership m
  join material_tags mt
    on mt.question_id = m.question_id
   and mt.module_name = m.module_name
  group by m.module_name, m.node_level, m.node_term, mt.term
), material_breakdown as (
  select
    module_name,
    node_level,
    node_term,
    jsonb_object_agg(term, material_question_count) as breakdown
  from material_counts
  group by module_name, node_level, node_term
), module_nodes as (
  select
    ms.module_name,
    'module'::text as node_type,
    null::text as tag_type,
    ms.module_name as tag_value,
    '行测/' || ms.module_name as display_path,
    null::text as parent_level,
    null::text as parent_term,
    ms.question_count,
    ms.visual_required_count,
    ms.image_unsupported
  from module_stats ms
), family_nodes as (
  select
    fs.module_name,
    'knowledge_family'::text as node_type,
    'knowledge_family'::text as tag_type,
    fs.family_term as tag_value,
    '行测/' || fs.module_name || '/' || fs.family_term as display_path,
    'module'::text as parent_level,
    fs.module_name as parent_term,
    fs.question_count,
    fs.visual_required_count,
    fs.image_unsupported
  from family_stats fs
), point_nodes as (
  select
    ps.module_name,
    'knowledge_point'::text as node_type,
    'knowledge_point'::text as tag_type,
    ps.point_term as tag_value,
    '行测/' || ps.module_name
      || coalesce('/' || ps.family_term, '')
      || '/' || ps.point_term as display_path,
    case when ps.family_term is not null then 'knowledge_family' else 'module' end as parent_level,
    coalesce(ps.family_term, ps.module_name) as parent_term,
    ps.question_count,
    ps.visual_required_count,
    ps.image_unsupported
  from point_stats ps
  where ps.survives
), skill_nodes as (
  select
    sf.module_name,
    'micro_skill'::text as node_type,
    'micro_skill'::text as tag_type,
    sf.skill_term as tag_value,
    '行测/' || sf.module_name
      || case
           when sf.parent_level = 'knowledge_point'
             then coalesce('/' || sf.chain_family_term, '') || '/' || sf.parent_term
           when sf.parent_level = 'knowledge_family'
             then '/' || sf.parent_term
           else ''
         end
      || '/' || sf.skill_term as display_path,
    sf.parent_level,
    sf.parent_term,
    sf.question_count,
    sf.visual_required_count,
    sf.image_unsupported
  from skill_final sf
  where sf.survives
), unified as (
  select * from module_nodes
  union all
  select * from family_nodes
  union all
  select * from point_nodes
  union all
  select * from skill_nodes
), enriched as (
  select
    'km_' || substr(
      md5(
        'guokao|xingce|' || u.module_name || '|' || u.node_type || '|' ||
        coalesce(u.tag_type, 'module') || '|' || u.tag_value
      ),
      1,
      24
    ) as node_id,
    'guokao'::text as exam_scope,
    'xingce'::text as track,
    u.module_name,
    u.node_type,
    u.tag_type,
    u.tag_value,
    u.display_path,
    -- 父节点 id 用同一公式重算（父层 node_type 与 tag_type 键同名，module 层
    -- 二者皆为 'module'，与 v1 的 module 节点 id 公式保持一致）
    case
      when u.parent_level is null then null
      else 'km_' || substr(
        md5(
          'guokao|xingce|' || u.module_name || '|' || u.parent_level || '|' ||
          u.parent_level || '|' || u.parent_term
        ),
        1,
        24
      )
    end as parent_node_id,
    u.question_count,
    u.visual_required_count,
    u.image_unsupported,
    ca.collapsed_aliases,
    ca.collapsed_detail,
    mb.breakdown as material_form_breakdown,
    w.weight as near5_weight,
    w.occurrence_count as near5_occurrence_count,
    w.window_start_year as near5_window_start_year,
    w.window_end_year as near5_window_end_year
  from unified u
  left join collapsed_agg ca
    on ca.module_name = u.module_name
   and ca.absorber_level = u.node_type
   and ca.absorber_term = u.tag_value
  left join material_breakdown mb
    on mb.module_name = u.module_name
   and mb.node_level = u.node_type
   and mb.node_term = u.tag_value
  left join product.exam_tag_weights w
    on w.exam_scope = 'guokao'
   and w.track = 'xingce'
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
  coalesce(e.collapsed_aliases, '[]'::jsonb) as aliases,
  case
    when e.image_unsupported then 'image_unsupported'
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
    'generated_by', 'refresh_knowledge_map_nodes_v2',
    'vocab_version', (select vocab_version from params),
    'collapse_threshold', (select collapse_threshold from params),
    'source_tables', jsonb_build_array(
      'product.questions',
      'product.question_tags',
      'product.exam_tag_weights',
      'product.method_cards',
      'product.vocab_module_alias_map',
      'product.vocab_facet_rules'
    ),
    'question_count', e.question_count,
    'visual_required_count', e.visual_required_count,
    'collapsed_from', coalesce(e.collapsed_detail, '[]'::jsonb),
    'material_form_breakdown', coalesce(e.material_form_breakdown, '{}'::jsonb),
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
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v2'
    then excluded.display_path
    else product.knowledge_map_nodes.display_path
  end,
  parent_node_id = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v2'
    then excluded.parent_node_id
    else product.knowledge_map_nodes.parent_node_id
  end,
  aliases = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v2'
    then excluded.aliases
    else product.knowledge_map_nodes.aliases
  end,
  support_status = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v2'
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
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v2'
    then excluded.evidence
    else product.knowledge_map_nodes.evidence || jsonb_build_object(
      'last_generated_refresh',
      excluded.evidence - 'manual_confirmed'
    )
  end,
  status = case
    when product.knowledge_map_nodes.evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v2'
    then excluded.status
    else product.knowledge_map_nodes.status
  end,
  updated_at = now();
