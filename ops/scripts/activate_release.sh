#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
root="${STAGING_RELEASE_ROOT:-/srv/gongkao-dazi/releases}"
incoming="${root}/incoming/anan-${release_id}.tar.gz"
target="${root}/${release_id}"

cd "${root}/incoming"
shasum -a 256 -c "${incoming}.sha256"
mkdir -p "${target}"
tar -xzf "${incoming}" -C "${target}"
cd "${target}/backend"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
set -a
. /etc/gongkao-dazi/api.env
. /etc/gongkao-dazi/db.env
set +a
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
sudo -u "${MIGRATION_DATABASE_OS_USER:-postgres}" \
  env DATABASE_URL="${MIGRATION_DATABASE_URL}" \
  "${target}/backend/.venv/bin/alembic" upgrade head
ln -sfn "${target}" /srv/gongkao-dazi/current
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
  systemctl stop gongkao-dazi-commerce-worker
fi
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
