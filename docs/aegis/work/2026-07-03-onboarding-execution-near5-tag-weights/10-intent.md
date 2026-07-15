# Near-5-year guokao tag weights - Intent

## TaskIntentDraft

- Requested outcome: Implement section 8 near-5-year guokao tag weighting and section 8.5 knowledge-map product layer for planner/practice consumption.
- Goal: Planner/practice should consume a product-layer guokao heat map through a canonical knowledge-map owner, not hardcoded module/year rules or AI-created free tags.
- Success evidence:
- `product.exam_tag_weights` materialized from real guokao rows with a 2022-2026 window.
- `product.knowledge_map_nodes` materialized as the tag registry / learning map.
- Backend practice/taskstream selection uses published text-supported knowledge-point nodes.
- Missing derived data fails loudly instead of falling back.
- Tests cover latest-five-year weighting, weighted practice selection, unsupported image nodes, free-tag rejection, and manual-node preservation.
- Stop condition: Stop when success evidence is satisfied or a blocker/risk requires pause.
- Non-goals:
- none
- Scope: product tag weight schema/script, product knowledge-map schema/script, backend query owner, planner/practice consumption tests
- Change kinds:
- implementation
- Risk hints:
- none

## BaselineReadSetHint

- none

## BaselineUsageDraft

- Required baseline refs:
- none
- Acknowledged before plan:
- `docs/aegis/specs/2026-07-03-onboarding-and-execution-fixes-spec.md`
- `docs/aegis/specs/2026-07-03-method-cards-and-guokao-focus-spec.md`
- Cited in plan:
- `docs/aegis/specs/2026-07-03-onboarding-and-execution-fixes-spec.md#8`
- `docs/aegis/specs/2026-07-03-onboarding-and-execution-fixes-spec.md#8.5`
- `docs/aegis/specs/2026-07-03-method-cards-and-guokao-focus-spec.md#2.2`
- Missing refs:
- none
- Advisory decision: continue

## ImpactStatementDraft

- Compatibility boundary: No silent fallback selector; no runtime AI writes to `product.knowledge_map_nodes`; production refresh only rebuilds generated derived rows.
- Affected layers:
- product schema, backend knowledge/taskstream/practice domains, server refresh scripts, tests, Aegis specs
- Owners:
- `product.exam_tag_weights`
- `product.knowledge_map_nodes`
- Invariants:
- AI-created free tags must not enter learner memory.
- `image_unsupported` nodes stay out of automatic method-card/external wrong-question loops.
- Manually governed map nodes must not be overwritten by derived refresh.
- Non-goals:
- none

These records are Method Pack drafts / hints, not authoritative runtime decisions.
