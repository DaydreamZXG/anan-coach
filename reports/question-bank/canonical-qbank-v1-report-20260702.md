# Canonical QBank v1 报告

时间：2026-07-02

## 1. 执行边界

本轮把旧题库导出数据导入到新的本地派生 schema：`canonical_qbank`。

边界：
- 不续用旧项目代码。
- 不写线上服务器或生产库。
- 不做 fuzzy / semantic 去重。
- 轻微改写但本质同题仍保持为不同 canonical question。
- AI accepted 解析优先；原始解析保留为证据。
- 申论题保留为题目资产，但不生成解析压实记录。
- 占位/废题进入 quarantine，不进入 canonical question。

## 2. 本轮合并策略

已接受合并：

| 策略 | 组数 | 来源题成员 | 压掉来源题 |
|---|---:|---:|---:|
| auto_plain_exact | 241 | 482 | 241 |
| visual_asset_exact_supported | 2 | 8 | 6 |
| 合计 | 243 | 490 | 247 |

未接受合并，进入复核队列：

| 类型 | 组数 | 来源题成员 | 理论可压掉 |
|---|---:|---:|---:|
| review_taxonomy_conflict | 36 | 72 | 36 |
| review_visual_cluster | 23 | 50 | 27 |
| review_material_cluster | 23 | 48 | 25 |
| 合计 | 82 | 170 | 88 |

所以当前商业安全口径不是“把所有 v1 复核候选都压掉”，而是先只压掉有足够证据的 `247` 个来源题。

## 3. Canonical 导入结果

| 指标 | 数量 |
|---|---:|
| 旧源题总数 | 55,767 |
| 映射进 canonical 的源题 | 55,461 |
| quarantine 源题 | 306 |
| canonical question 总数 | 55,214 |
| 行测/客观题 canonical | 53,011 |
| 申论 canonical | 2,203 |
| source occurrence 导入 | 104,453 |
| quarantine occurrence | 309 |
| 试卷导入 | 764 |
| canonical explanation | 53,011 |
| 图片/HTML asset 引用 | 52,112 |
| tag evidence 行 | 560,397 |
| canonical tag 汇总行 | 557,844 |
| knowledge chunk link | 23,103 |
| merge review queue | 82 |

当前 `55,214` 是可落地的新题库底盘数。之前 `55,126` 是“所有 v1 复核候选也全部通过”的理论下限；本轮未把那 `88` 道高风险候选强行压掉。

## 4. 新 schema

`canonical_qbank` 里已经生成：

| 表 | 作用 |
|---|---|
| `import_policy` | 本轮导入策略与不做语义合并的规则 |
| `accepted_merge_group` | 已接受的合并组 |
| `source_question_map` | source question 到 canonical question 的唯一映射 |
| `rejected_source_question` | 占位/非候选题 quarantine |
| `canonical_question` | 新产品的一等题目身份与展示字段 |
| `exam_paper` | occurrence 引用到的旧试卷证据 |
| `question_occurrence` | 省份、年份、试卷、题号、来源路径等来源记录 |
| `canonical_explanation` | 默认解析，AI accepted 优先，并保留原始解析 |
| `question_asset` | stem/option/material/original HTML 里的图片引用 |
| `question_tag_evidence` | 每条旧 tag link 的 canonical 证据映射 |
| `canonical_question_tag` | canonical 级别去重后的标签汇总 |
| `knowledge_chunk_link` | 23,103 条知识片段重新挂到 canonical |
| `merge_review_queue` | 暂不合并的候选重复组 |
| `import_summary` | 导入核验指标 |

## 5. 核验

已通过检查：

| 检查 | 结果 |
|---|---:|
| 未映射也未 quarantine 的源题 | 0 |
| 未导入且未 quarantine 的 occurrence | 0 |
| 没有 representative 的 canonical question | 0 |
| 没有 explanation 的客观题 canonical | 0 |
| accepted merge 实际压掉来源题 | 247 |

## 6. 产物

SQL：
- `tools/question-bank/canonical-qbank-v1.sql`

计划：
- `docs/aegis/plans/2026-07-02-canonical-qbank-v1.md`

报告/导出：
- `reports/question-bank/canonical-qbank-v1-report-20260702.md`
- `reports/question-bank/canonical-qbank-v1-summary-raw-20260702.txt`
- `reports/question-bank/canonical-qbank-v1-summary-20260702.csv`
- `reports/question-bank/canonical-qbank-v1-merge-policy-raw-20260702.txt`
- `reports/question-bank/canonical-qbank-merge-review-queue-v1-20260702.csv`

## 7. 下一步

建议下一步做两个并行方向：

1. 复核 `merge_review_queue` 的 82 组，尤其材料题需要材料图片/材料组 ID/题号邻近关系，不要只看题干。
2. 在新 app schema 里把 `canonical_qbank` 转成正式迁移：题目、试卷、来源、解析、图片、标签、知识片段分表，保留这套 source map 审计能力。
