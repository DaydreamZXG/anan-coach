# Server Inventory

## 考公搭子 Production Candidate

- Provider: 火山云 / 火山引擎
- Public IP: `101.96.198.253`
- Planned API domain: `kg.xingguoxian.cn`
- Hostname: `iv-yepqjczoqoobzsym7hrf`
- OS/kernel observed: Ubuntu Linux, kernel `6.8.0-55-generic`
- Intended role: first low-cost production backend host for the WeChat Mini Program
- Planned app name: `考公搭子`
- Planned project slug: `gongkao-dazi`
- Purchased shape: `4C8G`, `60G` system disk, `2M` public bandwidth
- Observed resources on `2026-07-03`: `7.8GiB` memory, `/dev/vda2` `59G` total, `54G` available
- SSH private key path on local machine: `~/.ssh/gongkao_dazi_ed25519`
- SSH public key comment: `gongkao-dazi-codex`
- Created/recorded date: `2026-07-03`
- DNS status on `2026-07-03`: `kg.xingguoxian.cn` resolves to `101.96.198.253` via domestic public DNS (`223.5.5.5`, `119.29.29.29`); some overseas resolvers may lag.
- Deployment status on `2026-07-03`: minimal FastAPI service deployed under systemd as `gongkao-dazi-api.service`; Caddy is serving HTTPS for `kg.xingguoxian.cn`; PostgreSQL 16 with pgvector is installed and listens only on `127.0.0.1:5432`.
- Health check on `2026-07-03`: `GET https://kg.xingguoxian.cn/health` returned `{"status":"ok","app":"gongkao-dazi","environment":"production",...}`.
- Canonical qbank status on `2026-07-03`: source dump restored into `source_qbank`; approved canonical v1 rebuilt into `canonical_qbank`; 12 expected count checks matched in `ops.import_count_checks`.

## Operating Constraints

- Treat `2M` bandwidth as API-only capacity.
- Do not serve question images or user-uploaded photos through the server.
- Store question images, photo uploads, and database backups in TOS/object storage.
- Keep PostgreSQL, FastAPI, Redis/RQ, admin, and reverse proxy on this host for the first phase.
- Keep source/canonical qbank schemas as staging and audit layers; runtime APIs should read product-owned tables after the product import slice.
- Keep local logs bounded and rotated; target no more than `1-2G` retained logs.
- Revisit disk upgrade or migration when free disk drops below `10G` or PostgreSQL grows beyond `12-15G`.

## TOS Object Storage

- Bucket: `gongkao-dazi-prod`
- Region: `cn-guangzhou`
- External endpoint: `tos-cn-guangzhou.volces.com`
- Internal endpoint: `tos-cn-guangzhou.ivolces.com`
- External bucket domain: `gongkao-dazi-prod.tos-cn-guangzhou.volces.com`
- Internal bucket domain: `gongkao-dazi-prod.tos-cn-guangzhou.ivolces.com`
- Bucket policy: private
- Planned prefixes:
  - `question-assets/`
  - `user-uploads/`
  - `db-backups/`
  - `tmp/`
- Secret handling:
  - AccessKey file was provided outside the repo at `/Users/zxg/Desktop/AccessKey.txt`.
  - Do not commit AccessKey values.
  - Runtime credentials belong in a root-only server env file or managed secret store.

## AI Providers

- Text generation: OpenAI-compatible Gemini gateway at `https://gemini-web2api.ergouapi.co/v1`, chat completions path `/chat/completions`, model `gemini-3.5-flash`.
- Text gateway contract verified on `2026-07-18`: JSON object/schema output and token usage are available; the response body `id` is the audit request identifier. Repeating the same `Idempotency-Key` creates a new call, so the runtime truthfully marks it `nonreplayable_audited` and does not automatically retry ambiguous failures.
- Vision/OCR: the operator Grok gateway has moved to `https://cpa.ergou.pro/v1`. TLS negotiation and the authenticated `/v1/models` boundary were reachable on `2026-07-21`; this proves transport reachability only. Learner content remains disabled until the returned actual model, processor release, zero-retention terms, training prohibition, operator identity and scenario approvals are published in `ai_audit.processor_release_records`.
- Intended routing boundary: Gemini-compatible text processing covers companion, planning explanation, 行测错因、申论评阅/rubric、社区候选和未命中题目解析；vision processing covers printed OCR, handwriting OCR, OMR and score-report extraction. Neither route may receive learner content until its exact processor release is published as `approved`.
- Every production scenario still requires a passing golden evaluation and an active `ai_audit.scenario_deployments` record matching provider, model, prompt and schema before serving user traffic.
- Secret handling:
  - Provider keys were supplied in chat; they are never stored in the repository or release artifact.
  - Runtime keys belong only in `/etc/gongkao-dazi/api.env` with mode `0600` or a managed secret store.
  - Rotate both keys before public launch because they have appeared in chat history.

## Deferred Operations

- Virtual payment: intentionally disabled on `2026-07-18`; payment secrets are absent and `gongkao-dazi-commerce-worker` remains stopped.
- Database backups: intentionally deferred as of `2026-07-19`; `BACKUP_ENABLED=false` and no production timer is enabled. The repository now has a fail-closed, snapshot-consistent, encrypted and signed artifact contract plus an isolated empty-database restore verifier. This is engineering readiness only: the real cross-failure-domain target, retention, keys, first artifact and signed restore report are absent, so no production recovery guarantee exists yet.
- Customer support: the mini-program exposes a truthful placeholder. A controlled group QR can be enabled later through runtime configuration; enterprise support administration remains closed until a real IdP is supplied.
