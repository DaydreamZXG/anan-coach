#!/usr/bin/env bash
set -euo pipefail

if [[ "${REQUIRED_DATABASE_REVISION+x}" == "x" ]]; then
  printf '%s\n' \
    "REQUIRED_DATABASE_REVISION is forbidden for backup; the current release artifact owns database compatibility" \
    >&2
  exit 64
fi

: "${BACKUP_ENABLED:?BACKUP_ENABLED is required}"
: "${BACKUP_DATABASE_URL:?BACKUP_DATABASE_URL is required}"
: "${BACKUP_DIRECTORY:?BACKUP_DIRECTORY is required}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT is required}"
: "${BACKUP_SIGNING_PRIVATE_KEY_FILE:?BACKUP_SIGNING_PRIVATE_KEY_FILE is required}"
: "${BACKUP_SOURCE_ENVIRONMENT_ID:?BACKUP_SOURCE_ENVIRONMENT_ID is required}"
: "${BACKUP_TARGET_AUTHORITY_FILE:?BACKUP_TARGET_AUTHORITY_FILE is required}"
: "${BACKUP_RETENTION_DAYS:?BACKUP_RETENTION_DAYS is required}"
: "${BACKUP_METRICS_FILE:?BACKUP_METRICS_FILE is required}"
: "${PGPASSFILE:?PGPASSFILE is required}"

if [[ "${BACKUP_ENABLED}" != "true" ]]; then
  printf '%s\n' "database backup is intentionally disabled" >&2
  exit 64
fi
if [[ -n "${BACKUP_TARGET_ID:-}" ]]; then
  printf '%s\n' \
    "BACKUP_TARGET_ID is forbidden; use the root-owned target authority" >&2
  exit 64
fi
if [[ "${BACKUP_TARGET_AUTHORITY_FILE}" != \
  "/etc/gongkao-dazi/backup-target-authority.json" \
  || "${PGPASSFILE}" != "/etc/gongkao-dazi/secrets/backup.pgpass" \
  || "${BACKUP_SIGNING_PRIVATE_KEY_FILE}" != \
  "/etc/gongkao-dazi/secrets/backup-signing.pem" \
  || "${BACKUP_DIRECTORY}" != "/var/backups/gongkao-dazi/encrypted" \
  || "${BACKUP_METRICS_FILE}" != \
  "/var/lib/gongkao-backup/metrics/anan-backup.prom" ]]; then
  printf '%s\n' \
    "backup authority paths must match the systemd isolation contract" >&2
  exit 64
fi
if [[ ! "${BACKUP_RETENTION_DAYS}" =~ ^[1-9][0-9]*$ ]]; then
  printf '%s\n' "BACKUP_RETENTION_DAYS must be a positive integer" >&2
  exit 64
fi
if [[ "${BACKUP_DIRECTORY}" != /* ]]; then
  printf '%s\n' "BACKUP_DIRECTORY must be an absolute persistent path" >&2
  exit 64
fi
if [[ "${BACKUP_METRICS_FILE}" != /* ]]; then
  printf '%s\n' "BACKUP_METRICS_FILE must be an absolute path" >&2
  exit 64
fi
for command in age flock openssl pg_dump pg_restore shasum tar; do
  command -v "${command}" >/dev/null || {
    printf '%s\n' "required backup command is unavailable: ${command}" >&2
    exit 69
  }
done

umask 077
script_directory="$(cd "$(dirname "$0")" && pwd -P)"
release_root="$(cd "${script_directory}/../.." && pwd -P)"
artifact_helper="${script_directory}/backup_artifact.py"
backup_python="${release_root}/backend/.venv/bin/python"
artifact_database_contract="${release_root}/ops/release-db-compatibility.env"
test -f "${artifact_helper}"
test ! -L "${artifact_helper}"
test -x "${backup_python}"
test -f "${artifact_database_contract}"
test ! -L "${artifact_database_contract}"
"${backup_python}" "${artifact_helper}" verify-backup-authority \
  --target-authority-file "${BACKUP_TARGET_AUTHORITY_FILE}" \
  --source-environment-id "${BACKUP_SOURCE_ENVIRONMENT_ID}" \
  --backup-directory "${BACKUP_DIRECTORY}" \
  --signing-private-key-file "${BACKUP_SIGNING_PRIVATE_KEY_FILE}" \
  --passfile "${PGPASSFILE}" \
  >/dev/null
PYTHONPATH="${script_directory}" "${backup_python}" -c \
  'import os; from backup_artifact import _validate_database_url; _validate_database_url(os.environ["BACKUP_DATABASE_URL"])'
case "${BACKUP_DATABASE_URL}" in
  postgresql://*)
    compatibility_database_url="postgresql+psycopg://${BACKUP_DATABASE_URL#postgresql://}"
    ;;
  postgres://*)
    compatibility_database_url="postgresql+psycopg://${BACKUP_DATABASE_URL#postgres://}"
    ;;
  *)
    printf '%s\n' "database authority must be an explicit libpq URL" >&2
    exit 64
    ;;
esac
required_database_revision="$(
  PYTHONPATH="${release_root}/backend" \
    DATABASE_URL="${compatibility_database_url}" \
    "${backup_python}" -m app.core.database_compatibility assert-current \
      --backend-root "${release_root}/backend" \
      --contract "${artifact_database_contract}"
)"
if [[ -z "${required_database_revision}" ]]; then
  printf '%s\n' "release database compatibility validation returned no revision" >&2
  exit 1
fi
exec 9>"${BACKUP_DIRECTORY}/.anan-backup.lock"
if ! flock --nonblock 9; then
  printf '%s\n' "another database backup is already running" >&2
  exit 75
fi

run_id="$("${backup_python}" "${artifact_helper}" new-run-id)"
set_basename="anan-${run_id}.backup-set"
basename="anan-${run_id}.backup.age"
published_set="${BACKUP_DIRECTORY}/${set_basename}"
staging="$(
  mktemp -d "${BACKUP_DIRECTORY}/.${set_basename}.staging.XXXXXX"
)"
cleanup() {
  local exit_code=$?
  case "${staging}" in
    "${BACKUP_DIRECTORY}"/.anan-*.backup-set.staging.*)
      rm -rf -- "${staging}"
      ;;
    *) exit_code=1 ;;
  esac
  rm -f -- "${metrics_partial:-}"
  exit "${exit_code}"
}
trap cleanup EXIT

work="${staging}/.work"
payload="${work}/payload"
dump="${payload}/database.dump"
manifest="${payload}/manifest.json"
bundle="${work}/anan-${run_id}.backup.tar"
encrypted_partial="${staging}/.${basename}.partial"
signature_partial="${staging}/.${basename}.sig.partial"
checksum_partial="${staging}/.${basename}.sha256.partial"

mkdir -p "${payload}"
"${backup_python}" "${artifact_helper}" capture \
  --database-url "${BACKUP_DATABASE_URL}" \
  --dump "${dump}" \
  --manifest "${manifest}" \
  --required-revision "${required_database_revision}" \
  --source-environment-id "${BACKUP_SOURCE_ENVIRONMENT_ID}" \
  --backup-target-authority-file "${BACKUP_TARGET_AUTHORITY_FILE}" \
  --backup-directory "${BACKUP_DIRECTORY}"
tar --create --file "${bundle}" --directory "${payload}" manifest.json database.dump
age --recipient "${AGE_RECIPIENT}" --output "${encrypted_partial}" "${bundle}"
openssl dgst -sha256 -sign "${BACKUP_SIGNING_PRIVATE_KEY_FILE}" \
  -out "${signature_partial}" "${encrypted_partial}"
digest="$(shasum -a 256 "${encrypted_partial}" | awk '{print $1}')"
printf '%s  %s\n' "${digest}" "${basename}" > "${checksum_partial}"

# The only visible publication transition is a no-replace rename of this whole
# set directory. Until then every entry and parent name is hidden, on the same
# filesystem as BACKUP_DIRECTORY, and restore rejects hidden staging paths.
rm -rf -- "${work}"
encrypted="$(
  "${backup_python}" "${artifact_helper}" publish-set \
    --staging-directory "${staging}" \
    --destination-directory "${published_set}" \
    --artifact-basename "${basename}"
)"
metrics_directory="$(dirname "${BACKUP_METRICS_FILE}")"
test -d "${metrics_directory}"
metrics_partial="$(mktemp "${metrics_directory}/.anan-backup-metrics.XXXXXX")"
completed_at="$(date -u +%s)"
encrypted_bytes="$(wc -c < "${encrypted}" | tr -d ' ')"
# Retention is deliberately observation-only. Published backup history is
# evidence and must never be deleted as a side effect of creating a new backup.
# Any future destruction requires a separately authorised, independently
# audited lifecycle process.
retention_candidate_sets="$(
  find "${BACKUP_DIRECTORY}" -mindepth 1 -maxdepth 1 \
    -type d -name 'anan-*.backup-set' \
    -mtime "+${BACKUP_RETENTION_DAYS}" -print |
    wc -l | tr -d '[:space:]'
)"
legacy_retention_candidate_files="$(
  find "${BACKUP_DIRECTORY}" -mindepth 1 -maxdepth 1 \
    -type f -name 'anan-*.backup.age*' \
    -mtime "+${BACKUP_RETENTION_DAYS}" -print |
    wc -l | tr -d '[:space:]'
)"
{
  printf '%s\n' "# HELP anan_backup_last_success_unixtime Last successful encrypted backup."
  printf '%s\n' "# TYPE anan_backup_last_success_unixtime gauge"
  printf 'anan_backup_last_success_unixtime %s\n' "${completed_at}"
  printf '%s\n' "# HELP anan_backup_last_artifact_bytes Latest encrypted backup size."
  printf '%s\n' "# TYPE anan_backup_last_artifact_bytes gauge"
  printf 'anan_backup_last_artifact_bytes %s\n' "${encrypted_bytes}"
  printf '%s\n' "# HELP anan_backup_retention_candidate_sets Published backup sets older than the observation threshold; no automatic deletion occurs."
  printf '%s\n' "# TYPE anan_backup_retention_candidate_sets gauge"
  printf 'anan_backup_retention_candidate_sets %s\n' "${retention_candidate_sets}"
  printf '%s\n' "# HELP anan_backup_legacy_retention_candidate_files Legacy backup files older than the observation threshold; no automatic deletion occurs."
  printf '%s\n' "# TYPE anan_backup_legacy_retention_candidate_files gauge"
  printf 'anan_backup_legacy_retention_candidate_files %s\n' "${legacy_retention_candidate_files}"
} > "${metrics_partial}"
chmod 0644 "${metrics_partial}"
mv "${metrics_partial}" "${BACKUP_METRICS_FILE}"
printf '%s\n' "${encrypted}"
