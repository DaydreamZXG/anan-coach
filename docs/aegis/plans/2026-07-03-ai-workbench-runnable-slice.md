# AI Workbench Runnable Slice Plan

Date: 2026-07-03
Status: Approved for inline execution

## Goal

Implement the first runnable UI/API slice that matches the approved 考公搭子 direction: a NotebookLM-like 公考学习工作台 with three first-level learner entrances: 今日, 工作台, 我的. 今日 and 工作台 must visibly support 行测 and 申论 as first-class study tracks.

## Architecture

- Backend remains FastAPI and owns preview contracts for app bootstrap, daily task stream, and workbench objects.
- iOS remains native SwiftUI and renders preview data locally for this slice.
- The canonical product direction is taskstream/workbench-first. Practice, positions, review, essay, and assistant are contextual execution modes, not top-level destinations.

## Tech Stack

- Backend: FastAPI, pytest.
- iOS/iPadOS: SwiftUI, AuthenticationServices.
- Verification: backend pytest, iPhone simulator build, iPad simulator build, Aegis workspace check.

## Baseline / Authority Refs

- `docs/aegis/specs/2026-07-03-ai-agent-information-architecture.md`
- `docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md`
- `/Users/zxg/Desktop/deep-research-report.md`
- Current implementation files:
  - `backend/app/api/routes.py`
  - `backend/tests/test_api.py`
  - `apps/ios/Gongkao/Gongkao/AppModels.swift`
  - `apps/ios/Gongkao/Gongkao/AppShellView.swift`
  - `apps/ios/Gongkao/Gongkao/FeatureViews.swift`

## Compatibility Boundary

- No migration or deletion of live data.
- No open-web Deep Research in first release.
- No standalone first-level `刷题`, `选岗`, `目标`, `复盘`, or `搭子`.
- AI/provider details stay server-side; the client receives feature state and quota metadata, not provider routing details.
- App Store/StoreKit authority remains planned but not implemented in this slice.

## Anti-Entropy Declaration

- Deletion Class: code-retirement.
- Old Path/Object: `practice` and `positions` app sections as first-level navigation; feature-sample backend preview naming.
- New Canonical Owner: `今日` taskstream, `工作台` generated study objects, `我的` account/entitlement surface.
- Expected Preserved Behavior: user can still see practice-like, position-like, and account preview content.
- Expected Retired Behavior: no top-level `刷题` or `选岗` mental model.
- External Boundary Touched: no.
- Source-of-Truth Data Risk: none.
- User Confirmation Required: no.

Retirement Decision:

- Path: delete-first for internal first-level navigation names and preview route tests.
- Why: there is no proven external dependency on old preview endpoints or old iOS tabs.
- Non-edits: do not delete qbank data, position data, docs history, or server inventory.

## Verification

Run:

```bash
backend/.venv/bin/python -m pytest backend/tests
xcodebuild build -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO
xcodebuild build -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPad Pro 13-inch (M5)' CODE_SIGNING_ALLOWED=NO
python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
```

Expected:

- pytest passes.
- iPhone and iPad builds succeed.
- no first-level iOS label contains `刷题` or `选岗`.
- backend tests assert taskstream/workbench preview contracts.

## Tasks

### Task 1: Backend Preview Contract

Files:

- Modify `backend/app/api/routes.py`
- Modify `backend/tests/test_api.py`

Steps:

1. Replace `/api/v1/today/preview` with `/api/v1/taskstream/today`.
2. Add `/api/v1/workbench/preview`.
3. Retire `/api/v1/practice/sample` and `/api/v1/positions/sample` so old feature-preview contracts do not remain as hidden owners.
4. Remove provider base URL/model from bootstrap payload; expose only AI feature quota/state.
5. Add tests for three first-level sections and 行测/申论 track mix.

### Task 2: SwiftUI Navigation And Data Model

Files:

- Modify `apps/ios/Gongkao/Gongkao/AppModels.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppShellView.swift`

Steps:

1. Replace `AppSection` with `today`, `workbench`, `profile`.
2. Add preview models for track mix, taskstream items, workbench objects, source grounding, and essay rewrite loop.
3. Update iPhone `TabView` to render three tabs only.
4. Update iPad shell to present a workbench layout instead of the same feature list.

### Task 3: SwiftUI Workbench Screens

Files:

- Modify `apps/ios/Gongkao/Gongkao/FeatureViews.swift`

Steps:

1. Replace old Today hero with AI-arranged daily plan and track mix.
2. Add Workbench view grouped by 行测训练, 申论写作, 岗位策略, 周计划.
3. Add iPad three-pane workbench surface: plan/object list, selected task/object, AI/source feedback.
4. Remove standalone `PracticeView` and `PositionsView` as top-level screens.
5. Keep `ProfileView` focused on Apple login, AI Pro, and quota.

### Task 4: Documentation And Verification

Files:

- Modify `README.md`
- Modify `scripts/full_verify.sh` if route/test names require it

Steps:

1. Update README to describe the current slice as `今日 / 工作台 / 我的`.
2. Run verification commands.
3. Run lingering-reference checks:

```bash
rg -n 'case \\.practice|case \\.positions|navigationTitle\\("刷题"\\)|navigationTitle\\("选岗"\\)|tab.*刷题|tab.*选岗' apps/ios/Gongkao/Gongkao
rg -n '/api/v1/today/preview|/api/v1/practice/sample|/api/v1/positions/sample' backend/app backend/tests README.md
```

Expected lingering-reference result:

- No app shell/top-level references to retired first-level sections.
- Old sample endpoints are not present in app/backend/tests/README main paths.

## Risks

- This is still preview data, not full production taskstream.
- The iPad surface is structurally correct but not yet backed by live API state.
- StoreKit and Sign in with Apple server verification remain future slices.

## Stop Condition

Stop when tests/builds pass and the app’s first-level experience no longer presents as a generic question bank or position browser.
