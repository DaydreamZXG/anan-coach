# 岸岸 × 认知科学：机制对照与诚实边界（2026-07-27）

> 历史归档：本文件不再作为当前产品、实现或上线状态权威。当前说明见
> `docs/product/anan-product-system-spec.md`。

结论：机制层成立（下表可逐条溯源到代码），效果层未验证（无 cohort 数据）。
对外表述在拿到首届内测复测数据前，用「用认知科学的方法设计」，不用「基于」。

| 产品机制 | 代码位置 | 认知科学原理 |
| --- | --- | --- |
| FSRS 间隔调度（21 参数记忆卡） | backend/app/domains/xingce_learning/mastery/scheduler.py | 间隔效应 / 遗忘曲线 |
| 即时变式→1–3天复测→7–14天延时验证 | PRD §4.5；mastery 域调度 | 测试效应 / 迁移 |
| 作答后信心三档自查 | real_attempt/schemas.py confidence | 元认知校准 |
| 复盘原话保留 + 错因用户终审 | real_attempt reflection/decide_cause | 自我解释效应 / 归因理论 |
| 错因八码 + 证据强制引用 | cause_agent（contracts 校验） | 归因分类 + grounding |
| 动作轨迹/笔迹毫秒级采集 | 采集契约 v1（2026-07-26） | 过程性证据，认知诊断建模原材料 |
| 容量/精力/压力→当日任务分配 | planning 域 capacity | 认知负荷管理（工程近似） |

## 欠账清单（说「基于」之前必须完成）
1. FSRS 参数用真实用户作答数据重拟合（首届内测后）
2. 复测通过率 / 错因确认率 / 无证据错因比率的 cohort 报表（PRD §6.3 安全指标）
3. 对照口径：至少一个可比基线（如自学期 vs 使用期的模考轨迹）
