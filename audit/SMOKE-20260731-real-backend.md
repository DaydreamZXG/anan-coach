# 60 路由真实后端冒烟 · 首轮审计记录（2026-07-31）

## 目标与方式

- 验证对象：本地分支 `codex/commercial-selection-repair` HEAD（da2b3d14b，未推送的 10 个提交），
  而非生产服务器上的老版本后端。
- 方式：微信开发者工具 automator（自动化端口，经 `/v2/auto` HTTP API 开启）驱动小程序，
  逐一 reLaunch `page-interaction-matrix.mjs` 全部 60 个注册路由，
  **不 mock 任何网络**，页面真实请求本地新版后端。
- 执行脚本：`apps/wechat/e2e/run-real-backend-smoke.mjs`（本轮新增，保留）。
- 完整报告：`apps/wechat/e2e/real-backend-smoke-report.json`。

## 冒烟环境（已回收，可按下述步骤复建）

1. 独立 Postgres 集群：`initdb -D .smoke/pgdata`，`pg_ctl -D .smoke/pgdata -o "-p 5433 -k $PWD/.smoke" start`，
   建库 `gongkao_dazi_smoke`（数据目录保留在 `.smoke/`，服务已停）。
2. schema：先 `psql -f ops/sql/provision_worker_heartbeat_roles.sql`（sysusers 角色），
   再复用 `backend/tests/conftest.py::_reset_test_schema`（fixture DDL + alembic stamp/upgrade 到 head，
   与 141 测试套件同款 schema）。
3. 种子：最小账号 user_id `b0000000-0000-0000-0000-000000000001`、
   会话 token 明文 `smoke-session-token-001`（sha256 入库 `learner.app_sessions`）。
   注意 `seed_demo_account.sql` 在 head schema 下违反 "target_score 必须绑定目标职位" 约束，不能直接用。
4. 发布权威：按 `tests/test_commercial_data_publication_protocol.py::_activate` 同款流程
   （`ops/scripts/commercial_data_publication.py` 的 start → receipt → finish，需持 advisory lock），
   否则连接池 checkout 守卫对一切业务请求抛 500。
5. 后端：`APP_ENV=local DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/gongkao_dazi_smoke
   .venv/bin/python -m uvicorn app.main:app --port 8100`（`APP_ENV=smoke` 会触发 commit 门禁，起不来）。
6. 前端临时改动（均已还原）：`environment.ts` apiBaseUrl → `http://127.0.0.1:8100`；
   `project.config.json` 与 `project.private.config.json` 的 `urlCheck` → false
   （两处都要改，且 DevTools 需重开项目窗口才生效；首次冷启动会有信任弹窗需人工点掉）。

## 结果：13 pass / 19 degraded / 28 fail（60 路由，后端收到 63 个真实请求）

状态码分布：200×34、409×16、404×5、503×3、500×3。

### 1. 真实缺陷（go/no-go 级，待修）

- `GET /api/v2/positions/majors` → 500：`psycopg.errors.UndefinedTable:
  relation "product.major_categories" does not exist`。
  代码引用了该表但迁移链（fixture 基线 + head 迁移）从未创建它；141 测试也未覆盖此查询。
  影响路由 `/pages/positions/filter/index`。

（另 2 个 500 为发布权威激活前的手动探测，属预期，非缺陷。）

### 2. degraded ×19（页面 error 态，降级渲染本身正常）

- `409 revision_conflict` ×16：plans/learning-brain/retake/qualification/positions-workspace 系列。
  冒烟账号缺计划修订基线数据，属数据前提问题，待补种子后复测。
- `503 service_unavailable` ×3：community-method-cards、mock-exams/weekly。
  依赖冒烟环境未启用的服务，待确认是预期依赖缺失还是配置缺口。

### 3. fail ×28（"stuck loading"，大部分疑似误判）

- 判定器只识别 `status/state/loading` 数据字段；terms、support、login、onboarding/* 等
  静态/表单页无此类字段，被误判为卡死。后端仅收到 63 个请求也印证多数 fail 页面本就不发网络请求。
- **待办**：按交互矩阵各页的 readiness 信号细化判据后复跑，得出真实 fail 数。

## 第二轮修复与复跑（同日）

### 修复项

1. **迁移缺口已修**：新增 `backend/migrations/versions/20260731_89_major_catalog_tables.py`，
   幂等收编 `product.major_categories / major_sub_categories / major_catalog` 三表
   （DDL 与 `scripts/major_catalog_schema.sql` 一致，修正 pg_trgm 扩展语句顺序），
   空表时灌入 `scripts/major_catalog_seed.sql` 权威种子（14 门类 / 105 专业类 / 742 专业）。
   downgrade 为 forward-only。配套更新 `ops/release-db-compatibility.env`
   迁移头契约至 `20260731_89`。回归测试 `backend/tests/test_major_catalog.py`（2 passed，ruff 干净）。
2. **判据升级**：`run-real-backend-smoke.mjs` 改为三层就绪判据
   （状态合同 → 交互矩阵 selector → 通用 DOM 渲染），静态/表单页不再误判。
3. **409 根因**：非"修订冲突"，而是 `PlanningContractError`——
   种子 profile 缺 `study_mode`（入学时间表判定字段）。补 `study_mode='fulltime'`
   等字段后 `plans/today` 200。
4. **域名拦截根因**：`project.config.json` 的 `urlCheck` 字段对模拟器不生效，
   必须在 DevTools「详情 → 本地设置」勾选「不校验合法域名…」（GUI 级设置）。

### 第二轮结果：45 pass / 15 degraded / 0 fail

- 报告：`apps/wechat/e2e/real-backend-smoke-report-round2.json`；
  日志：`smoke-run-round2.log`。
- 15 个 degraded 全部为页面 error 态（降级渲染正常），对应两类后端问题：
  1. **前后端合同缺口（新发现，待修）**：前端调用了后端不存在的端点（404）：
     `GET /api/v2/mock-exams/weekly`、`GET /api/v2/onboarding/ability-baseline`、
     `GET /api/v2/onboarding/position-preference`、`GET /api/v2/onboarding/study-preference`、
     `GET /api/v2/reporting/reports/mock`。影响 mock-exam/mock-report/onboarding 相关页面。
  2. **503 service_unavailable ×3**：`community-method-cards`、`mock-exams/weekly(v1)`，
     依赖冒烟环境未启用的服务，需确认预发布环境是否具备。
  - 其余 degraded（essay 系列、ability、error-pattern 等）为冒烟账号缺业务数据
    （作文任务、能力评估等），页面 error 态属预期降级。
- 结论：60 路由无一"卡死/崩溃"；唯一 go/no-go 级代码缺陷（majors 500）已修复并验证；
  新增待办为上述 5 个 404 合同缺口的归属判定（前端调错 vs 后端漏实现）。

## 遗留物清单

- 保留：`apps/wechat/e2e/run-real-backend-smoke.mjs`（冒烟脚本）、
  两轮报告与日志（`real-backend-smoke-report*.json`、`smoke-run*.log`）、
  `.smoke/`（已停的冒烟库数据目录，含已迁移到 20260731_89 的库）、
  新迁移 `20260731_89_major_catalog_tables.py`、回归测试 `test_major_catalog.py`、
  契约更新 `ops/release-db-compatibility.env`。
- 已删除：probe-network.mjs、smoke_bootstrap_db.py、smoke_seed_account.sql、
  smoke_activate_publication.py、smoke_debug_today.py、backend/smoke-*.log、devtools-auto.log。
- 已还原：apiBaseUrl、两处 urlCheck。
