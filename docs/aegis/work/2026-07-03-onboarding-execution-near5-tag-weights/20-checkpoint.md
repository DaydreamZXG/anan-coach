# Near-5-year guokao tag weights - Checkpoint

- Task ID: 2026-07-03-onboarding-execution-near5-tag-weights
- Current todo: Section 8 and 8.5 implementation slice is materialized and verified; keep planner/method-card follow-up scoped to the map owner.
- Completed todos:
  - Audited real `product.question_occurrences` fields on server and confirmed guokao markers are available via `province='国考'`, paper titles, source paths, and parsed exam year from paper title.
  - Added `product.exam_tag_weights` schema to local test schema and server setup script.
  - Added `scripts/sql/refresh_guokao_exam_tag_weights.sql` plus server refresh wrapper.
  - Wired product import script to refresh derived tag weights after product knowledge import.
  - Replaced taskstream/practice hardcoded `判断推理 + 2026` selector with `KnowledgeService.select_weighted_xingce_practice`.
  - Added DB-backed tests for latest-five-year window, 70/30 selection, visual exclusion in practice, and no fallback when weights are absent.
  - Added `product.knowledge_map_nodes` as the tag registry / learning map owner.
  - Wired practice/taskstream selection to consume text-supported `knowledge_point` nodes from the map rather than direct scattered tag queries.
  - Added regression coverage that refresh preserves manually governed map nodes.
  - Materialized `product.knowledge_map_nodes` on production with 2462 guokao/xingce rows.
- Active slice: knowledge-map materialized and handed off.
- Blocked on: none.
- Next step: build external wrong-question ingestion and method-card publishing checks against `product.knowledge_map_nodes`; do not allow AI-created free tags into learner memory.

## DriftCheckDraft

- Original intent still served: yes, planner/practice now consume a product-layer near-5-year guokao basis through the knowledge map.
- Compatibility boundary: stayed within product knowledge schema, backend selection, scripts, tests, and specs; no API fallback path was added.
- New owner/fallback introduced: `product.exam_tag_weights` is the heat map; `product.knowledge_map_nodes` is the canonical tag registry. No AI-created free-tag path was introduced.
- Retirement track: old internal `判断推理/year=2026` path is absent from `backend/app`; direct planner/practice dependence on scattered `question_tags` is retired for this slice.
- Evidence state: backend tests, compile check, script syntax checks, production refresh, and production row-count/window verification passed.
- Decision: continue to external-ingestion/method-card enforcement work.
