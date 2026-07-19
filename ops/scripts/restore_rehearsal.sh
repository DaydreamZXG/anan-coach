#!/usr/bin/env bash
set -euo pipefail

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"
: "${AGE_IDENTITY_FILE:?AGE_IDENTITY_FILE is required}"
: "${BACKUP_SIGNING_PUBLIC_KEY_FILE:?BACKUP_SIGNING_PUBLIC_KEY_FILE is required}"
: "${RESTORE_REPORT_SIGNING_PRIVATE_KEY_FILE:?RESTORE_REPORT_SIGNING_PRIVATE_KEY_FILE is required}"
: "${RESTORE_REHEARSAL_ACK:?RESTORE_REHEARSAL_ACK is required}"
: "${RESTORE_TARGET_ENVIRONMENT_ID:?RESTORE_TARGET_ENVIRONMENT_ID is required}"
: "${RESTORE_REPORT_DIRECTORY:?RESTORE_REPORT_DIRECTORY is required}"

if [[ "${RESTORE_REHEARSAL_ACK}" != "isolated-disposable-database" ]]; then
  printf '%s\n' "restore requires an explicitly acknowledged isolated disposable database" >&2
  exit 64
fi
if [[ "${RESTORE_REPORT_DIRECTORY}" != /* ]]; then
  printf '%s\n' "RESTORE_REPORT_DIRECTORY must be an absolute persistent path" >&2
  exit 64
fi
for command in age openssl pg_restore python3 shasum; do
  command -v "${command}" >/dev/null || {
    printf '%s\n' "required restore command is unavailable: ${command}" >&2
    exit 69
  }
done

umask 077
temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT
bundle="${temporary}/backup.tar"
payload="${temporary}/payload"
dump="${payload}/database.dump"
manifest="${payload}/manifest.json"
artifact_basename="$(basename "${BACKUP_FILE}")"
rehearsal_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report="${RESTORE_REPORT_DIRECTORY}/${artifact_basename%.backup.age}-${rehearsal_timestamp}-restore-report.json"

(
  cd "$(dirname "${BACKUP_FILE}")"
  shasum -a 256 -c "${artifact_basename}.sha256"
)
openssl dgst -sha256 -verify "${BACKUP_SIGNING_PUBLIC_KEY_FILE}" \
  -signature "${BACKUP_FILE}.sig" "${BACKUP_FILE}"
age --decrypt --identity "${AGE_IDENTITY_FILE}" --output "${bundle}" "${BACKUP_FILE}"
python3 "$(dirname "$0")/backup_artifact.py" extract \
  --bundle "${bundle}" --output-directory "${payload}"
pg_restore --list "${dump}" >/dev/null
python3 "$(dirname "$0")/backup_artifact.py" restore-preflight \
  --database-url "${RESTORE_DATABASE_URL}" \
  --manifest "${manifest}" \
  --dump "${dump}" \
  --target-environment-id "${RESTORE_TARGET_ENVIRONMENT_ID}"
pg_restore --exit-on-error --no-owner --no-acl \
  --dbname="${RESTORE_DATABASE_URL}" "${dump}"
mkdir -p "${RESTORE_REPORT_DIRECTORY}"
artifact_sha256="$(shasum -a 256 "${BACKUP_FILE}" | awk '{print $1}')"
python3 "$(dirname "$0")/backup_artifact.py" verify-restore \
  --database-url "${RESTORE_DATABASE_URL}" \
  --manifest "${manifest}" \
  --dump "${dump}" \
  --target-environment-id "${RESTORE_TARGET_ENVIRONMENT_ID}" \
  --artifact-sha256 "${artifact_sha256}" \
  --report "${report}"
shasum -a 256 "${report}" > "${report}.sha256"
openssl dgst -sha256 -sign "${RESTORE_REPORT_SIGNING_PRIVATE_KEY_FILE}" \
  -out "${report}.sig" "${report}"
printf '%s\n' "${report}"
