# 考公搭子 Backend

Local development:

```bash
uv sync --locked --extra dev
uv run pytest
uv run uvicorn app.main:app --reload
```

The committed `uv.lock` is the reproducible dependency set. A plain virtual
environment remains available as an explicit compatibility path for environments
that cannot install `uv`:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
PYTHON="$PWD/.venv/bin/python" ../scripts/verify_backend.sh
uvicorn app.main:app --reload
```

`scripts/verify_backend.sh` uses an isolated `uv run --locked --extra dev`
environment by default and never auto-selects `backend/.venv`. Set `PYTHON`
explicitly only when using the compatibility path; CI must use the locked
default.

Initial endpoints:

- `GET /health`
- `GET /api/v1/bootstrap`
- `POST /api/v1/auth/apple`
- `POST /api/v2/auth/wechat`
- `GET /api/v1/auth/me`
- `GET /api/v1/learner/memory`
- `PUT /api/v1/learner/profile` (retired; always `410 Gone`)
- `GET /api/v1/onboarding/profile`
- `PUT /api/v1/onboarding/profile`
- `POST /api/v1/onboarding/complete` (retired; always `410 Gone`)
- `GET /api/v1/onboarding/placement-test` (deprecated diagnostic only)
- `POST /api/v1/onboarding/placement-test/answers` (deprecated diagnostic only)
- `GET /api/v2/onboarding/progress`
- `POST /api/v2/onboarding/qualification/complete`
- `GET /api/v2/onboarding/study-preference`
- `PUT /api/v2/onboarding/study-preference`
- `GET /api/v2/onboarding/result`
- `POST /api/v2/onboarding/complete`
- `GET /api/v1/positions/search`
- `GET /api/v1/position-decisions/recommendations`
- `GET /api/v1/taskstream/today`
- `GET /api/v1/feed/today`
- `GET /api/v1/workbench/preview`
- `GET /api/v1/knowledge/questions/search`
- `GET /api/v1/knowledge/questions/{question_id}`
- `GET /api/v1/method-cards/{card_id}`
- `POST /api/v1/companion/ask`
- `POST /api/v1/companion/external-questions/resolve`
- `POST /api/v1/companion/external-questions/{intake_id}/confirm`
- `POST /api/v1/mock-exams/weekly`
- `POST /api/v1/mock-exams/{session_id}/submit`
- `GET /api/v1/mock-exams/{session_id}/review`
- `POST /api/v1/mock-exams/{session_id}/review/view`
- `GET /api/v1/calibration/ability`
- `GET /api/v1/practice/tasks/{task_id}`
- `POST /api/v1/practice/tasks/{task_id}/answers`
- `GET /api/v1/essay/tasks/{task_id}` (deprecated read-only compatibility)
- `POST /api/v1/essay/tasks/{task_id}/review` (retired; always `410 Gone`)
- `POST /api/v1/essay/tasks/{task_id}/rewrite` (retired; always `410 Gone`)
- `GET /api/v2/essay-intelligence/tasks/{task_version_id}/workspace`
- `POST /api/v2/essay-intelligence/artifacts/complete-plan-task`
- `POST /api/v2/essay-intelligence/drafting/complete-plan-task`
- `POST /api/v2/essay-intelligence/reviews/current/ensure`
- `GET /api/v2/essay-intelligence/review-jobs/{job_id}`
- `POST /api/v2/essay-intelligence/review-jobs/{job_id}/regenerate`
- `GET /api/v1/commerce/catalog`
- `GET /api/v1/commerce/entitlement`

Learner-specific and method-card content endpoints require `Authorization: Bearer <backend-session-token>`.
The backend does not accept client-supplied learner IDs in production routes.

The v2 onboarding flow has one progress owner:
`learner.onboarding_progress`. Qualification fact writes do not complete a
milestone; the explicit qualification command binds the current qualification
profile version. The weekly schedule API appends to the existing
`learning.weekly_study_preference_versions` planning aggregate. Temporary
same-day changes remain exclusively owned by `PUT /api/v2/plans/capacity` and
never create a second weekly schedule. A06 completion is a separate idempotent
acknowledgement after the server can build one of four result projections:
`evidence_sufficient`, `baseline_required`, `position_selection_required`, or
`no_eligible_position`. Only a current, same-exam-year, unexpired, complete
Xingce + Shenlun estimate can produce a score gap. The legacy
`learner.profiles.onboarding_complete` column is transactionally maintained as
a projection and is protected against independent updates.

Stable onboarding error codes include `qualification_profile_missing`,
`onboarding_qualification_incomplete`,
`qualification_profile_version_conflict`,
`onboarding_progress_version_conflict`, `study_preference_missing`,
`study_preference_version_conflict`, `onboarding_result_not_ready`, and
`idempotency_key_reused`. Position evidence failures use
`position_release_unavailable`, `target_score_basis_expired`, and
`position_data_version_conflict`; clients must not infer these states from the
HTTP status alone.

The deprecated v1 30-question Xingce placement diagnostic writes only its
legacy placement/profile records. It does not advance v2 onboarding, publish a
formal ability estimate, enter position score-gap evidence, or feed the
dual-track planning contract. It will be removed together with the legacy App
onboarding flow.

Paid Companion commands (`/api/v1/companion/ask` and
`/api/v1/companion/external-questions/resolve`) also require a stable
`Idempotency-Key`. The same user, feature, key, and request replay the complete
stored response without reserving quota or calling the provider again; reusing
the key with changed input returns `409`. After a transport-uncertain provider
outcome, callers must retry with the same key so the verified provider replay
contract can recover the original result. Production remains disabled until the
approved Companion provider release, HTTPS allowlist, DPA, replay capability,
verified replay-retention window, header names, byte limits, and model price
card are configured. Once that persisted window expires, an uncertain command
stays blocked and is never dispatched as a new charge.

> **Production release gate:** the repository does not yet contain a Companion
> provider-status client, a scheduled reconciliation worker, or an audited
> operator transition that can resolve an expired `provider_uncertain` command.
> Keeping the reservation and refusing another provider dispatch prevents a
> duplicate charge, but it is containment rather than recovery. Do not expose
> either paid Companion route in production until all three recovery paths have
> been implemented, alerting and retention-window SLOs have been defined, and a
> timeout-through-expiry reconciliation drill has passed.
>
> Provider dispatch also performs one durable-attempt checkpoint through a
> short-lived `NullPool` connection while the request holds its normal database
> connection and advisory lock. This connection closes before provider I/O, but
> it is intentionally outside the API `QueuePool`. Capacity approval must use
> `workers × (main-pool maximum + simultaneous checkpoint maximum)`, then add
> background-worker, migration/operations, and failover headroom. The configured
> main-pool maximum is 10 per process (5 pooled + 5 overflow); if paid
> dispatches can fill that pool, a checkpoint burst can briefly approach 20
> connections per process. The current application does not
> impose an internal checkpoint-concurrency ceiling, so a verified ingress/API
> concurrency limit and a database saturation load test are also release gates.
>
> The runtime database engine bounds each process to 10 main-pool connections
> (5 pooled + 5 overflow), waits at most 5 seconds for a pool slot, cancels a
> database statement after 15 seconds, cancels lock acquisition after 3 seconds,
> and closes an idle transaction after 120 seconds. The API service runs two
> worker processes, so its main-pool ceiling is 20 connections. Background
> workers and checkpoint connections still require separate capacity headroom.

WeChat mini-program login exchanges the temporary `wx.login` code only on the
server. Configure `WECHAT_MINI_APP_ID` and `WECHAT_MINI_APP_SECRET`; the secret
must never be shipped to a client or committed. The API stores the WeChat
`session_key` only as a SHA-256 hash and returns the existing revocable backend
session contract.

Database changes are owned by Alembic from revision `20260712_00` onward. The
current single head is `20260713_18`. For
an existing audited production schema:

```bash
alembic stamp 20260712_00
alembic upgrade head
```

Commercial entitlement routes use the `commerce` domain as the owner for product
catalogs, order facts, entitlement state, feature quota reservation, and
entitlement audit logs. The current mini-program release is free-cold-start:
paid ordering is disabled until a separate payment release reopens it.

`GET /api/v1/position-decisions/recommendations` returns decision buckets with
`decisionTargetScore` and `decisionScoreBasis`. When a candidate position lacks
direct historical score evidence, `position_decision` can estimate the decision
target score from prior-year national-exam samples with matching region, major,
education, political status, service years, and the strongest available city/system
scope.

Method-card YAML import:

```bash
python scripts/import_method_cards.py data/method_cards
```

Production grounded-essay review is fail-closed at the external provider
boundary. A deployment must configure a versioned provider release, an exact
HTTPS hostname allowlist, confirmed data-processing agreement, and a verified
`replayable_queryable` idempotency contract with explicit idempotency-key and
response-request-ID header names. The default external gateway is never
accepted in production. See `ops/env.example` for the required variables.

The provider document contains only the task prompt and scoring limits, full
material text, rubric, answer text, and learner thinking when present. Domain
user/attempt IDs, timestamps, command/input fingerprints, and stored content
hashes are excluded. Version and segment references are request-local aliases;
provider output is mapped back to immutable domain references before grounding
validation. Raw provider request IDs are discarded and only their SHA-256
fingerprints may enter execution audit.

Production Guokao essay-rubric compilation is automatic and fail-closed. It
discovers current published tasks with complete material, performs one strictly
structured generator call and one independent critic/reviser call, runs the
deterministic quote and score-conservation compiler, then append-only publishes
`system_compiled + unverified`. The rubric provider document has no learner or
answer data. Run a real-cost dry run before publication:

```bash
backend/.venv/bin/python scripts/auto_publish_guokao_essay_rubrics.py \
  --database-url "$DATABASE_URL" \
  --dry-run
```

See `docs/operations/2026-07-13-guokao-essay-rubric-publisher.md` for provider
release, price-card, replay, audit, task-filter, and incident procedures.
