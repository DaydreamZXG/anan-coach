# Near-5-year guokao tag weights - Evidence

## Server Read-Only Audit

- Confirmed SSH connectivity to `101.96.198.253`.
- Read-only psql audit confirmed product columns used by the refresh SQL:
  - `product.question_occurrences`: `province`, `paper_title`, `occurrence_paper_title`, `source_path`, `year`.
  - `product.questions`: `domain`, `status`, `module_name`, `visual_required`.
  - `product.question_tags`: `tag_type`, `tag_value`.
- Guokao source markers are present in real data:
  - `province='国考'` has recent rows.
  - Paper titles include `2022年` through `2026年` 国家公务员行测卷.
  - Latest five exam years should therefore be derived from parsed paper title years, not raw occurrence calendar year alone.

## Files Changed

- `scripts/sql/refresh_guokao_exam_tag_weights.sql`
- `scripts/sql/refresh_knowledge_map_nodes.sql`
- `scripts/server_refresh_guokao_exam_tag_weights.sh`
- `scripts/server_refresh_knowledge_map_nodes.sh`
- `scripts/server_import_product_knowledge.sh`
- `scripts/server_setup_learning_ai_tables.sh`
- `backend/tests/sql/minimal_schema.sql`
- `backend/app/domains/knowledge/repository.py`
- `backend/app/domains/knowledge/service.py`
- `backend/app/domains/taskstream/service.py`
- `backend/app/domains/taskstream/router.py`
- `backend/app/domains/practice/service.py`
- `backend/app/domains/practice/router.py`
- `backend/tests/test_exam_tag_weights.py`
- `backend/tests/test_practice_taskstream_routes.py`
- `backend/tests/test_api.py`
- `docs/aegis/specs/2026-07-03-onboarding-and-execution-fixes-spec.md`
- `docs/aegis/specs/2026-07-03-method-cards-and-guokao-focus-spec.md`

## Verification

- `./.venv/bin/python -m pytest -q tests/test_exam_tag_weights.py tests/test_practice_taskstream_routes.py` from `backend/`
  - Result: `11 passed, 1 warning`.
- `./.venv/bin/python -m pytest -q` from `backend/`
  - Result: `53 passed, 1 warning`.
- `./.venv/bin/python -m compileall -q app tests` from `backend/`
  - Result: passed.
- `bash -n scripts/server_refresh_guokao_exam_tag_weights.sh`
  - Result: passed.
- `bash -n scripts/server_refresh_knowledge_map_nodes.sh`
  - Result: passed.
- `bash -n scripts/server_import_product_knowledge.sh`
  - Result: passed.
- `bash -n scripts/server_setup_learning_ai_tables.sh`
  - Result: passed.
- `python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py bundle --root /Users/zxg/Documents/新项目 --work 2026-07-03-onboarding-execution-near5-tag-weights`
  - Result: proof bundle assembled.
- `python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目`
  - Result: passed.
- Lingering-reference check:
  - `rg -n "today-xingce-joint-exam-logic|国考高频判断推理|module_name=\"判断推理\"|year=2026" backend/app`
  - Result: no matches.

## Production State

- Production derived-state materialization has been executed.
- `scripts/server_setup_learning_ai_tables.sh`
  - Result: succeeded; `product.exam_tag_weights` table/grants present.
- `scripts/server_refresh_guokao_exam_tag_weights.sh`
  - Result: succeeded; `INSERT 0 607`.
- `scripts/server_refresh_knowledge_map_nodes.sh`
  - Result: succeeded with current SQL; `DELETE 2462`, `INSERT 0 2462`.
- Read-only verification:
  - `product.exam_tag_weights` guokao/xingce rows: `607`.
  - window range: `2022-2026`.
  - `product.knowledge_map_nodes` guokao/xingce rows: `2462`.
  - `knowledge_point + text_supported` rows: `1180`; weighted rows: `341`.
  - `support_status=image_unsupported` rows: `271`.
  - weighted map nodes missing near-5-year window: `0`; min/max window: `2022-2026`.
  - top text-supported weighted knowledge points include `言语理解与表达/对应关系-前后呼应`, `资料分析/综合资料`, `言语理解与表达/成语填空`, `言语理解与表达/逻辑填空`.

## Anti-Entropy Notes

- `product.exam_tag_weights` remains the near-5-year guokao heat map.
- `product.knowledge_map_nodes` is the canonical tag registry / learning map for planner, practice, method-card matching, and future external wrong-question ingestion.
- `scripts/sql/refresh_knowledge_map_nodes.sql` deletes only derived rows where `evidence->>'generated_by' = 'refresh_knowledge_map_nodes_v1'`.
- Conflict updates preserve manually governed display/alias/support/status/evidence fields while still syncing derived near-5-year metrics and method-card links.
- Regression coverage: `test_knowledge_map_refresh_preserves_manually_governed_nodes`.

## EvidenceBundleDraft

- Artifact key: final-map-verification
- Type: verification
- Source: docs/aegis/work/2026-07-03-onboarding-execution-near5-tag-weights/90-evidence.md
- Summary: Backend tests passed 53/53; production knowledge_map_nodes refreshed to 2462 guokao/xingce rows; workspace check passed.
- Verifier: codex
