#!/usr/bin/env bash
set -uo pipefail

AUDIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_ROOT="$AUDIT_ROOT/audit/subagents/a/runtime-project"
OUTPUT_DIR="$AUDIT_ROOT/audit/baseline/wechat-preview"
CLI="/Applications/wechatwebdevtools.app/Contents/MacOS/cli"

mkdir -p "$OUTPUT_DIR"

started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
started_epoch="$(date '+%s')"

"$CLI" preview \
  --project "$PROJECT_ROOT" \
  --qr-format image \
  --qr-output "$OUTPUT_DIR/preview-qr.png" \
  --info-output "$OUTPUT_DIR/preview-info.json" \
  >"$OUTPUT_DIR/preview.log" 2>&1
exit_code=$?

finished_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
finished_epoch="$(date '+%s')"
status="PASS"
if [[ "$exit_code" -ne 0 ]]; then
  status="FAIL"
fi

{
  printf 'status=%s\n' "$status"
  printf 'exit_code=%s\n' "$exit_code"
  printf 'started_at=%s\n' "$started_at"
  printf 'finished_at=%s\n' "$finished_at"
  printf 'duration_seconds=%s\n' "$((finished_epoch - started_epoch))"
  printf 'project_snapshot=audit/subagents/a/runtime-project\n'
  printf 'network_policy=loopback_fail_closed\n'
} >"$OUTPUT_DIR/result.txt"

cat "$OUTPUT_DIR/result.txt"
exit "$exit_code"
