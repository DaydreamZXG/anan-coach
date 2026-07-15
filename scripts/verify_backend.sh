#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x "$ROOT/backend/.venv/bin/python" ]; then
  PY="$ROOT/backend/.venv/bin/python"
else
  PY="python3"
fi

cd "$ROOT/backend"
"$PY" -m pytest

HEADS="$("$PY" -m alembic heads | sed '/^[[:space:]]*$/d')"
HEAD_COUNT="$(printf '%s\n' "$HEADS" | wc -l | tr -d ' ')"
if [ "$HEAD_COUNT" -ne 1 ] || [[ "$HEADS" != *"(head)"* ]]; then
  echo "Expected exactly one Alembic head, found:" >&2
  printf '%s\n' "$HEADS" >&2
  exit 1
fi
printf 'Alembic migration head: %s\n' "$HEADS"
