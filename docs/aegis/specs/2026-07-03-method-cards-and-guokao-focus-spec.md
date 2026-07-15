# 规格：方法卡（method_cards）+ 国考口径收敛

- 日期：2026-07-03
- 作者：fable（架构与规则设计）；卡片内容产出：产品负责人（教研）
- 执行者：codex（建表/导入/API）、fable（planner 规则与 UI 接入）
- 关联：fable-backend-audit.md（P1-1 硬编码的解法）、fable-handoff-to-codex.md（任务纪律沿用）
- 状态：规格草案，等产品负责人确认卡片版式后生效

## 0. 一句话

方法卡是"这类题怎么判断、怎么做、错在哪"的结构化 SOP，由 AI 基于我们自己的题库生成、经产品负责人真题回测后发布。质量保证不来自出处，来自回测——这是给搭子装脑子、取代 taskstream 硬编码剧本的正道。

## 1. 战略前提：口径收敛到国考

### 1.1 现状错位（已核实）

- 岗位库 product.positions 是国考数据（60,471 岗位，2024-2026 三届，源 gwy.db）。
- 题库以国考真题权重最高。
- 但 taskstream/service.py 硬编码的目标是"广东省考 · 珠三角综合管理岗"，文案与数据错位，必须修正。

### 1.2 收敛决定

1. 产品第一阶段只服务国考（中央机关及其直属机构考试录用公务员）。
2. learner 记忆中 target_exam 默认且暂时只允许 guokao。
3. 所有 UI 文案、taskstream target、测试夹具中的"广东省考"改为国考口径，岗位组示例从真实 positions 表取。
4. 省考扩展留接口（method_cards 带 exam_scope 字段），第一阶段不实现。

## 2. 方法卡数据模型

### 2.1 表：product.method_cards

| 字段 | 类型 | 说明 |
|---|---|---|
| id | text PK | mc_ 前缀 + 短 uuid |
| exam_scope | text | 首期固定 guokao，为省考扩展预留 |
| track | text | xingce / shenlun |
| module_name | text | 与 product.questions.module_name 同枚举 |
| tag_path | text | 对齐现有 tag 体系的知识点路径。必须能与 question_tags 匹配，这是接入闭环的逻辑外键（不强约束） |
| title | text | 卡名，如"基期量计算：化除为乘" |
| recognition | text | 识别信号：什么题面特征说明该用这张卡（一句话） |
| applicability | jsonb | 适用/排除条件：{include: [...], exclude: [...]}，防止把相似但本质不同的题硬套同一卡 |
| method_steps | jsonb | 解法步骤数组，每步一句短话，不超过 5 步 |
| error_signatures | jsonb | 常见错因数组 [{code, description}]，code 为错因标签枚举，判分反馈映射到它 |
| fix_action | text | 修复动作模板，如"做 {n} 题同类，限时 {sec} 秒/题"，参数由 planner 填 |
| time_budget_sec | int | 单题建议耗时，planner 生成训练任务时用于限时 |
| accuracy_rule | text | 精度取舍规则，如"选项差距大于 5% 时估算，小于 5% 时保留一位小数" |
| pitfall | text | 一句话陷阱提示（可空） |
| author_note | text | 教研备注/方法出处的转述说明（内部字段，不下发客户端） |
| validation | jsonb | 发布证据：{tested_count, passed_count, sample_question_ids, reviewer, reviewed_at} |
| priority | int | 教研设定的重要度 1-5，planner 排序用 |
| status | text | draft / published，只有 published 参与闭环 |
| created_at / updated_at | timestamptz | |

### 2.2 内容纪律（法律与质量红线）

1. 产卡方式为"AI 生成 + 真题回测"（流水线见附录 C），不做名师内容蒸馏。禁止逐字复制任何机构讲义、课程文稿、独创口诀体系；名师图书仅作为回测不过关时的人工对照参考。
2. author_note 如实记录产出方式（如"AI 生成，基于题库 tag 抽样，经本人回测审定"），仅内部审计用，永不出现在 API 响应。
3. 每张卡必须能回答三问：怎么认出这类题？怎么做？错在哪最常见？缺一即打回 draft。
4. 回测验收是唯一 published 关口：按卡的 tag_path 抽 10 道真题，人工按 recognition + method_steps 走流程，通过不少于 8/10 才可 published。不过关的卡由产品负责人手工修或废弃。
5. 每张 published 卡的 tag_path 必须映射到 `product.knowledge_map_nodes` 的 published 节点；方法卡不直接消费模型自由生成的 tag 文本。
6. 产出顺序由数据驱动：冷启动按 `product.knowledge_map_nodes.near5_weight`（来源为 `product.exam_tag_weights`）排序；等 learner.practice_answers（codex T2）积累后，按"错误率 x 近 5 年国考 tag 权重"排知识点优先级。
7. 排除范围：图形推理及其他依赖图片题面的视觉题型不做方法卡（AI 解题不可靠，基于文本的回测流水线也跑不通）。判断推理只覆盖定义判断、类比推理、逻辑判断三个文本子模块。数量关系首期暂缓（考生普遍策略性放弃，性价比低）。

### 2.3 导入方式

- 教研以 YAML 产卡（模板见附录 A），入 data/method_cards/ 目录。
- codex 写幂等导入脚本（按 id upsert），走与题库一致的管线纪律。
- 卡片是 product.* 资产：客户端只读，禁止运行时生成。

## 3. 方法卡接入魔法闭环（三处）

### 3.1 判分反馈（practice submit）

现契约的 feedback.nextAction 从套话升级为方法卡驱动：

- 判分错误后，取该题 tag，匹配 published 方法卡（tag_path 最长前缀匹配）。
- 命中：nextAction = 卡的 fix_action（填参）；响应新增字段 methodCard: {id, title, recognition, methodSteps, pitfall}。
- 未命中：nextAction 维持现状，methodCard 为 null。不准编造。

UI 已留槽位：CoachFeedbackCard 的"一句话错因 + 同类识别法"两行直接吃 recognition、applicability 和 error_signatures 命中项。

### 3.2 规则版 planner（取代 taskstream 硬编码，P1-1 解法）

第一版不用 AI，用可解释规则，reason 全部引用真实数据：

- 输入：近 7 天 practice_answers（按 tag 聚合错误率）、essay_attempts（五维最低项）、learner 目标（guokao + 岗位组）、方法卡 priority。
- R1：若某 tag 错误率不低于 50% 且样本不少于 3，今日安排该 tag 修复任务。reason = "昨天{tag}错{x}/{y}题，先用「{卡title}」的方法修"。
- R2：若申论某维度低于 3/5，安排该维度重写任务，reason 引用维度分。
- R3：无学习记录（冷启动）时，按国考模块分值权重 + 卡 priority 出默认组合，reason 明说"还没有你的数据，这是国考高分值模块的起步安排"。诚实，不装懂你。
- 双轨配比：行测/申论时间按弱项分布调整，默认 60/40。

要点：R3 的诚实文案是产品原则。没数据就说没数据，禁止假装个性化。

### 3.3 工作台对象

错题触发的"错因复盘"对象引用命中的方法卡（卡 id 入对象 payload）。用户在工作台点开复盘时看到完整卡片，方法卡成为可沉淀、可回看的学习对象，构成闭环第 5 条的一部分。

## 4. API 变更（全部为新增，不破坏现契约）

| 端点 | 变更 |
|---|---|
| POST practice answers 响应 | 新增可空字段 methodCard |
| GET /api/v1/taskstream/today | target 改国考真数据；task.reason 由 planner 规则生成；新增 planBasis: "rules_v1" 字段，UI 据此控制措辞 |
| GET /api/v1/method-cards/{id} | 新增，工作台复盘对象用 |

## 5. 分工与顺序

| 步 | 内容 | 谁 | 依赖 |
|---|---|---|---|
| S1 | 确认本规格 + 附录 A 卡片模板 | 产品负责人 | 无 |
| S2 | 建表 + 导入脚本 + method-cards API | codex | S1；T2（进行中）不阻塞 |
| S3 | 首批 30 张卡（资料分析优先） | 产品负责人 | S1 |
| S4 | 判分反馈接卡 + 国考口径修正 | codex（规格即本文件 3.1/1.2） | S2、S3 |
| S5 | 规则版 planner（3.2） | fable 写实现规格与验收，codex 实现 | S4 + T2 数据 |
| S6 | UI 接 methodCard 槽位与 planBasis 措辞 | fable | S4 |

验收纪律沿用交接文档：无硬编码兜底、测试查库断言、未命中卡时返回 null 而非编造。

## 附录 A：方法卡产出模板（YAML）

    id: mc_ziliao_jiqiliang_01
    exam_scope: guokao
    track: xingce
    module_name: 资料分析
    tag_path: 资料分析/基期与现期/基期量计算
    title: 基期量计算：化除为乘
    recognition: 题干给出现期量和增长率，问"上年/基期"的量
    applicability:
      include:
        - 题干明确给出现期量
        - 题干给出同比、环比或增长率
      exclude:
        - 问增长量而不是基期量
        - 需要先从图表合并多个现期量
    method_steps:
      - 基期 = 现期 / (1 + 增长率)
      - 增长率绝对值小于 5% 时，用 现期 x (1 - r) 近似，误差可忽略
      - 选项差距大于 5% 时优先估算，不列竖式
    error_signatures:
      - code: base_period_formula_flip
        description: 把基期公式记成 现期 x (1 + r)
      - code: unit_trap
        description: 忽略题干与选项单位不一致
    fix_action: 做 {n} 题基期量同类题，限时 {sec} 秒/题
    time_budget_sec: 55
    accuracy_rule: 选项差距大于 5% 时估算；差距小于 5% 时保留一位小数核算
    pitfall: 增长率为负时 (1 + r) 是减法，别看错符号
    author_note: AI 生成，基于题库基期量 tag 抽样 30 题，经本人回测审定
    validation:
      tested_count: 10
      passed_count: 8
      sample_question_ids:
        - cq_example_01
        - cq_example_02
      reviewer: zxg
      reviewed_at: 2026-07-03
    priority: 5
    status: draft

## 附录 B：错因标签（error_signatures.code）治理

- code 全库唯一、snake_case、英文；同一错因跨卡复用同一 code。
- codex 判分落库时（T2 的 learner.practice_answers 表）预留 error_code 可空列，命中卡后回填，为 planner R1 提供聚合维度。
- 首期错因标注策略：答错即以命中卡的第一个 error_signature 作为候选标注（弱标注）。后续引入 AI 判因需 quota 约束，另出规格。

## 附录 C：产卡流水线（AI 生成 + 真题回测）

决策背景：方法卡不做名师内容蒸馏。名师方法论已通过公开语料融入前沿模型；卡片质量由我们题库上的回测保证，与出处无关。此路线零版权风险，且卡片为我们的 tag 分布定制。

流水线（每张卡）：

1. 抽样：按目标 tag_path 从 product.questions 抽 20-30 道真题（含解析）。
2. 生成：AI 基于抽样题生成 draft 卡。prompt 必须锚定人类应试约束——普通考生、无计算器、50-60 秒/题、只求选对不求算准；要求输出判定流程（识别信号、精度取舍阈值、放弃条件），禁止百科式知识罗列。
3. 自我对抗：同 tag 另抽 10 题，让 AI 按自己产的卡逐步走流程解题。走不通或步骤含糊处即为伪 SOP，回炉重生成。
4. 人工回测：产品负责人按 2.2 第 4 条执行（10 题过 8）。回测中同时校验 recognition 是否能在 5 秒内命中题面。
5. 发布：通过则 status=published 入库；不过关则人工修（可参考名师图书作对照）或废弃。

范围与节奏：

- 首期只做资料分析（约 30-40 张）＋判断推理文本三子模块（约 30 张）＋言语理解（视回测效果试点 10 张）。
- 图形推理、数量关系、常识判断首期不做卡（依据 2.2 第 6 条；常识判断无 SOP 可言，靠积累不靠方法）。
- 申论卡另行设计（方法形态是写作框架而非判定流程），等行测卡流水线跑通后出补充规格。
