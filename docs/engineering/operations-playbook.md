# 操作备忘（Operations Playbook）

> 活知识：本文件是"怎么做"的唯一汇总，会随工具链演化持续更新。历史交接文档里的
> 操作片段（如 `2026-07-26-session-handoff.md` §四）已并入此处，交接文档只作历史追溯。
> 产品和系统边界见 `docs/product/anan-product-system-spec.md`；待办账本见
> `docs/engineering/global-backlog.md`，其中状态使用前必须按当前代码与环境重验。

## 1. 前端门禁（微信小程序）

在 `apps/wechat/` 下运行：

- **`npm run check`**：一键全链门禁——prettier / typecheck / eslint / tokens+design+interactions+controls+navigation+structure+visual+assets+package 各验证 / API 生成一致性 / 测试。这是提交前必须全绿的基线。
- **`npm run test` / `npm run test:node` / `npm run test:cloud`**：单测、node 测试、云函数测试。
- **vitest**：约 491 项（随功能增加）。配合 `npm run test` 使用。
- 后端配套：`backend` 下 `.venv/bin/python -m pytest -k "xingce or mock"`（约 93 项）。
- `design-file.tokens.wxss` 哈希锁定：**不要对整个仓库跑 `prettier` glob**——会碰坏它。
  只 prettier 明确改过的文件。

## 2. 视觉套件与设备基线

- 认证机型：**iPhone 12/13 mini**（375×812、3x、基础库 3.17.0）。设备配置不符时视觉检查会提前失败。
- 设备切换：写 `WeappLocalData` 两个 localstorage 的 `device.current`。
- **DevTools 单实例互斥**：连接死循环时 `pkill` + `cli auto --auto-port 9420` + 等端口 + 20s 再连。
- c10 场景天生慢（90s 预算）。
- `--update` 是唯一基线权威。
- 视觉基线的历史与成品见 `apps/wechat/DESIGN_SPEC.md` 及设计文档。

## 3. 冒烟与真实后端验证

- 冒烟脚本：`apps/wechat/e2e/run-real-backend-smoke.mjs`。真实后端冒烟基线曾达
  45 pass / 15 degraded / 0 fail；degraded 多为 B/C 类正常降级（A 类 404 已复核为误判）。
- 真实后端 E2E 缺少专用 token / 用户 / 写入确认即拒绝执行；写入类检查还需要第二重确认。

## 4. 生产库只读审计

- 走 `scripts/server_import_product_knowledge.sh` 的 ssh 约定（只读，不对生产写数据）。
- 视觉指纹等数据审计：`2026-08-01-question-bank-p12-handoff.md` 里有当时的方法与结论，
  但**指纹在 staging 库（gongkao_dazi_staging），不在生产库（gongkao_dazi）**，别查错库。

## 5. 部署与发布（运行侧）

- 运行手册：`ops/RUNBOOK.md`。发布对象唯一性由 manifest + 签名 + 验签链保证，
  激活与回滚都重新解析 current、重新验签、复核 DB 兼容性，避免调用端验签后的替换窗口。
- 发布候选数据（`DATA_RELEASE_RECEIPT` 及签名）必须由数据发布成功后交给
  `ops/scripts/release_staging.sh`；微信证据缺一即拒绝。
- `activate_release.sh` 不执行 migration / 业务写入；它只验证并切换控制面与 runtime env。
- **生产备份目前仍未启用**（`BACKUP_ENABLED=true` + timer 未开），这是 T2 上线阻塞，
  见 `global-backlog.md` §1/§3。
- 迁移链唯一 head：以 `backend/migrations/versions/` 最新文件为准（发布契约已解钉）。

## 6. AI 与外部服务

- 错因 AI：方舟 Ark `ark-code-latest`（Auto），OpenAI 兼容端点 `https://ark.cn-beijing.volces.com/api/plan/v3`。
- fail-closed 守卫：商业数据发布权威 / PII 出口 / 对象归属 / 版本不可变（触发器等）。
  遇到 403/409/429 先怀疑守卫在拦，别当 bug。
- 契约增量制度：所有跨端字段改动标注 `v1/v2`，旧数据缺省合法，前后端必须同源（OpenAPI 生成类型）。

## 7. 产品与 UI 文案

- 产品边界：`docs/product/anan-product-system-spec.md`。
- UI 体系规范：`apps/wechat/DESIGN_SPEC.md`（青色主系、灰阶 4 档、字号 13 档、按钮五档、
  validate-tokens 退役规则防回流）。
- 岸岸声音：`docs/design/2026-07-26-anan-voice-guide.md`（文案/语气参考）。
