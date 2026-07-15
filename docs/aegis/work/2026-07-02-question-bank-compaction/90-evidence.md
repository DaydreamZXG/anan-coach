# Question Bank Compaction - Evidence

No evidence has been recorded yet.

## EvidenceBundleDraft

- Artifact key: compaction-v1-report
- Type: report
- Source: reports/question-bank/compaction-v1-report-20260702.md
- Summary: Documents local restore counts, explanation priority, risk buckets, asset-aware candidate identity, and v1 compaction results.
- Verifier: psql summaries from qb_compaction_20260702 and regenerated CSV exports

## EvidenceBundleDraft

- Artifact key: joint-exam-profile
- Type: report
- Source: reports/question-bank/joint-exam-profile-raw-20260702.txt
- Summary: Profiles province joint-exam occurrences and remaining candidate duplicate groups.
- Verifier: psql against local qb_compaction_20260702

## EvidenceBundleDraft

- Artifact key: joint-exam-compaction-v1
- Type: sql-view-export
- Source: tools/question-bank/joint-exam-compaction-v1.sql
- Summary: Creates strict province-joint-exam occurrence and candidate-group views; exports 161 plain exact duplicate groups, 2 visual review groups, and 2 taxonomy review groups.
- Verifier: psql -h 127.0.0.1 -p 55432 -d qb_compaction_20260702 -v ON_ERROR_STOP=1 -f tools/question-bank/joint-exam-compaction-v1.sql

## EvidenceBundleDraft

- Artifact key: joint-exam-visual-asset-hashes-v1
- Type: asset-hash-evidence
- Source: reports/question-bank/joint-exam-visual-asset-hash-summary-v1-20260702.csv
- Summary: Hashes 26 visual asset files from 2 province-joint-exam visual clusters; both clusters have exact SHA-256 image-sequence matches and no missing files.
- Verifier: python3 tools/question-bank/hash-visual-assets.py --root /Users/zxg/Documents/新项目

## EvidenceBundleDraft

- Artifact key: canonical-qbank-v1
- Type: local-schema-import
- Source: tools/question-bank/canonical-qbank-v1.sql
- Summary: Builds local canonical_qbank schema with 55,214 canonical questions, source map, occurrences, explanations, assets, tags, knowledge chunk links, quarantine, and merge review queue.
- Verifier: psql -h 127.0.0.1 -p 55432 -d qb_compaction_20260702 -v ON_ERROR_STOP=1 -f tools/question-bank/canonical-qbank-v1.sql

## EvidenceBundleDraft

- Artifact key: canonical-qbank-v1-report
- Type: report
- Source: reports/question-bank/canonical-qbank-v1-report-20260702.md
- Summary: Documents accepted merge policy, canonical import counts, schema table responsibilities, verification checks, and remaining 82-group merge review queue.
- Verifier: canonical_qbank.import_summary and psql integrity checks against local qb_compaction_20260702

## EvidenceBundleDraft

- Artifact key: commercial-execution-master-plan
- Type: plan
- Source: docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md
- Summary: Merged commercial foundation and bridge plans into a single execution authority with auth slice, position import boundary, AI/photo boundary, manual announcements, entitlement usage audit, and no legacy runtime dependencies.
- Verifier: python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目

## EvidenceBundleDraft

- Artifact key: commercial-business-boundary-merge
- Type: plan-update
- Source: docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md
- Summary: Updates execution authority with iOS-first native client, Sign in with Apple, App Store IAP, deferred announcements, tight AI quotas, Gemini reverse proxy as an explicit configured provider, and AI/IAP usage audit.
- Verifier: python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
