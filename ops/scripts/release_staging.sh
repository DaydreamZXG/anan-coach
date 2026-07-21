#!/usr/bin/env bash
set -euo pipefail

: "${STAGING_HOST:?STAGING_HOST is required}"
: "${STAGING_RELEASE_ROOT:?STAGING_RELEASE_ROOT is required}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
case "${release_id}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "release id contains unsupported characters" >&2
    exit 2
    ;;
esac
export RELEASE_ID="$release_id"
artifact="$(${root}/ops/scripts/build_immutable_artifact.sh)"
ssh_args=()
if [[ -n "${STAGING_SSH_IDENTITY_FILE:-}" ]]; then
  test -f "${STAGING_SSH_IDENTITY_FILE}"
  ssh_args=(-i "${STAGING_SSH_IDENTITY_FILE}")
fi
scp "${ssh_args[@]}" "${artifact}" "${artifact}.sha256" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/"
ssh "${ssh_args[@]}" "${STAGING_HOST}" \
  "set -eu; cd '${STAGING_RELEASE_ROOT}/incoming'; \
   shasum -a 256 -c 'anan-${release_id}.tar.gz.sha256'; \
   controller=\$(mktemp /tmp/anan-activate.XXXXXX); \
   trap 'rm -f \"\${controller}\"' EXIT; \
   tar -xOf 'anan-${release_id}.tar.gz' ops/scripts/activate_release.sh > \"\${controller}\"; \
   chmod 0700 \"\${controller}\"; \
   STAGING_RELEASE_ROOT='${STAGING_RELEASE_ROOT}' \"\${controller}\" '${release_id}'"
