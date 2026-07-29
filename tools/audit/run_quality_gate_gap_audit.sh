#!/usr/bin/env bash
set -uo pipefail

AUDIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="$AUDIT_ROOT/audit/baseline/quality-gate-gaps"
mkdir -p "$OUTPUT_DIR"

RESULTS_FILE="$OUTPUT_DIR/results.tsv"
printf 'check\tstatus\texit_code\tlog\n' >"$RESULTS_FILE"

run_check() {
  local check_name="$1"
  shift
  local log_path="$OUTPUT_DIR/${check_name}.log"
  (
    cd "$AUDIT_ROOT/apps/wechat" || exit 125
    "$@"
  ) >"$log_path" 2>&1
  local exit_code=$?
  local status="PASS"
  if [[ "$exit_code" -ne 0 ]]; then
    status="FAIL"
  fi
  printf '%s\t%s\t%s\t%s\n' \
    "$check_name" \
    "$status" \
    "$exit_code" \
    "audit/baseline/quality-gate-gaps/${check_name}.log" \
    >>"$RESULTS_FILE"
  printf '%s: %s (exit %s)\n' "$check_name" "$status" "$exit_code"
}

node <<'NODE' >"$OUTPUT_DIR/package-script-inventory.json"
const fs = require("node:fs");
const packageJson = JSON.parse(
  fs.readFileSync("apps/wechat/package.json", "utf8"),
);
const scriptFiles = fs
  .readdirSync("apps/wechat/scripts")
  .filter((name) => /\.(?:mjs|py)$/u.test(name))
  .sort();
const serializedScripts = Object.values(packageJson.scripts ?? {}).join("\n");
const inventory = scriptFiles.map((file) => ({
  file,
  referencedByPackageScript: serializedScripts.includes(file),
}));
process.stdout.write(
  `${JSON.stringify(
    {
      packageScripts: packageJson.scripts,
      auditClaims: {
        readmeApiCheckPresent: Boolean(packageJson.scripts?.["api:check"]),
        readmeStructureCheckPresent: Boolean(packageJson.scripts?.["structure:check"]),
      },
      scriptFiles: inventory,
    },
    null,
    2,
  )}\n`,
);
NODE

run_check "repository-verify-wechat" "$AUDIT_ROOT/scripts/verify_wechat.sh"
run_check "readme-api-check" npm run api:check
run_check "readme-structure-check" npm run structure:check
run_check "direct-openapi-check" node scripts/generate-api.mjs --check
run_check "unwired-validate-structure" node scripts/validate-structure.mjs
run_check "unwired-validate-visual-coverage" node scripts/validate-visual-coverage.mjs
run_check "unwired-validate-interaction-coverage" node scripts/validate-interaction-coverage.mjs
run_check "unwired-validate-anan-assets" node scripts/validate-anan-assets.mjs
