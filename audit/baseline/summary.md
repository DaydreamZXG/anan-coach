# 全局审计基线摘要

## 1. 范围与判读边界

本摘要只汇总基线命令、原始结果与质量门禁接线情况，不对后端失败进行根因归类，也不把静态门禁输出直接转换为产品 Bug。基线执行期间没有修改正式业务源码、依赖、锁文件或项目配置；所有新增产物位于 `audit/**` 或 `tools/audit/**`。

微信预览命令针对 `audit/subagents/a/runtime-project` 审计副本执行，并采用 `loopback_fail_closed` 网络策略；后端测试使用本机临时 PostgreSQL。未连接生产数据库、真实用户数据或真实支付。

## 2. 代码与环境快照

| 项目                | 值                                                       |
| ------------------- | -------------------------------------------------------- |
| 基线开始时间        | `2026-07-28T04:51:45Z`（北京时间 `2026-07-28 12:51:45`） |
| Git 分支            | `codex/ui-ux-remediation`                                |
| Git commit          | `01752f1d7b8b606b6900160eebba0888d8def294`               |
| Node.js             | `v24.16.0`                                               |
| npm                 | `11.13.0`                                                |
| Python              | `3.12.13`（`backend/.venv`）                             |
| uv                  | `0.11.26`                                                |
| `DATABASE_URL`      | 未设置                                                   |
| `TEST_DATABASE_URL` | 未设置；pytest 使用本机临时 PostgreSQL                   |

基线开始前工作树已经不是 clean 状态。`audit/baseline/git-status-before.txt` 明确记录了既有的已修改文件和未跟踪文件；其中 `backend/tests/test_release_lifecycle_scripts.py` 在本轮基线执行前就是未跟踪文件。前后两份状态清单中的正式源码/运维文件集合没有新增变化，见：

- `audit/baseline/environment.txt`
- `audit/baseline/git-status-before.txt`
- `audit/baseline/git-status-after.txt`

因此，下面的 pytest 数量是“该 commit 加当时既有工作树”的结果，不能等同于 clean commit 的测试结果。

## 3. 基线命令结果

主基线共执行 15 项检查：13 项通过，2 项失败。命令由 `tools/audit/run_baseline.sh` 逐项独立执行，因此前置失败没有阻止后续检查。

| 检查             | 实际命令（工作目录）                                                                                                                | 结果     | 数量/摘要                                                             |                          耗时 |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------- | -------- | --------------------------------------------------------------------- | ----------------------------: |
| 架构边界         | `PYTHONDONTWRITEBYTECODE=1 scripts/check_architecture.py`（仓库根）                                                                 | PASS     | `Architecture boundaries: OK`                                         |                            0s |
| 小程序格式       | `CI=1 npm run format:check`（`apps/wechat`）                                                                                        | **FAIL** | 1 个文件：`miniprogram/features/onboarding/result-presentation.ts`    |                            6s |
| TypeScript       | `CI=1 npm run typecheck`                                                                                                            | PASS     | `tsc --noEmit`，0 个错误                                              |                            4s |
| ESLint           | `CI=1 node_modules/.bin/eslint . --max-warnings 0`                                                                                  | PASS     | 0 个错误/警告                                                         |                           17s |
| Token 规则       | `CI=1 npm run tokens:validate`                                                                                                      | PASS     | 85 个运行时样式表，0 个豁免                                           |                            0s |
| 基础交互规则     | `CI=1 npm run interactions:validate`                                                                                                | PASS     | 89 个模板、472 个按钮                                                 |                            0s |
| 控件对齐         | `CI=1 npm run controls:validate`                                                                                                    | PASS     | 316 个定高扁平控件                                                    |                            4s |
| 导航规则         | `CI=1 npm run navigation:validate`                                                                                                  | PASS     | 34 个自定义导航页：26 workbench、3 task-shell、5 例外                 |                            1s |
| 设计来源         | `CI=1 npm run design:validate`                                                                                                      | PASS     | 22 个精确资产、58 个路由处置等                                        |                            0s |
| Vitest           | `CI=1 node_modules/.bin/vitest run --no-cache --reporter=verbose`                                                                   | PASS     | 98 个测试文件、554 个测试，全部通过                                   |       8s（Vitest 报告 7.24s） |
| 云函数 Node 测试 | `CI=1 npm run test:cloud`                                                                                                           | PASS     | 9 个测试，全部通过                                                    |           0s（报告 119.14ms） |
| 后端 Ruff        | `PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m ruff check . --no-cache`（`backend`）                                        | PASS     | 0 个错误                                                              |                            1s |
| 后端 pytest      | `PYTHONDONTWRITEBYTECODE=1 TEST_DATABASE_URL= backend/.venv/bin/python -m pytest -p no:cacheprovider --junitxml=... --durations=25` | **FAIL** | 993 collected；69 failed、924 passed、0 skipped、0 errors、4 warnings | 2381s（pytest 报告 2377.75s） |
| Alembic heads    | `PYTHONDONTWRITEBYTECODE=1 DATABASE_URL= backend/.venv/bin/python -m alembic heads`                                                 | PASS     | 单 head：`20260728_70`                                                |                            0s |
| Alembic history  | `PYTHONDONTWRITEBYTECODE=1 DATABASE_URL= backend/.venv/bin/python -m alembic history --verbose`                                     | PASS     | 命令成功；日志中 71 个 revision                                       |                            1s |

逐项状态、UTC 起止时间、exit code 和原始日志路径见 `audit/baseline/results.tsv`。主要原始证据：

- `audit/baseline/wechat-format.log`
- `audit/baseline/wechat-typecheck.log`
- `audit/baseline/wechat-lint.log`
- `audit/baseline/wechat-vitest.log`
- `audit/baseline/wechat-cloud-tests.log`
- `audit/baseline/backend-ruff.log`
- `audit/baseline/backend-pytest.log`
- `audit/baseline/backend-pytest.xml`
- `audit/baseline/alembic-heads.log`
- `audit/baseline/alembic-history.log`

### 3.1 后端 pytest 数量的必要拆分

JUnit 与终端日志一致：共 993 项，69 失败、924 通过。69 个失败中：

- 13 个失败来自 `backend/tests/test_release_lifecycle_scripts.py`；
- 该文件在执行前已由 `git-status-before.txt` 记录为未跟踪文件，并非本轮审计创建；
- 剩余 56 个失败分布在已跟踪测试中；
- 本摘要不把这些失败归并为产品根因，后端/数据专项审计应结合复现与代码证据另行去重。

这一区分不代表 13 个失败可以忽略；它只说明“69”受审计前脏工作树影响，不能直接当作 clean commit 的回归数量。

### 3.2 微信开发者工具预览

另行执行了真实微信 CLI 预览命令：

```text
/Applications/wechatwebdevtools.app/Contents/MacOS/cli preview \
  --project audit/subagents/a/runtime-project \
  --qr-format image \
  --qr-output audit/baseline/wechat-preview/preview-qr.png \
  --info-output audit/baseline/wechat-preview/preview-info.json
```

结果为 **FAIL（exit 255，30s）**，错误为开发者工具尝试连接既有 IDE 端口 `42517` 时 `wait IDE port timeout`。该次命令没有得到编译成功或编译失败的判定，因此应记录为“预览/真实构建验证被 IDE 连接阻塞”，不能据此认定小程序业务代码构建失败。

证据：

- `audit/baseline/wechat-preview/result.txt`
- `audit/baseline/wechat-preview/preview.log`

## 4. Alembic 基线的准确含义

`alembic heads` 返回唯一终点 `20260728_70 (head)`；`alembic history --verbose` 成功输出并包含 71 个 revision。这只证明当前源码中的迁移图在静态枚举时是单 head，并且 history 命令可读取。

**不能从单 head 推断以下事项已经成功：**

- 任一历史数据库可从其实际 revision 顺利升级到 head；
- 每个迁移的 DDL、数据回填、约束与现有数据兼容；
- 某个真实环境已正确 stamp 到预期 revision；
- downgrade、回滚或跨版本恢复可用；
- 生产部署执行迁移后能够健康启动。

本轮为了保持只读和不接触生产，没有执行 `alembic upgrade head`。同时，pytest 中 `tests/test_migrations.py::test_wechat_identity_migration_upgrades_from_audited_baseline` 失败，所以更不能用“单 head”替代历史升级验证。此处只陈述基线事实，不对失败根因归类。

## 5. OpenAPI 类型新鲜度

只读导出当前 FastAPI OpenAPI 后，在 `audit/api-contract/generated/` 重新生成类型并与正式生成文件比较：

| 指标                                    | 结果                                               |
| --------------------------------------- | -------------------------------------------------- |
| OpenAPI paths                           | 233                                                |
| OpenAPI operations                      | 244                                                |
| 正式 `types.gen.ts`                     | 32,769 行                                          |
| 审计时重新生成 `types.gen.ts`           | 33,270 行                                          |
| SHA-256                                 | 不一致                                             |
| unified diff                            | 579 行；502 行新增、1 行删除、10 个 hunk           |
| `node scripts/generate-api.mjs --check` | **FAIL（exit 1）**，报告 generated API types stale |

这证明提交的生成类型与当前后端 schema 不一致；本基线不把差异直接推断为某个页面的运行时产品 Bug。证据：

- `audit/api-contract/openapi.json`
- `audit/api-contract/summary.txt`
- `audit/api-contract/hashes.txt`
- `audit/api-contract/types.diff`
- `audit/baseline/quality-gate-gaps/direct-openapi-check.log`

## 6. package scripts、CI 与 README 的接线核对

### 6.1 已确认的入口行为

`apps/wechat/package.json` 中：

- `check` 依次执行 format、typecheck、ESLint、tokens、基础 interactions、controls、navigation、design；
- `test` 与 `test:cloud` 存在，但没有被 `check` 调用；
- `api:check` 与 `structure:check` 不存在；
- `validate-structure.mjs`、`validate-visual-coverage.mjs`、`validate-interaction-coverage.mjs`、`validate-anan-assets.mjs` 没有被任何 package script 引用。

`scripts/verify_wechat.sh` 只执行 `npm run check`。`.github/workflows/ci.yml` 的 WeChat job 只调用 `scripts/verify_wechat.sh`。因此当前 WeChat CI 入口没有执行：

- Vitest；
- 云函数 `test:cloud`；
- OpenAPI 生成新鲜度检查；
- `validate-structure.mjs`；
- 视觉场景覆盖校验；
- 页面交互覆盖校验；
- Anan 资产校验。

后端 CI job 另行调用 `scripts/verify_backend.sh`，会运行 Ruff、pytest 和单-head 检查；它不补齐上述 WeChat/OpenAPI 接线。

### 6.2 README 与实际命令不一致

`apps/wechat/README.md` 宣称可运行 `npm run api:check` 和 `npm run structure:check`，但两条命令均因 “Missing script” 失败。

根目录 `README.md` 把 `./scripts/verify.sh` 描述为覆盖“后端、迁移、OpenAPI 契约、小程序结构/类型/测试和架构边界”的统一入口；实际调用链是：

```text
scripts/verify.sh
├── scripts/check_architecture.py
├── scripts/verify_backend.sh
└── scripts/verify_wechat.sh
    └── npm run check
```

其中 WeChat 的 Vitest、云函数测试、OpenAPI 新鲜度、未接线结构/覆盖校验均不在该调用链中。`scripts/full_verify.sh` 仅再调用 `scripts/verify.sh`，没有增加检查。

### 6.3 未接线校验器的只读执行结果

| 校验                                             | 结果 | 原始输出摘要                                                      |
| ------------------------------------------------ | ---- | ----------------------------------------------------------------- |
| `scripts/verify_wechat.sh`                       | FAIL | 在格式检查处停止；1 个文件未通过 Prettier                         |
| README 所述 `npm run api:check`                  | FAIL | package script 不存在                                             |
| README 所述 `npm run structure:check`            | FAIL | package script 不存在                                             |
| `node scripts/generate-api.mjs --check`          | FAIL | generated API types stale                                         |
| `node scripts/validate-structure.mjs`            | FAIL | 191 条输出：1 条 app pages 集合断言、190 条 selector 诊断         |
| `node scripts/validate-visual-coverage.mjs`      | FAIL | 1 个 assertion：将 `pages/profile/qualification/index` 判为未注册 |
| `node scripts/validate-interaction-coverage.mjs` | FAIL | 1 个 assertion：将 `pages/positions/index` 判为未知交互页面       |
| `node scripts/validate-anan-assets.mjs`          | PASS | Anan 1.0.0；266,874 core bytes；6 intents                         |

上述 191 条结构输出是校验器的原始诊断数量，不等于 191 个独立产品 Bug；视觉/交互覆盖失败也只证明当前校验器与当前注册结构不能一致通过。是否属于公共根因、受影响范围和严重级别，应由总审计去重后决定。

完整证据：

- `audit/baseline/quality-gate-gaps/results.tsv`
- `audit/baseline/quality-gate-gaps/package-script-inventory.json`
- `audit/baseline/quality-gate-gaps/*.log`
- `apps/wechat/package.json`
- `apps/wechat/README.md`
- `.github/workflows/ci.yml`
- `scripts/verify_wechat.sh`
- `scripts/verify_backend.sh`
- `scripts/verify.sh`
- `scripts/full_verify.sh`

## 7. 基线结论

当前可确认的基线是：

1. 小程序 TypeScript、ESLint、已接线静态规则、98 个 Vitest 文件/554 个测试和 9 个云函数测试均通过；
2. 正式 WeChat 验证入口因 1 个格式文件失败；
3. 后端 Ruff 通过，但完整 pytest 为 **69 failed / 924 passed / 993 total**，其中 13 个失败来自审计前已存在的未跟踪发布脚本测试；
4. Alembic 源码图是单 head `20260728_70`，但这不证明任何历史升级路径成功；
5. 提交的 OpenAPI 生成类型已过期；
6. README、package scripts、统一验证脚本和 CI 的实际覆盖范围不一致；
7. 微信 CLI 预览被开发者工具 IDE 端口连接阻塞，尚未获得真实构建判定。

本摘要到此停止，不包含修复动作。
