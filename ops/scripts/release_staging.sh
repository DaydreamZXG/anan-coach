#!/usr/bin/env bash
set -euo pipefail

# Lightweight staging lane for small-scale product testing. Production keeps its
# separate release authority; this script only updates the isolated staging app.
: "${STAGING_HOST:?STAGING_HOST is required}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
staging_app_root="${STAGING_APP_ROOT:-/srv/gongkao-dazi/staging}"
staging_api_service="${STAGING_API_SERVICE:-gongkao-dazi-staging-api}"
staging_health_url="${STAGING_HEALTH_URL:-https://kg.xingguoxian.cn/staging/health}"
release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$(git -C "${root}" rev-parse --short HEAD)}"

if [[ ! "${STAGING_HOST}" =~ ^[A-Za-z0-9._@:-]+$ ]]; then
  printf '%s\n' "STAGING_HOST contains unsupported characters" >&2
  exit 2
fi
if [[ ! "${staging_app_root}" =~ ^/srv/gongkao-dazi/staging(/[A-Za-z0-9._/-]*)?$ ]]; then
  printf '%s\n' "STAGING_APP_ROOT must stay below /srv/gongkao-dazi/staging" >&2
  exit 2
fi
if [[ ! "${staging_api_service}" =~ ^gongkao-dazi-staging-[A-Za-z0-9_.@-]+$ ]]; then
  printf '%s\n' "STAGING_API_SERVICE must be a staging service" >&2
  exit 2
fi
if [[ ! "${release_id}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf '%s\n' "RELEASE_ID contains unsupported characters" >&2
  exit 2
fi
case "${staging_health_url}" in
  https://*) ;;
  *)
    printf '%s\n' "STAGING_HEALTH_URL must use HTTPS" >&2
    exit 2
    ;;
esac

ssh_args=()
if [[ -n "${STAGING_SSH_IDENTITY_FILE:-}" ]]; then
  test -f "${STAGING_SSH_IDENTITY_FILE}"
  ssh_args=(-i "${STAGING_SSH_IDENTITY_FILE}")
fi

# The server keeps configuration, secrets and its virtualenv. rsync owns only
# application source and deletes stale source files so staging cannot accumulate
# another hand-patched runtime.
rsync -az --delete -e "ssh ${ssh_args[*]}" \
  "${root}/backend/app/" \
  "${STAGING_HOST}:${staging_app_root}/backend/app/"

ssh "${ssh_args[@]}" "${STAGING_HOST}" \
  "set -eu; \
   service_pid=\$(systemctl show -p MainPID --value '${staging_api_service}'); \
   python_bin=\$(readlink -f /proc/\${service_pid}/exe); \
   test -x \"\${python_bin}\"; \
   \"\${python_bin}\" -m compileall -q '${staging_app_root}/backend/app'; \
   printf '%s\n' '${release_id}' > '${staging_app_root}/.staging-release-id'; \
   sudo systemctl restart '${staging_api_service}'; \
   sudo systemctl is-active --quiet '${staging_api_service}'"

health_attempt=1
until curl --fail --silent --show-error --max-time 10 "${staging_health_url}" >/dev/null; do
  if (( health_attempt >= 12 )); then
    printf '%s\n' "staging health check failed after release ${release_id}" >&2
    exit 1
  fi
  health_attempt=$((health_attempt + 1))
  sleep 2
done

printf '%s\n' "staging release ${release_id} is healthy"
