#!/usr/bin/env bash
set -Eeuo pipefail

# This file is not a standalone publisher.  commercial_data_publication.py
# execs it only after PostgreSQL has minted fd 8 from the exact session holding
# the global exclusive advisory lock.  Every child keeps descriptors 8 and 9;
# therefore controller death cannot release either lock while an in-flight
# mutation is still running, and no detached parent can start the next one.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
publication_helper="${root}/ops/scripts/commercial_data_publication.py"

: "${ANAN_PUBLICATION_REQUEST_DIGEST:?publication request digest is required}"
: "${ANAN_AUTHORITY_STATUS:?publication authority status is required}"
: "${ANAN_POINTER_RECEIPT:?publication pointer receipt is required}"
: "${ANAN_POINTER_SIGNATURE:?publication pointer signature is required}"
: "${ANAN_POINTER_RECEIPT_SHA256:?publication pointer digest is required}"
: "${ANAN_ARTIFACT_REVISION:?publication artifact revision is required}"
: "${ANAN_TEMPORARY_ROOT:?publication temporary root is required}"
: "${ANAN_AUTHORITY_ROOT:?publication authority root is required}"
: "${ANAN_COORDINATION_LOCK:?publication coordination lock is required}"
: "${ANAN_POSITION_WINDOW_MANIFEST:?position window manifest is required}"
: "${EXPECTED_DEPLOYMENT_ENVIRONMENT:?EXPECTED_DEPLOYMENT_ENVIRONMENT is required}"
: "${EXPECTED_GIT_COMMIT:?EXPECTED_GIT_COMMIT is required}"
: "${ROLLBACK_GIT_COMMIT:?ROLLBACK_GIT_COMMIT is required}"
: "${DATA_RELEASE_SIGNING_PRIVATE_KEY:?DATA_RELEASE_SIGNING_PRIVATE_KEY is required}"
: "${DATA_RELEASE_TRUST_PUBLIC_KEY:?DATA_RELEASE_TRUST_PUBLIC_KEY is required}"
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
: "${MIGRATION_PGPASSFILE:?MIGRATION_PGPASSFILE is required}"
: "${REQUIRED_DATABASE_REVISION:?REQUIRED_DATABASE_REVISION is required}"
: "${GONGKAO_POSITION_RELEASE_EXAM_YEAR:?position exam year is required}"
: "${GONGKAO_POSITION_RULE_SET_VERSION:?position rule-set version is required}"
: "${GONGKAO_POSITION_DATA_VERSION:?position data version is required}"
: "${GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION:?knowledge release version is required}"
: "${GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION:?knowledge source version is required}"
: "${GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL:?knowledge freshness is required}"
: "${COMMERCIAL_DATA_PUBLICATION_FENCE_FD:?publication fence fd is required}"
: "${COMMERCIAL_DATA_PUBLICATION_FENCE_BACKEND_PID:?publication fence backend is required}"
: "${COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_ADDRESS:?publication fence client address is required}"
: "${COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_PORT:?publication fence client port is required}"
: "${COMMERCIAL_DATA_PUBLICATION_FENCE_REQUEST_DIGEST:?publication fence request is required}"

if [[ "${COMMERCIAL_DATA_PUBLICATION_FENCE_FD}" != "8" ]] \
  || [[ ! "${COMMERCIAL_DATA_PUBLICATION_FENCE_BACKEND_PID}" =~ ^[1-9][0-9]*$ ]] \
  || [[ ! "${COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_PORT}" =~ ^[1-9][0-9]*$ ]] \
  || [[ "${COMMERCIAL_DATA_PUBLICATION_FENCE_REQUEST_DIGEST}" \
    != "${ANAN_PUBLICATION_REQUEST_DIGEST}" ]]; then
  printf '%s\n' "commercial data publication fence identity is invalid" >&2
  exit 1
fi
if [[ "${ANAN_ARTIFACT_REVISION}" != "${REQUIRED_DATABASE_REVISION}" ]]; then
  printf '%s\n' "locked publication revision changed after preflight" >&2
  exit 1
fi
case "${ANAN_AUTHORITY_STATUS}" in
  initialized|uninitialized) ;;
  *)
    printf '%s\n' "locked publication authority status is invalid" >&2
    exit 1
    ;;
esac
case "${ANAN_POINTER_ACTION:-}" in
  ""|redo-publication|verify-pointer-and-finish|verify-active-noop-or-start) ;;
  *)
    printf '%s\n' "locked publication pointer action is invalid" >&2
    exit 1
    ;;
esac
for safe_path in \
  "${ANAN_POINTER_RECEIPT}" \
  "${ANAN_POINTER_SIGNATURE}" \
  "${ANAN_TEMPORARY_ROOT}" \
  "${ANAN_AUTHORITY_ROOT}" \
  "${ANAN_COORDINATION_LOCK}" \
  "${ANAN_POSITION_WINDOW_MANIFEST}" \
  "${DATA_RELEASE_SIGNING_PRIVATE_KEY}" \
  "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  "${MIGRATION_PGPASSFILE}"; do
  if [[ ! "${safe_path}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "locked publication path is not a safe absolute path" >&2
    exit 2
  fi
done
case "${ANAN_TEMPORARY_ROOT}" in
  /tmp/anan-data-release.*) ;;
  *)
    printf '%s\n' "locked publication temporary root is invalid" >&2
    exit 1
    ;;
esac

migration_database_os_user="${MIGRATION_DATABASE_OS_USER:-postgres}"
knowledge_database_url="${
  MIGRATION_DATABASE_URL/postgresql+psycopg:/postgresql:
}"
artifact_contract="${root}/ops/release-db-compatibility.env"
artifact_revision="$(
  "${root}/backend/.venv/bin/python" \
    -m app.core.database_compatibility head \
    --backend-root "${root}/backend"
)"
if [[ "${artifact_revision}" != "${ANAN_ARTIFACT_REVISION}" ]]; then
  printf '%s\n' "locked publication artifact changed after preflight" >&2
  exit 1
fi
position_window_manifest="$(realpath "${ANAN_POSITION_WINDOW_MANIFEST}")"
if [[ "${position_window_manifest}" != "${ANAN_POSITION_WINDOW_MANIFEST}" ]]; then
  printf '%s\n' "locked publication position manifest changed after preflight" >&2
  exit 1
fi

authority_root="${ANAN_AUTHORITY_ROOT}"
revisions_root="${authority_root}/revisions"
coordination_lock="${ANAN_COORDINATION_LOCK}"
pointer_receipt="${ANAN_POINTER_RECEIPT}"
pointer_signature="${ANAN_POINTER_SIGNATURE}"
pointer_receipt_sha256="${ANAN_POINTER_RECEIPT_SHA256}"
temporary_root="${ANAN_TEMPORARY_ROOT}"
temporary_receipt="${temporary_root}/receipt.json"
temporary_signature="${temporary_root}/receipt.json.sig"
publication_request_digest="${ANAN_PUBLICATION_REQUEST_DIGEST}"

test -d "${temporary_root}"
test ! -L "${temporary_root}"
test -d "${revisions_root}"
test ! -L "${revisions_root}"
test -f "${pointer_receipt}"
test ! -L "${pointer_receipt}"
test -f "${pointer_signature}"
test ! -L "${pointer_signature}"

# This is an initial provenance assertion, not the lifetime mechanism.  fd 8
# itself continuously owns the PostgreSQL session lock until this controller
# and every inherited in-flight child have exited.
"${root}/backend/.venv/bin/python" \
  "${publication_helper}" assert-inherited-fence \
  --request-digest "${publication_request_digest}" \
  --backend-pid "${COMMERCIAL_DATA_PUBLICATION_FENCE_BACKEND_PID}" \
  --client-address "${COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_ADDRESS}" \
  --client-port "${COMMERCIAL_DATA_PUBLICATION_FENCE_CLIENT_PORT}" \
  --fence-fd 8 >/dev/null

assert_root_owned_file() {
  local path="$1"
  test -f "${path}"
  test ! -L "${path}"
  test "$(stat -c '%u' "${path}")" = 0
  test -z "$(find "${path}" -perm /022 -print -quit)"
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
  sudo -C 10 -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${root}/ops/scripts/data_release_authority.py" \
      "${verify_arguments[@]}" \
      verify \
      --receipt "${receipt}" >/dev/null
}

if [[ "${ANAN_AUTHORITY_STATUS}" != "uninitialized" ]] \
  && [[ "${ANAN_POINTER_ACTION:-}" == "verify-pointer-and-finish" ]]; then
  # The previous process advanced the signed pointer but died before its final
  # DB transition. Never recapture (observedAt would create false authority).
  verify_pointer_live "${pointer_receipt}" "${pointer_signature}"
  sudo -C 10 -u "${migration_database_os_user}" \
    env -i \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      "${publication_helper}" finish \
      --request-digest "${publication_request_digest}" \
      --receipt-sha256 "${pointer_receipt_sha256}" >/dev/null
  sudo -C 10 -u "${migration_database_os_user}" \
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
    "resumed data authority ${pointer_receipt_sha256} without recapturing it"
  exit 0
fi

# From this point every mutation inherits the live PostgreSQL fence fd.  sudo's
# close-from override preserves fd 8 (database fence) and fd 9 (filesystem
# coordination lock) in the database mutation process.
sudo -C 10 -u "${migration_database_os_user}" \
  env -i \
    DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/backend/.venv/bin/alembic" upgrade head
current_revision="$(
  sudo -C 10 -u "${migration_database_os_user}" \
    env -i \
      DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${root}/backend/.venv/bin/python" \
      -m app.core.database_compatibility assert-current \
      --backend-root "${root}/backend" \
      --contract "${artifact_contract}"
)"
if [[ "${current_revision}" != "${artifact_revision}" ]]; then
  printf '%s\n' "database migration did not reach the candidate revision" >&2
  exit 1
fi

sudo -C 10 -u "${migration_database_os_user}" \
  env -i \
    DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    DATA_RELEASE_COORDINATION_LOCK_HELD=1 \
    DATA_RELEASE_COORDINATION_LOCK_PATH="${coordination_lock}" \
  "${root}/backend/.venv/bin/python" \
    "${root}/backend/scripts/compile_position_rule_release.py" \
    --exam-code guokao \
    --exam-year "${GONGKAO_POSITION_RELEASE_EXAM_YEAR}" \
    --rule-set-version "${GONGKAO_POSITION_RULE_SET_VERSION}" \
    --position-data-version "${GONGKAO_POSITION_DATA_VERSION}" \
    --window-manifest "${position_window_manifest}" \
    --adopt-legacy-unwindowed-active \
    --activate

GONGKAO_KNOWLEDGE_MAP_DATABASE_URL="${knowledge_database_url}" \
GONGKAO_KNOWLEDGE_MAP_DATABASE_OS_USER="${migration_database_os_user}" \
GONGKAO_KNOWLEDGE_MAP_PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/scripts/server_refresh_knowledge_map_nodes.sh" --local

sudo -C 10 -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/backend/.venv/bin/python" \
    "${root}/ops/scripts/data_release_authority.py" \
    --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    --expected-revision "${artifact_revision}" \
    --compatible-commit "${EXPECTED_GIT_COMMIT}" \
    --compatible-commit "${ROLLBACK_GIT_COMMIT}" \
    capture \
    --output "${temporary_receipt}" \
    --position-exam-year "${GONGKAO_POSITION_RELEASE_EXAM_YEAR}" >/dev/null
openssl dgst -sha256 \
  -sign "${DATA_RELEASE_SIGNING_PRIVATE_KEY}" \
  -out "${temporary_signature}" \
  "${temporary_receipt}"
openssl dgst -sha256 \
  -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${temporary_signature}" \
  "${temporary_receipt}"

receipt_sha256="$(shasum -a 256 "${temporary_receipt}" | awk '{print $1}')"
sudo -C 10 -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/backend/.venv/bin/python" \
    "${publication_helper}" record-receipt \
    --request-digest "${publication_request_digest}" \
    --receipt-sha256 "${receipt_sha256}" >/dev/null
sudo -C 10 -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/backend/.venv/bin/python" \
    "${publication_helper}" assert-pairing \
    --receipt-sha256 "${receipt_sha256}" \
    --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    --expected-revision "${artifact_revision}" \
    --compatible-commit "${EXPECTED_GIT_COMMIT}" \
    --compatible-commit "${ROLLBACK_GIT_COMMIT}" \
    --allow-recorded-publishing >/dev/null

revision_target="${revisions_root}/${receipt_sha256}"
if [[ -e "${revision_target}" ]]; then
  test -d "${revision_target}"
  test "$(stat -c '%u' "${revision_target}")" = 0
  test -z "$(find "${revision_target}" -maxdepth 0 -perm /022 -print -quit)"
  cmp -s "${temporary_receipt}" "${revision_target}/receipt.json"
  openssl dgst -sha256 \
    -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
    -signature "${revision_target}/receipt.json.sig" \
    "${revision_target}/receipt.json"
else
  revision_staging="${revisions_root}/.${receipt_sha256}.$$"
  test ! -e "${revision_staging}"
  install -d -m 0750 "${revision_staging}"
  install -m 0444 "${temporary_receipt}" "${revision_staging}/receipt.json"
  install -m 0444 "${temporary_signature}" "${revision_staging}/receipt.json.sig"
  mv "${revision_staging}" "${revision_target}"
fi
current_staging="${authority_root}/.current.$$"
test ! -e "${current_staging}"
ln -s "revisions/${receipt_sha256}" "${current_staging}"
mv -Tf "${current_staging}" "${authority_root}/current"

verify_pointer_live \
  "${revision_target}/receipt.json" \
  "${revision_target}/receipt.json.sig"
sudo -C 10 -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/backend/.venv/bin/python" \
    "${publication_helper}" finish \
    --request-digest "${publication_request_digest}" \
    --receipt-sha256 "${receipt_sha256}" >/dev/null
sudo -C 10 -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${root}/backend/.venv/bin/python" \
    "${publication_helper}" assert-pairing \
    --receipt-sha256 "${receipt_sha256}" \
    --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    --expected-revision "${artifact_revision}" \
    --compatible-commit "${EXPECTED_GIT_COMMIT}" \
    --compatible-commit "${ROLLBACK_GIT_COMMIT}" >/dev/null

printf '%s\n' \
  "published data authority ${receipt_sha256} at revision ${current_revision}"
