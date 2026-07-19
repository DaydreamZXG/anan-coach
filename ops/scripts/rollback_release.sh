#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
root="${STAGING_RELEASE_ROOT:-/srv/gongkao-dazi/releases}"
target="${root}/${release_id}"

test -d "${target}/backend"
test -x "${target}/backend/.venv/bin/uvicorn"
ln -sfn "${target}" /srv/gongkao-dazi/current
runtime_units=(
  gongkao-dazi-api
  gongkao-dazi-external-learning-worker
  gongkao-dazi-commerce-worker
  gongkao-dazi-data-rights-worker
)
if [[ -f "${target}/backend/app/workers/coach_notifications.py" ]]; then
  runtime_units+=(gongkao-dazi-coach-notification-worker)
else
  systemctl stop gongkao-dazi-coach-notification-worker 2>/dev/null || true
fi
systemctl restart "${runtime_units[@]}"
curl --fail --silent http://127.0.0.1:8000/ready >/dev/null
printf '%s\n' "rolled back application release to ${release_id}"
