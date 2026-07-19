#!/usr/bin/env bash
set -euo pipefail

: "${BACKUP_ENABLED:?BACKUP_ENABLED is required}"
: "${BACKUP_DATABASE_URL:?BACKUP_DATABASE_URL is required}"
: "${BACKUP_DIRECTORY:?BACKUP_DIRECTORY is required}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT is required}"
: "${BACKUP_SIGNING_PRIVATE_KEY_FILE:?BACKUP_SIGNING_PRIVATE_KEY_FILE is required}"
: "${BACKUP_SOURCE_ENVIRONMENT_ID:?BACKUP_SOURCE_ENVIRONMENT_ID is required}"
: "${BACKUP_TARGET_ID:?BACKUP_TARGET_ID is required}"
: "${BACKUP_RETENTION_DAYS:?BACKUP_RETENTION_DAYS is required}"
: "${REQUIRED_DATABASE_REVISION:?REQUIRED_DATABASE_REVISION is required}"

if [[ "${BACKUP_ENABLED}" != "true" ]]; then
  printf '%s\n' "database backup is intentionally disabled" >&2
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
for command in age flock openssl pg_dump pg_restore python3 shasum tar; do
  command -v "${command}" >/dev/null || {
    printf '%s\n' "required backup command is unavailable: ${command}" >&2
    exit 69
  }
done

umask 077
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT
payload="${temporary}/payload"
dump="${payload}/database.dump"
manifest="${payload}/manifest.json"
bundle="${temporary}/anan-${timestamp}.backup.tar"
basename="anan-${timestamp}.backup.age"
encrypted="${BACKUP_DIRECTORY}/${basename}"
encrypted_partial="${temporary}/${basename}"
signature_partial="${temporary}/${basename}.sig"
checksum_partial="${temporary}/${basename}.sha256"

mkdir -p "${BACKUP_DIRECTORY}"
exec 9>"${BACKUP_DIRECTORY}/.anan-backup.lock"
if ! flock --nonblock 9; then
  printf '%s\n' "another database backup is already running" >&2
  exit 75
fi
mkdir -p "${payload}"
python3 "$(dirname "$0")/backup_artifact.py" capture \
  --database-url "${BACKUP_DATABASE_URL}" \
  --dump "${dump}" \
  --manifest "${manifest}" \
  --required-revision "${REQUIRED_DATABASE_REVISION}" \
  --source-environment-id "${BACKUP_SOURCE_ENVIRONMENT_ID}" \
  --backup-target-id "${BACKUP_TARGET_ID}"
tar --create --file "${bundle}" --directory "${payload}" manifest.json database.dump
age --recipient "${AGE_RECIPIENT}" --output "${encrypted_partial}" "${bundle}"
openssl dgst -sha256 -sign "${BACKUP_SIGNING_PRIVATE_KEY_FILE}" \
  -out "${signature_partial}" "${encrypted_partial}"
digest="$(shasum -a 256 "${encrypted_partial}" | awk '{print $1}')"
printf '%s  %s\n' "${digest}" "${basename}" > "${checksum_partial}"

# Publish sidecars first and the encrypted artifact last, so the .age filename is
# the atomic indication that a complete backup set is available.
mv "${signature_partial}" "${encrypted}.sig"
mv "${checksum_partial}" "${encrypted}.sha256"
mv "${encrypted_partial}" "${encrypted}"
find "${BACKUP_DIRECTORY}" -type f -name 'anan-*.backup.age*' \
  -mtime "+${BACKUP_RETENTION_DAYS}" -delete
printf '%s\n' "${encrypted}"
