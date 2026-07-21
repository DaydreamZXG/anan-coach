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

case "${release_id}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "release id contains unsupported characters" >&2
    exit 2
    ;;
esac

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
set +a
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
: "${REQUIRED_DATABASE_REVISION:?REQUIRED_DATABASE_REVISION is required}"
: "${WEKNORA_PRIVATE_KNOWLEDGE_MODE:?WEKNORA_PRIVATE_KNOWLEDGE_MODE must be explicit}"
if [[ "${REQUIRED_DATABASE_REVISION}" != "20260721_52" ]]; then
  printf '%s\n' "production release requires database revision 20260721_52" >&2
  exit 1
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
    "${target}/backend/.venv/bin/alembic" current | awk 'NR == 1 {print $1}'
)"
if [[ "${current_revision}" != "${REQUIRED_DATABASE_REVISION}" ]]; then
  printf '%s\n' "database revision does not match release requirement" >&2
  exit 1
fi

install_release_units "${target}"
install -D -m 0750 "${target}/ops/scripts/activate_release.sh" "${controller_root}/activate_release.sh"
install -D -m 0750 "${target}/ops/scripts/rollback_release.sh" "${controller_root}/rollback_release.sh"

runtime_units=(
  gongkao-dazi-api
  gongkao-dazi-external-learning-worker
  gongkao-dazi-data-rights-worker
)
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
  exit 1
fi
for unit in "${runtime_units[@]}"; do
  systemctl is-active --quiet "${unit}"
done
trap - ERR
printf '%s\n' "activated release ${release_id} at database revision ${current_revision}"
