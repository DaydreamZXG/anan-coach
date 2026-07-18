#!/usr/bin/env bash
set -euo pipefail

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"
: "${AGE_IDENTITY_FILE:?AGE_IDENTITY_FILE is required}"

temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT
dump="${temporary}/rehearsal.dump"

shasum -a 256 -c "${BACKUP_FILE}.sha256"
age --decrypt --identity "${AGE_IDENTITY_FILE}" --output "${dump}" "${BACKUP_FILE}"
pg_restore --list "${dump}" >/dev/null
pg_restore --clean --if-exists --no-owner --no-acl --dbname="${RESTORE_DATABASE_URL}" "${dump}"
psql "${RESTORE_DATABASE_URL}" -v ON_ERROR_STOP=1 -c "select count(*) from commerce.wechat_orders" >/dev/null
psql "${RESTORE_DATABASE_URL}" -v ON_ERROR_STOP=1 -c "select count(*) from privacy.data_export_requests" >/dev/null
printf '%s\n' "restore rehearsal passed"
