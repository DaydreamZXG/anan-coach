# 子审计 B：源码证据索引

以下行号基于 commit `01752f1d7b8b606b6900160eebba0888d8def294`。

## 启动与路由

- `apps/wechat/miniprogram/app.json:2-4`：首个页面是登录页。
- `apps/wechat/miniprogram/app.ts:28-36`：启动时安装全局 401 handler 并配置 API。
- `apps/wechat/miniprogram/app/navigation/routes.ts:9-68`：核心页面路由。
- `apps/wechat/miniprogram/app/navigation/routes.ts:162-220`：任务路由参数白名单和校验。

## B-CORE-001：401 后缺少统一恢复

- `apps/wechat/miniprogram/api/client.ts:115-134`：401 先调用全局 handler，再把请求作为
  `ApiClientError` 拒绝。
- `apps/wechat/miniprogram/api/auth.ts:13-16`：全局 handler 只清除本地会话，不广播状态，
  不重定向，也不更新当前页面的 API client。
- `apps/wechat/miniprogram/pages/essay-task/index.ts:637-647`：401 被归入普通 error。
- `apps/wechat/miniprogram/pages/essay-task/index.ts:655-657`：会话已被清除后，retry 直接返回。
- `apps/wechat/miniprogram/pages/essay-task/index.wxml:22-29`：error 状态仅提供“重新加载”，
  没有登录动作。
- `apps/wechat/miniprogram/pages/mock-exam/index.ts:454-470`：401 进入普通 error；retry 复用
  页面 onLoad 时创建的 runtime/client。
- `apps/wechat/miniprogram/pages/mock-exam/index.wxml:26-34`：只有 unavailable 状态才可能显示
  登录按钮；error 状态只显示 retry。
- 对照正确处理：
  - `apps/wechat/miniprogram/pages/practice/index.ts:1704-1729`
  - `apps/wechat/miniprogram/pages/xingce-task-intro/index.ts:188-200,241-249`
  - `apps/wechat/miniprogram/pages/xingce-mastery/index.ts:252-263`

动态复现：`tools/audit/b/session-expiry.audit.test.ts`。

## B-CORE-002：模考终态不收敛

- `apps/wechat/miniprogram/pages/mock-exam/index.ts:1250-1269`：提交冲突后读取最新会话；
  当服务端已不是 `assigned` 时清本地 pending 后抛普通 Error。
- `apps/wechat/miniprogram/pages/mock-exam/index.ts:1217-1235`：外层 catch 仅把
  `task_edit_owned_elsewhere` 当作终止交互；普通 Error 会令
  `interactionLocked=false`，且不设置 `status=submitted`、不停止计时。
- `apps/wechat/miniprogram/pages/mock-exam/index.wxml:10-14`：只要 status 仍为 `ready`，
  答题卡、草稿、工具和 footer 继续可用。
- `apps/wechat/miniprogram/pages/mock-exam/index.wxml:37-45`：正式已交卷视图要求
  `status === 'submitted' && receipt`。

动态复现：`tools/audit/b/mock-terminal-conflict.audit.test.ts`。

## 功能存在性与审计边界

- `backend/app/api/routes.py:63-73`：`announcements` 明确为 `False`；仓库没有公告页面或
  公告业务路由，故本版本标记 N/A。
- `backend/app/core/config.py:162`：`essay_review_worker_enabled` 默认 `False`；未提供安全
  provider/worker 联调环境，因此 AI 批改只验证到前端队列、轮询和恢复状态机。
- `backend/app/domains/task_content/service.py:275-353` 与
  `backend/tests/test_stage2_task_content_contract.py:86-112`：当前版本保留下划线空格。
  旧真机报告的临时截图不可访问，不能作为当前 commit 的正式证据。
