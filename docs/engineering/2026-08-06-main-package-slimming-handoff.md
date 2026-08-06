# 主包瘦身 + 云测修复交接（2026-08-06）

> 交接对象：codex 接手继续。分支 `codex/commercial-selection-repair`，83 个未提交改动（34 移动 + 46 修改）。
> 上下文：小程序主包 >1.5M 阻塞上传 + 微信云测报 6 类问题。本轮已完成部分，**主包是否达标待实测确认**。

## 0. 一句话状态

**34 个「仅被单一分包引用」的主包文件已物理移入对应分包目录**（微信打包：subPackages 路径外的文件都算主包，被分包引用的主包文件会留在主包 → 既撑大主包又触发「主包未使用JS」告警）。typecheck 干净，全量测试 865 过（仅剩 2 个预先存在的 pf001 flaky）。**但主包最终编译大小还没用微信官方报告确认，这是接手后的第一件事。**

## 1. 已完成（本会话）

### 1.1 微信云测 6 类问题

| # | 问题 | 处理 | 文件 |
|---|---|---|---|
| ① | 登录页标签语小屏转行孤行 | `--text-display`(72rpx) 超内容区必转行 → 改 `--text-hero`(56rpx)，全屏适配 | `pages/login/index.wxss` |
| ② | App+login 启动路径同步 Storage | `recordCoachNotificationOpen` 延迟 `wx.nextTick`；`currentDeviceId` 模块级记忆化 | `app.ts`、`auth/session.ts` |
| ③ | login/onboarding 声明未用组件 | **旧包告警**，当前代码已无 `channel-video`/`ui-button` | — |
| ④ | onboarding 高频同 key Storage | `pending-store` 加内存缓存：读走缓存、写/删失效 key、读仍全量校验 | `features/onboarding/pending-store.ts` |
| ⑤ | `wx.onAppRouteDone` 未解绑 | **旧包告警**，当前代码已无 | — |
| ⑥ | HTTP/2 未开启 | **服务端已开**：`curl --http2` 实测 `http_version=2`，Caddy 2 默认启用，微信侧网络栈行为 | — |

### 1.2 主包瘦身（核心工作）

**根因**（微信官方文档确认）：`subPackages` 配置路径外的所有文件都算主包。分包页面 import 主包根下的 feature 模块时，该文件物理留在主包 → 撑大主包 + 触发「主包内不应存在主包未使用的JS文件」告警。

**做法**：34 个「运行时仅被单一分包引用、主包闭包不可达」的 TS 文件 `git mv` 进对应分包 root 下的 `features/` 子目录，同步重写 207 处 import（含 22 个测试文件的路径）。

**移动映射**（旧 → 新）：
- `features/positions/*-controller.ts`、`presentation/error-state/runtime.ts` → `pages/positions/features/`
- `features/ability/{presentation,history,reporting}.ts` → `pages/positions/features/ability/`
- `api/ability.ts` → `pages/positions/features/api/ability.ts`
- `features/onboarding/*-controller.ts`、`result-presentation.ts` → `pages/onboarding/features/`
- `features/qualification/profile-editor-controller.ts` → `pages/onboarding/features/qualification/`
- `features/essay-task/{closure,closure-store,closure-flow,review-presentation,page-runtime}.ts` → `pages/essay-task/features/`
- `features/essay-handwriting/workflow.ts` → `pages/essay-task/features/essay-handwriting/`
- `features/external-learning/{capture-controller,match-presentation}.ts` → `pages/capture-import/features/external-learning/`
- `features/xingce/real_attempt/{controller,page-runtime}.ts` → `pages/practice/features/xingce/real_attempt/`
- `task-runtime/state/autosave.ts` → `pages/practice/features/task-runtime/state/`
- `api/private-knowledge.ts` → `pages/private-knowledge/features/api/`
- `api/xfyun.ts` → `pages/reflection/features/api/`

**验证已过**：typecheck 干净；`vitest run` 865 过 / 2 fail（pf001 flaky，预先存在）；微信模拟器编译 practice/positions/goal 页面无错误；prettier 格式化完成。

**未提交**：全部改动在 worktree，未 commit（交接后先 commit）。

## 2. 待办（接手后）

### 2.1 第一优先：实测主包大小

静态测算：主包根 TS 源码 2176KB → 1903KB（移走 273KB 源码）。但微信上报的是**编译后大小**，必须上传体验版看官方报告确认 <1.5M(1536KB)。上传动作需要用户在微信开发者工具点确认，codex 触发一次即可。

### 2.2 剩余「主包未使用JS」告警（15 个，分三类）

用户已贴云测清单，其中 **3 个是漏网可移的**（本会话已定位但未移）：

| 文件 | 目标分包 | 原因 |
|---|---|---|
| `api/math-ink.ts` | practice | 被 `components/math-ink-pad` type-only 引用（组件本身只被 practice 用），需连组件一起移 |
| `features/essay-task/forms.ts` | essay-task | 主包 `pending-store` 只 `import type` 引用它（编译擦除） |
| `features/positions/contracts.ts` | positions | 只被 positions 分包文件用 |

**11 个结构性共享（微信无法解决）**：`api/task-editing`、`api/task-help`、`features/task-editing/coordinator`、`features/learning-media/{draft-store,presentation,upload-controller}`、`features/xingce/real_attempt/{pending-store,presentation}`、`task-runtime/{state/runtime,swipe-nav,display-preferences,tool-policy/index}`。这些被 2-3 个分包共同引用，微信明确「packageA 无法 require packageB」→ 共享代码只能留主包。**留主包是正确设计，但微信会报「未使用」**。三条路待用户/codex 决策：
- A 接受（告警持续，若主包达标则无硬阻塞）
- B 复制进各分包（告警消失，代码冗余，维护要同步）
- C 忽略质量告警（只盯主包大小硬限制）

## 3. 关键知识（避免踩坑）

- **微信打包规则**：`subPackages` 路径外=主包；分包可 require 主包但不能 require 其他分包；独立分包(`independent:true`)不能依赖主包内容（本项目分包大量依赖主包 api/auth，不适用）。
- **「主包未使用JS」判定是运行时依赖**：`import type` 擦除后不算引用。静态 import 分析要区分 type-only，否则会误判文件在主包闭包内。
- **迁移正确做法**：先记录旧路径下所有 import 解析（移动后旧文件不存在无法解析），`git mv`，再按「旧目标 abs → 新目标 abs」从新文件位置重算相对路径。只重写 from 或 target 有移动的 import，否则会误伤（曾把 324 个文件误加 `.ts` 后缀后回滚）。
- **`.ts` 后缀**：本项目 import 不带 `.ts` 扩展名，重写后需去掉。

## 4. 相关文档

- 全局待办：`docs/engineering/global-backlog.md`（T14 主包超限、T15 云测修复已更新）
- 操作备忘：`docs/engineering/operations-playbook.md`
- 上一份交接：`docs/engineering/2026-08-04-repair-sprint-handoff.md`
