# 众测包内域数据说明（B 档）

本包**包含**岗位全量结构化数据 + 题库**结构/抽样**，用于让模型理解产品以「岗 + 题」为基座的复杂度。
本包**不包含**全量题图（约 5GB）与完整 paper_questions.jsonl（约 700MB+）。

## 岗位（全量结构化）

路径：`import-staging/positions/`

- `2024_guokao_entry_scores_from_gemu.csv` — 进面分等证据
- `2024_guokao_interview_list_gemu.xlsx` — 面试名单量级岗位信息

相关代码：`backend/app/domains/positions/`、`position_decision/`、`backend/app/importers/`

## 题（结构 + 抽样，无图）

路径：`data/question-bank-sample/`

| 文件 | 含义 |
| --- | --- |
| `taxonomy_nodes.jsonl` | 知识点/题型 taxonomy 全量节点 |
| `papers.jsonl` | 试卷元数据 |
| `paper_questions.sample-1500.jsonl` | 题目结构抽样约 1500 条 |
| `paper_question_taxonomies.sample-5000.jsonl` | 题-知识点关联抽样 |
| `question_fingerprints.sample-1500.jsonl` | 指纹抽样 |
| `graphic-reasoning-meta/` | 图形推理范围元数据（无图） |
| `tools/question-bank/*.sql` | canonical schema / compaction |

相关代码：`backend/app/domains/knowledge/`、`practice/`、`calibration/`、`method_cards`

## 方法卡

路径：`data/method_cards/` + `backend/tests/fixtures/method_cards/`

## 使用原则（模型）

1. 改选岗/分差/今日任务时，应阅读岗位 CSV 字段与 `position_decision` 代码，避免假字段。
2. 改练习/错因/岸岸讲解时，应阅读抽样题 JSONL 字段与 knowledge schema，避免无视模块/知识点。
3. **禁止**要求下载全量题图；无图题仍可用文本字段完成讲解 UI 与契约。
4. 本次主任务仍是 AI 上岸顾问壳改造（去 RPG + 岸岸 + 现代 UI）；域数据用于**接地气**，不是要求在沙箱重建生产库。
