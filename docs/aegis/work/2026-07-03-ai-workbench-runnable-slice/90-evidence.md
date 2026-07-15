# AI Workbench Runnable Slice Evidence

Date: 2026-07-03

## Implemented Scope

- Backend preview contract now exposes:
  - `/api/v1/taskstream/today`
  - `/api/v1/workbench/preview`
  - bootstrap navigation with `今日 / 工作台 / 我的`
- Bootstrap no longer exposes AI provider base URL or model to the client.
- iPhone SwiftUI first-level navigation is now `今日 / 工作台 / 我的`.
- iPad SwiftUI renders a three-pane study desk:
  - left: target, today tasks, generated study objects;
  - center: today's AI-arranged taskstream;
  - right: source-grounded AI feedback and 申论 rewrite loop.
- 行测 and 申论 are visible first-class study tracks in 今日 and 工作台.
- Standalone first-level `刷题` and `选岗` screens were retired.
- Old preview endpoints `/api/v1/practice/sample` and `/api/v1/positions/sample` were retired.

## Verification

```bash
backend/.venv/bin/python -m pytest backend/tests
```

Result:

- `4 passed, 1 warning`

```bash
xcodebuild build -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO
```

Result:

- `BUILD SUCCEEDED`

```bash
xcodebuild build -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPad Pro 13-inch (M5)' CODE_SIGNING_ALLOWED=NO
```

Result:

- `BUILD SUCCEEDED`

```bash
rg -n 'case \.practice|case \.positions|navigationTitle\("刷题"\)|navigationTitle\("选岗"\)|tab.*刷题|tab.*选岗' apps/ios/Gongkao/Gongkao
rg -n '/api/v1/today/preview|/api/v1/practice/sample|/api/v1/positions/sample' backend/tests README.md
```

Result:

- no matches

```bash
scripts/full_verify.sh
```

Result:

- backend tests passed;
- iPhone build succeeded;
- iPad build succeeded;
- Aegis workspace check passed.

## Residual Risk

- Data remains preview/static.
- Real onboarding, task completion, persistence, AI planner, and StoreKit are not implemented in this slice.
- The current repository state is mostly untracked, so worktree isolation was not usable without first committing the project baseline.
