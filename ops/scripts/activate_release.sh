#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
root="${STAGING_RELEASE_ROOT:-/srv/gongkao-dazi/releases}"
current_link="${GONGKAO_CURRENT_LINK:-/srv/gongkao-dazi/current}"
controller_root="${GONGKAO_CONTROLLER_ROOT:-/srv/gongkao-dazi/ops/scripts}"
systemd_root="${GONGKAO_SYSTEMD_ROOT:-/etc/systemd/system}"
config_root="${GONGKAO_CONFIG_ROOT:-/etc/gongkao-dazi}"
incoming="${root}/incoming/anan-${release_id}.tar.gz"
target="${root}/${release_id}"
previous_target="$(readlink -f "${current_link}" 2>/dev/null || true)"
switched=false
previous_compatible_with_candidate=false
release_retention_count="${RELEASE_RETENTION_COUNT:-3}"

case "${release_id}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "release id contains unsupported characters" >&2
    exit 2
    ;;
esac
if [[ ! "${release_retention_count}" =~ ^[0-9]+$ ]] \
  || (( release_retention_count < 2 )); then
  printf '%s\n' "RELEASE_RETENTION_COUNT must be an integer of at least 2" >&2
  exit 2
fi

install_release_units() {
  local release_root="$1"
  local unit
  local found=false
  shopt -s nullglob
  for unit in "${release_root}/ops/systemd/"*.service "${release_root}/ops/systemd/"*.timer; do
    install -m 0644 "${unit}" "${systemd_root}/$(basename "${unit}")"
    found=true
  done
  shopt -u nullglob
  if [[ "${found}" != true ]]; then
    printf '%s\n' "release contains no systemd unit definitions" >&2
    return 1
  fi
  systemctl daemon-reload
}

restore_previous_release() {
  local exit_code=$?
  trap - ERR
  if [[ "${switched}" == true && -n "${previous_target}" && -d "${previous_target}" ]]; then
    if [[ "${previous_compatible_with_candidate}" != true ]]; then
      printf '%s\n' \
        "refusing to restore an artifact not preflighted for the migrated database" \
        >&2
      exit "${exit_code}"
    fi
    ln -sfn "${previous_target}" "${current_link}"
    install_release_units "${previous_target}" || true
    systemctl disable --now gongkao-dazi-ilink-worker 2>/dev/null || true
    systemctl restart \
      gongkao-dazi-api \
      gongkao-dazi-external-learning-worker \
      gongkao-dazi-data-rights-worker || true
    for unit in gongkao-dazi-coach-notification-worker gongkao-dazi-commerce-worker; do
      if systemctl is-enabled --quiet "${unit}"; then
        systemctl restart "${unit}" || true
      fi
    done
  fi
  exit "${exit_code}"
}

prune_old_releases() {
  local entry
  local reserved=1
  local extra_kept=0
  local extra_limit
  if [[ -n "${previous_target}" && "${previous_target}" != "${target}" ]]; then
    reserved=2
  fi
  extra_limit=$((release_retention_count - reserved))
  while IFS= read -r entry; do
    [[ -n "${entry}" ]] || continue
    if [[ "${entry}" == "${target}" || "${entry}" == "${previous_target}" ]]; then
      continue
    fi
    if (( extra_kept < extra_limit )); then
      extra_kept=$((extra_kept + 1))
      continue
    fi
    case "${entry}" in
      "${root}"/*) rm -rf -- "${entry}" ;;
      *)
        printf '%s\n' "refusing unsafe release cleanup target ${entry}" >&2
        return 1
        ;;
    esac
  done < <(
    find "${root}" -mindepth 1 -maxdepth 1 -type d \
      ! -name incoming -print | sort -r
  )
  rm -f -- "${incoming}" "${incoming}.sha256"
}

trap restore_previous_release ERR

cd "${root}/incoming"
shasum -a 256 -c "${incoming}.sha256"
test ! -e "${target}"
mkdir -p "${target}"
tar -xzf "${incoming}" -C "${target}"
cd "${target}/backend"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
set -a
. "${config_root}/api.env"
. "${config_root}/db.env"
if [[ -f "${config_root}/ilink-channel.env" ]]; then
  . "${config_root}/ilink-channel.env"
fi
if [[ -f "${config_root}/ilink-worker.env" ]]; then
  . "${config_root}/ilink-worker.env"
fi
set +a
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
: "${REQUIRED_DATABASE_REVISION:?REQUIRED_DATABASE_REVISION is required}"
: "${WEKNORA_PRIVATE_KNOWLEDGE_MODE:?WEKNORA_PRIVATE_KNOWLEDGE_MODE must be explicit}"
artifact_contract="${target}/ops/release-db-compatibility.env"
artifact_revision="$(
  "${target}/backend/.venv/bin/python" \
    -m app.core.database_compatibility head \
    --backend-root "${target}/backend"
)"
"${target}/backend/.venv/bin/python" \
  -m app.core.database_compatibility compatible-revisions \
  --backend-root "${target}/backend" \
  --contract "${artifact_contract}" >/dev/null
if [[ "${REQUIRED_DATABASE_REVISION}" != "${artifact_revision}" ]]; then
  printf '%s\n' \
    "REQUIRED_DATABASE_REVISION must match release artifact head ${artifact_revision}" \
    >&2
  exit 1
fi
if [[ -n "${previous_target}" && -d "${previous_target}" ]]; then
  previous_contract="${previous_target}/ops/release-db-compatibility.env"
  "${target}/backend/.venv/bin/python" \
    -m app.core.database_compatibility assert-revision \
    --backend-root "${previous_target}/backend" \
    --contract "${previous_contract}" \
    --revision "${artifact_revision}" >/dev/null
  previous_compatible_with_candidate=true
fi
case "${WEKNORA_PRIVATE_KNOWLEDGE_MODE}" in
  canonical|weknora) ;;
  *)
    printf '%s\n' "WEKNORA_PRIVATE_KNOWLEDGE_MODE must be canonical or weknora" >&2
    exit 1
    ;;
esac
sudo -u "${MIGRATION_DATABASE_OS_USER:-postgres}" \
  env DATABASE_URL="${MIGRATION_DATABASE_URL}" \
  "${target}/backend/.venv/bin/alembic" upgrade head
current_revision="$(
  sudo -u "${MIGRATION_DATABASE_OS_USER:-postgres}" \
    env DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    "${target}/backend/.venv/bin/python" \
      -m app.core.database_compatibility assert-current \
      --backend-root "${target}/backend" \
      --contract "${artifact_contract}"
)"
if [[ "${current_revision}" != "${artifact_revision}" ]]; then
  printf '%s\n' "database revision does not match release requirement" >&2
  exit 1
fi

: "${GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION:?GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION is required}"
: "${GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION:?GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION is required}"
: "${GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL:?GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL is required}"
knowledge_database_url="${GONGKAO_KNOWLEDGE_MAP_DATABASE_URL:-}"
if [[ -z "${knowledge_database_url}" ]]; then
  knowledge_database_url="${MIGRATION_DATABASE_URL/postgresql+psycopg:/postgresql:}"
fi
GONGKAO_KNOWLEDGE_MAP_DATABASE_URL="${knowledge_database_url}" \
GONGKAO_KNOWLEDGE_MAP_DATABASE_OS_USER="${MIGRATION_DATABASE_OS_USER:-postgres}" \
  "${target}/scripts/server_refresh_knowledge_map_nodes.sh" --local

install_release_units "${target}"
install -D -m 0750 "${target}/ops/scripts/activate_release.sh" "${controller_root}/activate_release.sh"
install -D -m 0750 "${target}/ops/scripts/rollback_release.sh" "${controller_root}/rollback_release.sh"

runtime_units=(
  gongkao-dazi-api
  gongkao-dazi-external-learning-worker
  gongkao-dazi-data-rights-worker
  gongkao-dazi-cause-agent-worker
)
systemctl enable gongkao-dazi-cause-agent-worker
if [[ "${COACH_NOTIFICATIONS_ENABLED:-false}" == "true" \
  && -f "${target}/backend/app/workers/coach_notifications.py" ]]; then
  runtime_units+=(gongkao-dazi-coach-notification-worker)
else
  systemctl stop gongkao-dazi-coach-notification-worker 2>/dev/null || true
fi
if [[ "${COMMERCIAL_MODE:-free_cold_start}" == "paid" \
  && -n "${WECHAT_VIRTUAL_PAY_OFFER_ID:-}" \
  && -n "${WECHAT_VIRTUAL_PAY_APP_KEY:-}" \
  && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN:-}" \
  && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY:-}" ]]; then
  runtime_units+=(gongkao-dazi-commerce-worker)
else
  systemctl stop gongkao-dazi-commerce-worker 2>/dev/null || true
fi
if [[ "${ESSAY_REVIEW_WORKER_ENABLED:-false}" == "true" ]]; then
  "${target}/backend/.venv/bin/python" -c \
    'from app.core.config import get_settings; from app.domains.essay_intelligence.review_worker import GroundedEssayReviewWorker; GroundedEssayReviewWorker(settings=get_settings())'
  systemctl enable gongkao-dazi-essay-review-worker
  runtime_units+=(gongkao-dazi-essay-review-worker)
else
  systemctl disable --now gongkao-dazi-essay-review-worker 2>/dev/null || true
fi

case "${ILINK_CHANNEL_ENABLED:-false}" in
  true)
    "${target}/backend/.venv/bin/python" -c \
      'from app.workers.ilink_channel import ILinkWorker; ILinkWorker()'
    systemctl enable gongkao-dazi-ilink-worker
    runtime_units+=(gongkao-dazi-ilink-worker)
    ;;
  false)
    systemctl disable --now gongkao-dazi-ilink-worker 2>/dev/null || true
    ;;
  *)
    printf '%s\n' "ILINK_CHANNEL_ENABLED must be true or false" >&2
    exit 1
    ;;
esac

ln -sfn "${target}" "${current_link}"
switched=true
systemctl restart "${runtime_units[@]}"
ready=false
for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8000/ready >/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  systemctl --no-pager --full status gongkao-dazi-api >&2 || true
  false
fi
for unit in "${runtime_units[@]}"; do
  systemctl is-active --quiet "${unit}"
done
prune_old_releases
trap - ERR
printf '%s\n' "activated release ${release_id} at database revision ${current_revision}"
