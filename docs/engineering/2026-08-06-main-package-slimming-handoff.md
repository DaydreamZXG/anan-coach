# 主包瘦身 + 云测修复交接（2026-08-06）

> 交接对象：codex 接手继续。交接时分支 `codex/commercial-selection-repair` 有 83 个未提交改动；
> 首轮已提交为 `3efb3e95c`，后续完整闭包迁移仍在当前 worktree 待提交。
> 上下文：小程序主包 >1.5M 阻塞上传 + 微信云测报 6 类问题。接手续作已通过官方预览打包与开发者工具代码质量扫描。

## 0. 一句话状态

**微信开发者工具 15:16 代码质量扫描已全绿：未压缩主包 995,804 字节，低于 1.5 MiB 上限 577,060 字节；「主包未使用 JS/组件」均为 0。** 微信 CLI 官方预览回执主包为 704,218 字节（687.7 KiB），总包 2,833,502 字节。图片/音频资源为 188,390 / 204,800 字节。typecheck、lint、包边界、结构/路由/视觉覆盖均通过。

## 1. 已完成（本会话）

### 1.1 微信云测 6 类问题

| #   | 问题                           | 处理                                                                                     | 文件                                   |
| --- | ------------------------------ | ---------------------------------------------------------------------------------------- | -------------------------------------- |
| ①   | 登录页标签语小屏转行孤行       | `--text-display`(72rpx) 超内容区必转行 → 改 `--text-hero`(56rpx)，全屏适配               | `pages/login/index.wxss`               |
| ②   | App+login 启动路径同步 Storage | `recordCoachNotificationOpen` 延迟 `wx.nextTick`；`currentDeviceId` 模块级记忆化         | `app.ts`、`auth/session.ts`            |
| ③   | login/onboarding 声明未用组件  | **旧包告警**，当前代码已无 `channel-video`/`ui-button`                                   | —                                      |
| ④   | onboarding 高频同 key Storage  | `pending-store` 加内存缓存：读走缓存、写/删失效 key、读仍全量校验                        | `features/onboarding/pending-store.ts` |
| ⑤   | `wx.onAppRouteDone` 未解绑     | **旧包告警**，当前代码已无                                                               | —                                      |
| ⑥   | HTTP/2 未开启                  | **服务端已开**：`curl --http2` 实测 `http_version=2`，Caddy 2 默认启用，微信侧网络栈行为 | —                                      |

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

**首轮已提交**：提交 `3efb3e95c`。接手续作的完整闭包迁移与资源优化仍在 worktree，待本轮确认后提交。

### 1.3 接手续作：完整运行时闭包 + 资源门禁

- 新增 `scripts/analyze-package-runtime.mjs`：区分 type-only 与运行时依赖，门禁要求主包内「仅分包可达」运行时文件为 0。
- 15 个报告顶层文件连同相关组件/依赖闭包共 241,635 字节移入 `pages/<subpackage>/shared/`；单包文件 `forms.ts`、`contracts.ts` 分别进入 essay-task、positions。
- 将原主包内 33 个非 Tab 二级页面按业务聚合为 `plans/account/xingce/essay/community/reports` 6 个分包；登录页与 4 个 Tab 仍在主包。
- 跨分包代码生成 140 份包内副本；`scripts/subpackage-shared-manifest.mjs` 定义权威映射，`npm run subpackage-shared:sync` 同步，`--check` 防漂移。
- 客服二维码从 103,608 字节优化到 37,167 字节（512 px 长边），本地 QR 解码结果与原图一致；新增 200 KiB 图片/音频总量门禁。
- 微信 CLI `preview --info-output` 官方结果：总包 2,833,502 字节，主包 704,218 字节；最大分包 practice 285,448 字节。
- 微信开发者工具代码质量最终结果：未压缩主包 995,804 字节，所有检查项通过。
- 验证：typecheck、lint、包边界、60 条路由结构/视觉/交互覆盖均通过；后端深链相关 123 条测试全过。小程序全量为 870/872 通过，仅剩 2 条预先存在的 PF-001 请求次数断言。

## 2. 待办（接手后）

### 2.1 ✅ 已完成：实测主包大小

微信 CLI 官方预览打包已确认主包为 **704,218 字节（687.7 KiB）**。微信开发者工具未压缩口径为 **995,804 字节**，代码质量页已全绿。

### 2.2 ✅ 代码侧已完成：「主包未使用JS」告警

原清单中的 3 个单分包文件已移动：

| 文件                              | 目标分包   | 原因                                                                                    |
| --------------------------------- | ---------- | --------------------------------------------------------------------------------------- |
| `api/math-ink.ts`                 | practice   | 被 `components/math-ink-pad` type-only 引用（组件本身只被 practice 用），需连组件一起移 |
| `features/essay-task/forms.ts`    | essay-task | 主包 `pending-store` 只 `import type` 引用它（编译擦除）                                |
| `features/positions/contracts.ts` | positions  | 只被 positions 分包文件用                                                               |

原结构性共享模块已采用包内副本方案，并连同其仅分包可达依赖/组件整体迁移。静态运行时闭包门禁当前为 **0 个主包仅分包可达文件**；副本一致性由 140 份生成物检查保证。微信质量扫描已确认主包未使用 JS/组件均为 0。

## 3. 关键知识（避免踩坑）

- **微信打包规则**：`subPackages` 路径外=主包；分包可 require 主包但不能 require 其他分包；独立分包(`independent:true`)不能依赖主包内容（本项目分包大量依赖主包 api/auth，不适用）。
- **「主包未使用JS」判定是运行时依赖**：`import type` 擦除后不算引用。静态 import 分析要区分 type-only，否则会误判文件在主包闭包内。
- **跨分包共享维护**：修改权威副本后运行 `npm run subpackage-shared:sync`，提交前 `npm run package:validate` 会拒绝任何漂移。
- **迁移正确做法**：先记录旧路径下所有 import 解析（移动后旧文件不存在无法解析），`git mv`，再按「旧目标 abs → 新目标 abs」从新文件位置重算相对路径。只重写 from 或 target 有移动的 import，否则会误伤（曾把 324 个文件误加 `.ts` 后缀后回滚）。
- **`.ts` 后缀**：本项目 import 不带 `.ts` 扩展名，重写后需去掉。

## 4. 相关文档

- 全量云测执行说明：`apps/wechat/minitest/README.md`（109 条 Minium 用例、三类账号、三种设备形态）
- AI 全量计划：`apps/wechat/minitest/AI-FULL-PLAN.md`（25 条 AI 旅程和 3 组计划已同步云端，60/60 路由设计覆盖）
- 0.1.10 历史内测记录：`docs/product/_archive/2026-08-06-wechat-0.1.10-full-test-and-public-beta.md`
- 全局待办：`docs/engineering/global-backlog.md`（T14 主包超限、T15 云测修复已更新）
- 操作备忘：`docs/engineering/operations-playbook.md`
- 上一份交接：`docs/engineering/2026-08-04-repair-sprint-handoff.md`
