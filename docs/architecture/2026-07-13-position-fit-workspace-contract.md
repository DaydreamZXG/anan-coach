# 岗位工作台与确定性匹配契约

## 1. 读取边界

正式岗位工作台只由以下四类同版本事实组成：

- 当前已激活的国考岗位发布；
- 学习者当前 `learnerProfileVersion` 下的硬资格判断；
- `positions.PositionTargetScoreOwner` 返回的当前目标分区间；
- 同一考试年度、包含行测和申论、状态为 `complete` 且未过期的正式能力估分。

`GET /api/v2/positions/workspace` 返回这组事实的
`examYear + ruleSetVersion + positionDataVersion + learnerProfileVersion`，以及一次
读取的 `abilityEstimate`。分页中的所有岗位使用同一个版本上下文和同一份能力
估分，不能逐项选择不同的估分或岗位发布。

`GET /api/v2/positions/{positionId}/fit` 返回单岗位正式匹配；
`GET /api/v2/positions/{positionId}` 也内嵌同一 `abilityEstimate + fit`，详情页
不能另算一套结论。

## 2. 证据状态与 band

`fit.evidenceStatus` 只有四种状态：

| 状态 | 条件 |
|---|---|
| `unavailable` | 无正式能力估分、估分考试年度不一致，或目标分没有真实样本 |
| `partial` | 当前正式估分只有行测或申论其中一科 |
| `stale` | 当前正式估分已过有效期 |
| `ready` | 同年度、双科完整且未过期的能力估分与可用目标分同时存在 |

非 `ready` 状态下，`status=evidence_unavailable`，`band` 和 `scoreGap` 必须为
`null`，并通过 `evidenceReasonCodes` 说明缺失、单科、过期、年度不一致或目标分
不足。系统不得用旧行测 diagnostic、单科成绩或历史目标分补齐结论。

`ready` 时的 `band` 与 `status` 相同，只按区间边界确定：

```text
conservative : ability.total.lower >= target.upperBound
below_target : ability.total.upper < target.lowerBound
overlap      : 其余所有区间相交情况
```

这里没有胜率、录取概率、AI 置信概率或隐藏阈值。

## 3. 分差和要求负担

`scoreGap` 是分数区间，方向固定为目标减当前：

```text
lower       = target.lowerBound - ability.total.upper
recommended = target.recommended - ability.total.recommended
upper       = target.upperBound - ability.total.lower
unit        = points
direction   = positive_means_target_above_current
```

`requirementsBurden` 只返回可由资格规则重放的事实：

- `additionalExamRequired`；
- `pendingRequirementCount` 与排序后的 `pendingRequirementIds`；
- `hardConditionNotMetCount` 与排序后的
  `hardConditionNotMetRequirementIds`。

计数必须与引用数量一致。该契约不把加试、待确认条件或未满足硬条件换算成
学习小时，也不把资格负担掺入分数 gap。

## 4. 版本失败边界

工作台和单岗位匹配继续使用目标分 owner 的稳定错误：

- `position_release_unavailable`：没有当前已激活岗位发布；
- `target_score_basis_expired`：当前发布派生的目标分依据已过期；
- `position_data_version_conflict`：岗位、资格或目标分不属于同一发布。

这些情况停止输出正式工作台或匹配结果，不延长旧目标分有效期，也不切换到
历史发布。
