# 知识图谱发布清单完整性取证

时间：2026-08-15（Asia/Shanghai）

范围：只读检查服务器 `101.96.198.253` 上当前 `20260809_104` 数据库与既有数据库备份；未修改生产数据、发布指针或服务状态。

## 结论

当前 `product.knowledge_map_nodes` 中的 2,462 条“孤儿”不是未知批次，也不应直接删除。它们全部属于迁移 `20260727_65` 明确定义的历史发布：

- `release_id`: `86064dad-c59f-1939-e8d6-1076e75ef9a3`
- `release_version`: `knowledge-map@legacy-pre-20260727-65:guokao:xingce`
- `exam_scope / track`: `guokao / xingce`
- 节点数：2,462
- 节点指纹：`6012a30ea3b90ce00b11b0e13d758bbf6d5cfdad1515ba1d497a654cbcd94a0e`

该 `release_id` 精确等于 `md5('knowledge-map-legacy|guokao|xingce')::uuid`。当前缺失的是 `product.knowledge_map_releases` 中的一条父发布记录，以及首个正式发布 `f4e8d3e7-fc73-4886-8d8d-8fa256b05b0b` 的 `supersedes_release_id` 历史链路。

## 当前数据库事实

- Alembic：`20260809_104`
- `knowledge_map_nodes`: 4,305
- `knowledge_map_releases`: 3
- 不满足 `(release_id, exam_scope, track)` 父发布外键的节点：2,462
- 孤儿节点只有一个 `release_id`，全部为 `guokao/xingce`，创建与更新时间均为 `2026-07-03 21:22:15.517278+08`
- 2,413 条包含父节点引用，父子关系内部完整，缺失父节点数为 0
- 49 个模块；节点状态全部为 `published`
- 该历史发布没有题目绑定、外部拍题 intake、外部错题或方法卡引用
- 与后继发布共享 563/573 个稳定 node ID，说明它是后继知识图谱的历史来源，而非另一套活动图谱
- 当前活动发布仍是 `198bae5c-6008-44dc-97a2-b2f97284368e`（v5，640 节点、158,366 题目绑定）；运行时查询通过发布表 join，只会消费活动发布，不会把旧发布误投放给用户
- 外键元数据错误地标记为 `convalidated=true`，相关 FK trigger 处于启用状态，但真实行违反约束；这会让逻辑备份在恢复外键时失败

## 跨备份证据

### 2026-07-29 两份备份

以下备份都保存了完整历史父发布和首个后继链路：

- `/var/backups/gongkao-dazi/pre-20260728_74-20260729T041614Z-db74-bridge.dump`
- `/var/backups/gongkao-dazi/pre-user-reset-20260729T043130Z.dump`

父发布的完整原值为：

| 字段 | 原值 |
|---|---|
| release_id | `86064dad-c59f-1939-e8d6-1076e75ef9a3` |
| release_version | `knowledge-map@legacy-pre-20260727-65:guokao:xingce` |
| source_data_version | `legacy:source-version-unknown` |
| vocab_version | `legacy:vocab-version-unknown` |
| generator_version | `legacy:generator-version-unknown` |
| source_fingerprint | `6012a30ea3b90ce00b11b0e13d758bbf6d5cfdad1515ba1d497a654cbcd94a0e` |
| generated_at / fresh_until | `2026-07-03 21:22:15.517278+08` |
| status / node_count | `retired` / `2462` |
| activated_at | `NULL` |
| retired_at | `2026-07-27 18:40:16.983459+08` |
| supersedes_release_id | `NULL` |
| question_binding_count / fingerprint | `0` / `NULL` |

同一备份中，首个正式发布 `f4e8d3e7-fc73-4886-8d8d-8fa256b05b0b` 的 `supersedes_release_id` 正是上述旧发布 ID。

### 2026-08-05 与 2026-08-12 备份

- `/srv/gongkao-dazi/db-backups/pre-deploy-20260805_181235.dump` 已缺失旧发布父记录，同时首个正式发布的 `supersedes_release_id` 已变成 `NULL`
- `/srv/gongkao-dazi/question-bank-recovery-20260812/pre-source-identity-20260812_2345.dump` 仍保持这一缺口

因此缺口发生在 2026-07-29 备份之后、2026-08-05 18:12 备份之前。现有证据能恢复精确数据，但尚不能证明是哪条历史运维命令删除了父记录并断开链路。

## 修复边界

最小安全修复应是前向、幂等、失败关闭的迁移：

1. 只在 2,462 个节点的身份、数量、指纹、时间、scope、track 与备份证据全部精确匹配时继续。
2. 恢复备份中的一条历史 `retired` 发布清单原值，并把首个正式发布的 `supersedes_release_id` 恢复为该发布。
3. 不改任何节点、题目绑定、活动发布头、题目、用户作答、计划、错因、掌握或前端行为。
4. 重新创建并验证 `product_knowledge_map_nodes_release_scope_fkey`，不能只相信现有 `convalidated=true` 元数据。
5. 在当前 104 逻辑备份副本上完成 105→新 head 演练，再从修复后副本重新导出并零错误恢复到第二个空数据库。
6. 本单元不授权生产执行；生产迁移、制品发布、服务重启与活动指针切换需要单独发布门禁。

## 未知项

- 具体是哪次历史操作绕过外键与发布不可变触发器，现有日志尚未定位。
- 这不阻止精确恢复，因为父发布的全部字段与后继链路均由两份独立 7 月 29 日备份、迁移 65 的确定性规则和当前节点指纹交叉证明。
