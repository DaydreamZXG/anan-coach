# AI 上岸顾问改造执行意图

日期：2026-07-09

## Requested Outcome

按照 `docs/aegis/plans/2026-07-09-ai-shangan-advisor-transformation.md`，把当前项目从“官场生存刷题游戏 + AI 辅助训练”逐步改造成“AI 上岸决策系统 + AI 私教训练闭环”。

## Scope

当前执行只覆盖 Phase 1 的第一个后端切片：

- 新增 `position_decision` domain owner。
- 新增岗位决策推荐 API。
- 输出冲/稳/保岗位组合、分差、机会评分、风险标签、理由和下一步动作。
- 保持现有 `positions` 检索 API 兼容。

## Non-goals

- 不部署生产。
- 不改 iOS。
- 不做真实 AI provider 调用。
- 不做数据库破坏性迁移。
- 不删除旧游戏化链路。

## BaselineUsageDraft

- Required baseline refs:
  - `docs/HANDOVER.md`
  - `docs/aegis/plans/2026-07-09-ai-shangan-advisor-transformation.md`
  - `backend/app/domains/positions/*`
  - `backend/tests/test_onboarding_positions.py`
- Acknowledged before implementation refs:
  - Existing `PositionService.search_positions`
  - Existing target-score basis logic
  - Existing auth dependency and TestClient override pattern
- Missing refs: none for this slice
- Decision: continue

## ImpactStatementDraft

The slice adds a new public backend API and new domain owner. It does not mutate production data and does not retire old endpoints. The main architectural impact is separating “岗位数据检索” from “岗位决策建议”.

