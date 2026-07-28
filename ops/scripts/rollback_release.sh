#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
root="${STAGING_RELEASE_ROOT:-/srv/gongkao-dazi/releases}"
target="${root}/${release_id}"
current_link="${GONGKAO_CURRENT_LINK:-/srv/gongkao-dazi/current}"
systemd_root="${GONGKAO_SYSTEMD_ROOT:-/etc/systemd/system}"
config_root="${GONGKAO_CONFIG_ROOT:-/etc/gongkao-dazi}"
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

restore_original_pointer() {
  local exit_code=$?
  trap - ERR
  if [[ "${switched}" == true && -n "${previous_target}" && -d "${previous_target}" ]]; then
    ln -sfn "${previous_target}" "${current_link}"
    install_release_units "${previous_target}" || true
    systemctl restart gongkao-dazi-api \
      gongkao-dazi-external-learning-worker \
      gongkao-dazi-data-rights-worker || true
  fi
  exit "${exit_code}"
}

trap restore_original_pointer ERR

set -a
. "${config_root}/api.env"
. "${config_root}/db.env"
if [[ -f "${config_root}/ilink-channel.env" ]]; then
  . "${config_root}/ilink-channel.env"
fi
set +a

test -d "${target}/backend"
test -x "${target}/backend/.venv/bin/uvicorn"
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
compatibility_python="${current_link}/backend/.venv/bin/python"
test -x "${compatibility_python}"
artifact_contract="${target}/ops/release-db-compatibility.env"
"${compatibility_python}" \
  -m app.core.database_compatibility compatible-revisions \
  --backend-root "${target}/backend" \
  --contract "${artifact_contract}" >/dev/null
current_revision="$(
  sudo -u "${MIGRATION_DATABASE_OS_USER:-postgres}" \
    env DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    "${compatibility_python}" \
      -m app.core.database_compatibility assert-current \
      --backend-root "${target}/backend" \
      --contract "${artifact_contract}"
)"
install_release_units "${target}"
runtime_units=(
  gongkao-dazi-api
  gongkao-dazi-external-learning-worker
  gongkao-dazi-data-rights-worker
)
case "${ESSAY_REVIEW_WORKER_ENABLED:-false}" in
  true)
    test -f "${target}/backend/app/workers/essay_reviews.py"
    "${target}/backend/.venv/bin/python" -c \
      'from app.core.config import get_settings; from app.domains.essay_intelligence.review_worker import GroundedEssayReviewWorker; from app.workers.essay_reviews import _validate_worker_settings; settings = get_settings(); _validate_worker_settings(settings); GroundedEssayReviewWorker(settings=settings)'
    systemctl enable gongkao-dazi-essay-review-worker
    runtime_units+=(gongkao-dazi-essay-review-worker)
    ;;
  false)
    systemctl disable --now gongkao-dazi-essay-review-worker 2>/dev/null || true
    ;;
  *)
    printf '%s\n' "ESSAY_REVIEW_WORKER_ENABLED must be true or false" >&2
    exit 1
    ;;
esac
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
    test -f "${target}/backend/app/workers/ilink_channel.py"
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
if [[ "${ready}" != true ]]; then
  systemctl --no-pager --full status gongkao-dazi-api >&2 || true
  exit 1
fi
for unit in "${runtime_units[@]}"; do
  systemctl is-active --quiet "${unit}"
done
trap - ERR
printf '%s\n' \
  "rolled back application release to ${release_id} at database revision ${current_revision}"
