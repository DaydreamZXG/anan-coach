# WeKnora private retrieval projection

## Authority boundary

WeKnora v0.7.0 is a self-hosted, private-network retrieval engine. It is not the
question bank, answer authority, permission system, attempt ledger, learner brain,
or model-training corpus. Anan's PostgreSQL source versions, chunks, ownership and
citations remain canonical.

The reviewed upstream is pinned by `ops/upstreams/tencent-weknora-0.7.0.json`.
`ops/verify_weknora_upstream.py` must pass before a release changes or activates
the integration.

## Isolation and retrieval

- One opaque WeKnora knowledge base is provisioned for exactly one Anan learner.
- The Mini Program never sees or submits a WeKnora knowledge-base or knowledge ID.
- Each canonical Anan chunk is projected as one manual Markdown knowledge item.
- Search requests include only ready current-version knowledge IDs resolved by the
  server. Large scopes are split into bounded batches.
- Returned remote IDs are mapped back to exact current canonical chunks with the
  authenticated `owner_user_id`. Unknown, stale or cross-owner IDs fail closed.
- The response excerpt, title, page and anchor are read from Anan, never from the
  remote result payload.

This one-item-per-chunk projection is intentional: it makes remote semantic recall
useful without trusting a vendor chunk boundary or excerpt as a product fact.

## Projection lifecycle

Source creation and immutable source-version import enqueue durable jobs. A job:

1. reconciles or creates the learner's deterministic opaque knowledge base;
2. reconciles uncertain manual creates by deterministic chunk title;
3. waits for every remote parse state to become `completed`;
4. marks the new projection ready;
5. deletes the superseded version's remote items.

When `WEKNORA_PRIVATE_KNOWLEDGE_MODE=weknora`, missing configuration, provider
errors, invalid schemas, incomplete parsing and rejected mappings are terminal or
retryable explicit failures. The API does not silently fall back to local search.
`canonical` is a pre-activation architecture mode, not a runtime degradation path.

## Deletion

An individual private-source deletion deletes and confirms every mapped WeKnora
item before private objects and canonical source versions are erased. A database
guard rejects canonical deletion while any remote projection is not marked deleted.

The external-learning worker also watches queued or running account-erasure
requests, deletes the learner's remote items and dedicated knowledge base, and only
then marks the projection space deleted. The same database guard prevents the
data-rights worker from racing ahead; its durable retry succeeds after cleanup.

## Deployment contract

Use a dedicated WeKnora tenant on an operator-approved private HTTPS endpoint. The
server-only API key must have exactly the capabilities required by this adapter:

- `manage_kbs` for per-learner knowledge-base lifecycle;
- `ingest` for manual knowledge creation and deletion;
- `retrieve` for status reads and hybrid search.

Use three distinct server-only keys: a `manage_kbs` key, an `ingest` key and a
`retrieve` key. The adapter rejects reused key values and never uses a full-access
key. The keys, database and vector store must not be shared with a public WeKnora UI.
Provider logs must exclude request bodies, API keys and learner content. Private
materials are prohibited from model training. Activate only after the deployment
release ID, regional residency, backup deletion replay and zero-training policy
have been reviewed.

## Verification

```sh
backend/.venv/bin/python ops/verify_weknora_upstream.py
backend/.venv/bin/python -m pytest \
  backend/tests/test_private_knowledge_weknora.py \
  backend/tests/test_step6_private_knowledge_math_ink.py -q
```
