# 2026-08-04 结构健康度评估报告(重构决策用)

> 结论先行:**不做整体重写,做"结构性止血 + 定点治理"**。代码不是"设计全错",而是"巨型文件 + 职责重复 + 半吊子解耦"。bug 多的根因是结构债务,不是"门禁太多"——门禁(verify.sh / 架构边界 / 契约生成)反而是唯一在兜底的机制,第一刀绝不能动它。

---

## 0. 一句话判断

| 维度 | 现状 | 判断 |
|---|---|---|
| 规模 | 后端 493 文件 / 14.6 万行;前端 265 TS 文件 / 9.2 万行 | 重写不划算,以月为单位失去线上能力 |
| 近期 commit | 264 个里 fix 33 / feat 35,**几乎 1:1** | 一半精力在修自己刚写的 bug → 结构债务典型症状 |
| 门禁 | ruff + pytest + alembic 单头 + 架构边界 + openapi 契约 | **是安全网,不是 bug 来源**;codex 每次"decouple/align/recover"式 fix 才是堆叠破坏结构的机制 |
| 真正问题 | god 文件、双重建模、半吊子解耦(decouple 靠调用方传空参数维持)、死代码 | 全部可定点爆破,不需要夷平 |

---

## 1. 后端:四大 god 域评估

### 1.1 planning(repository.py 2888 行 / service.py 1574 行)

- 名为 19 个类,实为 **1 个类 + 6 个 dataclass + 大量模块级函数**,38 个方法分属 7 个互不相干的责任簇,簇间无共享状态。
- **真正的 god 藏在两处**:`execution_evidence_exists`(450 行,内联了 essay 证据契约引擎,`repository.py:2093-2548`)和 `essay_snapshot`(约 500 行 SQL)。
- **核心架构问题(确认)**:同一"行测完成"概念,`planning/xingce_completion.py` 与 `xingce_learning/real_attempt/service.py` 双重建模。写入点有三个(submit 的执行事实、decide_cause 的历史补齐、settle 的证据推进),靠 `completion_payload` 弱一致性握手,**没有单一状态机**。`decide_cause` 里有段 30 行历史兼容分支(`service.py:1280-1321`),注释自证"2026-08-04 之前的会话把计划完成错误地延后到错因决策"——**这是"答案和原因分析对不上"类 bug 的温床,且当前分支名 codex/commercial-selection-repair 正在这里打补丁**。
- 危险跨域:planning ↔ xingce_learning **域级双向依赖**(全项目唯一);`ilink_channel` 跨域 import 私有函数 `_goal_gap`。

### 1.2 companion(service.py 1873 行)

- **解耦方向对,但切到一半**。回复与动作已分离(服务端传空 `requests=()` 不信任模型动作),但 provider 侧整条 `requestedActions`→`CompanionActionRequest`→execute 死链路没删——**每次回答都在解析然后丢弃**。
- god 不是 `CompanionService` 类,是**模块级函数区**(585 行,四职责混合):上下文序列化 + 检索候选拼装 + 5 个引用摘要 + 高影响判定。
- **半吊子证据**:`_build_persona_prompt` 开头 `del context` 声明弃用入参,但 `ask:232` 仍传——函数想独立、调用方没跟上。
- 逆向依赖:`ai/provider.py` → `companion/agent`(provider 不该知道 companion 的动作模型)。

### 1.3 xingce_learning/real_attempt/service.py(2351 行)

- 职责边界比文件长度清晰(会话生命周期 / 复盘状态机 / 争议上报 / 投影拼装),是"一个聚合的命令门面"。
- 跨域横切塞满核心路径:submit 和 decide_cause 内联 6 处 planning 调用。**当前无重复写库风险**(每张表单写者 + 状态机守卫),历史 bug 已修,但留下脆弱兼容补丁。
- 两个函数全 backend 零调用(影子包装 `_safe_rich_html` / `_public_question_asset_url`)。

### 1.4 commerce(三个巨型文件)

- **内部边界比想象中好**:协议胶水层已抽对(`wechat_pay.py` / `app_store.py` 是正确样板)。问题在业务规则写在 SQL 仓库里(stage8_repository 的退款率 0.20、45/30/15 天窗口、周周期)。
- **全项目唯一一张真双写表**:`commerce.community_reward_awards`(community_rewards insert + commerce update 状态)。

### 1.5 essay_intelligence / community / data_rights

- essay_intelligence:生产者/消费者解耦良好,**最健康的一对**,不要拆坏。问题是指纹工具全域复制 5 份、slot 相位表硬编码 3 处、两个长方法。
- community:method_cards 是 service 上帝、rewards 是 repository 上帝(方向相反的薄厚倒挂)。有零风险死代码可删(约 400 行)。
- data_rights:47 条 erase SQL 是手工清单,任何域加个人字段都要来补一条——全域最脆弱维护点。

---

## 2. 前端:小程序结构评估

- 分层比常见小程序好:`api/`(统一 client)、`features/`、`platform/` 四层已存在;绕过 `api/client.ts` 的裸 wx.request 只有 4 处且都合理。**网络层健康**。
- **核心数据重复拉取**:"获取今日计划"被 13 个页面各自 `new PlanningApi(client)` 直接调用;`today-controller.ts` 是一个高质量的共享 controller(注入 + 竞态 + 401 重定向),**但只有 today 页用它**,day-adjustment 宁可自己在页面内重写 300 行幂等逻辑。
- **平台判断散落(已确认 bug 温床)**:`api/commerce.ts:100-104` 虚拟支付判断平台,**不识别平板,平板会被当成 iOS**——设备平台和客户端能力混为一谈。
- **考点枚举三份拷贝**:ability / day-adjustment 各维护"言语理解/数量关系/判断推理/资料分析/常识判断/政治理论",后端 schema 变一处漏两处。
- god 页面:essay-task 3033 行、practice 2401 行、mock-exam 2094 行,但**逻辑已抽到 features/,页面只是编排者**(好的方向)。
- 登录态三套并存:requireAuthenticatedClient(已收敛) / 裸拼 token / 直接 reLaunch 跳登录(30 处,绕过返回栈)。
- 死代码:`features/positions/contracts.ts` 手工类型零 import。

---

## 3. 硬编码 / 死代码 / 双写汇总

- **硬编码**:同一数值在 engine.py / candidates.py / service.py / repository.py 四处以字面量重复(120 分钟、25 分钟、60 天);契约版本字符串(essay-grounding-v2 等)内联在 repository;模型前缀 `deepseek-v4-` 硬编码判定 adapter;1440 分钟上限出现 2 次。
- **死代码**:`plan_task_late_evidence_versions` 表写了没人读;community_rewards 的 consume/reverse 死链路;CandidateProviderUnavailable 幽灵类;service.py 兼容 shim `_requested_plan_minutes`(仅测试引用);schemas 里 3 个死枚举值。
- **双写**:仅 community_reward_awards 一张真双写表;其余"双写"是两套状态机通过 work_item_key 约定耦合(非表级)。

---

## 4. 推荐路线图(按"风险 × 收益"排序)

### 阶段 A — 零/低风险止血(本周可完成)

1. **删死代码**(~400+ 行,零风险):community 幽灵类、consume/reverse 死链路、real_attempt 影子包装、前端 positions/contracts.ts。
2. **抽前端考点枚举 + 收敛支付平台判断**(低风险):消灭"平板=iOS"写死分支,补 `canIUse` 能力检测;考点三份拷贝合并到 `domain/`。
3. **companion 删 provider 死链路**(低中风险):删 `requestedActions`/`next_actions` 解析,让"replies 与 actions 解耦"从调用约定变成代码事实——这是 commit ff93a0e48 该做而没做完的那刀。同步删 `_build_persona_prompt` 的 `del context`。
4. **real_attempt 抽 `events.py` 证据拼装层**(低风险,纯函数搬移)。

### 阶段 B — 中风险结构治理(下一步)

5. **收敛 planning↔xingce 域边界(架构负债最重处)**:把 decide_cause 历史兼容块提为 `_settle_historical_completion` 独立函数,不在主决策路径;定义"完成事实唯一属主 = planning 的 complete_xingce_plan_task";`XingceCompletionReceipt` 迁到共享契约层消掉类型反向依赖。**必须先写针对历史会话的迁移测试**。
6. **companion 抽 `context_bundle.py`**(职责 C,纯搬移,service 瘦到 ~1200 行)。
7. **essay_intelligence 抽 `EssayReviewResultPersister`**(消灭 3 处手工拼装重复)。
8. **wechat_virtual_payment 协议解码收口**(XPAY 状态数字 `{2,3,4...}` 上移到协议类)。

### 阶段 C — 高成本、按需做

9. planning repository 按簇拆文件、essay_snapshot 抽仓库、execution_evidence_exists 抽 evidence_gate。
10. data_rights erase/export 数据化(唯一不可逆路径,必须测试护航)。
11. day-adjustment 复用 today-controller 消掉 300 行页面内重复幂等。
12. essay-task 专项大重构(前端最高成本项,单独立项)。

### 明确不建议做的

- ❌ 整体重写 / 夷平重建(规模与数据模型成本决定了不划算)。
- ❌ 合并 xingce_attempt_records 与 task_execution_versions(语义不同,合并才是错)。
- ❌ 动 verify.sh / .product-governance / 架构边界门禁(唯一安全网)。
- ❌ 统一全项目 ORM(每个域自成一套,现在统一是大动干戈)。

---

## 5. "第一刀"推荐

**后端:收敛 planning↔xingce_learning 的完成状态机**(阶段 B 第 5 项的止血部分)。理由:这是已确认的 bug 温床,当前分支名 codex/commercial-selection-repair 正在这里反复打补丁;修它比拆文件更止损,且迁移测试可护航。

**前端:`api/commerce.ts` 平台判断 + 考点枚举**(阶段 A 第 2 项)。理由:低风险、直接消灭两个已知 bug 源,顺带让"门禁烦"的感知降下来——先把结构修到 bug 不再 1:1 反噬,再谈治理减负。

---

## 6. 工作原则(写给未来的重构)

1. **机械优先**:每刀都先做纯文件移动 + import 调整,零行为变化,跑 `scripts/verify_backend.sh` / `verify_wechat.sh` 全绿再谈重构逻辑。
2. **SQL 逐字迁移**:repository 里的裸 SQL 有大量重复的 current-head 版本链 CTE,拆时**不顺手重构 SQL**。
3. **测试 import 要同步**:companion 已被 9 个测试文件密集引用(含直接 import 私有函数),拆文件必须同步改测试,否则 CI 立刻红。
4. **先写历史数据迁移测试**:凡是动完成状态机 / 删除路径,先覆盖历史会话。
5. **不新增裸跨域查**:新代码不再新增跨 schema 直查;存量各自成体系,不统一。
