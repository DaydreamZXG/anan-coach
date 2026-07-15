# 后端审计报告（fable）

- 日期：2026-07-03
- 审计范围：`backend/app/`、`backend/tests/`、`backend/pyproject.toml`、`scripts/full_verify.sh`、`scripts/server_setup_learning_ai_tables.sh`，以及 iOS 端 `AppModels.swift` 中与后端契约相关的部分
- 对照依据：`docs/aegis/specs/2026-07-03-ai-agent-information-architecture.md`、`docs/aegis/plans/2026-07-03-knowledge-base-to-magic-loop-master-plan.md`
- 状态：仅审计文档，未修改任何代码

---

## 总评

后端是一个结构干净的小型 FastAPI 应用：分域清晰（knowledge / taskstream / practice / essay / ai）、参数化 SQL、无 ORM 滥用、申论闭环有 quota + audit 双约束、测试是契约式的。**骨架是对的。**

但按"AI 学习搭子 + 可商业化"的标准衡量，有三条硬伤：

1. **身份体系是空的**：`X-Learner-Id` 由客户端任意提供，配额可被无限绕过，AI 成本敞口不受控；
2. **"AI 排好今日计划"目前是硬编码剧本**：taskstream 没有读任何 learner.* 记忆，也没有 AI 参与，产品主张与实现不符；
3. **行测做题记录不落库**：practice 提交答案后没有写任何 learner.* 表，"做完我会重排明天"无数据支撑，学习记忆边界只建了一半。

---

## 1. 是否以 product.* 知识库和 learner.* 用户记忆为运行边界

### 符合的部分

- `knowledge/repository.py` 全部查询严格限定在 `product.questions`、`product.question_options`、`product.question_explanations`、`product.question_occurrences`、`product.question_tags`、`product.knowledge_chunks`、`product.question_assets`，无跨 schema 泄漏；测试还显式断言响应中不出现 `source_qbank` / `canonical_qbank`（`test_knowledge_routes.py:126-127`），内部管线 schema 不外泄，这是好实践。
- `essay/repository.py` 的写入严格落在 `learner.essay_attempts`，且 `update_rewrite` 带 `learner_key` 过滤，防止跨用户改写他人 attempt。
- AI 调用留痕落在 `ai_audit.usage_daily` / `ai_audit.interaction_logs`，与建表脚本 `scripts/server_setup_learning_ai_tables.sh` 一致。

### 不符合的部分

- **practice 域没有 learner.* 写入**。`practice/service.py` 的 `submit_answer` 只做判分和拼装反馈，答题记录、错因、正确率完全不落库。learner 侧记忆只覆盖了申论，行测是"无记忆"的。taskstream 声称"做完我会重排明天"（`primary_action.subtitle`），但没有任何数据可供重排。
- **taskstream 完全不读 learner.***。`taskstream/service.py` 的 `get_today` 除了从 `product.questions` 取 3 个题目 ID，其余全部硬编码：`score_gap=14.0`、`exam="广东省考"`、`position_group="珠三角综合管理岗"`、42 分钟、双轨配比、所有 reason 文案。"今日计划"与用户状态零关联。
- **essay 任务素材在代码常量里**（`ESSAY_MATERIAL` / `ESSAY_PROMPT`），`source_grounding="curated_material"` 不指向任何 product.* 表。首版可接受，但这是知识库边界上的一个已知豁免口，需要显式承认并排期收编进 product.*。

**结论：读边界（product.*）执行到位；写边界（learner.*）只完成了申论一半；taskstream 与 learner.* 完全断连。**

## 2. 隐藏 fake user / fake auth / fake model fallback / PreviewData 式兜底

### 查到的问题

- **[P0] 事实上的 fake auth**：`essay/router.py` 的 `require_learner_key` 只检查 `X-Learner-Id` 头是否非空，不校验来源。iOS 端（`AppModels.swift:590`）用 UserDefaults 里的安装 ID 充当 learner key——重装 App 或任意伪造 header 即获得全新配额。`bootstrap` 接口宣称 `appleSignIn: true`，但后端不存在任何 Apple Sign In 校验路径。这不是"简化"，是**配额和成本控制被整体架空**。
- **[P0] 生产路由中的 PreviewData 式硬编码**：
  - `/api/v1/workbench/preview`（`api/routes.py:81-159`）整个响应是硬编码的假学习对象（"资料分析今日测验 8 题"、"言语主旨卡 12 张"、sourceCount 等），没有任何一条来自数据库。这正是主计划禁止的"假数据兜底"，且测试（`test_api.py:38-53`）把这份假数据固化成了契约。
  - `/api/v1/bootstrap` 中 `questionBank` 五个统计数字（55214 等）硬编码，会随数据导入静默失真。
- **[P1] taskstream 的"AI 判断"是剧本**：headline"搭子给你排好了今天 42 分钟"、rationale、全部 reason 均为写死文案，无 AI、无个性化。对照产品原则"AI 的判断要有依据"，当前是"文案假装有依据"。
- **无 fake model fallback（好消息）**：`ai/provider.py` 在 `AI_API_KEY` 缺失时抛 `AIProviderConfigurationError` → 503；响应非 JSON 或维度不符合契约时抛 `AIProviderResponseError` → 502。没有静默降级到假点评，符合诚实原则。`db/session.py` 同理，无 DATABASE_URL 时抛错而非内存兜底。
- 测试中的 `FakeEssayService` / `FakeKnowledgeService` 是 dependency_overrides 注入的测试替身，仅存在于 tests/，不构成生产兜底，合规。

## 3. practice / essay / taskstream / knowledge 的职责边界

| 域 | 职责 | 评价 |
|---|---|---|
| knowledge | product.* 只读查询 + DTO 映射 | 清楚。repository/service/router 分层规范，`_safe_tos_object_key` 有防路径穿越意识 |
| taskstream | 编排今日任务，引用 knowledge | 结构清楚但内容硬编码；依赖方向正确（taskstream → knowledge） |
| practice | 行测任务下发 + 判分 | 清楚但残缺：只认 `TODAY_XINGCE_TASK_ID` 一个任务；判分逻辑内聚；缺 learner 落库 |
| essay | 申论任务 + AI 点评 + 重写对比 | 最完整的域：AI、quota、audit、learner 存储齐备 |
| ai | provider + audit，横切能力 | 清楚，被 essay 正确复用 |

三处边界瑕疵：

- `practice/service.py` 和 `taskstream/service.py` 各自重复了一份相同的 `search_questions(domain="objective", module_name="判断推理", year=2026, limit=3)` 调用。两次调用之间若数据变化，taskstream 下发的 `question_ids` 与 practice 实际取到的题可能不一致——题目选择逻辑应有单一出处。
- `TODAY_XINGCE_TASK_ID` / `TODAY_ESSAY_TASK_ID` 定义在 taskstream，practice 和 essay 反向 import，形成"编排层常量被执行层依赖"的轻度耦合。任务 ID 应来自数据或共享常量模块。
- `api/routes.py`（bootstrap / workbench_preview）游离在域结构之外，且是假数据重灾区。

## 4. AI 调用是否被 quota/audit 约束、是否可能失控

### 做对的

- 唯一的 AI 调用点（essay review）先 `reserve_quota` 再调 provider；quota 用单条 upsert + `where used_count < limit_count` 原子扣减，并发下不会超扣（同 learner_key 行级锁串行化）。
- 成功与失败都写 `interaction_logs`，含 provider/model/latency/摘要，审计链完整。
- 超时 30s、`temperature=0.2`、响应 schema 严格校验（五维顺序固定），失控面小。

### 失控风险

- **[P0] quota 锚定在可伪造的 learner_key 上**：换一个 header 就是新额度。当前"每日 1 次"的限制对恶意调用者形同虚设，AI 成本敞口 = 无限。在真实身份体系落地前，任何放量投放都不安全。
- **[P1] 失败不返还配额**：AI 调用失败（超时/502）时，配额已扣、不退。免费用户一天仅 1 次，一次上游抖动就烧掉当日额度——商业化后必然产生客诉。应在失败路径返还，或改为"先调用后落账 + 幂等锁"。
- **[P1] limit=0 的边界 bug**：`reserve_quota` 的 INSERT 分支无条件写入 `used_count=1`，不检查 limit。若未来某 feature 配 `limit=0`（如 bootstrap 里 `studyPlanRegeneration.freePerDay: 0`），首日第一次调用会成功（used=1 > limit=0）。当前 essay limit=1 未触发，属潜伏炸弹。
- **[P1] 免费/Pro 无区分**：bootstrap 宣告了 free/pro 两档配额和 IAP 商品，但后端只有写死的 `ESSAY_REVIEW_DAILY_LIMIT = 1`，无订阅态校验路径，IAP 收入与权益发放当前无法闭环。
- **[P2] 上游是逆向代理**：`ai_provider_base_url` 默认指向 `gemini-web2api.ergouapi.co`（Gemini 网页逆向代理），provider_name 也自认 `gemini_reverse_proxy`。稳定性、合规性、封禁风险都不适合商业化生产依赖，需迁移到官方 API 或备案网关。
- **[P2] 同步 httpx 阻塞 worker**：`review_essay_answer` 在同步路由里同步等待最长 30s，并发略高即拖垮 uvicorn worker。应改 async client 或线程池。

## 5. 数据库 / schema / API 是否为商业化留了稳定边界

### 好的

- 多层 schema 命名（product / learner / ai_audit / ops，内部另有 source_qbank）语义清楚，权限最小化（`gongkao_api` 仅 select/insert/update）。
- API 响应统一 camelCase alias，DTO 与表结构解耦，iOS 契约稳定。
- `learner.essay_attempts` 用 jsonb 存 review_payload，点评 schema 演进不需要迁移，合理。

### 风险

- **[P1] 无迁移体系**：schema 变更靠 `scripts/server_setup_learning_ai_tables.sh` 手工 SSH 执行 `create table if not exists`，无版本、无回滚、无漂移检测。商业化维护期第一个要补的基建（Alembic 或等价物）。
- **[P2] 脚本里硬编码生产服务器 IP（101.96.198.253）+ root SSH**，基础设施细节进入代码仓库，且绕过一切变更审计。
- **[P2] API 缺防线**：无 CORS 策略、无网关限流、无请求体大小上限（仅 pydantic 字段级 max_length）。`/docs` 依赖 `APP_ENV=production` 才关闭，环境变量漏配即暴露。
- **[P2] 事务边界混乱**：repository 方法内部各自 `commit()`（`essay/repository.py`、`ai/audit.py`），service 无法把"扣配额 + 记日志 + 存 attempt"组成一个事务；当前顺序下 AI 成功但 `create_attempt` 失败时，配额已扣、日志已记、attempt 丢失。
- **[P3] README 与实际路由不一致**（README 写 `/api/v1/today/preview`，实际为 `/api/v1/taskstream/today` 与 `/api/v1/workbench/preview`）。

## 6. 测试覆盖评估

### 已覆盖

- health / bootstrap / workbench 契约（`test_api.py`）
- knowledge 详情（AI 解析优先、来源出处、内部 schema 不泄漏）、404、搜索（`test_knowledge_routes.py`）
- taskstream 今日契约 + 真实题目 ID 贯通、practice 任务下发与判分（`test_practice_taskstream_routes.py`）
- essay 任务契约、401（缺 learner key）、429（配额超限）、点评契约、重写契约（`test_essay_routes.py`）

### 缺口（按重要度排序）

1. **`AIAuditRepository.reserve_quota` 的 SQL 无任何测试**——这是全系统商业上最关键的一段 SQL（upsert + 条件扣减），其并发正确性、limit=0 边界、跨日重置行为全部未验证。需要真实 Postgres 的集成测试（如 testcontainers）。
2. **`AIProvider` 的响应解析无测试**：非 JSON、维度乱序、缺字段等分支（`_validate_essay_review_payload`）零覆盖。
3. **`KnowledgeRepository` 的 SQL 无测试**：search 的动态 where 拼接（tag/province/year 子查询）无库验证。
4. **essay service 的真实编排无测试**：quota 扣减 → AI 失败 → 日志落库的失败路径只在 fake 层模拟，`EssayService.review_answer` 本体从未被执行过。
5. **practice 判分边界**：answer 为 null 的题、小写输入、空白输入等仅部分隐式覆盖。
6. `full_verify.sh` 只跑 pytest + 双平台 xcodebuild；最后一行引用机器私有路径 `/Users/zxg/.codex/...`，任何其他环境（CI）必然失败，验证脚本不可移植。

## 7. 问题分级汇总

### P0（商业化阻断，必须先修）

| # | 问题 | 位置 |
|---|---|---|
| P0-1 | learner 身份可任意伪造：`X-Learner-Id` 无校验，配额可无限绕过，AI 成本敞口不受控；bootstrap 宣称 appleSignIn 但无实现 | `essay/router.py` `require_learner_key`、`api/routes.py`、`AppModels.swift:590` |
| P0-2 | `/api/v1/workbench/preview` 全量硬编码假学习对象并被测试固化为契约，直接违反"无假数据兜底"产品原则 | `api/routes.py:81-159`、`test_api.py:38-53` |

### P1（产品主张与实现不符 / 明确 bug，UI 重构同期或紧随修复）

| # | 问题 | 位置 |
|---|---|---|
| P1-1 | taskstream 今日计划全硬编码（score_gap、考试、配比、文案），不读 learner.*、无 AI 参与，"搭子排好了"名不副实 | `taskstream/service.py` |
| P1-2 | practice 答题记录不落 learner.*，行测无学习记忆，"做完重排明天"无数据基础 | `practice/service.py` |
| P1-3 | AI 调用失败不返还配额，免费用户一次上游故障烧掉当日额度 | `essay/service.py` `review_answer` |
| P1-4 | `reserve_quota` INSERT 分支不检查 limit，limit=0 的 feature 首次调用必然越限成功 | `ai/audit.py` |
| P1-5 | 免费/Pro 配额无订阅态校验，IAP 权益无法发放，bootstrap 承诺与后端能力脱节 | `api/routes.py`、`essay/service.py` |
| P1-6 | 无数据库迁移体系，schema 变更靠 SSH 脚本手工执行 | `scripts/server_setup_learning_ai_tables.sh` |
| P1-7 | bootstrap `questionBank` 统计硬编码，将随数据导入静默失真 | `api/routes.py` |

### P2（工程健康 / 中期修复）

| # | 问题 | 位置 |
|---|---|---|
| P2-1 | AI 上游为 Gemini 逆向代理（gemini-web2api），稳定性与合规不适合生产 | `core/config.py`、`ai/provider.py` |
| P2-2 | 同步 httpx 30s 阻塞 worker，并发能力差 | `ai/provider.py` |
| P2-3 | repository 层各自 commit，无法组成事务；AI 成功但 attempt 落库失败时数据不一致 | `essay/repository.py`、`ai/audit.py` |
| P2-4 | 关键 SQL（quota 扣减、knowledge 搜索）与 provider 解析零测试覆盖 | `backend/tests/` |
| P2-5 | 生产 IP + root SSH 硬编码入仓；无 CORS/限流/请求体上限；/docs 依赖环境变量漏配即暴露 | `scripts/*.sh`、`main.py` |
| P2-6 | taskstream 与 practice 各查一次题目选择逻辑，可能下发与执行不一致；任务 ID 常量反向耦合 | `taskstream/service.py`、`practice/service.py` |
| P2-7 | `full_verify.sh` 依赖机器私有路径，CI 不可移植 | `scripts/full_verify.sh` |

### P3（低优先级）

| # | 问题 | 位置 |
|---|---|---|
| P3-1 | README 路由清单与实际不一致 | `backend/README.md` |
| P3-2 | `_source_summary(occurrences: object)` 等松散类型标注 | `practice/service.py` |
| P3-3 | `api/routes.py` 游离于域结构之外，bootstrap/workbench 应归入各自域 | `api/routes.py` |

---

## 与 UI 重构的关系（建议排序）

UI 重构（fable-ui-diagnosis.md）可以先行，现有 API 契约足以支撑新视觉层级。但注意两点：

1. 新 iPad 右栏"搭子判断"如果按方案接 rationale/reason 字段，展示的仍是硬编码文案（P1-1）——UI 上不要用"AI 为你分析"字样修饰死数据，措辞保持中性，直到 taskstream 真正个性化；
2. P0-1（身份）和 P1-3/P1-4（配额）应在任何增长动作之前修复，与 UI 工作无依赖，可并行推进。
