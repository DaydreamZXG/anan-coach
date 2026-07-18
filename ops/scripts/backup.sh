#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_DIRECTORY:?BACKUP_DIRECTORY is required}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT is required}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT
dump="${temporary}/anan-${timestamp}.dump"
encrypted="${BACKUP_DIRECTORY}/anan-${timestamp}.dump.age"

mkdir -p "${BACKUP_DIRECTORY}"
pg_dump --format=custom --no-owner --no-acl --file="${dump}" "${DATABASE_URL}"
pg_restore --list "${dump}" >/dev/null
age --recipient "${AGE_RECIPIENT}" --output "${encrypted}" "${dump}"
shasum -a 256 "${encrypted}" > "${encrypted}.sha256"
find "${BACKUP_DIRECTORY}" -type f -name 'anan-*.dump.age*' -mtime +35 -delete
printf '%s\n' "${encrypted}"
