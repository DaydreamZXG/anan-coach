# Proof Bundle - 2026-07-03-onboarding-execution-near5-tag-weights

## Method Pack Boundary

This proof bundle is an advisory Aegis Method Pack record. It does not determine evidence sufficiency, produce authoritative `GateDecision`, or grant `completion authority`.

## Task Intent

- Requested outcome: Implement section 8 near-5-year guokao tag weighting and section 8.5 knowledge-map product layer for planner/practice consumption.
- Scope: product tag weight schema/script, product knowledge-map schema/script, backend query owner, planner/practice consumption tests

## Impact

- Compatibility boundary: Production refresh only rebuilds generated derived rows; missing derived data fails loudly.
- Non-goals:
- External wrong-question ingestion API
- Manual alias curation
- Shenlun knowledge map

## Evidence Bundle Refs

- docs/aegis/work/2026-07-03-onboarding-execution-near5-tag-weights/evidence-bundle-draft-final-map-verification.json

## Drift Check

- Scope status: within-section-8-and-8.5-product-weighting-and-knowledge-map-scope
- Compatibility status: backend-contract-preserved-with-503-on-missing-derived-data
- Retirement status: old-hardcoded-practice-selector-retired-from-backend-app-and-practice-selection-now-uses-knowledge-map-owner
- Advisory decision: continue
