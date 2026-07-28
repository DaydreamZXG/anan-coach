#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
SOURCE_REPOSITORY=${WECHAT_AUDIT_SOURCE_REPOSITORY:-"$REPO_ROOT"}
SOURCE_REPOSITORY=$(CDPATH= cd -- "$SOURCE_REPOSITORY" && pwd)
SOURCE_ROOT="$SOURCE_REPOSITORY/apps/wechat"
SOURCE_COMMIT=${WECHAT_AUDIT_SOURCE_COMMIT:-}
RUNTIME_ROOT=${WECHAT_AUDIT_RUNTIME_ROOT:-}
RUNTIME_PARENT="$REPO_ROOT/audit/repair-evidence"

if [ "${#SOURCE_COMMIT}" -ne 40 ]; then
  echo "WECHAT_AUDIT_SOURCE_COMMIT must be a full 40-character lowercase Git commit" >&2
  exit 1
fi
case "$SOURCE_COMMIT" in
  *[!0-9a-f]*)
    echo "WECHAT_AUDIT_SOURCE_COMMIT must be a full 40-character lowercase Git commit" >&2
    exit 1
    ;;
esac

RUNTIME_DIRECTORY=$(dirname -- "$RUNTIME_ROOT")
RUNTIME_NAME=$(basename -- "$RUNTIME_ROOT")
case "$RUNTIME_NAME" in
  runtime-project-?*) ;;
  *)
    echo "WECHAT_AUDIT_RUNTIME_ROOT must be a unique path under $REPO_ROOT/audit/repair-evidence named runtime-project-*" >&2
    exit 1
    ;;
esac
if [ "$RUNTIME_DIRECTORY" != "$RUNTIME_PARENT" ]; then
  echo "WECHAT_AUDIT_RUNTIME_ROOT must be a direct child of $RUNTIME_PARENT" >&2
  exit 1
fi

if [ ! -d "$SOURCE_ROOT/miniprogram" ] || [ ! -f "$SOURCE_ROOT/project.config.json" ]; then
  echo "Refusing invalid WeChat source repository: $SOURCE_REPOSITORY" >&2
  exit 1
fi

assert_source_stable() {
  SOURCE_HEAD=$(git -C "$SOURCE_REPOSITORY" rev-parse --verify HEAD)
  if [ "$SOURCE_HEAD" != "$SOURCE_COMMIT" ]; then
    echo "WECHAT_AUDIT_SOURCE_COMMIT does not match source repository HEAD" >&2
    exit 1
  fi
  SOURCE_STATUS=$(git -C "$SOURCE_REPOSITORY" status --porcelain=v1 --untracked-files=normal)
  if [ -n "$SOURCE_STATUS" ]; then
    echo "Refusing dirty source worktree:" >&2
    echo "$SOURCE_STATUS" >&2
    exit 1
  fi
}

assert_source_stable

PREPARE_RELATIVE_PATH=tools/audit/c/prepare_runtime_project.sh
PREPARE_TOOL_COMMIT=$(git -C "$REPO_ROOT" rev-parse --verify HEAD)
PREPARE_HEAD_BLOB=$(git -C "$REPO_ROOT" rev-parse "HEAD:$PREPARE_RELATIVE_PATH")
PREPARE_WORKING_BLOB=$(git -C "$REPO_ROOT" hash-object "$REPO_ROOT/$PREPARE_RELATIVE_PATH")
PREPARE_STATUS=$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=normal -- "$PREPARE_RELATIVE_PATH")
if [ "$PREPARE_HEAD_BLOB" != "$PREPARE_WORKING_BLOB" ] || [ -n "$PREPARE_STATUS" ]; then
  echo "Refusing to run an uncommitted runtime preparation tool" >&2
  exit 1
fi

if [ ! -d "$RUNTIME_PARENT" ] || [ -L "$RUNTIME_PARENT" ]; then
  echo "Runtime parent must be an existing real directory: $RUNTIME_PARENT" >&2
  exit 1
fi
if [ -e "$RUNTIME_ROOT" ] || [ -L "$RUNTIME_ROOT" ]; then
  echo "Refusing to overwrite existing repair runtime: $RUNTIME_ROOT" >&2
  exit 1
fi
if [ ! -d "$SOURCE_ROOT/node_modules" ]; then
  echo "Source node_modules is required as a read-only runtime dependency" >&2
  exit 1
fi

mkdir "$RUNTIME_ROOT"

# The audit runtime is deliberately isolated from the formal project. DevTools may
# create project.private.config.json and compilation metadata here without touching
# apps/wechat or tracked configuration.
rsync -a \
  --exclude 'project.private.config.json' \
  "$SOURCE_ROOT/miniprogram/" "$RUNTIME_ROOT/miniprogram/"

if [ -d "$SOURCE_ROOT/cloudfunctions" ]; then
  rsync -a "$SOURCE_ROOT/cloudfunctions/" "$RUNTIME_ROOT/cloudfunctions/"
fi

for filename in package.json project.config.json tsconfig.json; do
  if [ -f "$SOURCE_ROOT/$filename" ]; then
    cp "$SOURCE_ROOT/$filename" "$RUNTIME_ROOT/$filename"
  fi
done

ln -s "$SOURCE_ROOT/node_modules" "$RUNTIME_ROOT/node_modules"

AUDIT_ENVIRONMENT="$RUNTIME_ROOT/miniprogram/config/environment.ts"
perl -0pi -e '
  s#apiBaseUrl: "https://kg\.xingguoxian\.cn"#apiBaseUrl: "http://127.0.0.1:9"#g;
  s#mapleMonoCnRegularUrl: "https://kg\.xingguoxian\.cn/assets/fonts/maple-mono-cn-regular-v7\.9\.woff"#mapleMonoCnRegularUrl: null#g;
  s#mapleMonoCnBoldUrl: "https://kg\.xingguoxian\.cn/assets/fonts/maple-mono-cn-bold-v7\.9\.woff"#mapleMonoCnBoldUrl: null#g;
  s#ownedHttpsOrigins: \["https://kg\.xingguoxian\.cn"\]#ownedHttpsOrigins: []#g;
  s#supportGroupQrImageUrl: "[^"]*"#supportGroupQrImageUrl: null#g;
' "$AUDIT_ENVIRONMENT"

grep -Fq 'apiBaseUrl: "http://127.0.0.1:9"' "$AUDIT_ENVIRONMENT"
grep -Fq 'mapleMonoCnRegularUrl: null' "$AUDIT_ENVIRONMENT"
grep -Fq 'mapleMonoCnBoldUrl: null' "$AUDIT_ENVIRONMENT"
grep -Fq 'ownedHttpsOrigins: []' "$AUDIT_ENVIRONMENT"
grep -Fq 'supportGroupQrImageUrl: null' "$AUDIT_ENVIRONMENT"
AUDIT_URLS=$(grep -Eo 'https?://[^"]+' "$AUDIT_ENVIRONMENT" | sort -u)
if [ "$AUDIT_URLS" != "http://127.0.0.1:9" ]; then
  echo "Refusing runtime environment with a non-local URL" >&2
  exit 1
fi

RUNTIME_NONCE=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
if [ "${#RUNTIME_NONCE}" -ne 64 ]; then
  echo "Failed to generate a 32-byte audit runtime nonce" >&2
  exit 1
fi
case "$RUNTIME_NONCE" in
  *[!0-9a-f]*)
    echo "Failed to generate a 32-byte audit runtime nonce" >&2
    exit 1
    ;;
esac

AUDIT_APP="$RUNTIME_ROOT/miniprogram/app.ts"
AUDIT_RUNTIME_NONCE="$RUNTIME_NONCE" AUDIT_SOURCE_COMMIT="$SOURCE_COMMIT" \
  perl -0pi -e '
    my $interface_count = s/export interface CoachAppState \{\n/export interface CoachAppState {\n  __wechatAuditRuntimeIdentity: {\n    schemaVersion: 1;\n    nonce: string;\n    sourceCommit: string;\n  };\n/;
    my $data_count = s/  globalData: \{\n/  globalData: {\n    __wechatAuditRuntimeIdentity: {\n      schemaVersion: 1,\n      nonce: "$ENV{AUDIT_RUNTIME_NONCE}",\n      sourceCommit: "$ENV{AUDIT_SOURCE_COMMIT}",\n    },\n/;
    die "runtime identity injection failed\n"
      unless $interface_count == 1 && $data_count == 1;
  ' "$AUDIT_APP"

grep -Fq "__wechatAuditRuntimeIdentity" "$AUDIT_APP"
grep -Fq "nonce: \"$RUNTIME_NONCE\"" "$AUDIT_APP"
grep -Fq "sourceCommit: \"$SOURCE_COMMIT\"" "$AUDIT_APP"

# Re-check after all source reads and runtime-only mutations. A concurrent
# source change makes this unique runtime unusable rather than producing
# ambiguous provenance markers.
assert_source_stable

printf '%s\n' "$SOURCE_COMMIT" > "$RUNTIME_ROOT/.audit-source-commit"
printf '%s\n' \
  "{\"schemaVersion\":1,\"nonce\":\"$RUNTIME_NONCE\",\"sourceCommit\":\"$SOURCE_COMMIT\",\"prepareToolCommit\":\"$PREPARE_TOOL_COMMIT\",\"prepareToolBlob\":\"$PREPARE_HEAD_BLOB\"}" \
  > "$RUNTIME_ROOT/.audit-runtime-identity.json"
echo "$RUNTIME_ROOT"
