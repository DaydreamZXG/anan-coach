# 全局质量审计总结

审计基线：`01752f1d7b8b606b6900160eebba0888d8def294`  
优先范围：`apps/wechat/miniprogram`；并审计 FastAPI、PostgreSQL、Alembic、
OpenAPI 合同与 TypeScript/Vitest。  
性质：只读审计；没有修改正式业务源码、依赖、锁文件、项目配置、生产数据或支付。

## 1. 结论

“每次人工测试都会不断发现新 Bug”不是 88 个页面分别质量差，而是多个公共质量系统
失去同一权威：

1. 路由、视觉场景、交互合同、OpenAPI 生成物、README 与 CI 各自维护口径，已有检查
   没有全部进入正式门禁；
2. 生产内容已迁移到 frozen-release 合同，但公共测试工厂仍构造旧数据，52 条测试在
   到达业务断言前失败；
3. session、请求 generation 和逻辑写操作没有统一 owner，迟到 401、迟到响应和
   unknown outcome 分别由页面自行处理；
4. 后端命令缺少不可变 command identity 与单一事务 owner，重放可以只保存一条答案却
   重复发放 XP；
5. 异步任务没有把 producer、部署 consumer、lease、stale recovery、readiness 和告警
   绑定为一个可执行注册表。

这五类根因解释了为什么局部页面修补后，新的表面症状仍会从其他页面、worker 或发布
路径出现。正式问题已经按公共根因去重，没有把同一问题按页面重复登记。

## 2. 口径纠正与项目地图

仓库当前并非 88 个注册页面。可执行清点结果为：

- 64 个注册路由：36 个主包、28 个分包；
- 其中 58 个业务页面、6 个岸岸资产预览入口；
- 88 是覆盖这 58 个业务页面的视觉状态场景数，不是页面数；
- 58 个业务页面各有交互合同；另有 25 个小程序组件；
- FastAPI OpenAPI 有 233 个 path、244 个 operation。

因此，用户要求的“88 个页面中实际检查多少个”应准确报告为：**88/88 个业务视觉状态
已分别在标准手机、iPad 竖屏和 iPad 横屏真实渲染；实际注册页面为 64/64 个均完成
page-ready 打开验证**。不能把同一页面的 loading、error、ready 等多个状态冒充多个
页面。这里的 iPad 是微信开发者工具模拟器，不是实体平板。

项目地图：

- `audit/page-inventory.csv`
- `audit/component-inventory.csv`
- `audit/api-inventory.csv`
- `audit/critical-flows.md`
- `audit/coverage-matrix.csv`

## 3. 基线结果

主基线 15 项检查中 13 项通过、2 项失败：

| 范围 | 结果 |
|---|---|
| 小程序 TypeScript / ESLint | 通过，0 错误 |
| 已接线 token / interaction / control / navigation / design 规则 | 全部通过 |
| Vitest | 98 个文件、554 项，全部通过 |
| 云函数 Node 测试 | 9 项，全部通过 |
| 小程序格式 | 失败，1 个正式文件未通过 Prettier |
| 后端 Ruff | 通过 |
| 后端 pytest | 993 项：924 通过、69 失败、0 error |
| Alembic 静态图 | 单 head `20260728_70`，history 可枚举 71 个 revision |
| OpenAPI 重新生成 | 与提交类型不一致：579 行 diff，502 行新增、1 行删除 |
| 微信开发者工具真实预览 | 后续由 C 审计成功编译，包体 2,222,990 bytes |

69 个 pytest 失败中，13 个来自审计开始前已存在的未跟踪测试文件；其余 56 个
Git-tracked 失败归并为 3 个公共根因：迁移/版本权威 2 个、方法卡 exact replay 2 个、
frozen-release 测试夹具漂移 52 个。后者不是 52 个生产 Bug。

现有统一 WeChat CI 入口没有执行 554 项 Vitest、9 项 cloud tests、OpenAPI 新鲜度以及
未接线的 structure/visual/interaction/asset 校验。详细命令、exit code、耗时和日志见
`audit/baseline/summary.md` 与 `audit/baseline/results.tsv`。

## 4. 页面冒烟与真实 UI/UX

### 页面打开

- 静态注册与四件套：64/64；
- DevTools `reLaunch`、fixture settle、activeRoute 相等、page-ready 截图：64/64；
- 成功打开：64；
- 页面级打开阻塞：0；
- 缺失页面、磁盘孤儿页、精确重复页、未注册 route literal：均为 0。

标准屏采用 iPhone 12/13 Pro 模拟器，`390×844`、DPR 3。88 个业务状态与 6 个资产入口
均有当前 commit 的截图；业务路由覆盖 58/58。审计运行副本把请求、上传、下载和支付
全部 fail closed，没有请求 production。

尺寸交叉采样采用同一套真实 DevTools page-ready 判定：

| 设备档 | 实际 `systemInfo` | 场景 / 路由 | 运行结果 |
|---|---|---:|---|
| 小屏 iPhone 5 | `320×568`，DPR 2 | 16 / 15 | 16 captured，0 failure |
| 标准 iPhone 12/13 Pro | `390×844`，DPR 3 | 88 业务场景 + 6 资产入口 / 64 | 全部 page-ready |
| 长比例安卓 HUAWEI nova 14 Ultra | `363×817` 可用窗口，DPR 3.5 | 16 / 15 | 16 captured，0 failure |
| iPad 模拟器竖屏 | `768×1024`，DPR 2 | 88 业务场景 + 6 资产入口 / 64 | 94 captured，0 failure |
| iPad 模拟器横屏 | `1024×768`，DPR 2 | 88 业务场景 + 6 资产入口 / 64 | 94 captured，0 failure |

安卓机型菜单标称 `363×871`，DevTools `systemInfo` 扣除系统 chrome 后返回
`363×817`；报告使用实际窗口值。标准屏与 iPad 横、竖屏均完成全量状态覆盖，小屏与长
安卓各做 16 个高风险状态交叉采样，因此不能把后二者表述为 88/88 全量覆盖。实际 UI
渲染覆盖率为：标准屏、iPad 竖屏、iPad 横屏各为业务状态 100%（88/88）、业务路由
100%（58/58）；小屏和长安卓各为业务状态 18.2%（16/88）、业务路由 25.9%（15/58）。

主审复核了标准屏与两种平板方向的真实联系表，没有把主观审美差异当作 Bug。平板新增
确认一条独立问题：答题卡在 `768×1024` 竖屏越出右侧窗口，而同一状态在
`1024×768` 横屏没有溢出。真实截图中发现的正式 UI 条目以
`audit/findings.jsonl` 中 C 来源 finding 为准；仅有代码线索的内容保留在
`audit/hypotheses.md`。

## 5. 核心业务链路

- 用户指定链路：13/13 已识别；
- 当前版本实际存在：12；公告由 feature flag 明确关闭，记为 N/A；
- 页面/controller/契约动态覆盖：11/12（91.7%）；启动链路只有静态和环境级动态证据；
- 标准手机、iPad 竖屏和 iPad 横屏入口/关键状态真实渲染：现存链路均有对应场景；
- 小程序 → FastAPI → PostgreSQL 的隔离环境 E2E：0/12。

未执行真实跨服务 E2E 的原因不是测试成功，而是 develop、trial、release 都指向仓库中
记录为 production 的同一域名。本轮按限制没有请求该域，也没有真实账号、真实用户
数据、支付或 AI provider。登录、首页/今日任务、刷题、提交、结果、解析、错题、模考、
岗位筛选和 AI 批改均完成现有状态机/合同证据审计；公告为 N/A。

细粒度正常、加载、空、错误、慢请求、断网、登录过期、重复点击、返回与重进矩阵见
`audit/subagents/b/flow-coverage.csv`。

## 6. 正式问题台账

最终去重后正式 finding 共 15 条：

| 严重度 | 数量 |
|---|---:|
| P0 | 1 |
| P1 | 2 |
| P2 | 11 |
| P3 | 1 |

代表性已证实问题：

- `ROOT-MIGRATION-001`（P0）：audited baseline → head 在历史 revision 33 因调用可变
  当前 repository 接口而失败，同时 revision 权威存在 `_68/_69/_70` 三套；
- `BACKEND-COMMAND-001`（P1）：相同 verdict 重放保持 1 条 verdict，却产生 2 次
  `xp_gain`、XP=20；
- `ROOT-JOBS-001`（P1）：stale job 可永久保持 running/deleting，cause-agent producer
  在标准部署拓扑没有 consumer。
- `UI-CAPTURE-001`（P2）：相机失败时错误条覆盖相册和快门，长安卓快门越出可用窗口，
  已在小屏、长安卓及 iPad 横竖屏真实复现；
- `UI-PRACTICE-ANSWER-CARD-001`（P2）：答题卡在 iPad `768×1024` 竖屏右侧被裁切，
  主操作也越出窗口；同一状态在 `1024×768` 横屏正常；
- `UI-ESSAY-TITLE-001`（P3）：申论评分依据标题末字“楚”在 320、363、390px 三档均
  成为单字孤行。

完整复现、期望/实际、用户影响、证据、根因、影响范围与回归建议见：

- `audit/findings.md`
- `audit/findings.jsonl`

## 7. 结论分层

### 已证实问题

正式台账中的每一条均有动态测试、真实 PostgreSQL、DevTools 截图、命令日志或可定位
源码证据；UI 条目必须有真实渲染证据。所有正式问题状态为 `confirmed/open`。

### 高概率问题

缺参冷启动、9 个可能缺用户入口的路由、更多 fresh-key 写操作、刷题内岸岸重试、
账号切换迟到响应、auth last_seen 热点等只有静态或局部证据，均保留在
`audit/hypotheses.md`，不计入 P0/P1/P2/P3。

匿名 knowledge 接口返回答案/解析是已证实技术事实，但仓库没有证明 corpus 必须私有；
因此它是政策阻塞项，不把审计者偏好冒充安全 Bug。若产品/安全 owner 确认答案不得
匿名或提交前返回，应按本轮定义以 P0 进入正式台账。

### 审计阻塞项

- 没有隔离的非生产 API、PostgreSQL、可重置账号和种子数据；
- 真实 AI worker/provider 没有安全配置；
- production、真实 worker/queue、CDN/WAF 状态按要求未检查；
- 17 个必需参数页的全部错参/缺参、真实返回栈和真实上游入口未完成 E2E；
- iPad 横竖屏已在微信开发者工具模拟器全量覆盖，但没有实体平板硬件验证；
- 仓库没有当前 SwiftUI 客户端，SwiftUI 审计为 N/A；
- 后端以 raw SQL 为主，`target_metadata=None`，无法用 declarative ORM graph 做
  `alembic check`，已改用真实迁移回放和 schema contract。

完整清单见 `audit/blockers.md`。

### 未覆盖范围

本轮不声称生产当前 revision、生产是否已有卡单、真实 AI 质量/费用、真实支付、手机或
平板真机网络性能、全量无障碍、CDN/WAF 或跨设备账号数据一致性已经验证。

## 8. 第一修复波次

Wave 0 只恢复可信门禁，不混入业务功能修补：

1. `ROOT-MIGRATION-001`：单一 revision authority、audited baseline → head 和当前
   artifact startup smoke；
2. `ROOT-CONTRACT-001`：OpenAPI/生成类型新鲜度和鉴权元数据对账；
3. `ROOT-GATE-001`：统一 64/58/6 路由语义，并让 Vitest、cloud、route/visual/
   interaction/asset/OpenAPI 全部进入唯一 verify 入口；
4. `ROOT-TESTDATA-001`：canonical frozen-release factory，使 52 条测试到达原业务断言。

Wave 0 不接触生产，也不宣称业务已修复。共享 migration、生成代码、package/CI 和公共
合同各由单一 owner 串行集成；文件范围、测试、回滚和并行约束见
`audit/repair-plan.md`。

## 9. 审计完整性

审计开始前工作树已非 clean，既有修改与未跟踪文件保留并记录在
`audit/baseline/git-status-before.txt`。本轮新增或生成的内容仅位于 `audit/**` 和
`tools/audit/**`；没有修改正式业务源码。最终交付校验为 18 passed、0 skipped、
0 failed，日志见 `audit/qa/deliverables-tablet-final.log`。报告在此停止，不进入修复
波次。
