# 规格：言语理解（yanyu）方法卡 + 知识图谱词表完善

- 日期：2026-07-06
- 作者：fable（架构与词表设计）；卡片内容产出：产品负责人（教研）
- 执行者：codex（建表/导入/API 已就绪，本规格只增量产卡）、fable（planner 规则与 UI 接入）
- 关联：`2026-07-03-method-cards-and-guokao-focus-spec.md`（方法卡总规格，本文件是其言语子模块的细化）、`refresh_knowledge_map_nodes.sql`（节点生成）
- 状态：规格草案，等产品负责人按附录 C 回测后转 published

## 0. 一句话

言语卡是"这段文字怎么读、读什么、错在哪"的结构化 SOP，承接 2026-07-03 总规格的产卡纪律，本规格只解决两件事：**把言语理解的知识图谱词表补齐到可挂卡状态**，并**产出首批 10 张试点卡**。

## 1. 范围与口径

### 1.1 模块口径（已核实）

- 题库 `product.questions.module_name` 在言语模块存在两种写法：`言语理解` 与 `言语理解与表达`。
- `mock_exam/service.py` 的 `GUOKAO_MOCK_BLUEPRINT` 用 `言语理解与表达`（40 题）；`onboarding/service.py` 用 `言语理解`（8 题，摸底用）。
- **本规格定 `言语理解与表达` 为 canonical module_name**，与国考大纲口径、模考蓝图、`test_exam_tag_weights.py` 夹具一致。`言语理解` 作为别名挂在 `knowledge_map_nodes.aliases`，由 `refresh_knowledge_map_nodes.sql` 的 module 节点统一收敛。
- 方法卡 `module_name` 字段一律写 `言语理解与表达`，不写短名，避免 `method_card_ids` 反向匹配时漏挂。

### 1.2 首期覆盖范围（对齐 2026-07-03 附录 C 第 5 条）

| 子模块 | 是否产卡 | 说明 |
|---|---|---|
| 逻辑填空 | 产（3 张） | 实词、成语、关联词三类，纯文本可回测 |
| 片段阅读 | 产（5 张） | 主旨概括（转折/总分两类）、意图判断、细节判断、标题填入 |
| 语句表达 | 产（2 张） | 语句排序、语句填空 |
| 篇章阅读 | 不产 | 副省级题型，长材料回测成本高，等行测卡流水线跑通后再出补充规格 |
| 选词填空（虚词专项） | 缓 | 国考虚词题量低，性价比不高，首期并入逻辑填空关联词卡 |

排除依据沿用 2026-07-03 第 2.2 第 7 条：图片题面不做卡。言语模块全部为纯文本题面，`support_status` 应为 `text_supported`。

## 2. 知识图谱词表（核心产物）

### 2.1 词表设计原则

1. `tag_path` 三段式：`言语理解与表达/{子模块}/{知识点}`，与方法卡 `module_name` 同根，与 `product.knowledge_map_nodes.display_path` 对齐。
2. `tag_path` 的最末段必须能映射到 `product.knowledge_map_nodes` 中 `node_type=knowledge_point` 的 `tag_value`；映射规则走 2026-07-03 第 2.2 第 5 条（最长前缀匹配，不强约束）。
3. `error_signatures.code` 全库唯一、snake_case、英文；同一错因跨卡复用同一 code（沿用 2026-07-03 附录 B）。
4. 不发明题库里不存在的知识点。词表来源是 `product.question_tags` 里 `tag_type in ('knowledge_family','knowledge_point')` 的真实取值，本规格只做收敛与挂卡。

### 2.2 言语知识图谱词表（tag_path 枚举）

下表是本批次方法卡覆盖的 `tag_path` 与对应 `knowledge_point`。`knowledge_family` 一列对应 `product.questions.knowledge_family`。

| tag_path | knowledge_family | knowledge_point | 试点卡数 |
|---|---|---|---|
| 言语理解与表达/逻辑填空/实词辨析 | 逻辑填空 | 实词辨析 | 1 |
| 言语理解与表达/逻辑填空/成语辨析 | 逻辑填空 | 成语辨析 | 1 |
| 言语理解与表达/逻辑填空/关联词辨析 | 逻辑填空 | 关联词辨析 | 1 |
| 言语理解与表达/片段阅读/主旨概括-转折结构 | 片段阅读 | 主旨概括题 | 1 |
| 言语理解与表达/片段阅读/主旨概括-总分结构 | 片段阅读 | 主旨概括题 | 1 |
| 言语理解与表达/片段阅读/意图判断题 | 片段阅读 | 意图判断题 | 1 |
| 言语理解与表达/片段阅读/细节判断题 | 片段阅读 | 细节判断题 | 1 |
| 言语理解与表达/片段阅读/标题填入题 | 片段阅读 | 标题填入题 | 1 |
| 言语理解与表达/语句表达/语句排序题 | 语句表达 | 语句排序题 | 1 |
| 言语理解与表达/语句表达/语句填空题 | 语句表达 | 语句填空题 | 1 |

合计 10 张。主旨概括按"转折结构"与"总分结构"分两张卡，因为二者的识别信号与主旨定位规则相反，合一会让 recognition 含糊，违反 2026-07-03 第 2.2 第 3 条"三问"纪律。

### 2.3 error_signatures.code 词表（言语专属错因）

下列 code 为本批次引入或复用的错因标签，供 `learner.practice_answers.error_code` 聚合用（2026-07-03 附录 B）。

| code | 含义 | 出现卡 |
|---|---|---|
| logic_fill_context_ignored | 只看空格近处，忽略全文语境呼应 | 实词/成语/关联词三卡复用 |
| logic_fill_synonym_confused | 把近义词当作可互换，忽略轻重或感情色彩差异 | 实词辨析、成语辨析 |
| logic_fill_conjunction_reversed | 关联词逻辑关系判反（转折当因果、递进当并列） | 关联词辨析 |
| main_idea_turn_missed | 转折结构只看转折前，漏掉转折后主旨 | 主旨概括-转折 |
| main_idea_detail_as_main | 把分述句当主旨句，没区分总分层次 | 主旨概括-总分 |
| intent_over_infer | 意图题过度引申，选了文段未触及的对策 | 意图判断 |
| intent_under_infer | 意图题引申不足，把主旨句原话当意图 | 意图判断 |
| detail_swap_subject | 细节题偷换主语或客体 | 细节判断 |
| detail_swap_time | 细节题偷换时态或时间范围 | 细节判断 |
| title_too_narrow | 标题只覆盖某一段，不能统领全文 | 标题填入 |
| title_too_broad | 标题过度宽泛，失去针对性 | 标题填入 |
| sentence_order_head_wrong | 语句排序首句选错（把指代句或转折句当首句） | 语句排序 |
| sentence_order_link_missed | 语句排序没抓指代词或时间顺序的衔接信号 | 语句排序 |
| sentence_fill_link_missed | 语句填空没看前后句的逻辑衔接 | 语句填空 |
| sentence_fill_position_wrong | 语句填空把过渡句当总结句或反之 | 语句填空 |

### 2.4 与 knowledge_map_nodes 的对齐

`refresh_knowledge_map_nodes.sql` 现有逻辑从 `product.question_tags` 的 `tag_type in ('question_type','taxonomy_path','knowledge_family','knowledge_point')` 自动生成节点。本批次方法卡的 `tag_path` 末段（如"实词辨析""主旨概括-转折结构"）必须能在 `product.question_tags` 里找到对应 `tag_value`，否则：

1. 节点不会被生成，`method_card_ids` 反向挂载失败（SQL 第 207-221 行的 `mc.tag_path like e.tag_value || '/%'` 匹配不到）。
2. 判分反馈（2026-07-03 第 3.1 节）取不到卡，`methodCard` 返回 null。

**前置校验**：产卡前先跑一次

```sql
select distinct tag_type, tag_value
from product.question_tags qt
join product.questions q using (question_id)
where q.module_name = '言语理解与表达'
  and q.status = 'active'
  and qt.tag_type in ('knowledge_family','knowledge_point')
order by tag_type, tag_value;
```

确认上表 2.2 的 `knowledge_point` 列都在结果集里。若题库用的是"主旨概括题"而卡片 `tag_path` 用"主旨概括-转折结构"，需在 `knowledge_map_nodes.aliases` 里补别名映射，或在 `tag_path` 末段直接用题库的 `tag_value`，本规格采用后者（见 2.2 表）。

## 3. 产卡流水线（沿用 2026-07-03 附录 C）

每张卡按以下步骤：

1. **抽样**：按目标 `tag_path` 从 `product.questions` 抽 20-30 道国考真题（含解析）。言语题注意排除空格题面被图片替代的少数情况（`visual_required=true`）。
2. **生成**：AI 基于抽样题生成 draft 卡。prompt 锚定人类应试约束：普通考生、无计算器、片段阅读 50-60 秒/题、逻辑填空 40-50 秒/题；要求输出识别信号、排除条件、放弃条件；禁止百科式罗列语法术语。
3. **自我对抗**：同 `tag_path` 另抽 10 题，让 AI 按自己产的卡逐步解题。走不通或步骤含糊处回炉。
