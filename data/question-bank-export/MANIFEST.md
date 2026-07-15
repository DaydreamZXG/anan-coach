# Question Bank Export Manifest

Exported at: 2026-07-02 21:28-21:39 Asia/Shanghai

Source server: 47.120.51.182
Source database: peipeishua_qbank
Source data root: /opt/peipeishua/data

## Purpose

This export is a clean question-bank asset pull for rebuilding a new product from scratch.
It intentionally excludes old product code and user-business data such as users, sessions,
payments, essay submissions, memberships, and logs.

## Database Dump

Path: db/question-bank-subset-20260702.dump
Format: PostgreSQL custom dump
Size: 297 MB
SHA-256: 7d07c3296f047ffa038d56dcefc406fbc6ad78ac3a9bc57144e4c24ef9e572f5

Included tables:
- QuestionBankDataset
- QuestionBankPaper
- QuestionBankQuestion
- QuestionBankQuestionOccurrence
- QuestionBankTypeGroupRead
- QuestionBankKnowledgeNodeRead
- QuestionBankTag
- QuestionBankQuestionTag
- QuestionBankQuestionList
- QuestionBankQuestionListGroup
- QuestionBankQuestionListItem
- QuestionBankKnowledgeChunk

Known source counts:
- QuestionBankQuestion: 55,767
- QuestionBankQuestionOccurrence: 104,762
- QuestionBankQuestionTag: 562,539
- QuestionBankKnowledgeChunk: 23,103
- QuestionBankPaper: 1,633
- QuestionBankTag: 1,080

## File Assets

Pulled directories:

| Local path | Size | File count | Notes |
|---|---:|---:|---|
| files/question-bank | 741 MB | 3 | Main imported/deduped JSON files and patch backup |
| files/source-question-bank | 3.4 GB | 189,164 | Source question bank and image/material assets |
| files/assets | 160 MB | 294 | Local object assets referenced by app/data |
| files/image-sync | 218 MB | 5,337 | Image sync output |
| files/question-list-import | 126 MB | 1,698 | Imported question-list artifacts |
| files/aistudio-ocr-images | 30 MB | 398 | OCR image output/reference files |
| files/cleanup-manifests | 24 KB | n/a | Cleanup/reindex manifests |
| files/import-jobs | 20 KB | n/a | Import job logs |
| files/source-question-bank-backups | 0 B | n/a | Empty/placeholder backup directory |

Total local export size: approximately 5.0 GB.

## Non-Goals

- Do not continue the old PeiPeiShua implementation.
- Do not treat old user/product tables as migration authority.
- Do not preserve old fallback behavior, payment plumbing, membership state, or broken AI proxy configuration.
- Use this export as raw material for a new canonical question-bank pipeline.

## Immediate Research Targets

1. Build a canonical question identity model.
2. Compress duplicate 行测 questions beyond the current 55,767 canonical rows.
3. Preserve all 104,762 source occurrences as provenance, not as separate canonical questions.
4. Preserve tag links and knowledge chunks, but reattach them through the new canonical identity.
5. Special-case 图形推理 and material-based questions because plain stem text creates false duplicates.
