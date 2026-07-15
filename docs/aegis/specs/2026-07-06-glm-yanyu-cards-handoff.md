# GLM 产卡交接：言语理解与表达首批 10 张（执行文件）

- 日期：2026-07-06
- 上游规格：`2026-07-06-glm-yanyu-method-cards-spec.md`（fable，词表与流水线的唯一权威来源）
- 本文件角色：把上游规格压缩成 Trae/GLM 5.2 可直接执行的产卡指令。词表、卡数、错因 code 与上游冲突时，**一律以上游规格为准**。
- 历史注记：此前曾拟过一版 20 张卡、tag_path 用 `行测/...` 前缀的清单，经与 fable 规格 2.4 节对照，该前缀无法通过 `question_tags` 反向挂载，已废弃。

## 你的角色与边界（GLM 阅读起点）

你是「公考搭子」App 的方法卡内容作者。你只产出一个 YAML 文件，不接触数据库、不写代码、不修改规格。
产出会由工程 Agent 用 `scripts/import_method_cards.py` 校验入库，格式或词表不合规会被整批打回。

## 任务

撰写 **10 张**言语方法卡，输出为单个 YAML 文件（一个列表，每元素一张卡）。
每个 tag_path 恰好一张卡，tag_path 词表如下（一字不差复制，不得自创/改写/合并）：

| # | tag_path | 说明 |
|---|---|---|
| 1 | 言语理解与表达/逻辑填空/实词辨析 | 40-50 秒/题 |
| 2 | 言语理解与表达/逻辑填空/成语辨析 | 40-50 秒/题 |
| 3 | 言语理解与表达/逻辑填空/关联词辨析 | 40-50 秒/题 |
| 4 | 言语理解与表达/片段阅读/主旨概括-转折结构 | 50-60 秒/题 |
| 5 | 言语理解与表达/片段阅读/主旨概括-总分结构 | 50-60 秒/题 |
| 6 | 言语理解与表达/片段阅读/意图判断题 | 50-60 秒/题 |
| 7 | 言语理解与表达/片段阅读/细节判断题 | 50-70 秒/题 |
| 8 | 言语理解与表达/片段阅读/标题填入题 | 50-60 秒/题 |
| 9 | 言语理解与表达/语句表达/语句排序题 | 60-70 秒/题 |
| 10 | 言语理解与表达/语句表达/语句填空题 | 50-60 秒/题 |

不产篇章阅读卡、不产虚词专项卡（上游规格 1.2 节已裁定，勿自行加卡）。

## error_signatures.code 词表（只能从此表选用，禁止发明新 code）

| code | 含义 | 用于卡 |
|---|---|---|
| logic_fill_context_ignored | 只看空格近处，忽略全文语境呼应 | 1/2/3 复用 |
| logic_fill_synonym_confused | 近义词当可互换，忽略轻重或感情色彩 | 1、2 |
| logic_fill_conjunction_reversed | 关联词逻辑判反（转折当因果等） | 3 |
| main_idea_turn_missed | 转折结构只看转折前 | 4 |
| main_idea_detail_as_main | 分述句当主旨句 | 5 |
| intent_over_infer | 意图题过度引申 | 6 |
| intent_under_infer | 意图题引申不足，主旨原话当意图 | 6 |
| detail_swap_subject | 细节题偷换主语或客体 | 7 |
| detail_swap_time | 细节题偷换时态或时间范围 | 7 |
| title_too_narrow | 标题只覆盖某一段 | 8 |
| title_too_broad | 标题过度宽泛 | 8 |
| sentence_order_head_wrong | 排序首句选错 | 9 |
| sentence_order_link_missed | 排序漏抓指代/时间衔接信号 | 9 |
| sentence_fill_link_missed | 填空没看前后句逻辑衔接 | 10 |
| sentence_fill_position_wrong | 过渡句当总结句或反之 | 10 |

## 硬约束（违反任意一条 = 整批打回）

1. tag_path 与 error_signatures.code 只能从上方两个词表选取。
2. **status 一律 `draft`**；validation 各字段一律 0 / 空列表 / 空值。你没做过回测，不许声称做过。
3. **author_note 如实写明 AI 生成**：`AI 生成（GLM），基于言语高频考点方法论总结；待产品负责人按流水线回测`。
4. 不得编造统计数据，方法步骤里禁止"90% 的题都能用"这类无出处比例。
5. method_steps 是可执行动作序列，每条以动词开头，3-5 条；写"拿到题先做什么、再做什么"，不是知识点罗列。
6. 每卡必须含 applicability.exclude（什么情况这张卡不适用）与 pitfall（一句话，最易翻车点）。
7. fix_action 用占位符模板：`做 {n} 题XX同类题，限时 {sec} 秒/题`。
8. time_budget_sec 按上表说明列取整数秒。
9. id 命名 `mc_yanyu_<考点拼音缩写>_01`，全批不重复。
10. exam_scope 一律 `guokao`，track 一律 `xingce`，module_name 一律 `言语理解与表达`（不写短名"言语理解"）。
11. 人类应试锚定：普通考生、无计算器、按限时列的秒数能走完你写的步骤。走不完的步骤删掉。
12. 语言口吻：直接、像老师带练时的口头指令。禁止"综上所述""值得注意的是"等书面腔。

## YAML 格式（严格对照，字段不增不减）

```yaml
- id: mc_yanyu_zhuanzhe_01
  exam_scope: guokao
  track: xingce
  module_name: 言语理解与表达
  tag_path: 言语理解与表达/片段阅读/主旨概括-转折结构
  title: 转折结构主旨题：转折之后是重点
  recognition: 文段出现"但是、然而、却、其实、事实上"等转折词，题干问主旨或作者态度
  applicability:
    include:
      - 文段有明确转折词且问中心思想
      - 转折后内容与选项能直接对应
    exclude:
      - 转折词出现在举例内部，非文段主干
      - 双重转折需要还原原意的情况
  method_steps:
    - 通读时圈出转折词，直接跳到转折之后读
    - 概括转折后语义，与选项逐个比对
    - 排除只覆盖转折前内容的选项
    - 两个选项都在转折后时，选与主题词更贴的
  error_signatures:
    - code: main_idea_turn_missed
      description: 转折结构只看转折前，漏掉转折后主旨
  fix_action: 做 {n} 题转折结构主旨同类题，限时 {sec} 秒/题
  time_budget_sec: 55
  accuracy_rule: 转折词后语义与选项主题词一致才可选，只沾边不选
  pitfall: 转折之后才是作者真正想说的，转折前都是铺垫
  author_note: AI 生成（GLM），基于言语高频考点方法论总结；待产品负责人按流水线回测
  validation:
    tested_count: 0
    passed_count: 0
    sample_question_ids: []
    reviewer:
    reviewed_at:
  priority: 5
  status: draft
```

## 交付

单个文件，命名 `yanyu_batch01_draft.yaml`，直接输出完整 YAML 内容。
卡片顺序按词表 #1-#10 排列。示例卡（#4 转折）可在示例基础上完善，不必推翻重写。
