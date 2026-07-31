# 审计 QA 产物说明

当前权威交付：

- `audit/findings.jsonl` 与 `audit/findings.md`：15 条去重后的正式问题台账；
- `audit/summary.md`：最终覆盖率、严重度和系统性根因；
- `audit/repair-plan.md`：Wave 0–4 修复边界；
- `audit/subagents/c/manifest.json`：五档真实渲染聚合证据；
- `audit/qa/deliverables-tablet-final.log`：加入 iPad 横竖屏后的最终机器校验；
- `audit/qa/final-delta-review.md`：最终独立只读一致性复核。

`deliverables-pre-c*`、`deliverables-with-c*`、`deliverables-final*` 与两份
`findings-review*` 是合并过程的历史快照。它们保留用于追溯，但其中的 12/14 条问题、
旧设备覆盖和阶段性 blocker 不得用于当前统计或发布判断。
