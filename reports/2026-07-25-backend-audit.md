# 岸岸后端 · 全面审计报告

**日期**：2026-07-25　**对象**：`backend/`（FastAPI + SQLAlchemy + Postgres，48 领域 / 206 路由 / 约 13 万行 / 62 迁移）
**方法**：结构摸底 + 全量测试套件 + 科学层人工精读（4 核心机制）+ 2 个专项审计（鉴权隔离、幂等并发，关键结论逐条人工复核）+ 契约缺口核查。

---

## 一、总体结论

**后端可信度高于前端接入层。** 测试 766/767 全绿（真 Postgres，37 分钟）；鉴权与隔离**未发现任何可跨用户读写的 P0**；金钱路径（微信虚拟支付）层层设防无实锤；v2 写端点几乎全部实现"命令表+请求指纹+重放返回首次结果"的真幂等。实锤问题集中在 **v1 遗留游戏化端点**（应退役未退役）与**快速校准器的全历史偏差**。

| 维度 | 评级 | 一句话 |
|---|---|---|
| 测试基建 | ★★★★★ | 766/1，唯一失败是钉前端旧文案的跨仓验收测试 |
| 鉴权与隔离 | ★★★★☆ | 零 P0；私人资料库纪律最佳；纵深防御有缺口 |
| 幂等与并发 | ★★★★☆ | v2 真幂等成体系；v1 遗留双写实锤 |
| 科学层 | ★★★☆☆ | FSRS 调度 A 级；快速校准器 C 级拖后腿 |
| 金钱路径 | ★★★★☆ | 回调去重+advisory lock+CAS+对账收敛，无重复扣费实锤 |

---

## 二、科学层判分（人工精读）

| 机制 | 判分 | 证据 |
|---|---|---|
| 复测调度 `xingce_learning/mastery/scheduler.py` | **A · 有依据的实现** | 真 FSRS 6.3.1、参数冻结+策略版本化；信心数据进评级（猜对/低信心→Hard，高信心也只给 Good 永不跳级）；主动放弃="缺证据非负证据"不污染 FSRS 记忆态；证据阶梯 immediate→1-3天→7-14天钳制 |
| 能力估计 `ability_estimation/service.py` | **B+ · 结构正确的手调融合** | 证据源加权（自报0.35→正式考1.0）+逐源不确定度+限时/情境系数+365天时效+冲突撑宽；输出区间非点值；算法版本化 |
| 计划引擎 `planning/engine.py` | **B · 透明启发式** | 线性加权（岗位收益.30+能力增益.30+复测紧迫.25+时间适配.15−负载.15）+双轨平衡；与 FSRS 到期项真实闭环（`candidates.py` due_verification_count）；权重未经数据验证 |
| 快速校准器 `calibration/service.py` | **C · 危险启发式** | ①练习正确率**全历史累计**无时间窗（`repository.py` `count(*)`），用户进步被旧错题拖住，顶撞"能力更新"产品叙事；②与 ability_estimation 双估计器并存，口径分裂风险；③正确率×100 当分数无模块蓝图加权 |

**修复建议（科学层）**：calibration 改滚动时间窗或 EWMA（小改动）；明确两估计器分工或合并；计划权重待实效数据后再调。

---

## 三、鉴权与账号隔离（专项审计，抽验通过）

**P0：未发现。** 206 条路由全量扫描：业务端点均要求 `get_current_user`；身份恒取自 token（请求体无一处接受用户 ID）；接受资源 ID 的链路要么 SQL 带 user 过滤、要么 service 层显式比对属主。

**P1（纵深防御缺口，当前不可利用，未来易踩坑）**：仓库层裸查函数依赖上游校验——learning_media `transition` 更新不带 user（worker 专用）、community_method_cards 裸查最多、review_session 更新只 where card_id、xingce 版本表按 version_id 裸查、learning_packages `current_packet` 等。建议过滤下沉进 SQL。

**P2**：`/internal/metrics` 明文比较 token（`operations/router.py:228`，已亲验）应改 `hmac.compare_digest`；`/internal-runtime` 内部服务挂公网 app（有 API key 但建议网关拦截）；**题库可匿名爬取**（`knowledge/router.py:22,46`、`essay/router.py:45`，已亲验——商业资产裸奔，建议加鉴权）；docs_url 非 production 开放（确认 staging 不可公网即可）。

**亮点**：private_knowledge 全域每条 SQL 带 owner（含搜索索引与级联删除，已抽验）；文件访问全部 ≤15 分钟预签名 URL 且签发前验属主；data_rights 导出 30+ 表全按 user_id；幂等键统一 user 前缀，跨用户撞 key 只 409 不泄露。

---

## 四、幂等与并发（专项审计，关键项已亲验）

**P0（已亲验 + 影响面修正）**：
1. `POST /api/v1/practice/tasks/{task_id}/answers` 完全无幂等：重放=答题记录双写+XP 双发，且答题与 XP 是两个独立 commit（崩溃出现"有答题无 XP"）。
2. `review-sessions/.../verdict`：判卷行幂等但 XP 每次重放都加。
**影响面修正（我核验）**：新前端手写层对 practice v1/open-court/review-sessions/docket **零调用**——不是"用户今天在双写"，而是"公开端点可被旧客户端/攻击者刷 XP"。**正确修法是照 essay/memory v1 先例直接 410 退役**，而非补幂等。

**P1**：essay 学习工件 replay 检查在锁前且锁后不复查→并发同键 500（约束兜底不双写，客户端体验差）；commerce 下单/回调多段 commit 存在崩溃窗口（有对账收敛，依赖 worker 常开）；`task_editing.authorize_write` 不校验 expected_revision（当前两资源方都有 CAS 兜底，新增资源类型易漏）；docket open-court get-or-create 无锁（v1 低影响）。

**P2**：命令表无 TTL 永久增长；ilink 命令回执全局作用域（理论 key 抢占）；`GET /plans/today` 有写副作用（有锁并发安全，网关重试需注意）。

**亮点**：统一命令表范式（(user_id, command_id) 唯一+指纹+存响应重放+同事务提交+router 单点 commit）；几乎所有写路径先按用户行锁串行化；双端编辑权 lease+generation+takeover 完整建模；AI 额度为条件自增 upsert 不会双扣；outbox 规范（dedup key + skip locked）。

---

## 五、契约缺口清单（对接前端审计的"欠条"）

| # | 前端需要 | 后端现状 | 定性 |
|---|---|---|---|
| 1 | 改答历史（复盘 chip/改对改错分析） | checkpoint 收排除/存疑，无改答事件 | **真缺口**（=行为埋点 spec 落点） |
| 2 | 岗位列表总数 | 仅 cursor/hasMore | **真缺口**或前端改文案 |
| 3 | 考试日期/倒计时 | schema 无 exam_date | **真缺口** |
| 4 | 逐年进面分柱图 | `PositionScorePredictor` 已实现、逐年样本在库 | **仅差 API 投影** |
| 5 | 匹配候选题面 | `ScoredCandidate.content` 内部全量携带 | **仅差公开投影字段** |
| 6 | 拒绝候选（前端 P0-9） | 已有 `/recognitions/{id}/reject`（OCR 层）；match 层拒绝需确认/新增 | 半缺口 |

**结论**：一次小规模投影扩展（4/5 + 顺手 2/3）即可解锁前端 P0-9/10、岗位柱图与倒计时。

---

## 六、其他

- **跨仓脆性测试**：`tests/work_packages/stage35_wechat/test_acceptance.py` 断言前端 wxml 文案（10 处读取，当前 1 失败）；共 4 个后端测试文件引用 miniprogram。建议改为断言后端契约或挪至 apps/wechat。
- **未覆盖范围（如实说明）**：AI 治理专项（申论批改/RAG 提示注入面、额度防刷压测、云函数降级）未做——建议作为下一小波（1 个代理）；密钥/PII 全量扫尾仅部分完成；workers 重试语义、auth 会话生命周期、external_ingestion 13 写端点全链、长尾低敏领域（community/feed/rank 等）仅表层扫描。

---

## 七、修复优先级

1. **立即**：v1 practice/review_session/docket 写端点 410 退役（刷 XP 面）；calibration 加时间窗；
2. **短期**：题库端点加鉴权；metrics 改常数时间比较；essay 工件锁后复查 replay；契约投影扩展（第五节 4/5/2/3）+ checkpoint 行为埋点字段；
3. **中期**：仓库层裸查过滤下沉；authorize_write 校验 expected_revision；命令表归档策略；stage35 测试迁移；
4. **待办**：AI 治理专项审计。

---

*执行：Claude（Fable 5）。科学层与契约核查为人工精读；两专项由审计代理完成、P0 与关键 P2 逐条人工复核。*
