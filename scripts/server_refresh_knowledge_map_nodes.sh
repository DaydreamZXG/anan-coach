#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
DB_NAME="${GONGKAO_DB_NAME:-gongkao_dazi}"
REFRESH_SQL="${GONGKAO_KNOWLEDGE_MAP_SQL:-$SCRIPT_DIR/sql/refresh_knowledge_map_nodes.sql}"

if [[ ! -f "$REFRESH_SQL" ]]; then
  echo "Missing refresh SQL: $REFRESH_SQL" >&2
  exit 1
fi

echo "== refresh knowledge map nodes =="
ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=20 \
  "root@$HOST" \
  "sudo -u postgres psql -v ON_ERROR_STOP=1 -d '$DB_NAME'" < "$REFRESH_SQL"

echo "== knowledge map sample =="
ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=20 \
  "root@$HOST" \
  "sudo -u postgres psql -d '$DB_NAME' -c \"select module_name, node_type, tag_value, support_status, near5_occurrence_count, near5_weight from product.knowledge_map_nodes where exam_scope = 'guokao' and track = 'xingce' and status = 'published' order by near5_weight desc nulls last, near5_occurrence_count desc nulls last, module_name, tag_value limit 20;\""
