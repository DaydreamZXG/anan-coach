# Codex S2 Method Cards Evidence

- Date: 2026-07-03
- Scope: implement S2 from `docs/aegis/specs/2026-07-03-method-cards-and-guokao-focus-spec.md`.
- Boundary held: S2 only. No formal card content generated, no practice/planner/UI wiring, no destructive live-data operation.
- Product source of truth: added `product.method_cards` schema with published/draft status, JSONB teaching fields, internal `author_note` and `validation`.
- API: added authenticated `GET /api/v1/method-cards/{card_id}`. It returns only `published` cards and excludes `author_note` and `validation`.
- Import: added `scripts/import_method_cards.py` and `backend/app/domains/method_cards/importer.py`; YAML import is idempotent by `id` upsert.
- Import guardrails: `mc_` id, `guokao` exam scope, `xingce/shenlun` track, max 5 method steps, snake_case error codes, priority 1-5, and published validation threshold `tested_count >= 10`, `passed_count >= 8`.
- Test fixtures: added one published and one draft YAML under `backend/tests/fixtures/method_cards/`.
- Docs: updated `backend/README.md` route list and import command.
- Verification: `backend/.venv/bin/python -m pytest backend/tests/test_method_cards.py` -> 6 passed, 1 warning.
- Verification: `backend/.venv/bin/python -m pytest backend/tests` -> 36 passed, 1 warning.
- Verification: `scripts/full_verify.sh` -> backend 36 passed; iPhone 17 build succeeded; iPad Pro 13-inch (M5) build succeeded; Aegis workspace check passed.
- Remote deploy: synced backend app/tests/deps/import scripts to `101.96.198.253`, installed editable backend package, ran additive schema, restarted `gongkao-dazi-api.service`.
- Remote hygiene: an initial wide rsync created root-level code copies; no service/ops references existed, so they were moved into `/srv/gongkao-dazi/ops/archives/root-rsync-mistake-20260703-1708/`; service stayed active.
- Remote verification: `/srv/gongkao-dazi/backend/.venv/bin/python -m pytest tests` -> 36 passed, 1 warning.
- Remote smoke: `https://kg.xingguoxian.cn/api/v1/method-cards/mc_ziliao_jiqiliang_01` without Bearer -> 401; `/api/v1/bootstrap` still returns real counts; `product.method_cards` row count is 0 because no real cards were imported in S2.
- Residual risk: first real method-card content import is still pending S3.
