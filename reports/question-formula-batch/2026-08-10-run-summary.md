# 2026-08-10 题库公式结构化运行记录

## 结果

- 唯一公式资产：55,345
- 已处理：55,345
- 待处理：0
- 自动候选：52,219
- 需复核：3,122
- 人工确认：4
- Schema 无效：0

最终结果保存在 `reports/question-formula-batch/results/`，汇总保存在
`reports/question-formula-batch/verification-summary.json`。

## 流水线

1. Texo 在两张 GPU 上以 6 个互斥分片完成 55,345 张图片的离线初识别。
2. KaTeX 检查语法、异常重复和近截断输出。
3. 本地 Qwen3-VL 复核 766 条题干关键公式和 11,779 条解析公式。
4. 人工结果最后覆盖模型结果，避免人工确认被批处理回退。

## 最终门槛

- KaTeX 无法解析：41 条，全部保持 `needs_manual_review`。
- 疑似重复或截断：1 条，保持 `needs_manual_review`。
- 上述异常中误标为 `candidate_auto`：0 条。
- 含中文的 `qi_624d0acb431e720d0b14bfc8` 由人工按原图确认为
  `\text{全年旅客运输量}(-38.4\%)`，不再交给纯公式模型识别。

## 原始批次证据

- Texo 结果：55,345 个 JSON。
- Qwen 题干关键公式复核：766 个 JSON。
- Qwen 解析公式复核：11,779 个 JSON。
- 解析公式压缩包 SHA-256：
  `9d33aa0b425f79e7b58c5e330b09149dd9c0d9efcb1096ada6b157effd3b621d`。

候选结果表示通过自动门槛，不等于人工逐张确认；需复核项不会自动进入生产真源。
