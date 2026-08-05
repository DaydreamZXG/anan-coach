-- =============================================================================
-- refresh_knowledge_map_nodes v3 —— 可版本发布的国考行测四级知识树生成器
--   module → knowledge_family → knowledge_point → micro_skill
-- =============================================================================
-- 本文件是 psql 单事务发布脚本；禁止逐句复制到不同连接执行：
--   psql --set ON_ERROR_STOP=1 --file scripts/sql/refresh_knowledge_map_nodes.sql
-- 执行前必须在同一连接设置以下三个 GUC（缺失或空值会直接失败）：
--   set app.knowledge_map_release_version = 'knowledge-map@<version>';
--   set app.knowledge_map_source_data_version = '<source-version>';
--   set app.knowledge_map_fresh_until = '<future ISO-8601 timestamp>';
--
-- 发布事务：创建 draft → 写完整节点 → 核对 node_count / 新鲜度 →
-- 退休旧 active → 激活新 release。任一步失败，整笔事务回滚，不留下半棵树。
-- =============================================================================
\set ON_ERROR_STOP on
begin;

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
-- 本文件不主动连接生产库；生产执行由发布流程以 psql 单连接触发。
-- =============================================================================

-- 20260727_65 是本发布器的结构前置；不在脚本内静默补表或补列。
select release_id
from product.knowledge_map_releases
where false;

-- 同一 scope+track 只允许一个发布事务进入生成区，避免并发发布造成
-- supersedes 链跳版本或“后提交者退休刚激活版本”的竞态。
select pg_advisory_xact_lock(
  hashtextextended('knowledge-map-release|guokao|xingce', 0)
);

-- 发布指纹、节点生成和最终门禁必须看到同一份词表。词表装载是人工治理写入，
-- 不使用本发布 advisory key，因此在整个发布事务内持有 SHARE 锁阻止并发重载。
lock table product.vocab_module_alias_map in share mode;
lock table product.vocab_facet_rules in share mode;

-- R0/R3 前置守卫：vocab@v1 的 6 条 canonical 模块行必须已装载。
-- 未装载时词表 join 会静默隔离全部题目，因此这里用除零强制中止
-- （报错 division by zero 即代表词表未装载，先执行 load_vocabulary_seed.sql）。
select
  1 / (
    select (
      count(*) = 6
      and count(distinct canonical_code) = 6
      and count(*) filter (
        where (canonical_code, raw_module_name) in (
          values
            ('political_theory', '政治理论'),
            ('general_knowledge', '常识判断'),
            ('verbal_comprehension', '言语理解与表达'),
            ('quantitative_relations', '数量关系'),
            ('judgment_reasoning', '判断推理'),
            ('data_analysis', '资料分析')
        )
      ) = 6
    )::int
    from product.vocab_module_alias_map
    where version = 'vocab@v1'
      and action = 'canonical'
  ) as vocab_v1_canonical_guard;

-- 必填发布参数与数据快照指纹。输入先冻结到事务级临时表；这样同一
-- release_version 的重放可以重新计算并逐字段核对，而不是用
-- IF NOT EXISTS 吞掉内容不一致。指纹覆盖生成器所有事实输入；相同的
-- source_data_version 若底层题目、标签、权重、方法卡或词表发生变化，也会得到
-- 不同 fingerprint，不允许只相信人工填写的版本字符串。
create temporary table knowledge_map_publish_input
on commit drop
as
with release_input as (
  select
    nullif(
      btrim(current_setting('app.knowledge_map_release_version')),
      ''
    ) as release_version,
    nullif(
      btrim(current_setting('app.knowledge_map_source_data_version')),
      ''
    ) as source_data_version,
    current_setting('app.knowledge_map_fresh_until')::timestamptz as fresh_until,
    'vocab@v1'::text as vocab_version,
    'refresh_knowledge_map_nodes_v4'::text as generator_version,
    coalesce(
      nullif(
        current_setting('app.knowledge_map_collapse_threshold', true),
        ''
      )::integer,
      30
    ) as collapse_threshold
), source_digests as (
  select
    input.*,
    (
      select encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\x1f',
                  question.question_id,
                  question.domain,
                  question.status,
                  coalesce(question.module_name, ''),
                  coalesce(question.question_type, ''),
                  question.visual_required::text
                ),
                E'\x1e'
                order by question.question_id
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
      from product.questions question
      where question.domain = 'objective'
        and question.status = 'active'
    ) as question_digest,
    (
      select encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\x1f',
                  question_tag.question_tag_id,
                  question_tag.question_id,
                  question_tag.tag_type,
                  question_tag.tag_value
                ),
                E'\x1e'
                order by question_tag.question_tag_id
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
      from product.question_tags question_tag
      join product.questions question
        on question.question_id = question_tag.question_id
       and question.domain = 'objective'
       and question.status = 'active'
    ) as question_tag_digest,
    (
      select encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\x1f',
                  weight.module_name,
                  weight.tag_type,
                  weight.tag_value,
                  weight.window_start_year::text,
                  weight.window_end_year::text,
                  weight.weight::text,
                  weight.occurrence_count::text
                ),
                E'\x1e'
                order by weight.module_name, weight.tag_type, weight.tag_value
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
      from product.exam_tag_weights weight
      where weight.exam_scope = 'guokao'
        and weight.track = 'xingce'
    ) as weight_digest,
    (
      select encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\x1f',
                  method_card.id,
                  method_card.status,
                  method_card.exam_scope,
                  method_card.track,
                  coalesce(method_card.module_name, ''),
                  coalesce(method_card.tag_path, ''),
                  method_card.priority::text,
                  coalesce(method_card.knowledge_nodes::text, '')
                ),
                E'\x1e'
                order by method_card.id
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
      from product.method_cards method_card
      where method_card.exam_scope = 'guokao'
        and method_card.track = 'xingce'
        and method_card.status = 'published'
    ) as method_card_digest,
    (
      select encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\x1f',
                  alias.raw_module_name,
                  alias.action,
                  coalesce(alias.canonical_code, ''),
                  alias.basis
                ),
                E'\x1e'
                order by alias.raw_module_name
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
      from product.vocab_module_alias_map alias
      where alias.version = input.vocab_version
    ) as module_vocab_digest,
    (
      select encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\x1f',
                  facet.term,
                  facet.facet,
                  facet.tag_levels_observed,
                  facet.needs_review::text,
                  facet.basis
                ),
                E'\x1e'
                order by facet.term
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
      from product.vocab_facet_rules facet
      where facet.version = input.vocab_version
    ) as facet_vocab_digest
    ,
    (
      select encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\x1f',
                  manual_node.node_id,
                  manual_node.module_name,
                  manual_node.node_type,
                  coalesce(manual_node.tag_type, ''),
                  manual_node.tag_value,
                  manual_node.display_path,
                  coalesce(manual_node.parent_node_id, ''),
                  manual_node.aliases::text,
                  manual_node.support_status,
                  (manual_node.evidence - 'last_generated_refresh')::text,
                  manual_node.status
                ),
                E'\x1e'
                order by manual_node.node_id
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
      from product.knowledge_map_nodes manual_node
      where manual_node.release_id = (
        select predecessor.release_id
        from product.knowledge_map_releases predecessor
        where predecessor.exam_scope = 'guokao'
          and predecessor.track = 'xingce'
          and predecessor.status in ('active', 'retired')
        order by
          case predecessor.status when 'active' then 0 else 1 end,
          coalesce(
            predecessor.activated_at,
            predecessor.retired_at,
            predecessor.generated_at
          ) desc,
          predecessor.release_version desc
        limit 1
      )
        and manual_node.evidence->>'manual_confirmed' = 'true'
    ) as manual_node_digest
  from release_input input
), fingerprinted_release as (
  select
    source.*,
    encode(
      digest(
        convert_to(
          jsonb_build_object(
            'exam_scope', 'guokao',
            'track', 'xingce',
            'source_data_version', source.source_data_version,
            'vocab_version', source.vocab_version,
            'generator_version', source.generator_version,
            'collapse_threshold', source.collapse_threshold,
            'question_digest', source.question_digest,
            'question_tag_digest', source.question_tag_digest,
            'weight_digest', source.weight_digest,
            'method_card_digest', source.method_card_digest,
            'module_vocab_digest', source.module_vocab_digest,
            'facet_vocab_digest', source.facet_vocab_digest,
            'manual_node_digest', source.manual_node_digest
          )::text,
          'UTF8'
        ),
        'sha256'
      ),
      'hex'
    ) as source_fingerprint
  from source_digests source
)
select *
from fingerprinted_release;

with inserted_release as (
insert into product.knowledge_map_releases (
  release_id,
  release_version,
  exam_scope,
  track,
  source_data_version,
  vocab_version,
  generator_version,
  source_fingerprint,
  generated_at,
  fresh_until,
  status,
  node_count,
  activated_at,
  retired_at,
  supersedes_release_id
)
select
  gen_random_uuid(),
  release.release_version,
  'guokao',
  'xingce',
  release.source_data_version,
  release.vocab_version,
  release.generator_version,
  release.source_fingerprint,
  transaction_timestamp(),
  release.fresh_until,
  'draft',
  0,
  null,
  null,
  predecessor.release_id
from knowledge_map_publish_input release
left join lateral (
  select prior.release_id
  from product.knowledge_map_releases prior
  where prior.exam_scope = 'guokao'
    and prior.track = 'xingce'
    and prior.status in ('active', 'retired')
  order by
    case prior.status when 'active' then 0 else 1 end,
    coalesce(prior.activated_at, prior.retired_at, prior.generated_at) desc,
    prior.release_version desc
  limit 1
) predecessor on true
on conflict (release_version) do nothing
returning release_id
)
select set_config(
  'app.knowledge_map_inserted_release_id',
  coalesce(max(release_id::text), ''),
  true
)
from inserted_release;

-- 同版本仅允许两种状态：本事务刚创建的 draft，或此前已完整提交且
-- 输入指纹完全一致的 active。任何 draft/retired/元数据或内容指纹不一致
-- 都 fail closed，避免把“撞版本号”误判为幂等成功。
do $release_replay$
declare
  release_input record;
  stored_release product.knowledge_map_releases%rowtype;
  inserted_release_id uuid;
  actual_node_count integer;
begin
  select *
  into strict release_input
  from knowledge_map_publish_input;

  if release_input.release_version like 'knowledge-map@legacy-pre-%'
     or release_input.source_data_version like 'legacy:%'
     or release_input.generator_version like 'legacy:%'
     or release_input.vocab_version like 'legacy:%' then
    raise exception using
      errcode = '23514',
      message = 'legacy knowledge-map metadata cannot be published as active';
  end if;

  select release.*
  into strict stored_release
  from product.knowledge_map_releases release
  where release.release_version = release_input.release_version;

  inserted_release_id := nullif(
    current_setting('app.knowledge_map_inserted_release_id'),
    ''
  )::uuid;

  if stored_release.exam_scope <> 'guokao'
     or stored_release.track <> 'xingce'
     or stored_release.source_data_version is distinct from
        release_input.source_data_version
     or stored_release.vocab_version is distinct from release_input.vocab_version
     or stored_release.generator_version is distinct from
        release_input.generator_version
     or stored_release.source_fingerprint is distinct from
        release_input.source_fingerprint
     or stored_release.fresh_until is distinct from release_input.fresh_until then
    raise exception using
      errcode = '23505',
      message = 'knowledge-map release version already exists with different content';
  end if;

  if inserted_release_id is not null then
    if stored_release.release_id <> inserted_release_id
       or stored_release.status <> 'draft' then
      raise exception using
        errcode = '23514',
        message = 'new knowledge-map release did not enter draft state';
    end if;
    perform set_config('app.knowledge_map_publish_replay', 'false', true);
    return;
  end if;

  if stored_release.status <> 'active'
     or stored_release.activated_at is null
     or stored_release.retired_at is not null
     or stored_release.fresh_until <= clock_timestamp() then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map release version exists but is not a fresh committed release';
  end if;

  select count(*)::integer
  into actual_node_count
  from product.knowledge_map_nodes node
  where node.release_id = stored_release.release_id;

  if stored_release.node_count <= 0
     or actual_node_count <> stored_release.node_count
     or (
       select count(*)
       from product.vocab_module_alias_map canonical
       where canonical.version = stored_release.vocab_version
         and canonical.action = 'canonical'
     ) <> 6
     or (
       select count(distinct canonical.canonical_code)
       from product.vocab_module_alias_map canonical
       where canonical.version = stored_release.vocab_version
         and canonical.action = 'canonical'
     ) <> 6
     or (
       select count(distinct canonical.canonical_code)
       from product.vocab_module_alias_map canonical
       where canonical.version = stored_release.vocab_version
         and canonical.action = 'canonical'
         and exists (
           select 1
           from product.knowledge_map_nodes node
           where node.release_id = stored_release.release_id
             and node.module_name = canonical.raw_module_name
             and node.status = 'published'
             and node.support_status = 'text_supported'
             and node.node_type in ('micro_skill', 'knowledge_point')
             and node.tag_type is not null
             and coalesce(node.near5_occurrence_count, 0) > 0
         )
     ) <> 6 then
    raise exception using
      errcode = '23514',
      message = 'committed knowledge-map release failed six-module integrity validation';
  end if;

  perform set_config('app.knowledge_map_publish_replay', 'true', true);
end
$release_replay$;

-- 人工确认节点是治理事实，不因生成器换版丢失。先复制前序 release 的人工节点，
-- 再由本次生成结果仅刷新其统计证据；结构、别名与支持状态仍归人工治理所有。
insert into product.knowledge_map_nodes (
  release_id,
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
select
  draft.release_id,
  node.node_id,
  node.exam_scope,
  node.track,
  node.module_name,
  node.node_type,
  node.tag_type,
  node.tag_value,
  node.display_path,
  node.parent_node_id,
  node.aliases,
  node.support_status,
  node.near5_window_start_year,
  node.near5_window_end_year,
  node.near5_weight,
  node.near5_occurrence_count,
  node.stable_for_old_questions,
  node.method_card_ids,
  node.evidence,
  node.status,
  node.created_at,
  transaction_timestamp()
from product.knowledge_map_releases draft
join product.knowledge_map_nodes node
  on node.release_id = draft.supersedes_release_id
where draft.release_version =
      current_setting('app.knowledge_map_release_version')
  and draft.status = 'draft'
  and not current_setting('app.knowledge_map_publish_replay')::boolean
  and node.evidence->>'manual_confirmed' = 'true';

insert into product.knowledge_map_nodes as existing_node (
  release_id,
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
    release.release_id,
    release.release_version,
    release.source_data_version,
    release.source_fingerprint,
    release.vocab_version,
    release.generator_version,
    coalesce(
      nullif(current_setting('app.knowledge_map_collapse_threshold', true), '')::integer,
      30
    ) as collapse_threshold,
    '(图形|图形推理|空间|立体|折纸|位置规律|样式规律|数量规律)'::text as image_pattern
  from product.knowledge_map_releases release
  where release.release_version =
        current_setting('app.knowledge_map_release_version')
    and release.exam_scope = 'guokao'
    and release.track = 'xingce'
    and release.status = 'draft'
    and not current_setting('app.knowledge_map_publish_replay')::boolean
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
  (select release_id from params),
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
          -- 教学讲法 tag_path 直连考点（基础匹配）
          mc.tag_path = e.tag_value
          or mc.tag_path like e.tag_value || '/%'
          or mc.tag_path like '%/' || e.tag_value
          or mc.tag_path like '%/' || e.tag_value || '/%'
          -- AI 语义映射：方法卡 knowledge_nodes 里的 display_path 命中本节点，
          -- 或映射路径的末尾片段命中本节点 tag_value（兼容树折叠后父节点挂卡）
          or (
            mc.knowledge_nodes is not null
            and exists (
              select 1
              from jsonb_array_elements_text(mc.knowledge_nodes) kn
              where kn = e.display_path
                 or split_part(kn, '/', array_length(string_to_array(kn, '/'), 1)) = e.tag_value
            )
          )
        )
    ),
    '[]'::jsonb
  ) as method_card_ids,
  jsonb_build_object(
    'generated_by', (select generator_version from params),
    'release_version', (select release_version from params),
    'source_data_version', (select source_data_version from params),
    'source_fingerprint', (select source_fingerprint from params),
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
on conflict (release_id, node_id) do update set
  near5_window_start_year = excluded.near5_window_start_year,
  near5_window_end_year = excluded.near5_window_end_year,
  near5_weight = excluded.near5_weight,
  near5_occurrence_count = excluded.near5_occurrence_count,
  stable_for_old_questions = excluded.stable_for_old_questions,
  method_card_ids = excluded.method_card_ids,
  evidence = existing_node.evidence || jsonb_build_object(
    'last_generated_refresh',
    excluded.evidence - 'manual_confirmed'
  ),
  updated_at = transaction_timestamp();

update product.knowledge_map_releases release
set node_count = (
  select count(*)::integer
  from product.knowledge_map_nodes node
  where node.release_id = release.release_id
)
where release.release_version =
      current_setting('app.knowledge_map_release_version')
  and release.status = 'draft';

-- 将本次发布所依据的 question -> node 归因、题面内容版本、可投放事实与
-- 排序事实全部冻结到 release。后续消费者不得再用 mutable questions /
-- question_tags / occurrences 重新解释历史 release。
create temporary table knowledge_map_binding_candidates
on commit drop
as
with target_release as (
  select release.*
  from product.knowledge_map_releases release
  where release.release_version =
        current_setting('app.knowledge_map_release_version')
    and release.exam_scope = 'guokao'
    and release.track = 'xingce'
    and release.status = case
      when current_setting('app.knowledge_map_publish_replay')::boolean
        then 'active'
      else 'draft'
    end
), module_map as (
  select
    alias.raw_module_name,
    alias.canonical_code as module_code,
    canonical.raw_module_name as canonical_module
  from target_release release
  join product.vocab_module_alias_map alias
    on alias.version = release.vocab_version
   and alias.action in ('canonical', 'map')
  join product.vocab_module_alias_map canonical
    on canonical.version = alias.version
   and canonical.action = 'canonical'
   and canonical.canonical_code = alias.canonical_code
), governed_questions as (
  select
    release.release_id,
    question.question_id,
    question.module_name as raw_module_name,
    module_map.module_code,
    module_map.canonical_module,
    question.visual_required,
    question.shared_context_required,
    question.latest_year,
    coalesce(question.occurrence_count, 0)::integer as occurrence_count
  from target_release release
  join product.questions question
    on question.domain = 'objective'
   and question.status = 'active'
  join module_map
    on module_map.raw_module_name = question.module_name
), current_question_snapshots as (
  select
    governed.*,
    jsonb_build_object(
      'question_id', question.question_id,
      'module_name', question.module_name,
      'question_type', question.question_type,
      'stem', question.stem,
      'stem_html', question.stem_html,
      'answer', question.answer,
      'material_text', question.material_text,
      'material_html', question.material_html,
      'preferred_analysis', explanation.preferred_analysis,
      'analysis_source', explanation.analysis_source,
      'analysis_status', explanation.analysis_status,
      'prefer_ai_analysis', explanation.prefer_ai_analysis,
      'analysis_needs_review', explanation.analysis_needs_review,
      'options', coalesce(
        (
          select jsonb_agg(
            jsonb_build_object(
              'label', option.label,
              'option_text', option.option_text,
              'option_html', option.option_html
            )
            order by option.option_index
          )
          from product.question_options option
          where option.question_id = question.question_id
        ),
        '[]'::jsonb
      ),
      'assets', coalesce(
        (
          select jsonb_agg(
            jsonb_build_object(
              'asset_id', asset.asset_id,
              'part_kind', asset.part_kind,
              'part_index', asset.part_index,
              'image_ordinal', asset.image_ordinal,
              'image_src', asset.image_src,
              'object_key', asset.object_key,
              'is_representative_asset', asset.is_representative_asset
            )
            order by
              asset.part_kind nulls last,
              asset.part_index nulls last,
              asset.image_ordinal nulls last,
              asset.asset_id
          )
          from product.question_assets asset
          where asset.question_id = question.question_id
        ),
        '[]'::jsonb
      )
    ) as current_snapshot
  from governed_questions governed
  join product.questions question
    on question.question_id = governed.question_id
  left join product.question_explanations explanation
    on explanation.question_id = question.question_id
), direct_bindings as (
  select
    question.release_id,
    node.node_id,
    question.question_id,
    question.module_code,
    question.canonical_module,
    'direct'::text as binding_kind,
    tag.tag_type as source_tag_type,
    tag.tag_value as source_tag_value
  from current_question_snapshots question
  join product.question_tags tag
    on tag.question_id = question.question_id
  join product.knowledge_map_nodes node
    on node.release_id = question.release_id
   and node.module_name = question.canonical_module
   and node.tag_type = tag.tag_type
   and node.tag_value = tag.tag_value
   and node.status = 'published'
), collapsed_bindings as (
  select
    question.release_id,
    node.node_id,
    question.question_id,
    question.module_code,
    question.canonical_module,
    'collapsed'::text as binding_kind,
    tag.tag_type as source_tag_type,
    tag.tag_value as source_tag_value
  from current_question_snapshots question
  join product.question_tags tag
    on tag.question_id = question.question_id
  join product.knowledge_map_nodes node
    on node.release_id = question.release_id
   and node.module_name = question.canonical_module
   and node.status = 'published'
  cross join lateral jsonb_array_elements(
    coalesce(node.evidence->'collapsed_from', '[]'::jsonb)
  ) collapsed
  where collapsed->>'node_type' = tag.tag_type
    and collapsed->>'tag_value' = tag.tag_value
), governed_bindings as (
  select distinct on (release_id, node_id, question_id)
    release_id,
    node_id,
    question_id,
    module_code,
    canonical_module,
    binding_kind,
    source_tag_type,
    source_tag_value
  from (
    select * from direct_bindings
    union all
    select * from collapsed_bindings
  ) binding
  order by
    release_id,
    node_id,
    question_id,
    case binding_kind when 'direct' then 0 else 1 end,
    source_tag_type,
    source_tag_value
), versioned_bindings as (
  select
    binding.*,
    snapshot.visual_required,
    snapshot.shared_context_required,
    snapshot.latest_year,
    snapshot.occurrence_count,
    snapshot.current_snapshot,
    content_head.content_version_id,
    content_version.snapshot_payload as frozen_snapshot,
    node.near5_window_start_year,
    node.near5_window_end_year
  from governed_bindings binding
  join current_question_snapshots snapshot
    on snapshot.release_id = binding.release_id
   and snapshot.question_id = binding.question_id
  join product.knowledge_map_nodes node
    on node.release_id = binding.release_id
   and node.node_id = binding.node_id
  left join product.question_content_heads content_head
    on content_head.question_id = binding.question_id
  left join product.question_content_versions content_version
    on content_version.question_id = binding.question_id
   and content_version.content_version_id = content_head.content_version_id
), binding_facts as (
  select
    binding.*,
    occurrence.latest_recent_exam_year,
    occurrence.recent_occurrence_count,
    occurrence.latest_old_exam_year,
    occurrence.old_occurrence_count,
    coalesce(
      nullif(btrim(binding.frozen_snapshot->>'stem'), '') is not null
      and nullif(btrim(binding.frozen_snapshot->>'answer'), '') is not null
      and jsonb_typeof(binding.frozen_snapshot->'options') = 'array'
      and exists (
        select 1
        from jsonb_array_elements(
          coalesce(binding.frozen_snapshot->'options', '[]'::jsonb)
        ) option
        where upper(btrim(option->>'label')) =
              upper(btrim(binding.frozen_snapshot->>'answer'))
      )
      and (
        not binding.visual_required
        or exists (
          select 1
          from jsonb_array_elements(
            coalesce(binding.frozen_snapshot->'assets', '[]'::jsonb)
          ) asset
          where asset->>'image_src' ~
                '^https://kg\\.xingguoxian\\.cn(?::443)?/assets/'
        )
      ),
      false
    ) as delivery_eligible
  from versioned_bindings binding
  left join lateral (
    select
      max(source.exam_year) filter (
        where source.exam_year between
          binding.near5_window_start_year and binding.near5_window_end_year
      )::integer as latest_recent_exam_year,
      count(distinct source.occurrence_id) filter (
        where source.exam_year between
          binding.near5_window_start_year and binding.near5_window_end_year
      )::integer as recent_occurrence_count,
      max(source.exam_year) filter (
        where source.exam_year < binding.near5_window_start_year
      )::integer as latest_old_exam_year,
      count(distinct source.occurrence_id) filter (
        where source.exam_year < binding.near5_window_start_year
      )::integer as old_occurrence_count
    from (
      select
        occurrence.occurrence_id,
        coalesce(
          nullif(
            substring(
              coalesce(
                occurrence.paper_title,
                occurrence.occurrence_paper_title,
                ''
              )
              from '([12][0-9]{3})年'
            ),
            ''
          )::integer,
          occurrence.year
        ) as exam_year
      from product.question_occurrences occurrence
      where occurrence.question_id = binding.question_id
        and (
          coalesce(occurrence.province, '') = '国考'
          or coalesce(
            occurrence.paper_title,
            occurrence.occurrence_paper_title,
            ''
          ) ~ '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
          or coalesce(occurrence.source_path, '') ~
             '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
        )
    ) source
  ) occurrence on true
), prepared_bindings as (
  select
    release_id,
    node_id,
    question_id,
    content_version_id,
    module_code,
    canonical_module,
    binding_kind,
    source_tag_type,
    source_tag_value,
    delivery_eligible,
    visual_required,
    shared_context_required,
    latest_year,
    occurrence_count,
    latest_recent_exam_year,
    coalesce(recent_occurrence_count, 0)::integer as recent_occurrence_count,
    latest_old_exam_year,
    coalesce(old_occurrence_count, 0)::integer as old_occurrence_count,
    current_snapshot,
    frozen_snapshot
  from binding_facts
)
select
  prepared.*,
  product.knowledge_map_question_binding_row_fingerprint(
    row(
      prepared.release_id,
      prepared.node_id,
      prepared.question_id,
      prepared.content_version_id,
      prepared.module_code,
      prepared.canonical_module,
      prepared.binding_kind,
      prepared.source_tag_type,
      prepared.source_tag_value,
      prepared.delivery_eligible,
      prepared.visual_required,
      prepared.shared_context_required,
      prepared.latest_year,
      prepared.occurrence_count,
      prepared.latest_recent_exam_year,
      prepared.recent_occurrence_count,
      prepared.latest_old_exam_year,
      prepared.old_occurrence_count,
      repeat('0', 64),
      transaction_timestamp()
    )::product.knowledge_map_question_bindings
  ) as binding_fingerprint
from prepared_bindings prepared;

do $binding_sources$
declare
  candidate_count integer;
begin
  select count(*)::integer
  into candidate_count
  from knowledge_map_binding_candidates;

  if candidate_count <= 0 then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map release has no frozen question bindings';
  end if;

  if exists (
    select 1
    from knowledge_map_binding_candidates candidate
    where candidate.content_version_id is null
       or candidate.frozen_snapshot is null
       or candidate.current_snapshot is distinct from candidate.frozen_snapshot
  ) then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map question content head is missing or stale';
  end if;
end
$binding_sources$;

insert into product.knowledge_map_question_bindings (
  release_id,
  node_id,
  question_id,
  content_version_id,
  module_code,
  canonical_module,
  binding_kind,
  source_tag_type,
  source_tag_value,
  delivery_eligible,
  visual_required,
  shared_context_required,
  latest_year,
  occurrence_count,
  latest_recent_exam_year,
  recent_occurrence_count,
  latest_old_exam_year,
  old_occurrence_count,
  binding_fingerprint,
  created_at
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
  candidate.binding_fingerprint,
  transaction_timestamp()
from knowledge_map_binding_candidates candidate
where not current_setting('app.knowledge_map_publish_replay')::boolean;

with binding_manifest as (
  select
    release.release_id,
    manifest.binding_count,
    manifest.binding_fingerprint
  from product.knowledge_map_releases release
  cross join lateral product.knowledge_map_question_binding_manifest(
    release.release_id
  ) manifest
  where release.release_version =
        current_setting('app.knowledge_map_release_version')
    and release.status = 'draft'
    and not current_setting('app.knowledge_map_publish_replay')::boolean
)
update product.knowledge_map_releases release
set
  question_binding_count = manifest.binding_count,
  question_binding_fingerprint = manifest.binding_fingerprint
from binding_manifest manifest
where release.release_id = manifest.release_id;

-- 同版本 replay 也必须根据当前 source 重新计算候选绑定。只校验历史表自身
-- 不足以发现 questions/tags/content heads/occurrences 在发布后的漂移。
do $binding_manifest$
declare
  target_release product.knowledge_map_releases%rowtype;
  stored_count integer;
  stored_fingerprint text;
  candidate_count integer;
  candidate_fingerprint text;
begin
  select release.*
  into strict target_release
  from product.knowledge_map_releases release
  where release.release_version =
        current_setting('app.knowledge_map_release_version')
    and release.exam_scope = 'guokao'
    and release.track = 'xingce'
    and release.status = case
      when current_setting('app.knowledge_map_publish_replay')::boolean
        then 'active'
      else 'draft'
    end;

  select manifest.binding_count, manifest.binding_fingerprint
  into stored_count, stored_fingerprint
  from product.knowledge_map_question_binding_manifest(
    target_release.release_id
  ) manifest;

  select
    count(*)::integer,
    encode(
      digest(
        convert_to(
          coalesce(
            string_agg(
              concat_ws(
                E'\x1f',
                candidate.node_id,
                candidate.question_id,
                candidate.binding_fingerprint
              ),
              E'\x1e'
              order by candidate.node_id, candidate.question_id
            ),
            ''
          ),
          'UTF8'
        ),
        'sha256'
      ),
      'hex'
    )
  into candidate_count, candidate_fingerprint
  from knowledge_map_binding_candidates candidate;

  if stored_count <= 0
     or stored_count <> target_release.question_binding_count
     or stored_fingerprint is distinct from
        target_release.question_binding_fingerprint
     or candidate_count <> stored_count
     or candidate_fingerprint is distinct from stored_fingerprint then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map question binding manifest does not match its source';
  end if;
end
$binding_manifest$;

-- 激活前的最后门禁必须在同一事务内读取实际落库事实，不能相信调用方声明。
do $publish$
declare
  pending_release product.knowledge_map_releases%rowtype;
  actual_node_count integer;
  publish_replay boolean :=
    current_setting('app.knowledge_map_publish_replay')::boolean;
begin
  select release.*
  into strict pending_release
  from product.knowledge_map_releases release
  where release.release_version =
        current_setting('app.knowledge_map_release_version')
    and release.exam_scope = 'guokao'
    and release.track = 'xingce'
    and release.status = case
      when publish_replay then 'active'
      else 'draft'
    end;

  select count(*)::integer
  into actual_node_count
  from product.knowledge_map_nodes node
  where node.release_id = pending_release.release_id;

  if pending_release.node_count <= 0
     or actual_node_count <> pending_release.node_count then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map release node count is incomplete';
  end if;

  if pending_release.fresh_until <= pending_release.generated_at
     or pending_release.fresh_until <= clock_timestamp() then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map release freshness window has expired';
  end if;

  if (
    select count(*)
    from product.vocab_module_alias_map canonical
    where canonical.version = pending_release.vocab_version
      and canonical.action = 'canonical'
  ) <> 6
     or (
       select count(distinct canonical.canonical_code)
       from product.vocab_module_alias_map canonical
       where canonical.version = pending_release.vocab_version
         and canonical.action = 'canonical'
     ) <> 6
     or (
       select count(*)
       from product.vocab_module_alias_map canonical
       where canonical.version = pending_release.vocab_version
         and canonical.action = 'canonical'
         and (canonical.canonical_code, canonical.raw_module_name) in (
           values
             ('political_theory', '政治理论'),
             ('general_knowledge', '常识判断'),
             ('verbal_comprehension', '言语理解与表达'),
             ('quantitative_relations', '数量关系'),
             ('judgment_reasoning', '判断推理'),
             ('data_analysis', '资料分析')
         )
     ) <> 6
     or (
    select count(distinct canonical.canonical_code)
    from product.vocab_module_alias_map canonical
    where canonical.version = pending_release.vocab_version
      and canonical.action = 'canonical'
      and exists (
        select 1
        from product.knowledge_map_nodes node
        join product.knowledge_map_question_bindings binding
          on binding.release_id = node.release_id
         and binding.node_id = node.node_id
         and binding.module_code = canonical.canonical_code
         and binding.canonical_module = canonical.raw_module_name
         and binding.delivery_eligible
        where node.release_id = pending_release.release_id
          and node.module_name = canonical.raw_module_name
          and node.status = 'published'
          and node.support_status = 'text_supported'
          and node.node_type in ('micro_skill', 'knowledge_point')
          and node.tag_type is not null
          and coalesce(node.near5_occurrence_count, 0) > 0
      )
  ) <> 6 then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map release does not cover all six actionable modules';
  end if;
end
$publish$;

update product.knowledge_map_releases
set
  status = 'retired',
  retired_at = transaction_timestamp()
where exam_scope = 'guokao'
  and track = 'xingce'
  and status = 'active'
  and not current_setting('app.knowledge_map_publish_replay')::boolean;

update product.knowledge_map_releases
set
  status = 'active',
  activated_at = transaction_timestamp()
where release_version =
      current_setting('app.knowledge_map_release_version')
  and exam_scope = 'guokao'
  and track = 'xingce'
  and status = 'draft'
  and not current_setting('app.knowledge_map_publish_replay')::boolean;

-- 提交前再次读取最终相态；若激活 UPDATE 未命中，必须让整笔发布回滚，
-- 不能提交一个后续只能人工清理的 draft。
do $activation_state$
declare
  active_release product.knowledge_map_releases%rowtype;
begin
  select release.*
  into strict active_release
  from product.knowledge_map_releases release
  where release.release_version =
        current_setting('app.knowledge_map_release_version')
    and release.exam_scope = 'guokao'
    and release.track = 'xingce'
    and release.status = 'active'
    and release.activated_at is not null
    and release.retired_at is null;

  if (
    select count(*)
    from product.knowledge_map_releases release
    where release.exam_scope = 'guokao'
      and release.track = 'xingce'
      and release.status = 'active'
  ) <> 1 then
    raise exception using
      errcode = '23514',
      message = 'knowledge-map activation did not leave exactly one active release';
  end if;
end
$activation_state$;

select
  case
    when current_setting('app.knowledge_map_publish_replay')::boolean
      then 'already_committed'
    else 'published'
  end as publish_status,
  release.release_id,
  release.release_version,
  release.source_data_version,
  release.source_fingerprint,
  release.fresh_until,
  release.node_count
from product.knowledge_map_releases release
where release.release_version =
      current_setting('app.knowledge_map_release_version')
  and release.exam_scope = 'guokao'
  and release.track = 'xingce'
  and release.status = 'active';

commit;
