# 2026-08-01 题库地基大会话压缩交接（P12 图像匹配已完成）

> 来源会话:7151+ 条消息（「安装 MCP」会话），已因上下文超限跑过 /compact 仍再爆，
> 故人工压缩。**大部分成果已固化在 `docs/engineering/global-backlog.md`，本文只记「文档没有、
> 但对话里有、丢了会断」的增量**。读本文前先看 global-backlog §2/§3 与 §商业题库地基。

## 一、这条线已经做完了（全部提交，global-backlog 已记）

商业题库地基 7 项全绿，分支 `codex/commercial-selection-repair` 领先 main 30+ 提交：
OCR adapter、双源导入（sydw 118k + fenbi 104k）、去重压实（删 4.7 万）、知识点标签
（15.3 万题 / 247 个）、模块映射归一（16.9 万题挂标准 6 模块）、公基分离（6.5 万独立
gongji）、来源脱敏（fenbi:→an:，58k 题 + 46k occurrence）。

**不要再去 peipeishua 服务器搬重资产了**——这条线开头的决策已经翻篇：图像匹配主路径
定为「文字匹配（OCR→MatchingService）」，图像匹配只兜底 5% 纯图题（图形推理/图表）。
最终结论是**纯本地做，不上服务器搬 ORB/SSCD/FAISS 重型索引**。

## 二、P12 已全部完成（2026-08-01 当天收口）

### 交付物（全部已提交）

| 文件 | 内容 |
| --- | --- |
| `backend/app/domains/external_ingestion/recognition/visual_hash.py` | 感知哈希族 phash/dhash/ahash/edge_dhash，纯 PIL 无 opencv/numpy 依赖，64 位 hex + fingerprint_sha256 + hamming_distance + similar() |
| `backend/scripts/generate_visual_fingerprints.py` | 回填脚本：带资产+content_version 的题生成指纹，`distinct on question_id` 每题一条，路径去省份前缀 |
| `backend/migrations/versions/20260801_90_visual_fingerprint_hashes.py` | 迁移：指纹表加 phash/dhash/ahash/edge_dhash 四列（16-hex，null 保护） |
| `ops/release-db-compatibility.env` | 契约更新到 head `20260801_90` |

### 关键工程决策（踩坑后修正）

1. **edge_dhash 性能 bug**：原实现全尺寸原图上跑纯 Python Sobel，700×1004 图 3.5s/张，
   52k 张要 ~25 小时。修正为「先缩到 2 倍目标尺寸再提边缘（FIND_EDGES）再缩到目标」→
   1ms/张，且缩放鲁棒性反而更好（50% 缩放汉明 9-10，全尺寸 Sobel 是 15-18）。
2. **每题一条指纹**：最初对每个代表资产插一行 → 同题多图产生多条。改为 `distinct on
   (question_id)` 只取每题第一张代表资产。
3. **精确 sha256 → 四哈希相似度**：指纹表原来只有合成 sha256（精确匹配，拍照必失配）。
   加四哈希列，支持汉明距离近似匹配。

### 验证结果（staging 实测）

- **写入 7361 条指纹，7361 题**，四哈希齐全，`phash/dhash/ahash/edge_dhash` 各 16-hex。
- **精确匹配 30/30**：图 → 4 哈希 → sha256 → 精确查库 → 命中原题。
- **近似匹配（组命中率）75%**（20 题 × 8 种拍照变换）：
  | 变换 | 命中率 |
  | --- | --- |
  | 亮度/对比度 | 100% |
  | 放大 1.4x | 95% |
  | 缩小一半 | 90% |
  | 噪声 | 90% |
  | 模糊 | 75% |
  | 裁边 5% | 35% |
  | 旋转 3° | 15% |
  - **结论**：缩放/明暗/噪声鲁棒（拍照最常见的变换），旋转/裁剪是感知哈希的已知短板。
    真实「拍照找原题」场景，图像匹配是纯图题的**兜底**，主路径仍是 OCR→文字匹配。

### 两个发现（重要）

1. **7361 个指纹只有 4717 个唯一 phash，2644 个重复**——有 884 道题共享同一张图。
   这暴露 canonical_qbank 里存在**同图题未去重**（同图配不同题干，或导入时漏去重）。
   是否处理待产品判断（对用户场景，命中任一重复版都算找对，问题不大）。
2. **指纹覆盖只有 7361/15028**：能生成指纹的题（有 assets+content_version）是 15028，
   但只有 7361 的文件实际存在于 staging `files/` 下——**7667 个文件缺失**。原因是 P14
   迁移时只传了 28,392 张被引用图，而 representative assets 的 image_src 更多。这是
   数据缺口，补文件或接受现状由产品决定。

### 尚未做的（明确留给后续）

- **pp_/an: 新导入题没有 content_version**（0 条），无法生成指纹。给带图新题补内容版本
  是独立工程（对话判定「比图像匹配本身大」），**未做**。补上后能大幅提升指纹覆盖。
- **没有正式 API/服务**把「拍照图 → 匹配」串成接口——目前是脚本验证，未接进后端路由。
- **视觉匹配没接进 MatchingService**：`matching/fingerprint.py` 的 `visual_similarity`
  是 256 位 dHash 语义，与新的四哈希（各 16-hex）不同，需对齐后才能用。

## 三、SSH 操作教训（这条线反复踩）

- staging 服务器 101.96.198.253 对**频繁 SSH 连接限流**（Connection reset/closed）。
  缓解：**合并成单条命令减少连接次数**，断连后等 2-3 分钟再试，或用后台循环重试。
- staging 文件结构：`files/<试卷名>_images/xxx`（**无省份前缀**）；image_src 带省份前缀，
  拼接时要 `image_src.split("/",1)[1]`。
- staging 后端 venv 在 `/srv/gongkao-dazi/backend/.venv`（有 psycopg，需装 pillow 才有 PIL）。
- 连接串在 `/etc/gongkao-dazi/db.env` 的 `QBANK_IMPORT_DATABASE_URL`（gongkao_importer，
  对 product 表有写权限；`question_content_versions`/`question_visual_fingerprints` 的权限
  是本次手动 grant 的）。
- staging 的 `alembic_version` 停在 `20260729_76`，但 major_catalog 等表手动建过（89 之前
  就这样）——**staging 迁移链与代码 release 漂移**。本次表结构改动是**直接 SQL 加列**，
  没走 alembic；生产由正式迁移 20260801_90 管。

### ⚠️ 环境拓扑（2026-08-01 修正，必读）

这台机器 **101.96.198.253 上跑着两套隔离环境**，连不同库：

| 环境 | systemd 服务 | 数据库 | 题量 |
| --- | --- | --- | --- |
| **生产** | `gongkao-dazi-api`(8000) | `gongkao_dazi` | **5.5 万**(canonical 层) |
| **Staging** | `gongkao-dazi-staging-api`(8101) | `gongkao_dazi_staging` | **23.1 万**(导入的新题) |

- **23.1 万新题在 `gongkao_dazi_staging` 库**（导入脚本默认连这个库）。
- `gongkao_dazi` 是生产库，只有 5.5 万 canonical 题。**P12 的指纹最初误写进了生产库**
  （因为用了 `QBANK_IMPORT_DATABASE_URL`=`gongkao_dazi`），已修正：生产库清空并还原表结构，
  指纹在 staging 库（`gongkao_dazi_staging`）重新生成。
- **指纹现在在 staging 库**：7361 条/7361 题，四哈希齐全，精确匹配 30/30 验证通过。
- **「门禁」真实存在但只拦迁移，不拦读**：`ops/release-db-compatibility.env` 的
  `ARTIFACT_DATABASE_MIGRATION_HEAD` 必须与 Alembic head 一致，否则 `alembic upgrade`
  会被 `DatabaseCompatibilityError` 拦截。新增迁移后要同步更新该文件（本地和 staging 各一份）。
  读取（`gongkao_staging` 角色读 product 各表）畅通无阻，已实测 23.1 万题全可读。

### 操作备忘（staging 库相关命令）

- staging API 连接串：`/etc/gongkao-dazi/staging-db.env` 的 `DATABASE_URL`（`gongkao_staging` 角色）。
- staging 库跑 alembic：`cd /srv/gongkao-dazi/staging/backend && source /etc/gongkao-dazi/staging-db.env && DATABASE_URL=... alembic upgrade head`
  （release venv 有 alembic：`/srv/gongkao-dazi/releases/20260729T1208Z-qualification-297076ef7/backend/.venv/bin/alembic`）。
- 指纹回填（staging 库）：`source /etc/gongkao-dazi/staging-db.env && QBANK_IMPORT_DATABASE_URL="${DATABASE_URL//+psycopg/}" python3 generate_visual_fingerprints.py`。

## 四、下一步（接手者从这里继续）

0. **先执行 qbank-v1 收口（代码已完成，尚未写 staging）**：先把本次迁移和
   `ops/release-db-compatibility.env` 随同同一 staging 代码版本部署；再依次运行
   `finalize_question_bank.py audit`、`apply`、`verify`。该程序只接受
   `*_staging` 数据库，只追加来源 occurrence / 内容版本并归一模块，不删除题目。
   本地隔离 PostgreSQL 已验证：同源重放不重复、单题失败不回滚整批、合格题可生成当前内容版本。
   **不得**把本地脚本直接拷到当前 staging 目录后执行：该目录的代码身份与仓库不一致，先形成一个
   可追溯的 staging 代码版本。
1. **P15（OCR 真实配置验证）**：staging 启用 MinerU，真实照片「拍照 → OCR → 文字匹配」——
   这是「拍照找原题」的**主路径**，与 P12 图像匹配同链条。优先级高于图像匹配深化。
2. 决定两个数据缺口怎么处理：①7667 个缺失文件是否从本地补传；②是否给 pp_ 新题补
   content_version（大幅提升指纹覆盖）。
3. 若图像匹配要接产品：把四哈希汉明匹配接进 MatchingService，暴露接口。
4. 同图题去重（884 题共享一张图）——产品判断是否需要处理。

## 附：对话里其他已记录、不展开的

- 23 万题量是**全量题池**（20.6 万客观 + 2.4 万判断），判断题去重只删了 201 题（判断句重复少，
  无需专门去重）；商业化的正确姿势是「精选子集上线」而非物理删减，对话里已与用户对齐。
- 「知识库用不用 Obsidian」问题已答：不用，走数据库索引分层（PG B-tree/GIN + matching 引擎 +
  可选向量），模块标签正是让「按模块刷题」走索引不扫全表的关键。
