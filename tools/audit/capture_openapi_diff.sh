#!/usr/bin/env bash
set -euo pipefail

AUDIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="$AUDIT_ROOT/audit/api-contract"
SCHEMA_PATH="$OUTPUT_DIR/openapi.json"
GENERATED_DIR="$OUTPUT_DIR/generated"
COMMITTED_TYPES="$AUDIT_ROOT/apps/wechat/miniprogram/api/generated/types.gen.ts"
GENERATED_TYPES="$GENERATED_DIR/types.gen.ts"

mkdir -p "$OUTPUT_DIR" "$GENERATED_DIR"

env PYTHONDONTWRITEBYTECODE=1 \
  "$AUDIT_ROOT/backend/.venv/bin/python" \
  "$AUDIT_ROOT/backend/scripts/export_openapi.py" \
  --output "$SCHEMA_PATH" \
  >"$OUTPUT_DIR/export.log" 2>&1

"$AUDIT_ROOT/apps/wechat/node_modules/.bin/openapi-ts" \
  --input "$SCHEMA_PATH" \
  --output "$GENERATED_DIR" \
  --plugins "@hey-api/typescript" \
  --no-log-file \
  --silent \
  >"$OUTPUT_DIR/generate.log" 2>&1

{
  shasum -a 256 "$COMMITTED_TYPES"
  shasum -a 256 "$GENERATED_TYPES"
} >"$OUTPUT_DIR/hashes.txt"

set +e
diff -u "$COMMITTED_TYPES" "$GENERATED_TYPES" >"$OUTPUT_DIR/types.diff"
DIFF_EXIT=$?
set -e

{
  printf 'diff_exit_code=%s\n' "$DIFF_EXIT"
  printf 'committed_lines=%s\n' "$(wc -l <"$COMMITTED_TYPES" | tr -d ' ')"
  printf 'generated_lines=%s\n' "$(wc -l <"$GENERATED_TYPES" | tr -d ' ')"
  printf 'diff_lines=%s\n' "$(wc -l <"$OUTPUT_DIR/types.diff" | tr -d ' ')"
  printf 'schema_paths=%s\n' \
    "$("$AUDIT_ROOT/backend/.venv/bin/python" -c \
      'import json,sys; print(len(json.load(open(sys.argv[1]))["paths"]))' "$SCHEMA_PATH")"
} >"$OUTPUT_DIR/summary.txt"
