# Initial App - Checkpoint

## Checkpoint Update

- Current todo: move from first runnable slice to real product data integration.
- Active slice: initial runnable app and health check complete.
- Completed todos:
  - Created FastAPI backend skeleton with `health`, `bootstrap`, `today`, `practice`, and `positions` preview endpoints.
  - Created native SwiftUI iPhone/iPad app shell for 考公搭子.
  - Added iPad-specific NavigationSplitView path rather than scaled iPhone-only layout.
  - Deployed backend to the new 火山云 server under systemd.
  - Configured Caddy HTTPS for `kg.xingguoxian.cn`.
  - Added root README, deploy/env examples, and verification script.
- Evidence refs:
  - `backend/tests/test_api.py`
  - `apps/ios/Gongkao/Gongkao/AppShellView.swift`
  - `apps/ios/Gongkao/Gongkao/FeatureViews.swift`
  - `docs/aegis/work/2026-07-03-initial-app/90-evidence.md`
- Blocked on: none for today's initial version.
- Next step: implement real backend persistence schema and connect the iOS app to `/api/v1/bootstrap` and preview endpoints.

## DriftCheckDraft

- Scope status: stayed inside the initial runnable app goal.
- Compatibility status: no old project runtime dependency introduced.
- Retirement status: Flutter-first and old runtime paths remain retired.
- New risk signals:
  - current iOS content is local preview data, not yet API-backed;
  - server lacks PostgreSQL/Redis product schema setup;
  - Gemini and TOS secrets are not on server yet.
- Advisory decision: continue
