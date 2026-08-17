#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
config_root="${GONGKAO_CONFIG_ROOT:-/etc/gongkao-dazi}"
authority_root="${DATA_RELEASE_AUTHORITY_ROOT:-${config_root}/data-releases}"
coordination_lock="${DATA_RELEASE_COORDINATION_LOCK:-${config_root}/data-release.lock}"
release_root="${STAGING_RELEASE_ROOT:-/srv/gongkao-dazi/releases}"
current_link="${GONGKAO_CURRENT_LINK:-/srv/gongkao-dazi/current}"
publication_helper="${root}/ops/scripts/commercial_data_publication.py"
locked_publication="${root}/ops/scripts/publish_commercial_data_release_locked.sh"

: "${EXPECTED_DEPLOYMENT_ENVIRONMENT:?EXPECTED_DEPLOYMENT_ENVIRONMENT is required}"
: "${EXPECTED_GIT_COMMIT:?EXPECTED_GIT_COMMIT is required}"
: "${ROLLBACK_GIT_COMMIT:?ROLLBACK_GIT_COMMIT is required}"
: "${DATA_RELEASE_SIGNING_PRIVATE_KEY:?DATA_RELEASE_SIGNING_PRIVATE_KEY is required}"
: "${DATA_RELEASE_TRUST_PUBLIC_KEY:?DATA_RELEASE_TRUST_PUBLIC_KEY is required}"
: "${RELEASE_TRUST_PUBLIC_KEY:?RELEASE_TRUST_PUBLIC_KEY is required}"

for commit in "${EXPECTED_GIT_COMMIT}" "${ROLLBACK_GIT_COMMIT}"; do
  if [[ ! "${commit}" =~ ^[0-9a-f]{40,64}$ ]]; then
    printf '%s\n' "data release application commit is invalid" >&2
    exit 2
  fi
done
if [[ "${EXPECTED_GIT_COMMIT}" == "${ROLLBACK_GIT_COMMIT}" ]]; then
  printf '%s\n' "candidate and rollback commits must differ" >&2
  exit 2
fi
case "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "deployment environment is invalid" >&2
    exit 2
    ;;
esac

assert_root_owned_file() {
  local path="$1"
  test -f "${path}"
  test ! -L "${path}"
  test "$(stat -c '%u' "${path}")" = 0
  test -z "$(find "${path}" -perm /022 -print -quit)"
}

assert_guard_capable_release() {
  local manifest="$1"
  local release_root="$2"
  local marker="${release_root}/ops/commercial-data-publication-guard.json"
  assert_root_owned_file "${marker}"
  python3 - "${manifest}" "${marker}" <<'PY'
from hashlib import sha256
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
with open(sys.argv[2], "rb") as handle:
    encoded_marker = handle.read()
marker = json.loads(encoded_marker)
marker_digest = sha256(encoded_marker).hexdigest()
expected_marker = {
    "schemaVersion": "anan-release-capability/v1",
    "capability": "commercial-data-publication-guard",
    "contractVersion": "commercial-data-publication-guard@v1",
}
expected_authority = {
    "commercialDataPublication": {
        "capability": "commercial-data-publication-guard",
        "contractVersion": "commercial-data-publication-guard@v1",
        "markerPath": "ops/commercial-data-publication-guard.json",
        "markerSha256": marker_digest,
    }
}
if (
    marker != expected_marker
    or manifest.get("schemaVersion") != 4
    or manifest.get("guardCapabilities") != expected_authority
):
    raise SystemExit(
        "release lacks the signed commercial data publication guard capability"
    )
PY
}

source_trusted_env() {
  local path="$1"
  assert_root_owned_file "${path}"
  set -a
  # shellcheck disable=SC1090
  . "${path}"
  set +a
}

json_field() {
  local path="$1"
  local field="$2"
  python3 - "${path}" "${field}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
for part in sys.argv[2].split("."):
    value = value[part]
if value is None:
    print("")
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

for safe_path in \
  "${config_root}" \
  "${authority_root}" \
  "${coordination_lock}" \
  "${release_root}" \
  "${current_link}" \
  "${DATA_RELEASE_SIGNING_PRIVATE_KEY}" \
  "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  "${RELEASE_TRUST_PUBLIC_KEY}"; do
  if [[ ! "${safe_path}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "data publication path is not a safe absolute path" >&2
    exit 2
  fi
done
if [[ "$(git -C "${root}" rev-parse --is-inside-work-tree)" != "true" ]] \
  || [[ "$(git -C "${root}" rev-parse HEAD)" != "${EXPECTED_GIT_COMMIT}" ]] \
  || [[ -n "$(git -C "${root}" status --porcelain --untracked-files=normal)" ]]; then
  printf '%s\n' \
    "data publication requires the exact clean candidate Git checkout" >&2
  exit 1
fi

environment_file="${config_root}/release-environment"
assert_root_owned_file "${environment_file}"
actual_environment="$(tr -d '\r\n' < "${environment_file}")"
if [[ "${actual_environment}" != "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" ]]; then
  printf '%s\n' "data publication environment does not match target" >&2
  exit 1
fi
for trust_file in \
  "${DATA_RELEASE_SIGNING_PRIVATE_KEY}" \
  "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  "${RELEASE_TRUST_PUBLIC_KEY}"; do
  assert_root_owned_file "${trust_file}"
done
test "$(stat -c '%a' "${DATA_RELEASE_SIGNING_PRIVATE_KEY}")" = 600

test -d "${config_root}"
test "$(stat -c '%u' "${config_root}")" = 0
test -z "$(find "${config_root}" -maxdepth 0 -perm /022 -print -quit)"
if [[ ! -e "${coordination_lock}" ]]; then
  install -m 0600 /dev/null "${coordination_lock}"
fi
test -f "${coordination_lock}"
test ! -L "${coordination_lock}"
test "$(stat -c '%u' "${coordination_lock}")" = 0
test "$(stat -c '%a' "${coordination_lock}")" = 600
exec 9>>"${coordination_lock}"
flock -x 9
export DATA_RELEASE_COORDINATION_LOCK_HELD=1
export DATA_RELEASE_COORDINATION_LOCK_PATH="${coordination_lock}"

current_target="$(readlink -f "${current_link}")"
case "${current_target}" in
  "${release_root}/"*) ;;
  *)
    printf '%s\n' "current application release escapes the trusted root" >&2
    exit 1
    ;;
esac
current_release_id="$(basename "${current_target}")"
current_manifest="${current_target}/release-authority/release.json"
current_manifest_signature="${current_manifest}.sig"
assert_root_owned_file "${current_manifest}"
assert_root_owned_file "${current_manifest_signature}"
openssl dgst -sha256 \
  -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${current_manifest_signature}" \
  "${current_manifest}"
python3 - \
  "${current_manifest}" \
  "${current_release_id}" \
  "${ROLLBACK_GIT_COMMIT}" \
  "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    authority = json.load(handle)
if (
    authority.get("releaseId") != sys.argv[2]
    or authority.get("source", {}).get("gitCommit") != sys.argv[3]
    or authority.get("deployment", {}).get("environment") != sys.argv[4]
):
    raise SystemExit("current application release authority is invalid")
PY
assert_guard_capable_release "${current_manifest}" "${current_target}"

source_trusted_env "${config_root}/api.env"
source_trusted_env "${config_root}/db.env"
while IFS= read -r environment_name; do
  if [[ "${environment_name}" == PG* ]]; then
    printf '%s\n' \
      "commercial data publication rejects inherited ${environment_name}" >&2
    exit 1
  fi
done < <(compgen -e)
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
: "${MIGRATION_PGPASSFILE:?MIGRATION_PGPASSFILE is required}"
: "${REQUIRED_DATABASE_REVISION:?REQUIRED_DATABASE_REVISION is required}"
: "${GONGKAO_POSITION_RELEASE_EXAM_YEAR:?GONGKAO_POSITION_RELEASE_EXAM_YEAR is required}"
: "${GONGKAO_POSITION_RULE_SET_VERSION:?GONGKAO_POSITION_RULE_SET_VERSION is required}"
: "${GONGKAO_POSITION_DATA_VERSION:?GONGKAO_POSITION_DATA_VERSION is required}"
: "${GONGKAO_POSITION_RELEASE_WINDOW_MANIFEST:?GONGKAO_POSITION_RELEASE_WINDOW_MANIFEST is required}"
: "${GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION:?GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION is required}"
: "${GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION:?GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION is required}"
: "${GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL:?GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL is required}"
if [[ -n "${GONGKAO_KNOWLEDGE_MAP_DATABASE_URL:-}" ]]; then
  printf '%s\n' \
    "commercial data publication forbids a second knowledge-map database URL" \
    >&2
  exit 1
fi
knowledge_database_url="${MIGRATION_DATABASE_URL/postgresql+psycopg:/postgresql:}"
migration_database_os_user="${MIGRATION_DATABASE_OS_USER:-postgres}"
if [[ ! "${MIGRATION_PGPASSFILE}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  printf '%s\n' "migration pgpass file must be a safe absolute path" >&2
  exit 2
fi
test -f "${MIGRATION_PGPASSFILE}"
test ! -L "${MIGRATION_PGPASSFILE}"
test "$(stat -c '%a' "${MIGRATION_PGPASSFILE}")" = 600
test "$(stat -c '%u' "${MIGRATION_PGPASSFILE}")" = "$(
  id -u "${migration_database_os_user}"
)"

position_window_manifest="$(
  realpath "${GONGKAO_POSITION_RELEASE_WINDOW_MANIFEST}"
)"
case "${position_window_manifest}" in
  "${config_root}"/*) ;;
  *)
    printf '%s\n' \
      "position window manifest must be inside the trusted config root" >&2
    exit 1
    ;;
esac
assert_root_owned_file "${position_window_manifest}"
position_window_manifest_sha256="$(
  shasum -a 256 "${position_window_manifest}" | awk '{print $1}'
)"
test -x "${root}/backend/.venv/bin/alembic"
test -x "${root}/backend/.venv/bin/python"
test -x "${root}/scripts/server_refresh_knowledge_map_nodes.sh"
test -f "${publication_helper}"
test ! -L "${publication_helper}"
test -f "${locked_publication}"
test ! -L "${locked_publication}"

test -d "${authority_root}"
test ! -L "${authority_root}"
test "$(stat -c '%u' "${authority_root}")" = 0
test -z "$(find "${authority_root}" -maxdepth 0 -perm /022 -print -quit)"
revisions_root="${authority_root}/revisions"
install -d -m 0750 "${revisions_root}"
test -d "${revisions_root}"
test ! -L "${revisions_root}"
test "$(stat -c '%u' "${revisions_root}")" = 0
test -z "$(find "${revisions_root}" -maxdepth 0 -perm /022 -print -quit)"

authority_current_target="$(readlink -f "${authority_root}/current")"
case "${authority_current_target}" in
  "${revisions_root}/"*) ;;
  *)
    printf '%s\n' \
      "commercial data publication requires an offline signed baseline authority" \
      >&2
    exit 1
    ;;
esac
pointer_receipt="${authority_current_target}/receipt.json"
pointer_signature="${authority_current_target}/receipt.json.sig"
assert_root_owned_file "${pointer_receipt}"
assert_root_owned_file "${pointer_signature}"
pointer_receipt_sha256="$(
  shasum -a 256 "${pointer_receipt}" | awk '{print $1}'
)"
if [[ "$(basename "${authority_current_target}")" != "${pointer_receipt_sha256}" ]]; then
  printf '%s\n' "data authority directory does not match its receipt" >&2
  exit 1
fi
openssl dgst -sha256 \
  -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${pointer_signature}" \
  "${pointer_receipt}"

temporary_root="$(mktemp -d /tmp/anan-data-release.XXXXXX)"
fence_pgpass="$(mktemp /tmp/anan-data-release-pgpass.XXXXXX)"
cleanup() {
  local exit_code=$?
  case "${temporary_root}" in
    /tmp/anan-data-release.*) rm -rf -- "${temporary_root}" ;;
    *) exit_code=1 ;;
  esac
  case "${fence_pgpass}" in
    /tmp/anan-data-release-pgpass.*) rm -f -- "${fence_pgpass}" ;;
    *) exit_code=1 ;;
  esac
  exit "${exit_code}"
}
trap cleanup EXIT
install -m 0600 "${MIGRATION_PGPASSFILE}" "${fence_pgpass}"
test "$(stat -c '%u' "${fence_pgpass}")" = 0
chown "${migration_database_os_user}" "${temporary_root}"
chmod 0700 "${temporary_root}"
temporary_receipt="${temporary_root}/receipt.json"
temporary_signature="${temporary_root}/receipt.json.sig"
publication_request="${temporary_root}/publication-request.json"
publication_inspection="${temporary_root}/publication-inspection.json"
publication_begin="${temporary_root}/publication-begin.json"
publication_plan="${temporary_root}/publication-plan.json"
exclusive_ready="${temporary_root}/exclusive-lock-ready.json"
signing_probe="${temporary_root}/signing-probe.sig"

openssl dgst -sha256 \
  -sign "${DATA_RELEASE_SIGNING_PRIVATE_KEY}" \
  -out "${signing_probe}" \
  "${current_manifest}"
openssl dgst -sha256 \
  -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${signing_probe}" \
  "${current_manifest}"
rm -f -- "${signing_probe}"

artifact_contract="${root}/ops/release-db-compatibility.env"
artifact_revision="$(
  "${root}/backend/.venv/bin/python" \
    -m app.core.database_compatibility head \
    --backend-root "${root}/backend"
)"
if [[ "${artifact_revision}" != "${REQUIRED_DATABASE_REVISION}" ]]; then
  printf '%s\n' "configured database revision does not match candidate" >&2
  exit 1
fi
"${root}/backend/.venv/bin/python" \
  -m app.core.database_compatibility compatible-revisions \
  --backend-root "${root}/backend" \
  --contract "${artifact_contract}" >/dev/null
current_contract="${current_target}/ops/release-db-compatibility.env"
"${root}/backend/.venv/bin/python" \
  -m app.core.database_compatibility assert-revision \
  --backend-root "${current_target}/backend" \
  --contract "${current_contract}" \
  --revision "${artifact_revision}" >/dev/null

sudo -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/backend/.venv/bin/python" \
    "${root}/ops/scripts/data_release_authority.py" \
    --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    --expected-revision "${artifact_revision}" \
    --compatible-commit "${EXPECTED_GIT_COMMIT}" \
    --compatible-commit "${ROLLBACK_GIT_COMMIT}" \
    validate-connection >/dev/null

"${root}/backend/.venv/bin/python" "${publication_helper}" build-request \
  --output "${publication_request}" \
  --candidate-commit "${EXPECTED_GIT_COMMIT}" \
  --rollback-commit "${ROLLBACK_GIT_COMMIT}" \
  --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
  --expected-revision "${artifact_revision}" \
  --position-exam-code guokao \
  --position-exam-year "${GONGKAO_POSITION_RELEASE_EXAM_YEAR}" \
  --position-rule-set-version "${GONGKAO_POSITION_RULE_SET_VERSION}" \
  --position-data-version "${GONGKAO_POSITION_DATA_VERSION}" \
  --position-window-manifest-sha256 "${position_window_manifest_sha256}" \
  --knowledge-exam-scope guokao \
  --knowledge-track xingce \
  --knowledge-release-version "${GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION}" \
  --knowledge-source-data-version \
    "${GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION}" \
  --knowledge-fresh-until "${GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL}" >/dev/null
chown "${migration_database_os_user}" "${publication_request}"
chmod 0400 "${publication_request}"
publication_request_digest="$(
  "${root}/backend/.venv/bin/python" - "${publication_request}" <<'PY'
import json
import sys
from hashlib import sha256

with open(sys.argv[1], encoding="utf-8") as handle:
    request = json.load(handle)
encoded = json.dumps(
    request,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
print(sha256(encoded).hexdigest())
PY
)"

inspect_publication() {
  sudo -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${publication_helper}" inspect >"${publication_inspection}"
}

verify_pointer_live() {
  local receipt="$1"
  local signature="$2"
  local -a receipt_metadata
  local receipt_environment
  local receipt_revision
  local -a receipt_commits
  assert_root_owned_file "${receipt}"
  assert_root_owned_file "${signature}"
  openssl dgst -sha256 \
    -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
    -signature "${signature}" \
    "${receipt}"
  mapfile -t receipt_metadata < <(
    python3 - "${receipt}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    receipt = json.load(handle)
print(receipt["environment"])
print(receipt["database"]["alembicRevision"])
for commit in receipt["compatibleApplicationCommits"]:
    print(commit)
PY
  )
  if (( ${#receipt_metadata[@]} < 4 )); then
    printf '%s\n' "signed data receipt compatibility is incomplete" >&2
    return 1
  fi
  receipt_environment="${receipt_metadata[0]}"
  receipt_revision="${receipt_metadata[1]}"
  receipt_commits=("${receipt_metadata[@]:2}")
  if [[ "${receipt_environment}" != "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" ]]; then
    printf '%s\n' "signed data receipt environment is wrong" >&2
    return 1
  fi
  local -a verify_arguments=(
    --environment "${receipt_environment}"
    --expected-revision "${receipt_revision}"
  )
  local commit
  for commit in "${receipt_commits[@]}"; do
    verify_arguments+=(--compatible-commit "${commit}")
  done
  sudo -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${root}/ops/scripts/data_release_authority.py" \
      "${verify_arguments[@]}" \
      verify \
      --receipt "${receipt}" >/dev/null
}

inspect_publication
authority_status="$(json_field "${publication_inspection}" authorityStatus)"
begin_action=""
if [[ "${authority_status}" == "uninitialized" ]]; then
  # First adoption is only safe with an offline signed baseline and all traffic
  # stopped. Prove that baseline still describes the pre-migration database.
  if [[ "${COMMERCIAL_DATA_BOOTSTRAP_ACK:-}" \
    != "traffic-and-all-workers-stopped" ]]; then
    printf '%s\n' \
      "first publication requires the explicit offline bootstrap acknowledgement" \
      >&2
    exit 1
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    printf '%s\n' \
      "first publication requires systemd unit verification" >&2
    exit 1
  fi
  for bootstrap_unit in \
    gongkao-dazi-api.service \
    gongkao-dazi-cause-agent-worker.service \
    gongkao-dazi-coach-notification-worker.service \
    gongkao-dazi-commerce-worker.service \
    gongkao-dazi-data-rights-worker.service \
    gongkao-dazi-essay-review-worker.service \
    gongkao-dazi-external-learning-worker.service \
    gongkao-dazi-ilink-object-deletion-worker.service \
    openilink-hub.service; do
    if systemctl is-active --quiet "${bootstrap_unit}"; then
      printf '%s\n' \
        "first publication refuses active unit ${bootstrap_unit}" >&2
      exit 1
    fi
  done
  bootstrap_schema_version="$(
    json_field "${pointer_receipt}" schemaVersion
  )"
  if [[ "${bootstrap_schema_version}" \
    != "anan-data-bootstrap-baseline/v1" ]]; then
    printf '%s\n' \
      "first publication requires a signed bootstrap baseline receipt" >&2
    exit 1
  fi
  sudo -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${publication_helper}" bootstrap-upgrade-and-begin \
      --request "${publication_request}" \
      --bootstrap-receipt "${pointer_receipt}" \
      --bootstrap-receipt-sha256 "${pointer_receipt_sha256}" \
      --backend-root "${root}/backend" \
      >"${publication_begin}"
  previous_receipt_sha256="${pointer_receipt_sha256}"
  begin_action="$(json_field "${publication_begin}" action)"
else
  sudo -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${publication_helper}" classify-pointer \
      --pointer-receipt-sha256 "${pointer_receipt_sha256}" \
      >"${publication_plan}"
  pointer_action="$(json_field "${publication_plan}" action)"
  publication_state="$(json_field "${publication_inspection}" state)"
  if [[ "${publication_state}" == "active" ]]; then
    verify_pointer_live "${pointer_receipt}" "${pointer_signature}"
    previous_receipt_sha256="${pointer_receipt_sha256}"
  else
    previous_receipt_sha256="$(
      json_field "${publication_inspection}" previousReceiptSha256
    )"
  fi
fi

if [[ -z "${begin_action}" ]]; then
  sudo -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${publication_helper}" begin \
      --request "${publication_request}" \
      --previous-receipt-sha256 "${previous_receipt_sha256}" \
      >"${publication_begin}"
  begin_action="$(json_field "${publication_begin}" action)"
fi
if [[ "${begin_action}" == "already_active" ]]; then
  verify_pointer_live "${pointer_receipt}" "${pointer_signature}"
  sudo -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${publication_helper}" assert-pairing \
      --receipt-sha256 "${pointer_receipt_sha256}" \
      --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
      --expected-revision "${artifact_revision}" \
      --compatible-commit "${EXPECTED_GIT_COMMIT}" \
      --compatible-commit "${ROLLBACK_GIT_COMMIT}" >/dev/null
  printf '%s\n' \
    "commercial data authority ${pointer_receipt_sha256} is already active"
  trap - EXIT
  cleanup
fi

rm -f -- "${exclusive_ready}"
export ANAN_PUBLICATION_REQUEST_DIGEST="${publication_request_digest}"
export ANAN_AUTHORITY_STATUS="${authority_status}"
export ANAN_POINTER_ACTION="${pointer_action:-}"
export ANAN_POINTER_RECEIPT="${pointer_receipt}"
export ANAN_POINTER_SIGNATURE="${pointer_signature}"
export ANAN_POINTER_RECEIPT_SHA256="${pointer_receipt_sha256}"
export ANAN_ARTIFACT_REVISION="${artifact_revision}"
export ANAN_TEMPORARY_ROOT="${temporary_root}"
export ANAN_AUTHORITY_ROOT="${authority_root}"
export ANAN_COORDINATION_LOCK="${coordination_lock}"
export ANAN_POSITION_WINDOW_MANIFEST="${position_window_manifest}"

# The helper and the locked controller are one process identity: after the
# server grants the exclusive lock the helper duplicates that exact PostgreSQL
# session to inherited fd 8 and execs the controller.  If this PID is killed,
# this parent can only fail and clean up; it has no mutation path of its own.
PGPASSFILE="${fence_pgpass}" \
  "${root}/backend/.venv/bin/python" \
    "${publication_helper}" exec-exclusive \
    --request-digest "${publication_request_digest}" \
    --ready-file "${exclusive_ready}" \
    -- /bin/bash "${locked_publication}"
trap - EXIT
cleanup
