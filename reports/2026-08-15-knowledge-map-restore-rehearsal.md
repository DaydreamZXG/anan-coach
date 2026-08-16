# 知识图谱发布清单修复与二次逻辑恢复演练

时间：2026-08-15（Asia/Shanghai）

发布单元：`RU-KNOWLEDGE-MAP-RESTORE-CLOSURE-024`

范围：本地隔离 PostgreSQL 16；输入为从服务器只读导出的真实 `20260809_104` 逻辑备份。未修改服务器数据库、发布指针或服务。

## 输入

- 原始 104 dump：`/Users/zxg/Downloads/gongkao-dazi-104-rehearsal-20260814.dump`
- 大小：869 MiB
- SHA-256：`2c424bdb4a5323390edf9247b1d01623c2d97db54240aadcee942d792c4c0fb9`
- 原数据库核心基线：233,696 题、4,305 个知识节点、3 条发布记录、2,462 个 release-scope 孤儿

## 第一次恢复：复现缺口

将原始 dump 恢复到空数据库 `source104`：

- `pg_restore` 退出码：1
- 恢复尾部报告：`errors ignored on restore: 2`
- 精确对象对账确认实际缺失对象只有一个：`product_knowledge_map_nodes_release_scope_fkey`
- 失败原因：2,462 个节点引用 `86064dad-c59f-1939-e8d6-1076e75ef9a3 / guokao / xingce`，但父发布不存在
- 原始日志：`/Users/zxg/Downloads/anan-024-first-restore.log`

恢复命令没有使用 `--exit-on-error`，是为了保留除该末尾外键外的完整 104 副本，供前向迁移修复；该次失败不被当作成功证据。

## 104 → 113 前向迁移

在 `source104` 执行完整 Alembic 链：

`20260809_104 → 105 → 106 → 107 → 108 → 109 → 110 → 111 → 112 → 20260815_113`

迁移 113 的结果：

- Alembic head：`20260815_113`
- release-scope 孤儿：2,462 → 0
- 历史父发布：0 → 1
- 首个正式发布 `f4e8d3e7-fc73-4886-8d8d-8fa256b05b0b` 的 `supersedes_release_id` 恢复为 `86064dad-c59f-1939-e8d6-1076e75ef9a3`
- release-scope FK 被物理重建并 `VALIDATE`，`convalidated=true`
- 4 个 FK trigger 全部启用
- 非内部 disabled trigger：0
- 题目：233,696（不变）
- occurrence：336,244（不变）
- 内容版本 / 当前头：238,038 / 232,660（不变）
- 知识节点：4,305（不变）
- 知识发布：3 → 4（只恢复一条备份原值）
- 知识题目绑定：416,141（不变）
- 复盘 / mastery / 作答 session：38 / 5 / 34（不变）
- 活动发布仍为 v5：640 节点、158,366 题目绑定，发布 ID 与全部清单字段不变

恢复后的旧发布行逐字段等于两份 2026-07-29 备份，节点指纹仍为：

`6012a30ea3b90ce00b11b0e13d758bbf6d5cfdad1515ba1d497a654cbcd94a0e`

## 修复后 dump

- 输出：`/Users/zxg/Downloads/gongkao-dazi-113-repaired-rehearsal-20260815.dump`
- 大小：869 MiB
- SHA-256：`2528394fbfb43618b648189d34da87f7131c7dc555d894c7b05ae11fc8a87895`

## 第二次恢复：硬门通过

将修复后的 113 dump 恢复到第二个空数据库 `target113`：

- 使用 `pg_restore --exit-on-error --jobs=4 --no-owner --no-acl`
- 退出码：0
- stderr/stdout 日志大小：0 字节
- 日志：`/Users/zxg/Downloads/anan-024-second-restore.log`

第二空库与 `source104` 的规范化事实文件逐行比较：

- `diff` 退出码：0
- 源事实：`/Users/zxg/Downloads/anan-024-source104-facts.txt`
- 目标事实：`/Users/zxg/Downloads/anan-024-target113-facts.txt`
- 比较覆盖 revision、题目、occurrence、内容版本/头、节点、发布、绑定、复盘、mastery、session、孤儿、旧发布原值、后继链、活动发布、外键定义/验证状态、trigger 启用状态和未验证约束总数
- FK trigger 的 PostgreSQL 内部名字含对象 OID，因恢复后必然变化；比较使用 4/4 数量与启用状态，不把内部名字当业务身份

## 自动化回归

- `backend/tests/test_knowledge_release_manifest_repair.py`
- `backend/tests/test_migrations.py`
- `backend/tests/test_private_question_books.py`
- 结果：34 passed，0 failed，耗时 187.30 秒

专项测试覆盖：精确修复、证据不符失败关闭、健康重放幂等、冲突父/后继拒绝、事务锁并发、外键重建/验证与坏写入拒绝。

## 清理与保留

- 本地 PostgreSQL 临时实例已停止。
- 12 GiB 临时数据目录已移动到 `/Users/zxg/.Trash/anan-pg-rehearsal-20260815-024`，可恢复；需要释放磁盘时由用户清空废纸篓。
- 原始 104 dump、修复后 113 dump、两次恢复日志和源/目标事实文件保留在 Downloads。

## 结论与边界

024 已在真实 104 数据副本上证明：精确恢复历史发布清单可以关闭原逻辑恢复错误，并且修复后的完整 dump 能在第二空库零错误恢复。该证据不等于生产已迁移；生产仍是 `20260809_104`，生产升级、应用制品部署、服务重启和发布指针切换仍需单独发布门禁与现场备份。
