# 题库压实 v1 报告

时间：2026-07-02

## 1. 本轮目标

从已导出的旧题库资产里建立新的题库压实基线。旧项目实现不续用，旧库只作为数据来源。

本轮执行边界：
- 非申论题进入压实分析。
- 申论题不做解析压实。
- AI 洗过且已接受的解析优先作为 `preferred_analysis`。
- 保留原始解析作为证据，不覆盖删除。
- 所有来源记录必须可追溯，不把来源记录当重复题直接丢弃。

## 2. 数据恢复

本地分析库：`qb_compaction_20260702`

关键表计数：

| 表 | 计数 |
|---|---:|
| QuestionBankQuestion | 55,767 |
| QuestionBankQuestionOccurrence | 104,762 |
| QuestionBankQuestionTag | 562,539 |
| QuestionBankKnowledgeChunk | 23,103 |
| QuestionBankPaper | 1,633 |
| QuestionBankTag | 1,080 |

非申论题：53,564  
申论题：2,203

## 3. 解析优先级

非申论 53,564 题全部有可用解析。

| 解析来源 | 状态 | 数量 |
|---|---|---:|
| ai_rewrite | ai_rewrite_accepted | 35,312 |
| original_analysis | original_no_ai_rewrite | 8,884 |
| original_analysis | human_review_fallback_original | 4,779 |
| original_analysis | review_fallback_original | 3,325 |
| original_analysis | preserve_original_analysis_image | 1,264 |

结论：新题库解释字段应拆成：
- `preferred_analysis`：用户默认看到的解析。AI accepted 优先。
- `original_analysis`：原始解析证据。
- `analysis_source` / `analysis_status`：解析来源与审核状态。

## 4. 风险分桶

| 分桶 | 数量 |
|---|---:|
| plain_text | 35,797 |
| material | 10,478 |
| graphic | 4,023 |
| visual_or_multimodal | 3,266 |
| essay | 2,203 |

直接按题干文本去重会产生严重误合并。尤其图形推理题，题干经常都是“选择最合适的一个填入问号处”，但真正身份在图片。

## 5. 天真文本重复观察

如果只按 `题干 + 选项 + 答案` 标准化文本签名：

| 指标 | 数量 |
|---|---:|
| 重复组 | 2,051 |
| 涉及题目 | 6,900 |
| 看似可压掉 | 4,849 |

但风险拆开后：

| 风险 | 重复组 | 涉及题目 | 看似可压掉 |
|---|---:|---:|---:|
| graphic_false_merge_likely | 521 | 3,092 | 2,571 |
| material_needs_context_check | 1,025 | 2,668 | 1,643 |
| plain_exact_candidate | 505 | 1,140 | 635 |

结论：`4,849` 不是可直接删除空间，大部分是图形/材料题误合并风险。

## 6. v1 候选身份规则

本轮建立了 `compaction.candidate_identity`：

| 类型 | 身份规则 |
|---|---|
| placeholder_excluded | 占位题隔离，不进入 canonical |
| essay_excluded | 申论题不进入解析压实 |
| graphic_asset_signature | 图形题用图像签名或图像 HTML 参与身份 |
| material_context_signature | 材料题用材料 HTML/文本 + 题干 + 选项 + 答案 |
| visual_html_signature | 非图形但含图题用 HTML + 文本 |
| plain_text_signature | 普通题用题干 + 选项 + 答案 |

## 7. v1 压实结果

排除申论和占位题后，进入候选 canonical 的成员数：53,258  
候选 canonical 组数：52,923  
v1 可压掉重复成员数：335

| 处理决策 | 组数 | 成员数 | 可压掉 | AI 解析代表题数 |
|---|---:|---:|---:|---:|
| auto_merge_plain_exact | 241 | 482 | 241 | 225 |
| review_taxonomy_conflict | 36 | 72 | 36 | 33 |
| review_visual_cluster | 25 | 58 | 33 | 1 |
| review_material_cluster | 23 | 48 | 25 | 18 |
| singleton_keep | 52,598 | 52,598 | 0 | 34,770 |

v1 自动合并只允许 `auto_merge_plain_exact`。其他重复组进入人工/脚本复核队列，不自动合并。

自动合并主要分布：

| 模块 | 组数 | 成员数 | 可压掉 |
|---|---:|---:|---:|
| 言语理解与表达 | 190 | 380 | 190 |
| 常识判断 | 26 | 52 | 26 |
| 判断推理 | 14 | 28 | 14 |
| 数量关系 | 9 | 18 | 9 |
| 常识应用能力 | 1 | 2 | 1 |
| 知觉速度与准确性 | 1 | 2 | 1 |

## 8. 已生成资产

SQL：
- `tools/question-bank/compaction-v1.sql`
- `tools/question-bank/joint-exam-compaction-v1.sql`

脚本：
- `tools/question-bank/hash-visual-assets.py`

本地数据库视图：
- `compaction.question_features`
- `compaction.candidate_identity`
- `compaction.candidate_groups_v1`
- `compaction.candidate_members_v1`
- `compaction.joint_exam_occurrences_v1`
- `compaction.joint_exam_question_summary_v1`
- `compaction.joint_exam_candidate_groups_v1`
- `compaction.joint_exam_candidate_members_v1`
- `compaction.joint_exam_visual_asset_refs_v1`

报告/导出：
- `reports/question-bank/compaction-profile-raw-20260702.txt`
- `reports/question-bank/candidate-groups-summary-raw-20260702.txt`
- `reports/question-bank/candidate-groups-v1-20260702.csv`
- `reports/question-bank/candidate-duplicate-members-v1-20260702.csv`
- `reports/question-bank/joint-exam-profile-raw-20260702.txt`
- `reports/question-bank/joint-exam-compaction-v1-summary-raw-20260702.txt`
- `reports/question-bank/joint-exam-auto-merge-groups-v1-20260702.csv`
- `reports/question-bank/joint-exam-auto-merge-members-v1-20260702.csv`
- `reports/question-bank/joint-exam-visual-review-members-v1-20260702.csv`
- `reports/question-bank/joint-exam-visual-asset-refs-v1-20260702.csv`
- `reports/question-bank/joint-exam-visual-asset-hashes-v1-20260702.csv`
- `reports/question-bank/joint-exam-visual-asset-hash-summary-v1-20260702.csv`
- `reports/question-bank/joint-exam-taxonomy-review-members-v1-20260702.csv`
- `reports/question-bank/joint-exam-partial-overlap-v1-20260702.csv`

## 9. 省联考重点画像

用户补充确认：压实要重点关注省联考题，因为这类题常由多个省份共同考试，真正应该压实的是
“同一题在多省出现”的来源记录，而不是轻微改写题。

本轮按试卷标题中包含“联考”的非申论 occurrence 做画像：

| 指标 | 数量 |
|---|---:|
| 联考来源记录 | 28,127 |
| 联考 distinct canonical questions | 5,677 |
| 联考试卷 | 143 |
| 覆盖省份 | 28 |
| 多省出现的 canonical questions | 3,147 |
| 多 occurrence 的 canonical questions | 3,273 |
| occurrence 层已压实空间 | 22,450 |
| 多省题 occurrence 层已压实空间 | 22,323 |

结论：旧库里省联考的大头已经不是“重复 canonical question”，而是
`QuestionBankQuestionOccurrence` 挂在同一个 canonical question 下。新库设计必须保留这个结构：
`canonical_question` 表示唯一题目，`question_occurrence` 表示省份、年份、试卷、题号、来源路径。

联考题风险分布：

| 分桶 | canonical 题数 |
|---|---:|
| plain_text | 3,783 |
| material | 1,113 |
| visual_or_multimodal | 397 |
| graphic | 384 |

联考里 v1 仍发现的候选重复：

| 决策 | 类型 | 组数 | 成员 | 可压掉 |
|---|---|---:|---:|---:|
| auto_merge_plain_exact | plain_text_signature | 161 | 322 | 161 |
| review_visual_cluster | visual_html_signature | 2 | 8 | 6 |
| review_taxonomy_conflict | plain_text_signature | 2 | 4 | 2 |

已按更严格口径导出：候选组内至少 2 个成员都来自联考，才算“联考内部重复”。另外存在 `15` 个 partial overlap 组：
候选组里只有 1 个成员来自联考，不能混入省联考重复压实主结果，后续可在全局精确重复 pass 里处理。

### 9.1 省联考候选导出与图片核验

本轮新增 `joint_exam_compaction_v1`，把联考候选拆成四类：

| 分类 | 组数 | 联考成员 | 联考 occurrence | 处理 |
|---|---:|---:|---:|---|
| joint_auto_merge_candidate | 161 | 322 | 1,975 | 普通题精确重复，可进入自动合并候选 |
| joint_visual_review | 2 | 8 | 11 | 含图片，必须看图片哈希证据 |
| joint_taxonomy_review | 2 | 4 | 26 | 文本相同但分类冲突，人工/规则复核 |
| partial_overlap_not_joint_duplicate | 15 | 15 | 61 | 不是联考内部重复，不进入本轮主压实 |

图片题核验结果：

| candidate_group_id | 题数 | 图片数 | 缺失/错误 | 精确图片序列 | 感知哈希序列 | 结论 |
|---|---:|---:|---:|---:|---:|---|
| cg_20f265b0abacbed8982a | 5 | 20 | 0 | 1 | 1 | asset_exact_match_supported |
| cg_62bd97fb40054f6af698 | 3 | 6 | 0 | 1 | 1 | asset_exact_match_supported |

这两个视觉组的图片文件 SHA-256 序列完全一致，不只是感知哈希一致。它们可以升级为“图片证据支持合并”，但仍应在导入器里与
普通文本自动合并分开标记，避免以后把所有图片题都默认自动压掉。

## 10. 下一步

v1 是安全压实，不追求一次性压到最低。用户已确认一条关键产品规则：
**题干轻微改写但本质相同的题，仍视为不同题，不做语义压实。**

下一轮应该做：
1. 把省联考 `161` 个普通精确重复组和 `2` 个图片证据支持组写成 canonical merge plan。
2. 把省联考 occurrence 作为新 schema 的一等公民，不把多省来源丢进题干字段里。
3. 对 `joint_taxonomy_review` 的 2 组确认分类冲突是标签差异还是题目身份差异。
4. 对全局 `review_material_cluster` 加入材料图片哈希、材料组 ID、题号邻近关系。
5. 对普通题只做可解释的精确压实，不做 fuzzy/semantic 合并。
6. 建新 schema：`canonical_question`、`question_occurrence`、`canonical_explanation`、`question_asset`、`question_tag_evidence`、`knowledge_chunk_link`。
