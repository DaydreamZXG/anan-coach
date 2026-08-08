# Third-wave independent verification

- Scope: read-only final product verification at commit `52568db3d8cf231e09eb3f3dedbfd088deb12a23`.
- Product code/config mutations: forbidden.
- Deployment, WeChat preview/upload, remote push: forbidden.
- Generated evidence in this directory is intentionally untracked until the root integrator reviews it.

## Result snapshot

- Final backend related suite: 146 passed, 0 failed, including the targeted-selection selector that had used a stale monkeypatch target. The test now patches the neutral owner. See `backend-final-targeted.json`.
- Backend full suite was not rerun at the final commit. The retained intermediate result is 1738 passed / 5 failed: four pre-existing commercial publication protocol baseline failures plus the now-resolved stale test target. It is historical disclosure only.
- Earlier WeChat static/full-suite logs remain commit-scoped in `summary.json`; they are not relabeled as final-commit executions.
- The root integrator reran the complete `npm run check` on the final commit: format/type/lint/OpenAPI and all structure gates passed, together with 21 Node tests, 130 files / 907 Vitest tests, 9 Cloud tests, and 269 generated targets / 146 cross-package replicas. See `wechat-final-check.json`.
- Official DevTools `PACKAGE_SIZE_LIMIT` was explicitly rescanned at `2026/08/09 01:11` on the final commit and passed (`compiledPkg`, main package below 1.5 M); exact bytes are not exposed. See `devtools-main-package-compiled-size.json` and `.png`.
- Local DevTools simulator was rerun on the final commit: phone, portrait iPad, and landscape iPad each passed 4/4 practice draft/offline recovery/swipe/scroll, material annotation/table horizontal scroll, and reflection replay/unload checks with zero runtime failures. Each profile has a final-commit `receipt.json` and page-only screenshots.

The separate 1626.3 KB dependency-analysis value belongs to the earlier commit and is a raw source ownership metric, not the compiled package gate. It is retained only as a historical diagnostic receipt.
