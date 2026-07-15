# Question Bank Compaction - Checkpoint

- Task ID: 2026-07-02-question-bank-compaction
- Current todo: Define first execution slice.
- Active slice: initial
- Blocked on: none
- Next step: Read baseline refs and start the next safe slice.

## Checkpoint Update

- Current todo: Continue with province-joint-exam occurrence compaction and asset-aware exact checks after v1 asset-aware exact pass.
- Active slice: v1 asset-aware exact compaction complete
- Completed todos:
- Exported question-bank assets pulled locally
- Local restored database qb_compaction_20260702 verified against manifest counts
- Built compaction.question_features, candidate_identity, candidate_groups_v1, candidate_members_v1
- Generated v1 report and CSV exports
- Evidence refs:
- reports/question-bank/compaction-v1-report-20260702.md
- tools/question-bank/compaction-v1.sql
- Blocked on: none
- Next step: Design and run v2 province-joint-exam analysis, focusing on same joint-exam questions appearing across multiple provinces as occurrences under one canonical question. Do not semantic-merge light rewrites.

## DriftCheckDraft

- Scope status: Still rebuilding new canonical question-bank pipeline from exported assets; no app work started.
- Compatibility status: No writes to old servers or production DB; old schema used as source evidence only.
- Retirement status: Old project remains discarded; no fallback or legacy product path introduced.
- New risk signals:
- Naive text dedupe greatly overstates removable duplicates because graph/material questions cause false merge risk.
- Advisory decision: continue

## Checkpoint Update

- Current todo: Prioritize province joint-exam occurrence compaction; do not semantic-merge light rewrites.
- Active slice: joint-exam profile added
- Completed todos:
- Ran joint-exam occurrence profile: 28,127 occurrences, 5,677 canonical questions, 22,450 occurrence extras already represented as occurrences
- Updated compaction report with joint-exam priority and corrected next steps
- Evidence refs:
- reports/question-bank/joint-exam-profile-raw-20260702.txt
- reports/question-bank/compaction-v1-report-20260702.md
- Blocked on: none
- Next step: Review 161 province-joint-exam auto_merge_plain_exact groups first, then image-hash the 2 joint visual clusters.

## DriftCheckDraft

- Scope status: Scope corrected toward province joint-exam occurrence compaction and away from semantic dedupe.
- Compatibility status: Still local-only; no old server writes or app continuation.
- Retirement status: Fuzzy/semantic merge direction retired from next steps after user correction.
- New risk signals:
- Joint-exam duplicates mostly live in occurrence provenance, not duplicate canonical question rows.
- Advisory decision: continue

## Checkpoint Update

- Current todo: Prepare canonical merge plan for joint-exam exact duplicate groups and image-supported visual groups.
- Active slice: joint-exam compaction v1 exports complete
- Completed todos:
- Created joint-exam compaction SQL views for strict joint duplicate groups.
- Exported 161 province-joint-exam plain exact duplicate groups and 322 members.
- Exported visual review candidates and hashed 26 referenced image files.
- Updated compaction report with joint-exam CSV outputs and visual hash evidence.
- Evidence refs:
- tools/question-bank/joint-exam-compaction-v1.sql
- tools/question-bank/hash-visual-assets.py
- reports/question-bank/joint-exam-auto-merge-groups-v1-20260702.csv
- reports/question-bank/joint-exam-auto-merge-members-v1-20260702.csv
- reports/question-bank/joint-exam-visual-asset-hash-summary-v1-20260702.csv
- reports/question-bank/compaction-v1-report-20260702.md
- Blocked on: none
- Next step: Write canonical merge plan and new schema/importer design using occurrence-preserving identity.

## DriftCheckDraft

- Scope status: Still rebuilding a new canonical question-bank pipeline from exported assets; no old product code and no server writes.
- Compatibility status: Local restored database and exported files are source evidence only; generated views and CSVs are derived analysis state.
- Retirement status: Semantic/fuzzy duplicate merging remains retired; visual questions require asset hash evidence before merge-plan inclusion.
- New risk signals:
- Groups with only one joint-exam member are partial overlaps and must not be counted as province-joint-exam internal duplicates.
- Advisory decision: continue

## Checkpoint Update

- Current todo: Turn canonical_qbank v1 local schema into formal app migration/importer after review of remaining 82 groups.
- Active slice: canonical_qbank v1 built locally
- Completed todos:
- Created and indexed canonical_qbank v1 implementation plan.
- Implemented tools/question-bank/canonical-qbank-v1.sql as a repeatable local canonical schema builder.
- Built canonical_qbank with 55,214 canonical questions, 104,453 imported occurrences, 53,011 explanations, 52,112 asset refs, 560,397 tag evidence rows, and 23,103 knowledge chunk links.
- Exported canonical import summary and merge review queue reports.
- Evidence refs:
- docs/aegis/plans/2026-07-02-canonical-qbank-v1.md
- tools/question-bank/canonical-qbank-v1.sql
- reports/question-bank/canonical-qbank-v1-report-20260702.md
- reports/question-bank/canonical-qbank-v1-summary-raw-20260702.txt
- reports/question-bank/canonical-qbank-merge-review-queue-v1-20260702.csv
- Blocked on: none
- Next step: Review merge_review_queue material/taxonomy/visual candidates, then convert canonical_qbank into the new app production schema migration.

## DriftCheckDraft

- Scope status: Still local-only canonical question-bank rebuild from exported assets; no old app continuation.
- Compatibility status: Old restored tables remain source evidence; canonical_qbank is local derived state and can be rebuilt by SQL.
- Retirement status: Old QuestionBankQuestion is no longer treated as product canonical owner; source IDs are retained in source_question_map for audit.
- New risk signals:
- Current canonical count is 55,214 because 82 review groups remain intentionally unmerged; do not advertise theoretical 55,126 as current built state.
- Advisory decision: continue

## Checkpoint Update

- Current todo: Execute the commercial master plan starting with Slice 0 product foundation.
- Active slice: commercial execution master plan merged
- Completed todos:
- Reviewed Claude bridge plan and verified position database counts from /Users/zxg/Documents/公考选岗计划/data/gwy.db.
- Created docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md as execution authority.
- Marked the foundation and bridge plans as superseded for execution while retaining them as references.
- Evidence refs:
- docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md
- docs/aegis/plans/2026-07-02-gongkao-app-commercial-foundation.md
- docs/aegis/plans/2026-07-02-mvp-bridge-plan.md
- Blocked on: none
- Next step: Start Slice 0: scaffold backend, mobile, admin, ops, product schema and question import/practice foundation.

## Checkpoint Update

- Current todo: Execute the commercial master plan starting with Slice 0 product foundation and question-bank import.
- Active slice: commercial boundary merge complete
- Completed todos:
- Merged the latest business decisions into the master plan.
- Changed first client target from cross-platform/Flutter-first to iOS-first native SwiftUI.
- Changed learner auth from email/password to Sign in with Apple; kept admin auth separate.
- Moved announcements/deadlines out of the core execution plan.
- Changed commercialization from deferred payment scaffold to App Store IAP plus audited entitlements.
- Tightened free AI quota and added AI usage/cost audit requirements.
- Recorded Gemini reverse proxy as an explicit configured provider route, not a hidden fallback.
- Evidence refs:
- docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md
- Blocked on: none
- Next step: Start Slice 0: scaffold backend, iOS, admin, ops, product schema and question import/practice foundation.

## DriftCheckDraft

- Scope status: Still building a new commercial product from scratch; old project remains discarded.
- Compatibility status: First-release compatibility boundary is now iOS App Store, Sign in with Apple, StoreKit/IAP, and product-owned PostgreSQL schema.
- Retirement status: Flutter-first, learner email/password, announcements-in-core, and deferred-payment assumptions are retired from execution authority.
- New risk signals:
- StoreKit/IAP and Apple auth must be verified before release rather than added after entitlement scaffolding.
- AI usage must be quota-checked before model calls to avoid cost leakage.
- Advisory decision: continue
