-- =============================================================================
-- 知识词表治理种子 vocab@v1（2026-07-26）
-- =============================================================================
-- 这是仓库治理资产，不是自动执行的迁移。绝不在本文件里连接/写入生产库；
-- 由运维在人工审阅 docs/engineering/2026-07-26-knowledge-vocabulary-governance.md
-- 之后，在目标环境用 psql \copy 手动装载。
--
-- 治理规则（与治理文档 §2 一一对应，generator 消费时必须遵守）：
--   R0  未命中 vocab_module_alias_map 的 module_name 一律按 quarantine 处理
--       （宁隔离不误并；生产端脏值 42 个 ⊆ 本表 48 个非 canonical 行，
--         对账见治理文档 §7）。
--   R1  action='map' 才允许直接并入 canonical_code；
--       action='map_needs_review' 在人工复核通过前一律按 quarantine 处理；
--       action='quarantine' 的题不进知识地图/组卷池，仅保留原始 module_name。
--   R2  facet_rules 未命中材料形态词表的 tag 默认留在 skill 面；
--       facet='material_form' 的 tag（综合资料/文字资料/统计表/统计图）
--       不得作为技能节点产出（对应修复 scripts/sql/refresh_knowledge_map_nodes.sql
--       :86-114 将其与考点混挂的问题），应改挂独立的材料形态轴或降为题目属性；
--       facet='exclude'（待分类/其他）为占位符，不建任何节点。
--   R3  每行 basis 为可审计依据，修订必须换版本号（vocab@v2...），不得原地改写。
-- =============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 1. 模块别名映射表
-- ---------------------------------------------------------------------------
create table if not exists product.vocab_module_alias_map (
  version              text not null,
  raw_module_name      text not null,
  action               text not null check (
    action in ('canonical', 'map', 'map_needs_review', 'quarantine')
  ),
  canonical_code       text check (
    canonical_code in (
      'political_theory',
      'general_knowledge',
      'verbal_comprehension',
      'quantitative_relations',
      'judgment_reasoning',
      'data_analysis'
    )
  ),
  local_question_count integer not null default 0,
  basis                text not null,
  primary key (version, raw_module_name),
  -- quarantine 行必须无 canonical_code，其余行必须有（R1 可审计约束）
  check ((action = 'quarantine') = (canonical_code is null))
);

comment on table product.vocab_module_alias_map is
  '模块名治理词表：raw module_name -> 六模块 code 或隔离。来源 scripts/sql/vocabulary/module_alias_map.csv（vocab@v1）';

-- ---------------------------------------------------------------------------
-- 2. 标签面拆分规则表（材料形态 vs 技能考点）
-- ---------------------------------------------------------------------------
create table if not exists product.vocab_facet_rules (
  version             text not null,
  term                text not null,
  facet               text not null check (facet in ('material_form', 'skill', 'exclude')),
  tag_levels_observed text not null,
  local_occurrences   integer not null default 0,
  needs_review        boolean not null default false,
  basis               text not null,
  primary key (version, term)
);

comment on table product.vocab_facet_rules is
  '知识标签面治理词表：材料形态/技能考点/占位符三分。来源 scripts/sql/vocabulary/facet_rules.csv（vocab@v1）';

-- 同版本重放先完整装入事务级 staging。两个 CSV 必须先通过自身完整性校验，
-- 再比较目标版本；任何畸形、增、删、改都以 23514 终止整笔事务。
create temporary table staged_vocab_module_alias_map
  (like product.vocab_module_alias_map
    including defaults including constraints including indexes)
  on commit drop;

create temporary table staged_vocab_facet_rules
  (like product.vocab_facet_rules
    including defaults including constraints including indexes)
  on commit drop;

-- 路径相对仓库根；CSV 的未加引号空字段由 COPY CSV 语义装载为 NULL。
\copy staged_vocab_module_alias_map (raw_module_name, action, canonical_code, local_question_count, basis, version) from 'scripts/sql/vocabulary/module_alias_map.csv' with (format csv, header true);
\copy staged_vocab_facet_rules (term, facet, tag_levels_observed, local_occurrences, needs_review, basis, version) from 'scripts/sql/vocabulary/facet_rules.csv' with (format csv, header true);

do $vocab_replay$
declare
  canonical_row_count integer;
  canonical_code_count integer;
  exact_canonical_pair_count integer;
begin
  if exists (
    select 1
    from staged_vocab_module_alias_map staged
    where staged.version <> 'vocab@v1'
  ) then
    raise exception using
      errcode = '23514',
      message = 'module vocabulary staging contains an unexpected version';
  end if;

  select
    count(*)::integer,
    count(distinct canonical_code)::integer,
    count(*) filter (
      where (canonical_code, raw_module_name) in (
        values
          ('political_theory', '政治理论'),
          ('general_knowledge', '常识判断'),
          ('verbal_comprehension', '言语理解与表达'),
          ('quantitative_relations', '数量关系'),
          ('judgment_reasoning', '判断推理'),
          ('data_analysis', '资料分析')
      )
    )::integer
  into canonical_row_count, canonical_code_count, exact_canonical_pair_count
  from staged_vocab_module_alias_map
  where action = 'canonical';

  if canonical_row_count <> 6
     or canonical_code_count <> 6
     or exact_canonical_pair_count <> 6 then
    raise exception using
      errcode = '23514',
      message = 'module vocabulary staging must contain the exact six canonical module code-name pairs';
  end if;

  if exists (
    select 1
    from staged_vocab_module_alias_map mapped
    where mapped.action in ('map', 'map_needs_review')
      and not exists (
        select 1
        from staged_vocab_module_alias_map canonical
        where canonical.action = 'canonical'
          and canonical.canonical_code = mapped.canonical_code
      )
  ) then
    raise exception using
      errcode = '23514',
      message = 'module vocabulary staging maps to a missing canonical code';
  end if;

  if not exists (select 1 from staged_vocab_facet_rules) then
    raise exception using
      errcode = '23514',
      message = 'facet vocabulary staging must not be empty';
  end if;

  if exists (
    select 1
    from staged_vocab_facet_rules staged
    where staged.version <> 'vocab@v1'
  ) then
    raise exception using
      errcode = '23514',
      message = 'facet vocabulary staging contains an unexpected version';
  end if;

  -- Validate both target versions before inserting either one. An existing
  -- version is accepted only when its complete semantic row set is identical.
  if exists (
    select 1
    from product.vocab_module_alias_map existing
    where existing.version = 'vocab@v1'
  ) and exists (
    (
      select
        raw_module_name, action, canonical_code, local_question_count, basis, version
      from product.vocab_module_alias_map
      where version = 'vocab@v1'
      except
      select
        raw_module_name, action, canonical_code, local_question_count, basis, version
      from staged_vocab_module_alias_map
    )
    union all
    (
      select
        raw_module_name, action, canonical_code, local_question_count, basis, version
      from staged_vocab_module_alias_map
      except
      select
        raw_module_name, action, canonical_code, local_question_count, basis, version
      from product.vocab_module_alias_map
      where version = 'vocab@v1'
    )
  ) then
    raise exception using
      errcode = '23514',
      message = 'vocab_module_alias_map same-version content drift: vocab@v1';
  end if;

  if exists (
    select 1
    from product.vocab_facet_rules existing
    where existing.version = 'vocab@v1'
  ) and exists (
    (
      select
        term, facet, tag_levels_observed, local_occurrences,
        needs_review, basis, version
      from product.vocab_facet_rules
      where version = 'vocab@v1'
      except
      select
        term, facet, tag_levels_observed, local_occurrences,
        needs_review, basis, version
      from staged_vocab_facet_rules
    )
    union all
    (
      select
        term, facet, tag_levels_observed, local_occurrences,
        needs_review, basis, version
      from staged_vocab_facet_rules
      except
      select
        term, facet, tag_levels_observed, local_occurrences,
        needs_review, basis, version
      from product.vocab_facet_rules
      where version = 'vocab@v1'
    )
  ) then
    raise exception using
      errcode = '23514',
      message = 'vocab_facet_rules same-version content drift: vocab@v1';
  end if;

  if exists (
    select 1
    from product.vocab_module_alias_map existing
    where existing.version = 'vocab@v1'
  ) then
    raise notice 'vocab_module_alias_map vocab@v1 replay is semantically identical';
  else
    insert into product.vocab_module_alias_map (
      raw_module_name, action, canonical_code, local_question_count, basis, version
    )
    select
      raw_module_name, action, canonical_code, local_question_count, basis, version
    from staged_vocab_module_alias_map;
  end if;

  if exists (
    select 1
    from product.vocab_facet_rules existing
    where existing.version = 'vocab@v1'
  ) then
    raise notice 'vocab_facet_rules vocab@v1 replay is semantically identical';
  else
    insert into product.vocab_facet_rules (
      term, facet, tag_levels_observed, local_occurrences,
      needs_review, basis, version
    )
    select
      term, facet, tag_levels_observed, local_occurrences,
      needs_review, basis, version
    from staged_vocab_facet_rules;
  end if;
end
$vocab_replay$;

commit;

-- ---------------------------------------------------------------------------
-- 3. generator 对接（已完成，2026-07-26）
-- ---------------------------------------------------------------------------
-- scripts/sql/refresh_knowledge_map_nodes.sql 已升级为 v2 四级树生成器并按
-- R0/R1/R2 消费本词表（module_map / skill_tags / material_tags 三个 CTE），
-- micro_skill node_type 真实产出、材料形态降为节点属性
-- evidence->'material_form_breakdown'。设计决策与本地验证见
-- docs/engineering/2026-07-26-knowledge-vocabulary-governance.md §11，
-- 测试见 backend/tests/test_knowledge_map_tree_v2.py。
-- 执行顺序（生产，人工触发）：本文件装载词表 → refresh_guokao_exam_tag_weights.sql
-- → refresh_knowledge_map_nodes.sql（未装载词表时 v2 有除零守卫直接中止）。
