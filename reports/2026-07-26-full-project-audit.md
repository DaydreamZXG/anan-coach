# 岸岸 · 全项目审计（前端 / 后端 / 交付门禁 / 运维）

**日期**：2026-07-26　**分支**：`codex/ui-ux-remediation`（工作区有前端在改动，本审计只读）
**范围**：`apps/wechat`（58 页 / 101 WXSS / 86 WXML / 478 测试）、`backend`（50 域 / 230 路径 / 240 操作 / 63 迁移 / 767 测试）、`scripts` + `.github` 门禁、`ops` 部署与运维、仓库卫生
**方法**：源码级只读扫描 + 实际执行全部门禁脚本与测试套件 + 与《产品功能边界》(2026-07-18) 逐条核对 + 复核 2026-07-24/25 三份既有审计的整改状态

---

## 一、结论

**代码质量本身不差；真正的风险在「门禁没连上」和「上一轮结论没落地」。**

- 前端视觉治理这一个月是有效的：硬编码颜色从 1,713 处降到 **67 处**（且 65 处在 token 层内、2 处有豁免注释）。
- 但**四个已经写好的校验脚本没有接进 `npm run check`，整个前端测试套件也没接进 CI**。此刻工作区就有 3 个测试文件失败，而 CI 会全绿。
- 2026-07-25 后端审计的「立即」与「短期」修复项，**一项都没有执行**：题库仍可匿名爬取、刷 XP 端点仍在线、metrics token 仍是明文比较、calibration 仍是全历史统计。
- 视觉 token 只管住了颜色。**字号、圆角、阴影仍是字面值主导**，而且**近一半正文字号小于 12px**——对一个长时间阅读的备考产品，这是产品级问题而非样式洁癖。

| 维度 | 评级 | 一句话 |
|---|---|---|
| 后端正确性与鉴权 | ★★★★☆ | 无 SQL 注入、无 TODO、幂等范式成体系；遗留 v1 端点是唯一实锤面 |
| 后端可观测性 | ★★☆☆☆ | 全仓 22 条日志且全在 worker，API 请求零日志 |
| 前端工程质量 | ★★★★☆ | 控制器分层、in-flight 去重、契约 230/230 同步 |
| 前端视觉一致性 | ★★★☆☆ | 颜色已收口，字号/圆角/阴影未收口 |
| 可读性与无障碍 | ★★☆☆☆ | 459 处 <12px 文字，116 个 image 零 aria-label |
| 交付门禁完整性 | ★★☆☆☆ | 门禁脚本写好了但没挂上，测试不在 CI |
| 运维与发布 | ★★★☆☆ | systemd/备份/回滚齐全，缺日志、指标与多进程 |

---

## 二、P0 · 必须先处理

### P0-1　题库可匿名全量爬取（商业资产裸奔）

`backend/app/domains/knowledge/router.py:23,46` — 两个端点**完全没有鉴权依赖**：

```
GET /api/v1/knowledge/questions/search   （limit 上限 100，可按模块/题型/年份/省份枚举）
GET /api/v1/knowledge/questions/{question_id}
```

返回 `QuestionDetail`：题干、全部选项、`QuestionExplanation.preferredAnalysis`（已发布 AI 解析）、`QuestionTag`、`KnowledgeChunk.content`、`QuestionOccurrence.sourcePath`（原始来源路径）。

同类问题：`app/domains/essay/router.py:45` `GET /api/v1/essay/tasks/{task_id}` 也无鉴权（标了 deprecated 但仍在线）。

直接违反产品边界 §18「防止批量爬题」与 §17.2「内容权利」。**且小程序对这两个端点零调用**——只存在于 `types.gen.ts`，删掉或加鉴权都不影响现有前端。

> 2026-07-25 后端审计已列为 P2「短期」，一天后仍未处理。考虑到生产域名 `kg.xingguoxian.cn` 已可访问，建议升级为 P0。

### P0-2　478 条前端测试从未在 CI 执行

`apps/wechat/package.json` 的 `check` = `format:check + typecheck + eslint + tokens + interactions + controls + navigation + design`。
**`npm test`（vitest）不在其中**；`scripts/verify_wechat.sh` 只跑 `npm run check`；`.github/workflows/ci.yml` 的 wechat job 只跑 `verify_wechat.sh`。

全仓检索 `vitest` / `npm test` 在 `scripts/`、`.github/`、`apps/wechat/scripts/` 中的引用数：**0**。

实测当前工作区：

```
Test Files  3 failed | 89 passed (92)
     Tests  3 failed | 475 passed (478)
```

失败的正是 `task-shell.test.ts`、`stage2-material-reader.test.ts`、`stage2-content-renderer.test.ts`——即当前正在改动的运行时。这不是指责改动本身，而是证明：**这类回归今天可以直接进 main 且 CI 全绿。**

### P0-3　四个门禁脚本写好了但没有接上

| 脚本 | 现在跑的结果 | 是否在 `check` 中 |
|---|---|---|
| `validate-structure.mjs` | **exit 1，185 条报错**（如 `weekly-plan/index.wxss -> button.day-trigger` 违反「组件 WXSS 只用类选择器」） | 否 |
| `validate-interaction-coverage.mjs` | **exit 1**（`pages/private-knowledge/index` WXML 缺 `.search-button`） | 否 |
| `generate-api.mjs --check` | 通过（契约同步） | 否 |
| `validate-anan-assets.mjs` / `validate-visual-coverage.mjs` | 通过 | 否 |

`scripts/check_architecture.py` 对生成契约只校验**首行 ownership 注释**，不校验内容是否与后端 OpenAPI 一致——而 `--check` 模式本来就存在，接上即可。

### P0-4　上一轮后端「立即修复」项 0% 执行

复核 `reports/2026-07-25-backend-audit.md` 第七节：

| 该报告要求 | 现状（2026-07-26 实测） |
|---|---|
| v1 `POST /practice/tasks/{id}/answers` 退役为 410 | 仍是 `@router.post(...)` 正常在线，仍无幂等 |
| `review-sessions/.../verdict` XP 重复累加 | 端点原样在线 |
| `docket /open-court` | 原样在线 |
| `/internal/metrics` 改 `hmac.compare_digest` | `operations/router.py:229` 仍是 `authorization != expected` 明文比较 |
| calibration 加时间窗 | `calibration/repository.py:39,44,104` 仍是全历史 `count(*)`，无任何 `interval` |

这一周的工程力全部投在 UI 上，后端整改队列完全静止。

### P0-5　服务端没有请求日志，Request ID 链路是断的

`backend/app/` 全量：`logger.info/warning/error/exception` 共 **22 处，全部在 `app/workers/` 与一个 worker 文件里**；API 请求路径**零日志**。同时没有 Prometheus / OpenTelemetry / Sentry 任何接入（唯一的 `/internal/metrics` 是手写 SQL count 端点）。

后果直接顶撞产品边界 §16：错误页要求用户复制 Request ID 供定位，`app/main.py:59-62` 也确实生成并回写 `X-Request-ID`——**但服务端从不把这个 ID 写进任何日志**。拿到 Request ID 之后无处可查。

§20「统计记录内部 learner ID 关联的事件、耗时、成功失败和闭环结果」目前也无实现载体。

---

## 三、P1 · 影响真实体验与可维护性

### P1-1　近一半正文字号小于 12px

运行时 WXSS 字面 `font-size` 共 962 处，其中 **459 处 < 24rpx（<12px）**：

| 字号 | 出现次数 | 等效 px |
|---:|---:|---:|
| 22rpx | 130 | 11 |
| 21rpx | 99 | 10.5 |
| 24rpx | 101 | 12 |
| 20rpx | 90 | 10 |
| 23rpx | 68 | 11.5 |
| 19rpx | 38 | 9.5 |
| 18rpx | 30 | 9 |

集中页面：`essay-task`(43) `features/positions/page-shell`(40) `reflection`(33) `capture-import`(23) `practice`(21) `me`(19)。

这是备考产品——用户每天在这里读长材料、看解析、比对岗位表。9–11px 不是「精致」，是**逼用户眯眼**。建议定一条硬底线（正文 ≥28rpx、辅助信息 ≥24rpx、说明性最小 22rpx），并写进 token 校验。

### P1-2　Token 体系只管住了颜色

`validate-tokens.mjs` 只扫十六进制色值。其余维度：

| 属性 | 声明总数 | 用 `var()` | 字面值种类 |
|---|---:|---:|---:|
| `font-size` | 1,165 | 136 (12%) | **52** |
| `border-radius` | 682 | 131 (19%) | **66** |
| `box-shadow` | 204 | 66 (32%) | **91** |

`design-system/tokens/index.wxss` 里已经定义了完整的 `--figma-font-*` / `--radius-*` / `--shadow-*` 阶梯，页面绕过它们。颜色能收口说明这套方法有效——把同样的门禁扩展到这三个属性即可。

### P1-3　平板旋转会错位

`app.json` 同时开着 `"resizable": true` 与 `"pageOrientation": "auto"`，但：

- 全仓 `onWindowResize` / `wx.onWindowResize` 引用数：**0**
- `components/safe-top/index.ts` 只在 `lifetimes.attached` 量一次 `holderHeight`
- `app.ts:37-40` 的 `statusBarHeight` / `capsuleInset` 只在 `onLaunch` 量一次

`design-system/responsive/index.wxss` 的 `@media (min-width: 768px)` 会跟着变，但 **JS 量出来的顶部占位不会**。iPad 旋转后顶部安全区与媒体查询布局不一致。违反 §19「横竖屏保持位置」。

### P1-4　无障碍基本空白

- `<image>` 标签 116 个，带 `aria-label` 的：**0**
- 全部 86 个 WXML 中 `aria-label` 共 50 处、`aria-role` **0** 处、`aria-hidden` 55 处
- `min-height` 声明 202 条，其中 **95 条 < 88rpx（44px）**，最小到 38rpx

### P1-5　主包逼近 2MB 硬上限

实测字节（非 du 块估算）：

| | 大小 |
|---|---:|
| 主包源码合计 | **3,577 KB**（TS 2,287 / WXSS 428 / WXML 421 / 图 407 / JSON 33） |
| 其中 `api/generated/types.gen.ts` | 712 KB（纯类型，编译产物 ≈ 0） |
| 6 个分包合计 | 2,099 KB，**其中 2,092 KB 全是岸岸 webp 素材** |

也就是说：**58 个业务页面 100% 在主包，分包只装了吉祥物图**。minify 后主包大概率落在 1.6–1.9MB 区间，紧贴 2MB 上限。`lazyCodeLoading: "requiredComponents"` 已开是好事，但下一批页面会直接顶到墙。建议把申论工作台、社区方法卡、私人资料库、报告中心几组低频页拆进真正的业务分包。

### P1-6　后端事务边界不统一

`app/` 内 `.commit()` **319 处**、`.rollback()` **200 处**、显式事务块（`session.begin`）**2 处**。

两种范式并存：
- **新域**（`xingce_learning/real_attempt`）：repository 零 commit，router 单点 `session.commit()`（`router.py:186,368`）——正确的 unit-of-work。
- **老域**（`commerce`、`community_method_cards`、`memory`、`mock_exam`、`auth` 等）：repository 方法各自 commit。service 里连续调三个 repo 方法 = 三个独立事务，中途失败留下半写状态。

`auth/repository.py:434` 就是一例：`get_user_by_access_token` 在**每个鉴权请求**里 `update last_seen_at` 并单独 commit——每次 API 调用多一次写事务。

### P1-7　领域循环依赖 25 组，架构守卫查不到

`scripts/check_architecture.py` 只禁止 `platform` / `core` / `db` 三层向上导入，**对 `app/domains/` 之间零约束**。实测 127 条跨域依赖边、382 条 import，存在 25 组环：

```
auth ↔ onboarding          planning ↔ xingce_learning
mock_exam ↔ planning       position_decision ↔ qualification
daily_opening ↔ planning   auth ↔ memory ↔ onboarding
```

### P1-8　提示注入防御只有 8 条正则

`app/domains/ai/governance/routing.py:43-56` 的 `_INJECTION_PATTERNS` 是 8 条中英文正则黑名单，应用在两处：用户提问路由（`routing.py:130`）与不可信 RAG 候选（`retrieval.py:128`）。

而私人资料库按设计**接收任意用户上传的讲义、笔记、PDF**并进入检索增强。黑名单绕过成本极低（换措辞、英文、编码、翻译），产品边界 §18 要求的是「文档中的指令只能作为资料原文，不能控制系统提示词」——那是**结构性隔离**（明确定界 + 系统提示加固 + 检索内容不得触发工具调用），不是关键词过滤。

好消息是 §11 已要求写档案/改计划的操作卡必须用户确认，所以影响上限是「诱导用户点确认」而非直接改数据。但这条应作为下一轮 AI 治理专项的起点（07-25 审计也把它列为未覆盖项）。

---

## 四、P2 · 值得排进队列

| # | 问题 | 证据 |
|---|---|---|
| 1 | `get_settings()` **无缓存**，每次重建 Settings（83 次 `os.getenv`），54 处调用点，多数在请求依赖里 | `core/config.py:196` 无 `@lru_cache`（对比 `db/session.py` 的 `get_engine` 有） |
| 2 | 连接池与线程池不匹配：**所有 299 个端点都是 `sync def`**（走 40 线程的 threadpool），而 `create_engine` 用默认池（5 + 10 overflow），systemd 又没有 `--workers` | `db/session.py:24`、`ops/systemd/gongkao-dazi-api.service` |
| 3 | API client 无重试、无网络类型区分；`fail` 分支丢弃 `errMsg` 只留「网络请求失败。」；全接口统一 15s 超时（含 AI 批改） | `miniprogram/api/client.ts:119-121,87` |
| 4 | `wx.onNetworkStatusChange` 只用来重试**字体加载**，不重拉业务数据 | `app.ts:60-64`；顶撞 §19「恢复联网后按版本同步」 |
| 5 | 无 `App.onError` / `onUnhandledRejection` 上报；`getRealtimeLogManager` 只记录 product-font | `app.ts:42-58`；§21 要求发布版本无运行时错误，但目前没有发现手段 |
| 6 | 文档漂移：README 写「今日、岗位、我的**三栏**主路径 and **53** registered pages」，实际是 今日/能力/岸岸/我的**四 tab** + **58** 页 | `README.md:8` vs `app.json` |
| 7 | **岗位没有 tab 入口**——主链第二步「定岗」只能从 我的 / 岸岸 / 资格页 进入 | `pages/me/index.ts:364`、`pages/anan/index.ts:251` |
| 8 | 信息架构冗余：5 个报告页（`report-center` / `report-overview` / `global-report` / `external-report` / `mock-report`）+ 4 个能力类页（`ability` / `ability-module` / `xingce-mastery` / `error-pattern`） | `app.json` pages |
| 9 | 运行时列探测 `_extra_exam_column_available()` 说明代码与迁移存在漂移兜底 | `positions/repository.py:123,127,287` |
| 10 | 本地 `.git` **11 GB**：4.31 GiB pack + **6.14 GiB `tmp_pack_*` 垃圾**（中断的 gc）。GB 级 `exports/` 快照被 `refs/codex/turn-diffs/checkpoints/*` 钉住 | `git count-objects -vH`。**已核实这些巨型 blob 只存在于本地 codex checkpoint ref，未推到 origin**；`git gc --prune=now` + 清理 checkpoint ref 可回收约 10GB |
| 11 | `tmp-screenshots/` 未进 `.gitignore`（当前 252K，未被 track，但迟早误提交） | `git check-ignore` 无输出 |
| 12 | 18MB 的 `MapleMono-CN-Regular.ttf` 在仓库里存了两份（`third_party/` 和 `backend/app/internal_services/assets/`） | `git ls-files` 大小排序 |

---

## 五、做得好、不要动的部分

- **契约同步是真的**：`generate-api.mjs --check` 通过；后端 230 条路径与 `types.gen.ts` 的 230 条 URL **完全一致，双向零差异**。
- **无 SQL 注入面**：22 处 f-string SQL 全部只插入内部常量（表名、schema、固定列表达式），用户输入一律走 bind 参数。
- **零 TODO / FIXME / HACK / print**，整个 `backend/app/` 447 个文件。
- **秘钥卫生达标**：`.gitignore` 覆盖 `.env*` / `*.pem` / `*.key` / `*AccessKey*` / `ops/secrets/`；`git ls-files` 只命中 `.env.example` 系列；内部服务用 `hmac.compare_digest`（`internal_services/router.py:67`）。
- **颜色治理是这一个月最成功的工程动作**：1,713 → 67，且 65 处在 token 层内。这套「定义 token + 写校验 + 接进 check」的方法论直接复制到字号/圆角/阴影即可。
- **状态视图覆盖 52/58 页**，未覆盖且有网络调用的只有 `retake` / `study-preference` / `anan` / `login` 四页。
- **前端控制器分层扎实**：`today-controller.ts` 有 in-flight 去重（`loadInFlight` + `loadInFlightSessionKey`）与 generation 守卫，`onLoad` + `onShow` 双触发不会产生重复请求。
- 测试体量真实：后端 767 条、前端 478 条，`tests/` 51k 行。

---

## 六、建议的执行顺序

**第 1 天（低风险、堵口子）**

1. 题库两个端点加 `get_current_user`（或直接删除——前端零调用）；`essay/tasks/{id}` 同处理。
2. `/internal/metrics` 换 `hmac.compare_digest`。
3. `npm run check` 追加 `npm test`、`node scripts/validate-structure.mjs`、`node scripts/validate-interaction-coverage.mjs`、`node scripts/generate-api.mjs --check`。**先修当前的 3 个测试失败 + 185 条 structure 报错，再挂门禁**，否则 CI 会卡死所有人。
4. `git gc --prune=now` 清 6GB 垃圾包；`tmp-screenshots/` 进 `.gitignore`。

**第 1 周**

5. v1 刷 XP 端点（practice answers / review verdict / docket open-court）按 essay/memory 先例改 410 退役。
6. 加请求日志中间件：`request_id` + method + path + status + 耗时 + user_id（不记敏感原文，§18/§20），先落 journald 即可。
7. calibration 改滚动时间窗或 EWMA。
8. 排版底线定档并写进 `validate-tokens.mjs`：正文 ≥28rpx、辅助 ≥24rpx、最小 22rpx，先批量修 `essay-task` / `positions/page-shell` / `reflection` 三个大户。

**第 2–3 周**

9. token 校验扩展到 `font-size` / `border-radius` / `box-shadow`。
10. `safe-top` 与 `app.ts` 的几何测量改为响应 `wx.onWindowResize`。
11. 主包瘦身：把申论工作台、社区方法卡、私人资料库、报告中心拆进业务分包。
12. `check_architecture.py` 加领域依赖方向规则，先冻结现有 25 组环不再增长。
13. 老域仓库层 commit 上提到 router 单点提交。

**待排期**

14. AI 治理专项：提示注入结构性隔离、额度防刷压测、云函数降级（07-25 与本轮都未覆盖）。
15. 无障碍专项：image aria-label、触控区 ≥88rpx。
16. IA 收敛：5 个报告页与 4 个能力页合并。

---

*方法说明：本轮所有数字均为 2026-07-26 在 `codex/ui-ux-remediation` 分支实测所得，非引用历史报告。前端 vitest、六个 validator、`generate-api.mjs --check`、OpenAPI 导出与契约比对、git 对象统计均实际执行。后端 767 条 pytest 在本机重跑中（上一轮记录为 766/767，需真 Postgres 约 37 分钟），本报告的后端结论来自源码核验而非测试结果。*
