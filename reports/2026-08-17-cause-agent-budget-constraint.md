# Cause Agent 预算约束阻断

2026-08-17 在 `test_xingce_cause_agent_billing.py` 的真实 PostgreSQL 迁移测试中，新定义发布写入 `maxModelCalls=6` 时被 `agent_definition_releases_budget_snapshot_check` 拒绝。

约束来源为 `20260716_24_xingce_cause_agent.py`，其硬编码要求 `maxModelCalls=2`。三条模型线路若不改数据库约束，最终应急线路与一次用户追问无法同时获得真实、可审计的有界预算。

修复只需追加一个迁移：保持其余工具、追问和无进展上限不变，把 `maxModelCalls` 的精确允许值从 2 更新为 6。回滚前必须确认没有任何已持久的 6 次预算定义，否则拒绝降级。
