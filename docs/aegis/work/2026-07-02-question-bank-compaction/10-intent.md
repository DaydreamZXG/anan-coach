# Question Bank Compaction - Intent

## TaskIntentDraft

- Requested outcome: Rebuild a new canonical question bank from exported PeiPeiShua question assets, prioritizing AI-cleaned explanations and excluding essay explanation compaction.
- Goal: Rebuild a new canonical question bank from exported PeiPeiShua question assets, prioritizing AI-cleaned explanations and excluding essay explanation compaction.
- Success evidence:
- Local dump restored or otherwise queryable offline.
- First profiling report identifies duplicate families, explanation source priority, and false-merge risks.
- First compaction candidate rules are documented with review queues instead of destructive deletion.
- Stop condition: Stop when the first local compaction analysis report exists, or pause if local DB/tooling/storage prevents offline analysis.
- Non-goals:
- Do not continue or patch the old PeiPeiShua product implementation.
- Do not write to the old servers or production database.
- Do not migrate user, session, payment, membership, essay submission, or other old business data.
- Do not build the new app before the canonical question-bank model is understood.
- Do not compact申论解析;申论题 may remain as raw/question assets but is out of explanation compaction scope.
- Scope: Local offline analysis and first compaction report from exported question-bank database/files; no old product code continuation and no server writes.
- Change kinds:
- data-pipeline
- Risk hints:
- none

## BaselineReadSetHint

- data/question-bank-export/MANIFEST.md
- data/question-bank-export/db/question-bank-subset-20260702.dump
- Exported QuestionBank* table schemas from the dump

## BaselineUsageDraft

- Required baseline refs:
- data/question-bank-export/MANIFEST.md
- Local dump table inventory after restore
- Acknowledged before plan:
- User instruction: AI-cleaned explanations have priority when present.
- User instruction:申论解析 is not needed.
- User instruction: old project is discarded; exported 104,762 occurrence records, 562,539 tag links, and 23,103 knowledge chunks are valuable assets.
- Cited in plan:
- data/question-bank-export/MANIFEST.md
- Missing refs:
- none
- Advisory decision: continue

## ImpactStatementDraft

- Compatibility boundary: The old production schema is source evidence only; the new canonical schema and compaction rules may diverge intentionally.
- Affected layers:
- Local analysis artifacts
- Future canonical question-bank data model
- Future import/compaction pipeline
- Owners:
- New question-bank pipeline owns canonical identity and compaction decisions.
- Old PeiPeiShua tables own only source provenance evidence.
- Invariants:
- Every source occurrence must remain traceable after compaction.
- Question compaction must preserve answer, assets/materials, tags, knowledge chunks, and best explanation provenance.
- AI-cleaned explanation wins over original explanation when accepted; original explanation remains evidence.
- Graphic reasoning and material-based questions require asset/material-aware identity checks.
- Non-goals:
- No production mutation.
- No old app continuation.
- No申论解析 compaction.

These records are Method Pack drafts / hints, not authoritative runtime decisions.

## BaselineUsageDraft

- Required baseline refs:
- data/question-bank-export/MANIFEST.md
- data/question-bank-export/db/question-bank-subset-20260702.dump
- Delivered context refs:
- none
- Acknowledged before plan:
- User instruction: AI-cleaned explanations win when accepted
- User instruction: essay explanations are not needed
- Cited in plan:
- reports/question-bank/compaction-v1-report-20260702.md
- Missing refs:
- none
- Advisory decision: continue
