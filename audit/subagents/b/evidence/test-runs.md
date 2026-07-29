# 子审计 B：测试执行证据

审计基线 commit：`01752f1d7b8b606b6900160eebba0888d8def294`

执行日期：2026-07-28（Asia/Shanghai）

所有命令均为只读测试；没有连接生产环境、真实用户、真实支付或真实 AI provider。

## 1. 核心链路既有 Vitest

工作目录：`apps/wechat`

```text
NO_COLOR=1 npm test -- --reporter=verbose \
  tests/login-controller.test.ts \
  tests/stage9-login.test.ts \
  tests/auth-return.test.ts \
  tests/stage10-daily-opening.test.ts \
  tests/task-runtime.test.ts \
  tests/xingce-task-intro.test.ts \
  tests/xingce-real-attempt.test.ts \
  tests/xingce-stage3-factored-results.test.ts \
  tests/xingce-mastery-queue.test.ts \
  tests/xingce-practice-page-lifecycle.test.ts \
  tests/stage2-mock-exam.test.ts \
  tests/position-controllers.test.ts \
  tests/position-page-lifecycle.test.ts \
  tests/stage5-essay-workbench.test.ts \
  tests/essay-task-page-lifecycle.test.ts \
  tests/essay-task-page-runtime.test.ts \
  tests/essay-closure-wechat.test.ts \
  tests/coach-notifications.test.ts
```

结果：

```text
Test Files  18 passed (18)
Tests       200 passed (200)
Duration    2.02s
```

覆盖重点：登录恢复与重复点击、Today 入口、行测说明/作答/提交/复盘、
掌握队列、模考计时/断点/冲突、岗位慢请求/返回刷新/未知写入结果，以及
申论评阅队列/轮询/跨设备恢复。

## 2. 路由、Today、内容、报告补充 Vitest

工作目录：`apps/wechat`

```text
NO_COLOR=1 npm test -- --reporter=verbose \
  tests/data-mode.test.ts \
  tests/today-dual-track.test.ts \
  tests/stage2-content-renderer.test.ts \
  tests/xingce-navigation.test.ts \
  tests/stage6-reporting.test.ts \
  tests/presentation.test.ts
```

结果：

```text
Test Files  6 passed (6)
Tests       43 passed (43)
Duration    1.06s
```

两组合计：`24/24` 文件、`243/243` 测试通过。

## 3. 本轮只读审计复现测试

工作目录：仓库根目录。

```text
NO_COLOR=1 ./apps/wechat/node_modules/.bin/vitest run \
  --config apps/wechat/vitest.config.ts \
  --reporter=verbose \
  tools/audit/b/session-expiry.audit.test.ts \
  tools/audit/b/mock-terminal-conflict.audit.test.ts
```

结果：

```text
✓ mock terminal conflict: server submitted, local page remains answerable
✓ essay workspace 401: retry becomes inert after global session clear
✓ mock recovery 401: retry reuses the expired bearer token

Test Files  2 passed (2)
Tests       3 passed (3)
Duration    979ms
```

这里“通过”表示生产页面模块稳定复现了断言中的缺陷，不表示行为正确。测试文件：

- `tools/audit/b/session-expiry.audit.test.ts`
- `tools/audit/b/mock-terminal-conflict.audit.test.ts`

## 4. 既有真机报告中“下划线空格”现状核验

工作目录：`backend`

```text
NO_COLOR=1 .venv/bin/pytest -q \
  tests/test_stage2_task_content_contract.py::test_rich_content_contract_preserves_underlined_blanks_from_the_source_stem
```

结果：

```text
1 passed in 0.32s
```

当前源码会保留 `<u>` 并为空白下划线生成 `____`。旧报告的临时截图路径已经
失效，且无法证明对应当前 commit，因此本轮没有把旧现象写成正式发现。

## 证据边界

- 上述为 Node/Vitest 或 Python 进程内动态验证，不等于微信开发者工具真实渲染。
- 未启动带安全种子数据的 FastAPI + PostgreSQL 联调环境。
- 未运行真实 AI provider 或 essay review worker。
- 没有截图证据；UI/UX 结论由子审计 C 负责。
