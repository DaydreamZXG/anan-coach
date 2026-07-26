# 知识词表治理：模块别名收敛与标签面拆分 vocab@v1（2026-07-26）

治理资产（仅仓库文件，不触生产）：

- `scripts/sql/vocabulary/module_alias_map.csv` — 模块名 → 六模块 code / 隔离（54 行，逐行 basis）
- `scripts/sql/vocabulary/facet_rules.csv` — 标签词 → material_form / skill / exclude（398 行）
- `scripts/sql/vocabulary/load_vocabulary_seed.sql` — 建表 + `\copy` 装载说明（人工审阅后由运维执行）

## 1. 背景：生产审计事实（2026-07-26 只读查证）

- `product.questions` 六大近五年模块：判断推理 3320 / 言语 2691 / 资料 2124 / 常识 1482 / 数量 1320 / 政治 670。
- `module_name` 全库有 **42 个脏值**（事业单位卷段名、别名、笔误「资料分斩」等）。
- 标签三级词表：knowledge_family 100% 填充 325 词、knowledge_point 85% 247 词、micro_skill 100% 334 词。
- 知识点与微技能里混着两个体系：**材料形态**（综合资料/文字资料/统计表/统计图）与**考点**（一般增长率 1222 / 综合分析 501 / 现期比重 487 / 基期计算 467 / 待分类 312，资料分析样本）。
- `taxonomy_path` 实为平面标签（121714 条 depth=1）；现有生成器 `scripts/sql/refresh_knowledge_map_nodes.sql:139-143` 把所有 tag 平挂模块根，node_type 五级枚举中 `micro_skill` 从不产出（`:89-94` 的 case 分支只映射三种 tag_type）。
- 目标表 DDL：`backend/tests/sql/minimal_schema.sql:415-448`（`product.knowledge_map_nodes`，含 parent_node_id/aliases/support_status/near5_weight）。

## 2. 治理原则（铁律）

1. **可审计**：每一条合并/隔离在 CSV 里都有 `basis` 列写明依据（契约文件行号、本地源库抽样证据、任务审计指令三类）；修订必须换版本号（`vocab@v2`…），不得原地改写。
2. **宁隔离不误并**：拿不准的、内容跨模块的、题型不符合行测单选契约的，一律 `quarantine` 并写原因。
3. **R0 缺省隔离**：生产端任何未命中 `module_alias_map` 的 module_name 一律按 quarantine 处理。
4. **R1 复核门**：`action='map_needs_review'` 在人工复核通过前等同 quarantine；只有 `canonical`/`map` 行可直接进图。
5. **R2 面隔离**：`facet='material_form'` 的词不得建为技能节点；`facet='exclude'`（占位符）不建任何节点；未命中词表的 tag 默认留 skill 面。

## 3. 依据来源与提取方法

生产库本次**未连接**。证据来自仓库内的生产同源导出
`data/question-bank-export/files/question-bank/fenbi-final-analysis-dedup-v2-20260613-fallback.json`
（53,564 题，即生产 `product.questions` 的导入源，见 `data/question-bank-export/MANIFEST.md`），提取字段：

- `classification.module` → 53 个不同模块名（本地题量分布，写入 `local_question_count` 列）；
- `classification.path_id` 一/二/三级 → 模块/知识族(37 词)/知识点(171 词)；
- `classification.micro_skill` → 323 词；
- `asset_refs.occurrences[].paperTitle` → 脏模块名的卷源抽样（如「判断题」全部来自河北选调《基本素质和能力测试》）。

提取为只读正则扫描（`"module":"…"`、`"path_id":"…"`、`"micro_skill":"…"`、按模块名邻域抽 paperTitle），可随时复跑核对。

## 4. 模块别名映射汇总（module_alias_map.csv）

| action | 行数 | 含义 |
| --- | ---: | --- |
| canonical | 6 | 六模块自映射行（供 generator 全量 join），code 见 `backend/app/contracts/guokao_xingce.py:7-14` |
| map | 27 | 可直接并入：官方别名(言语理解)、语序/措辞变体、单模块同质卷段名（子题型名、上海「+能力」段）、OCR 笔误（资科分析/资料分斩） |
| map_needs_review | 2 | 方向已给（→general_knowledge）但含政治理论混入，复核前按隔离处理：常识应用（广东乡镇卷）、常识应用能力（上海卷） |
| quarantine | 19 | 见 §5 隔离清单 |

非 canonical 行共 **48** 个，其中 29 个给出并入方向（27 直并 + 2 待复核）、19 个隔离。

## 5. 隔离清单（19 项）及依据

| 类别 | 名称 | 关键依据 |
| --- | --- | --- |
| 事业单位/选调卷名 | 公共基础知识、公共基础知识-单项选择题、公共基础知识-判断题、公共基础知识-多项选择题、综合知识、综合基础知识、法律基础知识 | 任务指令明确隔离 公共基础知识*/综合知识；抽样卷源为天津选调综合知识卷等；内容跨法律/政治/经济/人文 |
| 非行测题型段 | 判断题、辨析题、单项选择题、常识判断（多选题） | 对错判断/主观辨析/多选不符合行测单选契约；卷源为河北/天津选调 |
| 他考特有科目 | 科学素养、科学推理、知觉速度与准确性 | 事业单位C类式双域(自然328+社会200)；广东乡镇卷理科推理；江苏B类(2008-2013)感知速度测验——六模块均无一一对应 |
| 跨模块混合段 | 思维能力测验、数理能力、数量关系与资料分析、综合分析能力、行政职业能力测验 | 本地 path 抽样证实跨模块：思维=判断79+数量14；数理=数量165+资料189；数资=18:20；综合分析能力=常识60+政治49（与「资料分析类」预期矛盾）；行测=整卷名 |

## 6. 与任务提示的偏差说明（2 处，均按铁律从严）

1. **数理能力**：提示为「存疑标注 → quantitative_relations」。本地证据显示其题量数量:资料 ≈ 165:189，整体并入将误并近半，故按『宁隔离不误并』改判 `quarantine`；复核后正确动作是**题级拆分**（按 path_id 一级），不是整体并入。
2. **综合分析能力**：常见认知是上海卷的资料分析段，但本地 path 抽样为常识 60 + 政治 49，与预期矛盾，故隔离待人工复核，未按字面并入 data_analysis。

## 7. 生产 42 对本地 48 的对账（复核点 A）

生产审计计得 42 个脏 module_name；本词表从导入源枚举出 48 个非 canonical 名（含 2 个括号变体「数量关系（数学运算/数字推理）」与 0 题的顶级「图形推理」）。48 应为 42 的超集（导入时部分值可能被归一或无 active 题）。上线前必须执行：

```sql
select distinct q.module_name
from product.questions q
left join product.vocab_module_alias_map am
  on am.version = 'vocab@v1' and am.raw_module_name = q.module_name
where am.raw_module_name is null;
```

结果应为空；任何新脏值按 R0 先隔离、再补词表并升版本。

## 8. 面定义与拆分结果（facet_rules.csv，398 词）

- **material_form（4 词）**：综合资料、文字资料、统计表、统计图。资料分析的材料载体形态，在本地 family 级与 micro_skill 级两级混挂（如 统计表 family 40 次 + micro 590 次），必须与考点分轴。
- **skill（392 词）**：其余全部考点/题型词。其中 17 词为逐词判定（词面含图/表/资料/材料但实为考点）：**综合分析是考点不是形态**（对整篇材料设问的综合判断题，生产 micro_skill 501 次）——任务审计专项判定；组合排列-材料、直接找数、图形推理、图形数阵、语句表达、几何公式类-平面图形 等同理。
- **exclude（2 词）**：待分类、其他。占位符不建节点，`needs_review=true` 进人工归类队列。

词表覆盖为**本地观察集**（family 37 + point 171 + micro 323，去重 398）；生产词表更大（325/247/334），未观察到的词按 R2 默认 skill，见复核点 B。

## 9. 后续人工复核点

- **A. 生产对账**：§7 的空集校验；diff 出的新值按 R0 处理。
- **B. 词表补全**：生产 knowledge_family 325 词 vs 本地 37 词——需拉取生产 distinct 词表 diff 本表，重点排查是否还有材料形态词（如「图表资料」「混合资料」类变体）未收录。
- **C. map_needs_review 两项**：常识应用/常识应用能力按题级 path_id 把 政治理论/* 题改挂 political_theory 后，方可降为 `map`。
- **D. 题级拆分三项**：数理能力、数量关系与资料分析、思维能力测验——复核通过后按 path_id 一级拆分归模块，而非整体并入。
- **E. 综合分析能力定性**：抽读原题确认上海卷该段实际构成，再决定拆分方案。
- **F. 多选/判断题型**：常识判断（多选题）、公共基础知识-判断题 等待题型字段（domain/format）核对后决定是否以「题型受限」形式保留。
- **G. generator 对接**：~~`refresh_knowledge_map_nodes.sql` 改为 join 本词表~~ **已完成（2026-07-26，v2 生成器）**，见 §11。

## 10. 版本纪律

`vocab@v1` 为首版。任何行级修改（含复核通过后的 action 变更）都必须以新版本整体发布，旧版本行保留在 CSV 历史（git）与库表（version 主键）中备审计。

## 11. 四级树生成器 v2（refresh_knowledge_map_nodes_v2，2026-07-26）

`scripts/sql/refresh_knowledge_map_nodes.sql` 升级为四级树生成器，消费本词表（vocab@v1）。
仅仓库文件 + 本地 fixture 验证（`backend/tests/test_knowledge_map_tree_v2.py`），**未执行生产刷新**，生产执行留给发布流程（先 `load_vocabulary_seed.sql` 装载词表，再依序跑 `refresh_guokao_exam_tag_weights.sql`、`refresh_knowledge_map_nodes.sql`）。

### 结构决策（逐条依据）

1. **四级父链** `module → knowledge_family → knowledge_point → micro_skill`：
   - 知识点的父族 = 与其**共现题数最多**的 knowledge_family（`question_tags` 同题共现，可重算复核；并列按共现数降序、族名升序定序）；无共现族的点直挂模块。
   - 微技能的父点 = 共现最多的 knowledge_point；其族**严格沿父点继承**（保证树形，不产生「技能的族 ≠ 父点的族」的交叉边）；无父点时回退共现最多的族，再退模块。
   - 依据：taxonomy_path 是平面标签（§1），层级只能由三级词表共现重建。v1 消费的 `question_type`/`taxonomy_path` 不再进树主链。
2. **material_form 面的裁决：降为节点属性，不建 facet 节点。**
   - 原因（按你判断并在此说明）：`knowledge_map_nodes.node_type` 的 DDL 枚举只有 module/question_type/knowledge_family/knowledge_point/micro_skill 五值（`backend/tests/sql/minimal_schema.sql:415-448`），建独立 facet 节点必须借用错误的 node_type 或改生产 DDL——两者都违反「不触生产、宁隔离不误并」。材料形态是**题目载体属性**而非技能，故聚合为每个节点的 `evidence->'material_form_breakdown'`（该节点题集内 综合资料/文字资料/统计表/统计图 的去重题数分布），沿链每层可见，未来做「按材料形态筛题」时直接可用；若之后决定单列形态轴，再以 DDL 迁移 + 生成器 v3 处理。
   - `facet='exclude'`（待分类/其他）不建任何节点（R2）。
3. **自适应折叠**：叶子(micro_skill)题量 < 阈值 → 上收进父 knowledge_point；knowledge_point < 阈值 → 上收 family。被折叠词记入吸收节点 `aliases` 与 `evidence->'collapsed_from'`（含题量，可审计）。父点被折叠时其存活叶子改挂祖父族。**module 不作为吸收目标**：直挂模块的稀疏节点保留不折叠（把叶子并进模块根会丢失全部区分度，宁保留细节不向根误并）。阈值是 SQL 变量：默认 30，执行前可 `set app.knowledge_map_collapse_threshold = <n>` 会话级覆盖，实际生效值随 `evidence->'collapse_threshold'` 落盘。
4. **question_count 每层聚合**：节点题量 = 命中该节点 tag **或其任一后代节点 tag** 的去重题数（knowledge_point 15% 缺标的题也能计入上层）。
5. **image_unsupported 沿父链向下继承**：节点不支持 = 自身命中图形类词面 / 全部题 visual_required / 任一祖先不支持。
6. **near5_weight** 仍按 (module, tag_type, tag_value) 直连 `exam_tag_weights`；为让 micro_skill 层有权重，`refresh_guokao_exam_tag_weights.sql` 的 tag_type 白名单新增 `'micro_skill'`（权重按 tag_type 内归一，不影响既有层分布）。别名模块（如 资科分析）的近五年权重行仍按原始 module_name 键存放、并入后的 canonical 节点不聚合它们——国考卷段名即 canonical 名，实际影响为空集，留作已知边界。
7. **治理规则接线**：R0（词表未命中模块不进图）、R1（仅 canonical/map 进图，map_needs_review 等同隔离）、R2（材料形态/占位符不建技能节点）在生成器 CTE `module_map`/`skill_tags`/`material_tags` 中逐条落地并有注释指回本文档；执行前有 vocab@v1 装载守卫（未装载即除零中止，防止静默空产出）。
8. **版本与幂等**：产出 `evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v2'`；每次刷新清除 v1（平挂结构一次性淘汰）与 v2 的历史产出后重建，人工治理节点（`manual_confirmed`）沿用 v1 的保留语义。

### 本地验证（不触生产）

`backend/tests/test_knowledge_map_tree_v2.py`（对齐 `test_exam_tag_weights.py` 的 SQL 测试模式，本地 Postgres + minimal_schema + 分号拆句执行仓库 SQL）7 个用例断言：四级父链闭合与 display_path、折叠与 collapsed_from/aliases、阈值 GUC 可调、quarantine/R0/R1 模块零节点与别名并入、材料形态属性化、image_unsupported 父链继承、micro_skill 层 near5 权重传播。词表数据直接从 `scripts/sql/vocabulary/*.csv` 装载（与 `\copy` 等价），测试口径与生产装载口径一致。
