#!/usr/bin/env bash
set -uo pipefail

AUDIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="$AUDIT_ROOT/audit/baseline"
mkdir -p "$OUTPUT_DIR"

RESULTS_FILE="$OUTPUT_DIR/results.tsv"
printf 'check\tstatus\texit_code\tstarted_at\tfinished_at\tduration_seconds\tlog\n' >"$RESULTS_FILE"

run_check() {
  local check_name="$1"
  local work_dir="$2"
  shift 2

  local started_at
  local finished_at
  local started_epoch
  local finished_epoch
  local exit_code
  local status
  local log_path="$OUTPUT_DIR/${check_name}.log"

  started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  started_epoch="$(date '+%s')"
  (
    cd "$work_dir" || exit 125
    "$@"
  ) >"$log_path" 2>&1
  exit_code=$?
  finished_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  finished_epoch="$(date '+%s')"

  if [[ "$exit_code" -eq 0 ]]; then
    status="PASS"
  else
    status="FAIL"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$check_name" \
    "$status" \
    "$exit_code" \
    "$started_at" \
    "$finished_at" \
    "$((finished_epoch - started_epoch))" \
    "audit/baseline/${check_name}.log" \
    >>"$RESULTS_FILE"

  printf '%s: %s (exit %s, %ss)\n' \
    "$check_name" \
    "$status" \
    "$exit_code" \
    "$((finished_epoch - started_epoch))"
}

{
  printf 'audit_started_at_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'workspace=%s\n' "$AUDIT_ROOT"
  printf 'git_commit=%s\n' "$(git -C "$AUDIT_ROOT" rev-parse HEAD)"
  printf 'git_branch=%s\n' "$(git -C "$AUDIT_ROOT" branch --show-current)"
  printf 'node=%s\n' "$(node --version)"
  printf 'npm=%s\n' "$(npm --version)"
  printf 'backend_python=%s\n' "$("$AUDIT_ROOT/backend/.venv/bin/python" --version 2>&1)"
  printf 'uv=%s\n' "$(uv --version)"
  if [[ -n "${DATABASE_URL+x}" ]]; then
    printf 'database_url=SET_NOT_USED\n'
  else
    printf 'database_url=UNSET\n'
  fi
  if [[ -n "${TEST_DATABASE_URL+x}" ]]; then
    printf 'test_database_url=SET_NOT_USED\n'
  else
    printf 'test_database_url=UNSET_LOCAL_EPHEMERAL_POSTGRES\n'
  fi
} >"$OUTPUT_DIR/environment.txt"

git -C "$AUDIT_ROOT" status --short --branch >"$OUTPUT_DIR/git-status-before.txt"

run_check "architecture" "$AUDIT_ROOT" \
  env PYTHONDONTWRITEBYTECODE=1 "$AUDIT_ROOT/scripts/check_architecture.py"

run_check "wechat-format" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run format:check

run_check "wechat-typecheck" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run typecheck

run_check "wechat-lint" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 "$AUDIT_ROOT/apps/wechat/node_modules/.bin/eslint" . --max-warnings 0

run_check "wechat-tokens" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run tokens:validate

run_check "wechat-interactions" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run interactions:validate

run_check "wechat-controls" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run controls:validate

run_check "wechat-navigation" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run navigation:validate

run_check "wechat-design" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run design:validate

run_check "wechat-vitest" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 "$AUDIT_ROOT/apps/wechat/node_modules/.bin/vitest" run \
    --no-cache \
    --reporter=verbose

run_check "wechat-cloud-tests" "$AUDIT_ROOT/apps/wechat" \
  env CI=1 npm run test:cloud

run_check "backend-ruff" "$AUDIT_ROOT/backend" \
  env PYTHONDONTWRITEBYTECODE=1 "$AUDIT_ROOT/backend/.venv/bin/python" \
    -m ruff check . --no-cache

run_check "backend-pytest" "$AUDIT_ROOT/backend" \
  env PYTHONDONTWRITEBYTECODE=1 TEST_DATABASE_URL= \
    "$AUDIT_ROOT/backend/.venv/bin/python" \
    -m pytest \
    -p no:cacheprovider \
    --junitxml="$OUTPUT_DIR/backend-pytest.xml" \
    --durations=25

run_check "alembic-heads" "$AUDIT_ROOT/backend" \
  env PYTHONDONTWRITEBYTECODE=1 DATABASE_URL= \
    "$AUDIT_ROOT/backend/.venv/bin/python" -m alembic heads

run_check "alembic-history" "$AUDIT_ROOT/backend" \
  env PYTHONDONTWRITEBYTECODE=1 DATABASE_URL= \
    "$AUDIT_ROOT/backend/.venv/bin/python" -m alembic history --verbose

git -C "$AUDIT_ROOT" status --short --branch >"$OUTPUT_DIR/git-status-after.txt"
