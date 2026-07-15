# 岗位目标分统一契约

## Owner 与公开契约

`positions.PositionTargetScoreOwner` 是正式岗位目标分的唯一 owner。`GET /api/v2/positions`、岗位对比、`GET /api/v2/positions/{positionId}`、`GET /api/v2/positions/{positionId}/target-score` 和主目标影响预览只能使用 owner 返回的同一 `TargetScoreRange`：

- `lowerBound` / `recommended` / `upperBound`；
- `sampleCount`、`basisLevel` 和 `evidenceLevel`；
- `generatedAt` 和非空 `validUntil`。

列表和详情在证据不足时返回 `targetScore: null`，不用虚构数值表示 unavailable。主目标预览必须有可用目标分证据，否则拒绝写入。

## 版本和有效期策略

目标分是“年度岗位数据发布”的派生结果，不是请求时刻的滑动缓存：

- `generatedAt = active position release.activated_at`；
- `validUntil = generatedAt + 365 天`；
- 同一 `ruleSetVersion + positionDataVersion` 在任意读取面观察到完全相同的生成时间和有效期；
- 365 天覆盖一个完整国考岗位年度周期。超过该边界仍未激活新发布时，系统必须停止输出目标分，不延长旧证据。

主目标预览会同时校验资格快照的 `ruleSetVersion` 和 `positionDataVersion`，不允许把旧资格结论与新目标分拼接。

## 稳定失败边界

| 错误码 | 含义 | 客户端动作 |
| --- | --- | --- |
| `position_release_unavailable` | 没有已激活的对应国考岗位发布 | 停止展示决策结论 |
| `target_score_basis_expired` | 发布派生目标分已超过 365 天，或存量 JSON 早于正式契约 | 刷新或重新生成预览 |
| `position_data_version_conflict` | 资格快照、岗位或目标分不属于同一发布 | 刷新资格与岗位数据 |
| `target_score_unavailable` | 真实进面分样本不足 | 不允许设为主目标 |

## 历史契约边界

旧 `position_goal_previews.target_score` 和 `position_goal_events.command_payload.targetScore` 可能没有 `basisLevel` / `generatedAt`。系统不根据分数或置信等级猜测 L1–L4，也不用当前请求时间伪造生成时间。这些存量预览和历史命令重放会稳定返回 `target_score_basis_expired`；已确认目标的历史记录保留，但客户端必须基于当前 active release 重新生成预览。
