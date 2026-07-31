# 最终差异 QA 复核

日期：2026-07-28  
结论：**PASS；未发现会阻止本轮审计交付的新增问题。**

## 1. 复核范围与边界

本次仅对最终审计产物、证据链、真实渲染记录、统计口径和源码边界做只读复核；未修改正式业务源码、生产环境、真实用户数据或支付数据。唯一写入是本 QA 报告。

权威交付物：

- `audit/summary.md`
- `audit/findings.jsonl`
- `audit/findings.md`
- `audit/repair-plan.md`
- `audit/page-inventory.csv`
- `audit/coverage-matrix.csv`
- `audit/subagents/c/manifest.json`
- `audit/baseline/summary.md`
- `audit/critical-flows.md`
- `audit/blockers.md`
- `audit/hypotheses.md`

## 2. 最终口径

| 项目 | 复核结果 |
|---|---:|
| 已登记页面路由 | 64（58 个业务路由 + 6 个资产入口） |
| 成功打开页面 | 64 / 64 |
| 页面级打开阻塞 | 0 |
| 业务视觉状态 | 88 |
| 正式发现 | 15，全部为 confirmed / open |
| 严重度 | P0 1 / P1 2 / P2 11 / P3 1 |
| 修复波次 | W0 4 / W1 6 / W2 2 / W3 2 / W4 1 |
| 核心流程 | 13 / 13 已识别；12 条当前存在；11 / 12 有页面 / controller / 契约动态覆盖 |
| 跨服务真实 E2E | 0 / 12，仍为审计阻塞项而非本轮交付阻塞 |

`88` 表示业务视觉状态数，不等于 `64` 个页面入口。该语义在 `audit/summary.md`、`audit/page-inventory.csv` 和 `audit/coverage-matrix.csv` 中一致。

其中公告流程为 N/A；启动流程目前仅有静态证据，不计入上述 11 条动态覆盖。

## 3. 平板双方向真实渲染复核

| 方向 | 运行时视口 | 总记录 | 业务状态 | 业务路由 | 资产 | 捕获失败 |
|---|---:|---:|---:|---:|---:|---:|
| iPad 竖屏（DevTools 模拟器） | 768 × 1024，portrait | 94 | 88 / 88 | 58 / 58 | 6 | 0 |
| iPad 横屏（DevTools 模拟器） | 1024 × 768，landscape | 94 | 88 / 88 | 58 / 58 | 6 | 0 |

竖屏原始批次目录历史命名为 `tablet-ipad-landscape-b1/b2`，但运行时权威字段均为 `768×1024` 和 `deviceOrientation=portrait`；横屏批次为 `tablet-ipad-landscape-real-b1/b2`，运行时字段为 `1024×768` 和 `deviceOrientation=landscape`。复核依据是四份运行清单，而不是目录名：

- `audit/subagents/c/logs/tablet-ipad-landscape-b1-manifest.json`
- `audit/subagents/c/logs/tablet-ipad-landscape-b2-manifest.json`
- `audit/subagents/c/logs/tablet-ipad-landscape-real-b1-manifest.json`
- `audit/subagents/c/logs/tablet-ipad-landscape-real-b2-manifest.json`

四个批次均满足：`activeRoute` 与目标路由一致、截图路径存在、`runtimeFailures=[]`、`error=""`，且无 actionable warning。这里只能证明微信开发者工具 iPad 模拟器渲染；未覆盖物理 iPad，已在 `audit/blockers.md` 中保留。

## 4. 新增独立 P2 的复核与去重

最终新增发现为 `UI-PRACTICE-ANSWER-CARD-001`，根因与既有 `UI-CAPTURE-001` 不同，未按页面重复提交。

复现：

1. 在微信开发者工具 iPad 竖屏模拟器打开 `/pages/practice/index?mode=targeted&planTaskId=visual-plan-task-001`。
2. 进入 `answer-sheet` 状态。
3. 观察答题卡面板及底部 CTA 超出 768 px 视口右边界。
4. 切换到 1024×768 横屏并重复，溢出消失。

证据：

- 竖屏截图：`audit/subagents/c/screenshots/tablet-ipad-landscape-b1/c09-xingce-answer-card.png`
- 竖屏几何：sheet `left=280,width=560,right=840`；CTA `left=300,width=520,right=820`；两项 overflow 均为 `true`
- 横屏对照：`audit/subagents/c/screenshots/tablet-ipad-landscape-real-b1/c09-xingce-answer-card.png`；相同右边界在 1024 px 视口内，两项 overflow 均为 `false`
- 源码定位：`apps/wechat/miniprogram/pages/practice/index.wxss:715` 与 `apps/wechat/miniprogram/pages/practice/index.wxss:1238`

媒体规则设置 `right: 50%` 与 `translateX(50%)` 时未清除基础规则中的 `left: 0`，与几何结果一致。该问题因此列为已证实 P2，而非 hypothesis。

既有 `UI-CAPTURE-001` 的证据已扩展到标准、平板竖屏和横屏；各方向均出现错误条遮挡相册/快门控件，属于同一公共根因，未拆分为多个页面或设备问题。

## 5. 证据与交付一致性

- `audit/findings.jsonl` 含 15 个唯一 finding ID、17 个必需字段；所有列表字段均为非空数组。
- 共引用 94 个 evidence 对象，路径全部存在。
- `audit/findings.md`、`audit/summary.md` 和 `audit/repair-plan.md` 的发现数、严重度及波次数一致。
- 修复计划覆盖全部 15 个 finding ID；每个工作包均包含文件范围、影响面、回归、回滚、并行约束和停止条件。
- W3 依赖图已同时包含 `W3-CAPTURE-RESPONSIVE` 与 `W3-PRACTICE-ANSWER-CARD`。
- 页面清单的 17 个显式必需参数契约及特殊路由契约均通过自动校验。
- C 组聚合清单为 314 次捕获：standard 94、small 16、tall 16、tablet portrait 94、tablet landscape 94。

## 6. 自动校验与源码边界

执行：

```text
python3 tools/audit/validate_deliverables.py
```

结果：**18 passed / 0 skipped / 0 failed**。校验覆盖路由集合、参数契约、五类视口状态数、发现唯一性与字段类型、94 个证据路径、摘要/计划完整性，以及 C 组聚合和平板 profile。

对正式业务目录执行差异和未跟踪文件复核后，没有发现本审计波次新增的业务源码修改。当前审计范围外的脏工作树项均可在 `audit/baseline/git-status-before.txt` 中追溯到审计前基线；本轮没有把它们归因于审计，也未覆盖它们。

## 7. 剩余阻塞项与未覆盖范围

以下内容不阻止审计产物交付，但阻止把结论扩大为“已完成生产级全链路验证”：

- 12 条跨服务流程尚未在同一 staging 环境完成真实 E2E。
- 未在物理 iPad/Android 真机上验证；平板证据来自微信开发者工具模拟器。
- AI 供应商真实响应、生产支付、真实用户数据及生产流量未触达。
- 小屏与高屏为定向覆盖，各为 16 / 88 个业务状态、15 / 58 个业务路由，不应表述为全量覆盖。
- 其余假设继续保留在 `audit/hypotheses.md`，未提升为正式 finding。

综上，最终交付物内部一致、证据可追溯、平板横竖屏覆盖口径真实且明确，源码只读边界成立；本轮最终差异 QA 结论为 **PASS**。
