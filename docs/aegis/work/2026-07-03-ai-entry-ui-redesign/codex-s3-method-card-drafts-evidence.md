# Codex S3 Method Card Drafts Evidence

- Date: 2026-07-03
- Scope: start Appendix C method-card production with first 资料分析 draft batch.
- Boundary held: content assets only; no API contract change; no planner/practice wiring; no card marked `published` without product-owner manual backtest.
- Production data read: `product.questions` shows 资料分析 high-frequency clusters including 一般增长率、基期计算、现期计算、增长量、现期比重、两期比重、平均数、比值计算/比较.
- Added draft source file: `data/method_cards/xingce/ziliao_batch01_draft.yaml`.
- Draft cards produced: 13 cards covering 一般增长率、混合增长率、基期量、现期量、增长量计算、增长量比较、现期比重、两期比重、基期比重、现期平均数、基期平均数、比值计算、比值比较.
- Review discipline: every card has `validation.tested_count=0`, `passed_count=0`, empty `sample_question_ids`, null reviewer/reviewed_at, and `status=draft`.
- Test coverage: `backend/tests/test_method_cards.py` now imports `data/method_cards` into real temporary PostgreSQL and asserts all imported production cards remain draft/pending review.
- Verification: `backend/.venv/bin/python -m pytest backend/tests/test_method_cards.py` -> 7 passed, 1 warning.
- Verification: `backend/.venv/bin/python -m pytest backend/tests` -> 44 passed, 1 warning.
- Remote verification: temporary PostgreSQL database via `TEST_DATABASE_URL` -> `/srv/gongkao-dazi/backend/.venv/bin/python -m pytest -p no:cacheprovider tests` -> 44 passed, 1 warning.
- Remote import boundary: first import with API DB role failed with `permission denied for table method_cards`, confirming the runtime role is read-only for product assets.
- Remote import: postgres management connection ran `scripts/import_method_cards.py data/method_cards --database-url postgresql+psycopg:///gongkao_dazi` -> imported 13 draft cards from 1 YAML file.
- Remote smoke: `product.method_cards` now has `draft=13`, all reviewers empty; unauthenticated method-card API still returns 401; `gongkao-dazi-api.service` remains active.
- Next gate: product owner/fable manually回测 10 questions per card; only cards with at least 8/10 pass should be flipped to `published`.
