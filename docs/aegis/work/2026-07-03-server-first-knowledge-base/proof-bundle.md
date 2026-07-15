# Proof Bundle - 2026-07-03-server-first-knowledge-base

## Method Pack Boundary

This proof bundle is an advisory Aegis Method Pack record. It does not determine evidence sufficiency, produce authoritative `GateDecision`, or grant `completion authority`.

## Task Intent

- Requested outcome: Execute the first server-first knowledge-base milestone for 考公搭子
- Scope: Repository baseline, server PostgreSQL setup, schema boundaries, qbank import input transfer; no product API implementation in this slice

## Impact

- Compatibility boundary: No hidden fallback auth/model/user path; no image serving through 2M API host; no staging schema as final runtime owner
- Non-goals:
- Implementing 行测/申论 full learning loop
- Serving image assets from the API server

## Evidence Bundle Refs

- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice0-baseline-commit.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice0-full-verify.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice1-postgresql-schema.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice2-qbank-staging.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice3-canonical-qbank-rebuild.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice3-server-smoke.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice4-product-knowledge-import.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice5-knowledge-api-tests-and-smoke.json
- docs/aegis/work/2026-07-03-server-first-knowledge-base/evidence-bundle-draft-slice6-practice-essay-ios-loop.json

## Drift Check

- Scope status: Work still serves initial app goal: real taskstream, real qbank-backed 行测 answer loop, and first-class 申论 review/rewrite loop.
- Compatibility status: No runtime route reads source_qbank/canonical_qbank. AI calls are feature-scoped, quota-gated, and audited. iOS uses backend contracts and does not silently fall back to PreviewData for 今日 execution.
- Retirement status: PreviewData remains only for static workbench/preview surfaces; 今日 execution path has moved to real backend AppState.
- Advisory decision: continue
