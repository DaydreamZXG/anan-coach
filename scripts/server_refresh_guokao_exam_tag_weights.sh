#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
DB_NAME="${GONGKAO_DB_NAME:-gongkao_dazi}"
REFRESH_SQL="${GONGKAO_EXAM_TAG_WEIGHT_SQL:-$SCRIPT_DIR/sql/refresh_guokao_exam_tag_weights.sql}"

if [[ ! -f "$REFRESH_SQL" ]]; then
  echo "Missing refresh SQL: $REFRESH_SQL" >&2
  exit 1
fi

echo "== refresh guokao exam tag weights =="
ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=20 \
  "root@$HOST" \
  "sudo -u postgres psql -v ON_ERROR_STOP=1 -d '$DB_NAME'" < "$REFRESH_SQL"

echo "== guokao exam tag weight sample =="
ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=20 \
  "root@$HOST" \
  "sudo -u postgres psql -d '$DB_NAME' -c \"select module_name, tag_type, tag_value, window_start_year, window_end_year, question_count, occurrence_count, weight from product.exam_tag_weights where exam_scope = 'guokao' and track = 'xingce' and tag_type in ('module', 'knowledge_point') order by tag_type, weight desc, occurrence_count desc limit 20;\""

if [[ "${GONGKAO_SKIP_KNOWLEDGE_MAP_REFRESH:-0}" != "1" ]]; then
  GONGKAO_SERVER_HOST="$HOST" \
  GONGKAO_SSH_KEY="$SSH_KEY" \
  GONGKAO_DB_NAME="$DB_NAME" \
  "$SCRIPT_DIR/server_refresh_knowledge_map_nodes.sh"
fi
