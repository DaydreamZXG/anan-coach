# 服务器环境交接文档（101.96.198.253 · 一台机器双环境）

> **这篇文档存在的理由**：这台机器上「一个环境备份基线用、一个环境修修改改用」容易搞混。
> 2026-08-01 曾因搞错库，把 P12 图像指纹写进了生产库，白跑一轮还要回滚。
> 所有涉及这台服务器的操作，先读本页确认你连的是哪个库、哪个 API。

## 一、一句话拓扑

**一台服务器（101.96.198.253 / kg.xingguoxian.cn），跑两套隔离环境，各连各的库：**

| 环境 | 作用 | systemd 服务 | 端口 | APP_ENV | 数据库 | 题量 |
| --- | --- | --- | --- | --- | --- | --- |
| **生产** | 备份/基线用的稳定环境 | `gongkao-dazi-api` | 8000 | `production` | **`gongkao_dazi`** | **5.5 万**（canonical 层） |
| **Staging** | 修修改改的实验环境 | `gongkao-dazi-staging-api` | 8101 | `local` | **`gongkao_dazi_staging`** | **23.1 万**（导入的新题） |

**一句话记法：`_staging` 库 = 我们干活的地方；`gongkao_dazi` 库 = 权威基线，别乱动。**

## 二、关键事实（全部实测确认）

1. **23.1 万新题在 `gongkao_dazi_staging` 库**。导入脚本（`import_pp_questions.py` /
   `import_fenbi.py`）默认连这个库。所有导入/去重/打标签/挂图/指纹的活都在这里做。
2. **生产库 `gongkao_dazi` 只有 5.5 万 canonical 题**（`source_qbank` → `canonical_qbank`
   → `product` 分层，见 `ops.import_runs` 的 `canonical_qbank_v1_20260703`）。这是权威
   基线，生产 API 在读它。**不要往它里面写实验数据**。
3. **两个 API 共用同一 release 代码**（`releases/20260729T1208Z-qualification-297076ef7`），
   但 staging API 用 `WorkingDirectory=/srv/gongkao-dazi/staging/backend` + 独立 env，
   连 staging 库。
4. **连接串**：
   - 生产 API / importer：`/etc/gongkao-dazi/db.env`（`DATABASE_URL` 连 `gongkao_dazi`；
     `QBANK_IMPORT_DATABASE_URL` 也是生产库——**坑**，见 §四）
   - staging API：`/etc/gongkao-dazi/staging-db.env`（`DATABASE_URL` 连
     `gongkao_dazi_staging`，角色 `gongkao_staging`）
5. **备份未启用**：`gongkao-dazi-backup.timer` 是 disabled/inactive，`db-backups/` 为空。
   （global-backlog CR-OPS-001 已记录，发布前必须补。）

## 三、环境拓扑细节

```
101.96.198.253 (kg.xingguoxian.cn)
├── 生产环境（APP_ENV=production）
│   ├── gongkao-dazi-api.service      :8000
│   ├── gongkao-dazi-*-worker.service   (cause-agent / data-rights / external-learning / ilink …)
│   ├── /etc/gongkao-dazi/{api,db}.env
│   └── PostgreSQL: gongkao_dazi  ← canonical 5.5 万题（基线，只读权威）
└── staging 环境（APP_ENV=local）
    ├── gongkao-dazi-staging-api.service  :8101
    ├── gongkao-dazi-staging-cause-agent-worker.service
    ├── /etc/gongkao-dazi/staging.env + staging-db.env
    ├── /srv/gongkao-dazi/staging/backend  (独立活目录，非 release)
    └── PostgreSQL: gongkao_dazi_staging  ← 23.1 万题（我们导入/修改/实验的地方）
```

## 四、容易踩的坑（这条线踩过）

1. **`QBANK_IMPORT_DATABASE_URL` 指向生产库**。这个环境变量在 `/etc/gongkao-dazi/db.env`
   里，连的是 `gongkao_dazi`（生产，5.5 万题）。**回填指纹 / 写实验数据时，必须显式用
   staging 连接串**，不要沿用这个变量。P12 就是栽在这——7361 条指纹写进了生产库。
2. **staging 库的正确连接串**：`source /etc/gongkao-dazi/staging-db.env && export
   QBANK_IMPORT_DATABASE_URL="${DATABASE_URL//+psycopg/}"`。
3. **「门禁」只拦迁移不拦读**：`ops/release-db-compatibility.env` 的
   `ARTIFACT_DATABASE_MIGRATION_HEAD` 必须与 Alembic head 一致，否则 `alembic upgrade`
   报 `DatabaseCompatibilityError`。新增迁移后同步更新**本地和 staging 各一份**。读取
   （staging 角色读 product 表）畅通无阻，23.1 万题已实测全可读。
4. **staging 的 alembic 跑法**：
   `cd /srv/gongkao-dazi/staging/backend && source /etc/gongkao-dazi/staging-db.env &&
   DATABASE_URL=... alembic upgrade head`（alembic 在 release venv：
   `/srv/gongkao-dazi/releases/20260729T1208Z-qualification-297076ef7/backend/.venv/bin/alembic`）。
5. **SSH 限流**：这台机器对频繁 SSH 连接限流（Connection reset/closed）。合并命令、
   断连等 2-3 分钟、用后台循环重试。

## 五、题库数据现状（2026-08-01）

| 项目 | 生产库 gongkao_dazi | staging 库 gongkao_dazi_staging |
| --- | --- | --- |
| 题量 | 55,214（canonical） | 231,078（导入+去重后） |
| 模块 | canonical 6 模块 + 公基 | 标准 6 模块 + gongji 6.5 万 |
| 知识点标签 | canonical | 15.3 万题 |
| 图像资产 | 52,112 assets / 15,090 题 | 81,194 assets / 11,796 题（P14） |
| 视觉指纹 | **0（已清空还原）** | **7,361 题 / 7,361 条，四哈希齐全（P12）** |
| 指纹验证 | — | 精确匹配 30/30，近似匹配 75% |

## 六、常用命令速查

```bash
# 连生产库（只读查询）
sudo -u postgres psql -d gongkao_dazi

# 连 staging 库（我们干活）
sudo -u postgres psql -d gongkao_dazi_staging

# 看生产 API 健康
curl -s http://127.0.0.1:8000/health

# 看 staging API 健康
curl -s http://127.0.0.1:8101/health

# staging 库回填指纹（正确的连接串来源）
ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253 '
  set -a; source /etc/gongkao-dazi/staging-db.env; set +a
  export QBANK_IMPORT_DATABASE_URL="${DATABASE_URL//+psycopg/}"
  /srv/gongkao-dazi/backend/.venv/bin/python3 /tmp/generate_visual_fingerprints.py'
```

## 七、相关文档

- 本仓库 docs 里 P12 交接：[2026-08-01-question-bank-p12-handoff.md](../engineering/2026-08-01-question-bank-p12-handoff.md)
- 全局待办：[global-backlog.md](../engineering/global-backlog.md)
- 机器上的运营手册：`/srv/gongkao-dazi/staging/ops/RUNBOOK.md`（生产操作，不可乱改数据）
- 机器上的审计交接：`/srv/gongkao-dazi/staging/audit/HANDOFF-20260801-v0.md`
