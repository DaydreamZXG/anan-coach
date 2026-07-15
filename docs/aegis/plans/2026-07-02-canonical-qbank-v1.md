# Canonical QBank v1 Plan

## Plan Basis

- Implement a new local canonical question-bank schema from exported PeiPeiShua data.
- Old product code and old table shape are source evidence only.
- Safe compaction only:
  - merge all `auto_merge_plain_exact` groups from `compaction.candidate_groups_v1`;
  - merge the 2 joint-exam visual groups with exact SHA-256 image-sequence evidence;
  - keep taxonomy/material/other visual review groups separate.
- Preserve source occurrence, tags, knowledge chunks, explanations, and assets as evidence.
- Do not semantic-merge lightly rewritten questions.

## BaselineUsageDraft

- Required baseline refs:
  - `data/question-bank-export/MANIFEST.md`
  - `reports/question-bank/compaction-v1-report-20260702.md`
  - `tools/question-bank/compaction-v1.sql`
  - `tools/question-bank/joint-exam-compaction-v1.sql`
- Acknowledged before plan refs:
  - User confirmed AI accepted explanations should be preferred.
  - User confirmed essay explanations are not needed.
  - User confirmed light rewrites stay separate.
  - User asked to proceed with a clean commercial-grade product base.
- Cited in plan refs:
  - `reports/question-bank/compaction-v1-report-20260702.md`
- Missing refs:
  - none
- Decision: continue

## Requirement Ready Check

- Requirement source refs: conversation instructions and compaction report.
- Goals and scope refs: local canonical schema/importer for question-bank assets.
- Requirement item refs:
  - canonical question identity;
  - occurrence as first-class provenance;
  - preferred explanation with original evidence;
  - asset references;
  - tag evidence;
  - knowledge chunk link;
  - review queue for not-yet-merged duplicate candidates.
- Acceptance / verification criteria refs:
  - schema rebuilds from local DB;
  - counts explain remaining canonical volume;
  - no source occurrence/tag/chunk silently disappears except quarantined placeholders are counted;
  - SQL and reports are repeatable.
- Open blocker questions: none for local v1.
- Decision: ready

## Files

- Create `tools/question-bank/canonical-qbank-v1.sql`.
- Create `reports/question-bank/canonical-qbank-v1-report-20260702.md`.
- Export `reports/question-bank/canonical-qbank-v1-summary-raw-20260702.txt`.
- Update active Aegis work checkpoint/evidence.

## Compatibility

- Old restored tables remain untouched.
- New schema is local derived state under `canonical_qbank`.
- Re-running the SQL may drop and rebuild `canonical_qbank`; this is not source data.
- Production servers and exported files are not mutated.

## Architecture Integrity Lens

- Invariant: canonical identity is owned by `canonical_qbank.source_question_map`, not by old `QuestionBankQuestion`.
- Canonical owner / contract:
  - `canonical_question` owns display and question identity.
  - `question_occurrence` owns province/year/paper/source occurrence.
  - `canonical_explanation` owns chosen explanation and original evidence.
  - `question_asset`, `question_tag_evidence`, and `knowledge_chunk_link` preserve evidence.
- Responsibility overlap: old tags, chunks, papers, and occurrences are copied as source evidence, not as product authority.
- Higher-level simplification: one deterministic import SQL is easier to audit than scattered migration scripts.
- Retirement / falsifier: if review groups are accidentally merged, the verification counts should show unexpected removals.
- Verdict: proceed.

## Plan-Time Complexity Check

- Target files: one SQL importer plus one report.
- Existing size / shape signals: current compaction SQL is standalone and reproducible.
- Owner fit: importer SQL is the right owner for local canonical schema derivation.
- Add-in-place risk: adding to `compaction-v1.sql` would blur profiling and canonical import.
- Better file boundary: separate `canonical-qbank-v1.sql`.
- Recommendation: add owner file.

## Tasks

1. Define deterministic merge policy.
   - Accepted merge groups are all `auto_merge_plain_exact` groups plus the 2 visual groups proven by image hashes.
   - Non-accepted candidate groups map each source question to its own canonical question.
   - Essay questions map one-to-one and do not produce explanation rows.
   - Placeholders are quarantined.

2. Build canonical schema and mapping.
   - Create `canonical_qbank.import_policy`.
   - Create `canonical_qbank.source_question_map`.
   - Create `canonical_qbank.rejected_source_question`.
   - Create `canonical_qbank.canonical_question`.

3. Preserve provenance.
   - Create `canonical_qbank.exam_paper`.
   - Create `canonical_qbank.question_occurrence`.
   - Create `canonical_qbank.canonical_explanation`.
   - Create `canonical_qbank.question_asset`.
   - Create `canonical_qbank.question_tag_evidence`.
   - Create `canonical_qbank.knowledge_chunk_link`.
   - Create `canonical_qbank.merge_review_queue`.

4. Verify.
   - Re-run compaction prerequisite SQLs.
   - Run canonical importer.
   - Check counts for canonical questions, accepted merges, occurrences, tags, chunks, assets, and quarantine.
   - Export report.

## Risks

- Image support currently covers only the 2 joint-exam visual groups with exact evidence.
- Material clusters still need a later material-aware pass.
- Occurrence source has no essay occurrence rows, so essay browsing by paper needs separate essay-source handling later.

## Retirement

- Retire old `QuestionBankQuestion` as product canonical owner.
- Retain old IDs in `source_question_map` for traceability.
- Do not delete old source data.
