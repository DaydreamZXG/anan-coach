# 核心业务链路地图

审计快照：2026-07-28  
Git commit：`01752f1d7b8b606b6900160eebba0888d8def294`

## 覆盖口径

- 用户指定链路已识别：13/13。
- 当前版本实际存在：12；“公告”由后端 feature flag 明确关闭，记为 N/A。
- 页面、控制器或契约级动态覆盖：11/12（91.7%）；启动链路只有静态和环境测试。
- 微信小程序 → FastAPI → PostgreSQL 的隔离环境 E2E：0/12。develop、trial、
  release 都指向已记录为 production 的同一 API 域，本轮按限制没有请求该域。
- 微信开发者工具标准手机、iPad 竖屏和 iPad 横屏真实 UI 各覆盖 88/88 个业务状态、
  58/58 个业务路由；小屏与长安卓各交叉采样 16 个核心状态、15 个路由。不以 Vitest
  或源码推断替代；iPad 数据来自开发者工具模拟器，不冒充实体平板。

## 链路矩阵

| ID | 链路 | 页面与主路径 | 关键接口或状态权威 | 本轮证据 | 结论 |
|---|---|---|---|---|---|
| F01 | 启动 | `app.ts` → `/pages/login/index` | 环境配置、session、通知打开记录 | 环境测试；DevTools 登录入口真实渲染（非完整 App launch） | 部分；跨服务启动阻塞 |
| F02 | 登录 | 登录 → onboarding 或 Today/原深链 | `POST /api/v2/auth/wechat`、onboarding progress | controller/auth-return 测试 | 401 公共恢复协议有确认缺陷 |
| F03 | 首页 | `/pages/today/index` | `GET /api/v2/plans/today`、daily opening | Today/presentation 测试 | 组件级通过；真实后端联调阻塞 |
| F04 | 今日任务 | Today 卡片 → 服务端 `startUrl` | plan task、task execution | Today 双轨/入口测试 | 无效入口会阻断；正常状态机有覆盖 |
| F05 | 刷题 | 任务说明 → `/pages/practice/index` | briefing/session/checkpoint | practice/runtime 测试 | 状态、计时、切题、返回保存有覆盖 |
| F06 | 提交 | practice → submit → reflection | submit/CAS/pending command | attempt/runtime 测试 | 既有状态机有覆盖；无真 E2E |
| F07 | 结果 | reflection/result presentation | attempt review | factored-result 测试 | 基于会话事实；无真 E2E |
| F08 | 解析 | reflection 逐题复盘 | material/stem/trajectory | result semantics 测试 | 证据不足分支有覆盖 |
| F09 | 错题与掌握 | `/pages/xingce-mastery/index` | mastery queue/action URL | mastery queue 测试 | 初始、空、分页、部分失败、401 有覆盖 |
| F10 | 模考 | Today/报告 → mock exam → report | weekly/session/checkpoint/submit | mock 测试 + 审计复现 | 提交冲突后的服务端终态收敛有确认缺陷 |
| F11 | 公告 | N/A | N/A | `backend/app/api/routes.py` feature flag | 当前版本明确未启用 |
| F12 | 岗位筛选 | positions → search/detail/compare/goal | qualification/positions/goal | controller/lifecycle 测试 | 查询竞态、分页、返回刷新有既有覆盖 |
| F13 | AI 批改 | Today → essay task → review queue/poll | workspace/answer/review job | essay 测试 + 审计复现 | 401 恢复有确认缺陷；真实 worker 阻塞 |

## 行测日常链路

```text
启动/登录
  → Today 读取服务端计划
  → xingce-task-intro 校验 planTaskId + mode
  → practice 创建或恢复会话
  → checkpoint / submit
  → reflection（结果、解析、错因）
  → xingce-mastery（待复盘、复习中、已掌握）
```

当前动态证据集中在控制器、页面模块、TaskRuntime 和 API 契约。后端全量基线还暴露了
内容发布契约漂移，可能使计划无法生成行测任务；该项以子审计 E 的最终归因和证据为准。

## 模考链路

```text
Today/报告入口
  → weekly create-or-restore / get session
  → 获取设备编辑权
  → checkpoint + trajectory
  → submit
  → submitted / report
```

确认缺陷：提交发生 409 后，服务端最新会话已经是 `submitted`，本地仍回到
`ready` 并开放作答。重新进入页面可恢复，因此当前评为 P2。

## 岗位链路

```text
岗位工作台
  → 搜索/筛选
  → 详情与资格
  → 对比
  → 主目标预览/确认
  → 岗位简报与计划重排
```

既有测试覆盖慢请求覆盖新查询、分页部分失败、返回刷新、未知写结果和 409 终态清理。
本轮没有安全账号和非生产 API，未宣称跨服务 E2E。

## 申论与 AI 批改链路

```text
Today
  → essay-task workspace
  → attempt / answer
  → queue or ensure review
  → queued / running / waiting_quota
  → review_ready
  → rewrite / transfer / plan completion
```

前端持久意图、跨设备恢复、配额等待、轮询和 closure 命令有动态测试。真实评阅 worker
默认关闭且没有安全 provider 配置；端到端质量、时延和外部错误返回未覆盖。

## 已确认的链路级问题

- 统一 401 恢复缺失：申论重试变为无动作，模考继续使用旧 token；旧请求的迟到 401
  还可清除新会话。与公共认证状态根因合并为一条。
- 模考终态不收敛：服务端已 `submitted`，当前页仍保持 `ready`。
- 岸岸聚合上下文把 planning 503 当成“无目标/0 任务”。
- 行测内容发布/计划契约问题由后端子审计归并，不按每个测试文件重复建单。

## 阻塞与未覆盖

- 没有独立的 non-production API/数据库/种子账号，真实跨服务 E2E 为 0。
- 不访问 production 域，不接触真实账号、真实用户数据或支付。
- AI provider/worker 未在安全隔离环境运行。
- 公告为产品 N/A，不是审计阻塞。

细粒度矩阵与原始测试证据：

- `audit/subagents/b/flow-coverage.csv`
- `audit/subagents/b/evidence/test-runs.md`
- `audit/subagents/b/evidence/source-evidence.md`
