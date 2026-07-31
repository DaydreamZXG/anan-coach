# Findings 阶段复核（二，历史快照）

> **非权威历史产物。** 本文件冻结于真实 UI/UX 子审计合并前，仅解释当时的 12 条
> 台账状态，已被 `audit/findings.jsonl`、`audit/findings.md` 和
> `audit/summary.md` 的 15 条正式口径取代；不得用于当前数量、严重度或交付判定。

当时的复核范围仅为尚未合并 C 的阶段版 `audit/findings.jsonl`；本次历史复核不评价 UI。

## 结论

上轮五项内容整改均已落实：

- 匿名答案/解析暴露已从正式 findings 移到 `audit/hypotheses.md` 的 policy blocker，并明确政策确认后按 P0 处理；
- `BACKEND-REPLAY-001` 已降为 P2；
- D-F002 与 E-004 已拆为 `ROOT-IDEMP-CLIENT-001`（P2）和 `BACKEND-COMMAND-001`（P1），关闭标准与证据边界分离；
- `ROOT-REQUEST-001` 的 confirmed actual/affected scope 已收窄到 report-center 与公共 ApiClient 能力缺口；
- `ROOT-GATE-001` 和 `ROOT-JOBS-001` 已补直接源码/部署证据。

结构复核同时通过：12 个唯一 finding，P0=1、P1=2、P2=9、P3=0；必需字段无缺失；73 个 evidence 路径全部存在。

## 剩余阻断

唯一剩余的 ledger 冻结阻断是状态元数据不一致：以下 5 条的业务 `status` 已为 `confirmed`，但 `ledger_state` 仍为 `draft`：

- `ROOT-AUTH-001`
- `ROOT-CONTRACT-001`
- `ROOT-GATE-001`
- `ROOT-IDEMP-CLIENT-001`
- `ROOT-REQUEST-001`

如果 `ledger_state` 参与最终汇总、计数或发布判定，应在冻结前统一改为 `open`；如果该字段只是非规范的合成过程标记且不会被任何消费者读取，则当前 JSONL **没有剩余内容阻断**。
