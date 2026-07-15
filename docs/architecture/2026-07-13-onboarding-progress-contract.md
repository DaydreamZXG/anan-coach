# 建档进度、学习时间与 A06 结果契约

## 1. 唯一事实源

正式建档状态只由 `learner.onboarding_progress` 决定。它保存：

- `qualificationReady` 与所绑定的 `qualificationProfileVersion`；
- `studyPreferenceReady` 与所绑定的正式周偏好版本；
- `resultReady`、`resultAcknowledged`、`completed`；
- 单调递增的 `progressVersion` 和确定性的 `nextStep`。

`learner.profiles.onboarding_complete` 只是同事务投影。数据库禁止在进度
owner 未完成时单独把该字段改成 `true`。认证登录读取同一个 progress
owner，并返回完整进度，因此中途重登恢复原步骤，不重新建档。

资格事实保存不会推进 `qualificationReady`。只有
`POST /api/v2/onboarding/qualification/complete` 能绑定当前资格档案版本；
未完成建档期间，资格事实产生新版本会使旧资格里程碑失效。最终完成只由
A06 的 `POST /api/v2/onboarding/complete` 发生。

每个命令保存不可变请求与完整响应，重复键同输入返回原响应，篡改输入
返回 `idempotency_key_reused`。状态变化另有 append-only 事件并与 outbox
在同一事务提交。

## 2. 时间 owner 边界

正式 A05 写入迁移 11 已建立的
`learning.weekly_study_preference_versions`，不建立第二套周计划：

- 工作日、周末每日分钟；
- 长整块、碎片分钟；
- 固定休息日、固定模考日；
- 时区、起效日期、版本与被替代关系。

某一天临时加减时、休息或调整精力只由
`learning.daily_capacity_versions(source_type=user_override)` 和
`PUT /api/v2/plans/capacity` 决定。周偏好响应显式返回这个 owner 与端点，
客户端不得在两个地方同时决定同一天容量。

## 3. A06 服务端结果

`GET /api/v2/onboarding/result` 只返回服务端组装的四种状态：

| 状态 | 含义 | 后续动作 |
|---|---|---|
| `evidence_sufficient` | 已选当前可用主目标，且同一国考年度行测、申论均有 complete、未过期正式估分 | `enter_workspace` |
| `baseline_required` | 已有主目标，但正式双科估分缺失、partial 或 stale | `record_baseline` |
| `position_selection_required` | 当前存在可报岗位，但用户尚未确认主目标 | `choose_position` |
| `no_eligible_position` | 当前资格版本和岗位发布下没有已确认可报岗位 | `choose_position` |

所有状态都返回正式周偏好与非空 `validUntil`；若已有能力估分，即使尚未
选岗也返回该估分。只有 `evidence_sufficient` 返回 `scoreGap`。分差单位为
`points`，方向固定为 `positive_means_target_above_current`：

```text
lower       = target.lower - ability.total.upper
recommended = target.recommended - ability.total.recommended
upper       = target.upper - ability.total.lower
```

单题申论 AI 训练分、旧行测 30 题 diagnostic、单科成绩和过期估分都不能
生成岗位总分差，也不能冒充 A06 正式能力入口。

## 4. 迁移策略

迁移 `20260713_18` 从 `20260713_17` 单头前进：

- 历史 `onboarding_complete=true` 且有合法 `daily_minutes` 的账户，把该
  明确时间事实迁成正式 confirmed 周偏好并保持完成；
- 历史已完成但没有合法时间事实的账户，不伪造 120 分钟默认值；仅保留
  `qualificationReady=true`，重开到 `study_preference`，同时把旧 boolean
  投影改回 `false`；
- 未完成账户不推断任何里程碑；
- 历史周偏好、建档命令和进度事件均不可覆盖或删除。

## 5. 稳定错误码

客户端必须读错误码，不可只猜 HTTP 状态：

- `qualification_profile_missing`
- `onboarding_qualification_incomplete`
- `qualification_profile_version_conflict`
- `onboarding_progress_version_conflict`
- `study_preference_missing`
- `study_preference_version_conflict`
- `onboarding_result_not_ready`
- `position_release_unavailable`
- `target_score_basis_expired`
- `position_data_version_conflict`
- `idempotency_key_reused`

旧 `POST /api/v1/onboarding/complete` 与
`PUT /api/v1/learner/profile` 已返回 `410 Gone`，避免形成第二完成事实源。
