#!/usr/bin/env bash
set -euo pipefail

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"
: "${AGE_IDENTITY_FILE:?AGE_IDENTITY_FILE is required}"
: "${BACKUP_SIGNING_PUBLIC_KEY_FILE:?BACKUP_SIGNING_PUBLIC_KEY_FILE is required}"
: "${RESTORE_REPORT_SIGNING_PRIVATE_KEY_FILE:?RESTORE_REPORT_SIGNING_PRIVATE_KEY_FILE is required}"
: "${RESTORE_REHEARSAL_ACK:?RESTORE_REHEARSAL_ACK is required}"
: "${RESTORE_TARGET_ENVIRONMENT_ID:?RESTORE_TARGET_ENVIRONMENT_ID is required}"
: "${RESTORE_TARGET_IDENTITY_FILE:?RESTORE_TARGET_IDENTITY_FILE is required}"
: "${RESTORE_REPORT_DIRECTORY:?RESTORE_REPORT_DIRECTORY is required}"
: "${RESTORE_METRICS_FILE:?RESTORE_METRICS_FILE is required}"

if [[ "${RESTORE_REHEARSAL_ACK}" != "isolated-disposable-database" ]]; then
  printf '%s\n' "restore requires an explicitly acknowledged isolated disposable database" >&2
  exit 64
fi
if [[ "${RESTORE_REPORT_DIRECTORY}" != /* ]]; then
  printf '%s\n' "RESTORE_REPORT_DIRECTORY must be an absolute persistent path" >&2
  exit 64
fi
if [[ "${RESTORE_METRICS_FILE}" != /* ]]; then
  printf '%s\n' "RESTORE_METRICS_FILE must be an absolute path" >&2
  exit 64
fi
for command in age openssl pg_restore python3 shasum; do
  command -v "${command}" >/dev/null || {
    printf '%s\n' "required restore command is unavailable: ${command}" >&2
    exit 69
  }
done

umask 077
artifact_helper="$(dirname "$0")/backup_artifact.py"
temporary=""
report_staging=""
metrics_partial=""
restore_lock_pid=""
restore_identity=""
restore_state_directory=""
cleanup() {
  local exit_code=$?
  if [[ -n "${restore_lock_pid}" ]]; then
    kill "${restore_lock_pid}" 2>/dev/null || true
    wait "${restore_lock_pid}" 2>/dev/null || true
    restore_lock_pid=""
  fi
  if [[ -n "${temporary}" ]]; then
    case "${temporary}" in
      "${restore_state_directory}"/.anan-restore-"${restore_identity}".work.*)
        rm -rf -- "${temporary}"
        ;;
      *) exit_code=1 ;;
    esac
  fi
  if [[ -n "${report_staging}" ]]; then
    case "${report_staging}" in
      "${RESTORE_REPORT_DIRECTORY}"/.anan-*.restore-report-set.staging."${restore_identity}".*)
        rm -rf -- "${report_staging}"
        ;;
      *) exit_code=1 ;;
    esac
  fi
  if [[ -n "${metrics_partial}" ]]; then
    rm -f -- "${metrics_partial}"
  fi
  exit "${exit_code}"
}
trap cleanup EXIT

mkdir -p "${RESTORE_REPORT_DIRECTORY}"
test -d "${RESTORE_REPORT_DIRECTORY}"
test ! -L "${RESTORE_REPORT_DIRECTORY}"
restore_identity="$(
  python3 "${artifact_helper}" restore-target-identity \
    --database-url "${RESTORE_DATABASE_URL}" \
    --target-environment-id "${RESTORE_TARGET_ENVIRONMENT_ID}" \
    --target-identity-file "${RESTORE_TARGET_IDENTITY_FILE}"
)"
restore_temp_parent="${TMPDIR:-/tmp}"
restore_state_directory="$(
  printf '%s/anan-restore-rehearsal-%s' \
    "${restore_temp_parent%/}" "${UID}"
)"
if [[ -n "${RESTORE_REHEARSAL_STATE_DIRECTORY:-}" ]]; then
  restore_state_directory="${RESTORE_REHEARSAL_STATE_DIRECTORY}"
fi
temporary="$(
  python3 "${artifact_helper}" create-restore-workspace \
    --state-directory "${restore_state_directory}" \
    --restore-identity "${restore_identity}"
)"
restore_lock_ready="${temporary}/restore-lock.ready"
python3 "${artifact_helper}" hold-restore-target-lock \
  --database-url "${RESTORE_DATABASE_URL}" \
  --target-environment-id "${RESTORE_TARGET_ENVIRONMENT_ID}" \
  --target-identity-file "${RESTORE_TARGET_IDENTITY_FILE}" \
  --expected-restore-identity "${restore_identity}" \
  --ready-file "${restore_lock_ready}" &
restore_lock_pid=$!
for ((attempt = 0; attempt < 200; attempt += 1)); do
  if [[ -s "${restore_lock_ready}" ]]; then
    break
  fi
  if ! kill -0 "${restore_lock_pid}" 2>/dev/null; then
    wait "${restore_lock_pid}" 2>/dev/null || true
    restore_lock_pid=""
    printf '%s\n' \
      "restore target is already locked or failed authority verification" >&2
    exit 75
  fi
  sleep 0.05
done
if [[ ! -s "${restore_lock_ready}" ]]; then
  printf '%s\n' "restore target lock readiness timed out" >&2
  exit 75
fi
python3 "${artifact_helper}" verify-restore-target-lock-ready \
  --ready-file "${restore_lock_ready}" \
  --expected-restore-identity "${restore_identity}" \
  --expected-holder-pid "${restore_lock_pid}"
python3 "${artifact_helper}" reap-restore-residue \
  --state-directory "${restore_state_directory}" \
  --report-directory "${RESTORE_REPORT_DIRECTORY}" \
  --restore-identity "${restore_identity}" \
  --active-workspace "${temporary}" >/dev/null

bundle="${temporary}/backup.tar"
payload="${temporary}/payload"
dump="${payload}/database.dump"
manifest="${payload}/manifest.json"
artifact_basename="$(basename "${BACKUP_FILE}")"
snapshot_directory="${temporary}/backup-set-snapshot"
python3 "${artifact_helper}" snapshot-restore-set \
  --backup-file "${BACKUP_FILE}" \
  --snapshot-directory "${snapshot_directory}" >/dev/null
snapshot_backup_file="${snapshot_directory}/${artifact_basename}"
rehearsal_run_id="$(python3 "${artifact_helper}" new-run-id)"
report_set_basename="anan-${rehearsal_run_id}.restore-report-set"
report_basename="${artifact_basename%.backup.age}-${rehearsal_run_id}-restore-report.json"
published_report_set="${RESTORE_REPORT_DIRECTORY}/${report_set_basename}"
report_staging="$(
  mktemp -d \
    "${RESTORE_REPORT_DIRECTORY}/.${report_set_basename}.staging.${restore_identity}.XXXXXX"
)"
report_partial="${report_staging}/.${report_basename}.partial"
signature_partial="${report_staging}/.${report_basename}.sig.partial"
checksum_partial="${report_staging}/.${report_basename}.sha256.partial"

(
  cd "${snapshot_directory}"
  shasum -a 256 -c "${artifact_basename}.sha256"
)
openssl dgst -sha256 -verify "${BACKUP_SIGNING_PUBLIC_KEY_FILE}" \
  -signature "${snapshot_backup_file}.sig" "${snapshot_backup_file}"
age --decrypt --identity "${AGE_IDENTITY_FILE}" \
  --output "${bundle}" "${snapshot_backup_file}"
python3 "${artifact_helper}" extract \
  --bundle "${bundle}" --output-directory "${payload}"
pg_restore --list "${dump}" >/dev/null
python3 "${artifact_helper}" restore-preflight \
  --database-url "${RESTORE_DATABASE_URL}" \
  --manifest "${manifest}" \
  --dump "${dump}" \
  --target-environment-id "${RESTORE_TARGET_ENVIRONMENT_ID}" \
  --target-identity-file "${RESTORE_TARGET_IDENTITY_FILE}"
pg_restore --exit-on-error --no-owner --no-acl \
  --dbname="${RESTORE_DATABASE_URL}" "${dump}"
artifact_sha256="$(shasum -a 256 "${snapshot_backup_file}" | awk '{print $1}')"
python3 "${artifact_helper}" verify-restore \
  --database-url "${RESTORE_DATABASE_URL}" \
  --manifest "${manifest}" \
  --dump "${dump}" \
  --target-environment-id "${RESTORE_TARGET_ENVIRONMENT_ID}" \
  --target-identity-file "${RESTORE_TARGET_IDENTITY_FILE}" \
  --artifact-sha256 "${artifact_sha256}" \
  --report "${report_partial}"
report_sha256="$(shasum -a 256 "${report_partial}" | awk '{print $1}')"
printf '%s  %s\n' "${report_sha256}" "${report_basename}" > "${checksum_partial}"
openssl dgst -sha256 -sign "${RESTORE_REPORT_SIGNING_PRIVATE_KEY_FILE}" \
  -out "${signature_partial}" "${report_partial}"
report="$(
  python3 "${artifact_helper}" publish-restore-report-set \
    --staging-directory "${report_staging}" \
    --destination-directory "${published_report_set}" \
    --report-basename "${report_basename}"
)"
metrics_directory="$(dirname "${RESTORE_METRICS_FILE}")"
test -d "${metrics_directory}"
metrics_partial="$(mktemp "${metrics_directory}/.anan-restore-metrics.XXXXXX")"
{
  printf '%s\n' "# HELP anan_restore_rehearsal_last_success_unixtime Last verified isolated restore."
  printf '%s\n' "# TYPE anan_restore_rehearsal_last_success_unixtime gauge"
  printf 'anan_restore_rehearsal_last_success_unixtime %s\n' "$(date -u +%s)"
} > "${metrics_partial}"
chmod 0644 "${metrics_partial}"
mv "${metrics_partial}" "${RESTORE_METRICS_FILE}"
printf '%s\n' "${report}"
