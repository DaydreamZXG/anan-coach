# Phase 2 Auth And Memory Evidence

- Date: 2026-07-03
- Scope: Apple-authenticated learner boundary, app sessions, learner profile memory, practice answer memory, iOS session wiring.
- Retired production path: iOS installation ID as learner key; backend `X-Learner-Id` learner feature auth.
- New production path: Apple identity token -> backend verified session -> `Authorization: Bearer` -> learner memory keyed by backend user.
- Local verification: `scripts/full_verify.sh` passed; backend `21 passed`; iPhone 17 build passed; iPad Pro 13-inch build passed; Aegis workspace check passed.
- Remote verification: remote backend pytest passed with `21 passed`; schema script created/verified `learner.users`, `learner.apple_identities`, `learner.app_sessions`, `learner.profiles`, `learner.practice_answers`.
- Remote smoke: `GET /health` returned 200; `GET /api/v1/auth/me` without bearer returned 401; `GET /api/v1/taskstream/today` without bearer returned 401; knowledge search still returned product question data.
- Production deployment: backend code and schema-additive changes deployed to `kg.xingguoxian.cn`; systemd service restarted and active.
- Residual risk: real Apple login needs an on-device Apple credential smoke test; StoreKit entitlement is still a later slice.
