# Initial App - Evidence

## EvidenceBundleDraft

- Artifact key: backend-tests
- Type: command
- Summary: `backend/.venv/bin/python -m pytest backend/tests` passed with 3 tests.
- Result: `3 passed, 1 warning`

## EvidenceBundleDraft

- Artifact key: iphone-build
- Type: XcodeBuildMCP build
- Summary: iPhone 17 simulator build succeeded for scheme `Gongkao`.
- Result: `SUCCEEDED`, no warnings or errors.

## EvidenceBundleDraft

- Artifact key: ipad-build
- Type: XcodeBuildMCP build
- Summary: iPad Pro 13-inch (M5) simulator build succeeded for scheme `Gongkao`.
- Result: `SUCCEEDED`, no warnings or errors.

## EvidenceBundleDraft

- Artifact key: iphone-runtime-ui
- Type: XcodeBuildMCP runtime snapshot and screenshot
- Summary: iPhone 17 app launched; UI snapshot found 今日 tab, 今日任务, 题库 `55,214`, 岗位 `60,471`, and 知识点 `23,103`.
- Screenshot: `/var/folders/zw/z2s69k4n4k1dfgxk37lkn09w0000gn/T/screenshot_optimized_1acdd8e0-67bf-4421-b852-513b46e8729e.jpg`

## EvidenceBundleDraft

- Artifact key: production-health
- Type: HTTPS health check
- Summary: `GET https://kg.xingguoxian.cn/health` returned ok through Caddy and FastAPI.
- Result: `{"status":"ok","app":"gongkao-dazi","environment":"production",...}`

## EvidenceBundleDraft

- Artifact key: full-verify
- Type: command
- Summary: `scripts/full_verify.sh` completed successfully.
- Result:
  - backend tests: `3 passed, 1 warning`;
  - iPhone 17 simulator build: `BUILD SUCCEEDED`;
  - iPad Pro 13-inch (M5) simulator build: `BUILD SUCCEEDED`;
  - Aegis workspace check: passed.

## EvidenceBundleDraft

- Artifact key: server-service-status
- Type: server check
- Summary: production candidate server has `gongkao-dazi-api.service` and `caddy` active.
- Result:
  - disk `/`: `59G` total, `54G` available;
  - memory: `7.8GiB` total, `7.1GiB` available.
