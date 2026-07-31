# Findings 阶段复核（一，历史快照）

> **非权威历史产物。** 本文件冻结于真实 UI/UX 子审计合并前，仅记录当时 12 条草案的
> 独立质检意见，已被 `audit/findings.jsonl`、`audit/findings.md` 和
> `audit/summary.md` 的 15 条正式口径取代；不得用于当前数量、严重度或交付判定。

当时的审阅对象：

- `audit/findings.jsonl`
- `audit/findings.md`
- 子审计 A、B、D、E 的报告与所引证据
- 用户给定的 P0/P1/P2/P3 定义

当时的审阅边界：C 的真实 UI/UX 审计尚未完成，本报告不评价任何 UI/UX finding，也不以历史截图补足阶段渲染证据。本报告只做只读质检，不修改主 findings 或业务源码。

## 1. 总体结论

当前台账的结构完整性良好，但在作为最终正式台账前有四处需要主审计裁决：

1. `BACKEND-AUTHZ-001` 的“技术暴露”有充分证据，但“违反产品权限政策”尚未被证实；当前 `high + formal P1` 把政策未知当成已确认 Bug。若产品/安全 owner 确认答案库不得匿名读取，按用户字面严重度定义应评 P0，而不是 P1；确认前应移到 policy blocker/hypothesis。
2. `BACKEND-REPLAY-001` 的缺陷证据充分，但现有证据只证明发布后精确重放失败、内容 rollout 可被阻断，没有证明当前核心用户任务完全不可完成；按用户定义更符合 P2，而不是 P1。
3. `ROOT-IDEMP-001` 把 D-F002 和 E-004 合并得过宽。两者属于同一“逻辑操作身份”主题，但不是同一已证实公共根因、不是同一端到端链路，也需要不同修复。建议拆为客户端 unknown-outcome attempt 与后端 command/UoW 两条。
4. `ROOT-REQUEST-001` 本体在 report-center 已动态确认；其余 8 组页面只有静态同构证据。它们可以作为回归扫描范围，但不应作为“已确认受影响页面/用户影响”留在正式 finding，应继续放在 hypothesis/待回归范围。

除上述事项外，`ROOT-MIGRATION-001`、`ROOT-JOBS-001`、`FLOW-MOCK-001`、`ROOT-AUTH-001`、`ROOT-CONTRACT-001`、`ROOT-GATE-001`、`ROOT-TESTDATA-001`、`STATE-ANAN-001` 的独立性、严重度和证据边界总体可接受。`ROOT-JOBS-001` 与 `ROOT-GATE-001` 仍建议补直接源码证据位置，避免只依赖子报告的二次归纳。

结论口径：当前 12 条不是“全部无条件通过”；8 条可接受，2 条需严重度/政策调整，1 条建议拆分，1 条需收窄确认范围。

## 2. 结构与证据路径检查

机器检查结果：

- `findings.jsonl`：12 行、12 个唯一 ID；
- 严重度计数与 `findings.md` 一致：P0=1、P1=4、P2=7、P3=0；
- 12 条均包含用户要求的 17 个核心字段；
- 共 68 个 evidence 引用，文件路径全部存在；
- `findings.md` 的 12 个章节 ID 与 JSONL 索引一致；
- A-BLOCK-001、D-BLOCK-01、无生产环境、无真实 UI 等审计阻塞项没有被单独伪装成产品 finding；
- C 尚未完成，因此当前没有无渲染证据的视觉问题误入正式 findings。

“路径存在”不等于“结论成立”。下表继续检查证据是否支持 actual behavior、严重度以及去重边界。

## 3. 逐条质检矩阵

| ID                   | 去重判断                                                                                                                                           | 证据支持实际行为                                                                                                                                   | 严重度判断                                                                                           | hypothesis / blocker 洁净度                                                                                           | 质检结论                                                                       |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `ROOT-MIGRATION-001` | 合理。revision 漂移、历史迁移绑定可变 runtime、shadow fixture 都落在同一迁移/发布权威失效域                                                        | 强。定向测试复现 revision 33 `TypeError`；revision inventory 证实 `_68/_69/_70` 并存                                                               | P0 可接受：支持的 audited baseline 无法升级，符合“无法启动/发布”定义；已明确不声称生产当前宕机       | 洁净；生产 revision 未检查被写入 evidence boundary                                                                    | **通过**                                                                       |
| `BACKEND-AUTHZ-001`  | search/detail 合并合理                                                                                                                             | 技术暴露强：无 bearer、answer/explanation 返回均有 route、schema 和测试证据；但“该暴露违反既定政策”无证据，现有 contract test 反而明确接受匿名行为 | 当前 P1 不自洽：若是 authorization/security Bug，用户定义要求 P0；若是预期 public corpus，则不是 Bug | `policy-review-required` 仍被放入正式 high-confidence finding，存在政策假设误入                                       | **需裁决：先移出正式 findings；政策确认后按 P0 进入**                          |
| `BACKEND-REPLAY-001` | 与 command idempotency 项分开合理；这是 immutable trigger/writer 协议                                                                              | 强。PostgreSQL 定向测试直接证明 exact replay 在 `BEFORE INSERT` 被拒绝                                                                             | 建议 P2：证实的是安全重试/重复导入失败和 rollout 风险，未证明当前核心用户主任务错误或完全不可完成    | 洁净                                                                                                                  | **证据通过，建议 P1→P2**                                                       |
| `ROOT-IDEMP-001`     | 三个 D 页面彼此合并合理；把 E-004 再并入不合理                                                                                                     | 两组证据分别都强：D 证明同 body 换 key；E probe 证明同 verdict 重放产生 XP=20/两条事件。但没有一条 E2E 证明它们是同一链路                          | 当前 P1 主要由 E-004 的错误进度结果支撑；D 客户端 unknown-outcome 风险单独应再按影响裁级             | `pages/practice/index` 被明确称为 hypothesis，却仍出现在正式 finding 的 affected list；应只留在 `audit/hypotheses.md` | **建议拆分并清除 hypothesis 范围**                                             |
| `ROOT-JOBS-001`      | 三域合为 job registry/lease/deployment 治理根因可以接受                                                                                            | data-rights stale claim 有动态 probe；cause-agent 标准 topology 缺 consumer 有直接配置证据。iLink 永久不可见目前主要由 E 报告转述源码推导          | P1 可接受：隐私任务或核心错因分析可永久不终止，属于主要功能无法完成                                  | 未声称生产已有卡单，边界正确；iLink 部分最好补直接 source evidence 或降为静态待验证                                   | **通过，建议补直接 iLink/cause worker 证据路径**                               |
| `FLOW-MOCK-001`      | 不应并入 idempotency；服务端终态已经取得，本地状态收敛是独立根因                                                                                   | 强。隔离测试复现 submitted 服务端状态与本地 ready/unlocked                                                                                         | P2 正确：当前页错误但退出重进可恢复，符合“有替代路径”                                                | 洁净                                                                                                                  | **通过**                                                                       |
| `ROOT-AUTH-001`      | B 两个页面症状与 D 旧 token 401 竞态合并合理，均源于无 owner 的全局 session-expiry 协议                                                            | 强。三个动态行为均有审计测试，源码证明 handler 无参数且无条件清 session                                                                            | P2 可接受：影响核心链路，但手工退出/重新登录/重进是替代路径；未证实永久不可恢复                      | 其他认证页只作为潜在范围，evidence boundary 已说明                                                                    | **通过**                                                                       |
| `ROOT-CONTRACT-001`  | stale generated types、未接线 check、手写合同和 OpenAPI 无 auth 语义可归入“OpenAPI 不是真实单一权威”；但修复时需区分 freshness 与 schema semantics | 强。diff、missing script、OpenAPI 内容、246-route auth inventory 均可复核；明确未声称当前 diff 已造成页面崩溃                                      | P2 正确：是质量/合同门禁失效，当前未证明核心运行时故障                                               | 洁净；Hub Bot 具体运行时漂移仍在 hypotheses                                                                           | **通过；标题应覆盖 freshness + semantic incompleteness，避免只写 stale types** |
| `ROOT-GATE-001`      | profile/positions 两个报错示例合并为分包后 route-semantics 根因合理                                                                                | 强。命令结果证明三个 validator 与 navigation 使用不同口径，package inventory 证明未接线                                                            | P2 正确：门禁失效而非已确认 22 个线上页面故障                                                        | 洁净；真实逐页打开阻塞没有进入 finding                                                                                | **通过；建议 evidence 增列三个 validator 的直接源码位置**                      |
| `ROOT-REQUEST-001`   | report-center 的 page/request ownership 根因独立；不能把未动态复现页面都算确认影响                                                                 | report-center actual 强；ApiClient 丢弃 RequestTask 与页面无 generation 有源码证据。其他页面仅 `state-risk-matrix.csv` 静态类比                    | P2 正确：会显示旧结果，有刷新/重进替代路径                                                           | 其余页面属于代码推测，应从 confirmed affected/user-impact 移到 hypotheses 或标为“回归扫描候选”                        | **本体通过，需收窄正式范围到 report-center**                                   |
| `ROOT-TESTDATA-001`  | 52 个失败合为 frozen-release fixture contract 漂移合理；不应拆成 52 个产品 Bug                                                                     | 强。failure cluster 为 52 个 tracked node，代表性定向测试和 baseline JUnit 支持它们在预期断言前退出                                                | P2 正确：是测试有效性/稳定性缺口，不是 52 个已证实核心线上错误                                       | 洁净                                                                                                                  | **通过**                                                                       |
| `STATE-ANAN-001`     | 当前只报岸岸，不扩报所有聚合页，去重和范围合理                                                                                                     | 强。动态测试直接复现 profile 成功 + today 503 被渲染为 ready/无目标/0 任务；源码 `.catch(() => null)` 支持                                         | P2 正确：明确错误事实和误导操作，但可重试/换入口                                                     | 洁净；不依赖真实 UI 审美判断                                                                                          | **通过**                                                                       |

## 4. E-004 与 ROOT-IDEMP 合并专项结论

### 4.1 为什么不应保持当前合并

D-F002 与 E-004 的共同主题是“没有稳定的 logical command identity”，但证据表明它们是两个不同缺陷：

| 维度       | D-F002                                             | E-004                                                            |
| ---------- | -------------------------------------------------- | ---------------------------------------------------------------- |
| 位置       | 微信页面操作 attempt                               | FastAPI legacy review/practice + rank repositories               |
| 已确认端点 | Companion、capacity、notification preference       | review verdict；practice answer 由源码扩展                       |
| 失败机制   | 首次结果未知后，同输入生成新 `Idempotency-Key`     | endpoint 没有统一 immutable command；多个 repository 独立 commit |
| 服务端能力 | 三个已确认服务端路径本来支持 stable-key replay/CAS | 同 body 重放仍重复 XP；稳定前端 key 本身不足以修复               |
| 动态证据   | wx.request stub 下 same body/different key         | 临时 PostgreSQL 下 1 verdict、XP=20、2 xp_gain                   |
| 修复 owner | 小程序 operation-attempt/store + API client 状态   | 后端 command ledger + use-case UoW/outbox                        |
| 共同 E2E   | 未执行                                             | 未执行                                                           |

当前 finding 的“根因”实际由两个并列句组成：客户端丢 logical attempt，以及后端无 command/UoW。这不是一个公共组件导致多个页面，而是两个架构层各自失效。把它们合成一条会产生三个问题：

1. 一个修复可能只修客户端，却让台账看起来整条已完成；
2. E-004 的已确认错误 XP 结果为 D-F002 的尚未 E2E 计额风险抬高严重度；
3. 回归测试和回滚范围跨无关端点，不能形成单一可验收 release unit。

### 4.2 建议拆法

- `ROOT-IDEMP-CLIENT-001`：仅覆盖 D-F002 的岸岸、容量、提醒偏好；把 `pages/practice/index` 留在 `audit/hypotheses.md`。严重度需主审计按“可能重复 AI command/已保存却冲突，是否有明确恢复路径”在 P1/P2 间裁决；在没有 response-loss 跨服务 E2E 时，P2 更审慎。
- `BACKEND-COMMAND-001`：覆盖 E-004 的 review verdict 重放、XP/rank event 和多事务边界。P1 合理，因为已经动态得到错误业务结果；practice answer 只有源码证据的部分应明确为同根回归范围，不写成已动态复现。

两条可以在 `summary.md` 归到同一个“端到端逻辑操作身份缺失”系统性根因，但不应共用一个关闭状态。

## 5. 严重度复核

按用户定义逐字执行后的建议：

- `ROOT-MIGRATION-001`：保留 P0。支持的历史升级路径实际停止，符合“无法启动/发布”。
- `BACKEND-AUTHZ-001`：不能稳定保留 P1。先确认是否违反政策；确认是 security/authorization issue 后应为 P0，未确认则不进入正式严重度计数。
- `BACKEND-REPLAY-001`：建议 P2。证据未达到“核心任务错误/无法完成主要任务”。
- E-004 拆出的后端 command finding：P1。重复 XP/rank event 是已确认错误结果。
- D-F002 拆出的客户端 finding：在无跨服务 response-loss E2E、且容量/偏好可重新读取的现有证据下建议 P2；若能证明 AI 额度/provider command 确实重复且无恢复路径，再升 P1。
- `ROOT-JOBS-001`：保留 P1。
- 其余 7 条 P2：与“有替代路径，但明显影响效率、理解或稳定性”一致。

由于 `BACKEND-AUTHZ-001` 仍有政策分支，且 `ROOT-IDEMP-001` 建议拆分，本质检不建议直接给出一个新的唯一 P0/P1/P2 总数；主台账完成上述裁决后再重新计算。

## 6. hypothesis 与审计阻塞项检查

正确留在正式 findings 之外的项目包括：

- A-BLOCK-001：DevTools page-ready/automator 全局阻塞；
- D-BLOCK-01：develop/trial/release 没有安全非生产 API origin；
- 真实生产 revision、worker 队列、gateway/WAF 状态未检查；
- 私人知识详情缺参、9 个潜在孤立入口；
- Hub Bot 手写合同运行时影响、无 runtime response validation；
- 其他 fresh-key writers、账号切换迟到响应；
- 性能、限流、metrics timing、其他 repository commit 路径；
- C 尚未完成的真实 UI/UX。

需要从正式 finding 退回或收窄的内容：

1. `BACKEND-AUTHZ-001` 的“违反权限政策”判断，在 policy owner 确认前属于 blocker/hypothesis；技术暴露事实可以保留在审计证据中。
2. `ROOT-IDEMP-001` affected list 中的 `pages/practice/index` 已被 D 明确标为 hypothesis，应只在 `audit/hypotheses.md` 出现。
3. `ROOT-REQUEST-001` 中 ability、me、membership、notification-settings、privacy、private-knowledge、profile/archive 只有静态同构证据，应标为待回归候选，不应计作已确认影响页。

## 7. 最终 QA 判定

在以下条件完成前，不建议把当前 `findings.jsonl` 标记为最终冻结：

1. 对 `BACKEND-AUTHZ-001` 做产品/安全政策确认，并据此移除或按 P0 重录；
2. 将 `BACKEND-REPLAY-001` 调整为 P2，或补充能证明核心主要任务不可完成的证据后保留 P1；
3. 拆分 D-F002 与 E-004，并分别定义关闭标准；
4. 从 `ROOT-IDEMP-001`、`ROOT-REQUEST-001` 的 confirmed scope 移除代码推测页；
5. 为 `ROOT-JOBS-001` 的 iLink 部分和 `ROOT-GATE-001` 的 validator semantics 补直接源码 evidence 引用。

完成这五项后，其余条目可以进入最终汇总和 repair wave 规划。
