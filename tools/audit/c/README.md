# WeChat real-render audit tooling

These tools create an isolated WeChat DevTools runtime and capture real-render
evidence without changing the formal source worktree.

## Trust boundaries

- `WECHAT_AUDIT_SOURCE_COMMIT` is mandatory and must be the source
  repository's full 40-character `HEAD`.
- The complete source worktree must be clean before preparation or capture.
  Preparation checks again after copying; capture checks again before writing
  its manifest and marks the run failed if the source evidence drifted.
- The executing capture script and its safety module must have working blobs
  identical to their paths in the tool repository's `HEAD`. Their commit and
  blob IDs are written to every manifest.
- A DevTools automation endpoint is optional, but when supplied it must be a
  loopback WebSocket endpoint with an explicit port.
- Every prepared runtime receives a random 32-byte identity. The identity is
  stored on disk and injected only into the audit runtime copy's `app.ts`.
  Capture does not reserve output or install fixtures until the running
  DevTools service returns the exact same identity.
- `screenshots/` and `logs/` may not traverse symlinks. A profile directory is
  an atomic `mkdir` lock, while its log and manifest use exclusive-create file
  handles. Scenario IDs are safe labels and screenshot paths must remain strict
  descendants of that profile directory.

The prepared runtime's `node_modules` entry is intentionally a symlink to the
clean source worktree. It is a read-only dependency input, not an output path.
Audit output must be outside the runtime tree. The allowed output root itself
must be a real directory; below that boundary, no output ancestor may be a
symlink and every existing realpath must remain inside the boundary.

## Prepare a unique runtime

Use a clean source worktree and a never-before-used runtime name:

```sh
SOURCE_REPOSITORY=/absolute/path/to/clean/integration
SOURCE_COMMIT=$(git -C "$SOURCE_REPOSITORY" rev-parse HEAD)
RUNTIME_ROOT=/Users/zxg/Documents/新项目/audit/repair-evidence/runtime-project-final-001

WECHAT_AUDIT_SOURCE_REPOSITORY="$SOURCE_REPOSITORY" \
WECHAT_AUDIT_SOURCE_COMMIT="$SOURCE_COMMIT" \
WECHAT_AUDIT_RUNTIME_ROOT="$RUNTIME_ROOT" \
tools/audit/c/prepare_runtime_project.sh
```

The runtime must be a direct child of `audit/repair-evidence/` whose name starts
with `runtime-project-`. The preparation command refuses existing runtime
directories. It writes:

- `.audit-source-commit`
- `.audit-runtime-identity.json`
- a local-only `miniprogram/config/environment.ts`
- the runtime-only identity field in `miniprogram/app.ts`

## Capture

Launch mode opens the exact prepared runtime:

```sh
WECHAT_AUDIT_SOURCE_REPOSITORY="$SOURCE_REPOSITORY" \
WECHAT_AUDIT_SOURCE_COMMIT="$SOURCE_COMMIT" \
WECHAT_AUDIT_PROJECT="$RUNTIME_ROOT" \
WECHAT_AUDIT_OUTPUT_ROOT=/Users/zxg/Documents/新项目/audit/repair-evidence/final-real-render \
AUDIT_PROFILE=standard-390x844 \
AUDIT_EXPECT_WIDTH=390 \
AUDIT_EXPECT_HEIGHT=844 \
AUDIT_EXPECT_ORIENTATION=portrait \
AUDIT_SCENARIOS=h01-capture-search-idle \
node tools/audit/c/capture-real-ui.mjs
```

Connect mode is allowed only for a loopback endpoint. It still verifies the
running runtime identity before creating any output:

```sh
WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT=ws://127.0.0.1:9420 \
WECHAT_AUDIT_SOURCE_REPOSITORY="$SOURCE_REPOSITORY" \
WECHAT_AUDIT_SOURCE_COMMIT="$SOURCE_COMMIT" \
WECHAT_AUDIT_PROJECT="$RUNTIME_ROOT" \
WECHAT_AUDIT_OUTPUT_ROOT=/Users/zxg/Documents/新项目/audit/repair-evidence/final-real-render \
AUDIT_PROFILE=tablet-1024x768-landscape \
AUDIT_EXPECT_WIDTH=1024 \
AUDIT_EXPECT_HEIGHT=768 \
AUDIT_EXPECT_ORIENTATION=landscape \
AUDIT_SCENARIOS=c09-xingce-answer-card \
node tools/audit/c/capture-real-ui.mjs
```

In DevTools landscape mode, `systemInfo` retains the model's base portrait
dimensions and reports `deviceOrientation=landscape`; the native screenshot is
rotated. The capture validator models that behavior explicitly.

## Tests

```sh
node --check tools/audit/c/capture-real-ui.mjs
node --check tools/audit/c/capture-safety.mjs
node --test tools/audit/c/capture-safety.test.mjs
sh -n tools/audit/c/prepare_runtime_project.sh
```
