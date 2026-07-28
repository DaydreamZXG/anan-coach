#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
LOCKED_RUNTIME_FLAG="--locked-runtime"

if [[ "${1:-}" == "$LOCKED_RUNTIME_FLAG" ]]; then
  if [[ "$#" -ne 1 || "${VERIFY_BACKEND_LOCKED_RUNTIME:-}" != "1" ]]; then
    echo "The locked backend runtime is an internal entrypoint." >&2
    exit 2
  fi
  PY="python"
elif [[ "$#" -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
elif [[ -n "${PYTHON:-}" ]]; then
  if [[ "${CI:-}" == "true" ]]; then
    echo "PYTHON compatibility fallback is forbidden in CI." >&2
    exit 2
  fi
  PY="$PYTHON"
else
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required for the locked backend gate." >&2
    echo "For the documented compatibility path, set PYTHON explicitly." >&2
    exit 127
  fi
  exec env \
    -u PYTHONHOME \
    -u PYTHONPATH \
    -u VIRTUAL_ENV \
    -u UV_ACTIVE \
    -u UV_NO_DEV \
    -u UV_NO_PROJECT \
    -u UV_NO_SYNC \
    -u UV_PROJECT_ENVIRONMENT \
    VERIFY_BACKEND_LOCKED_RUNTIME=1 \
    uv run \
    --project "$BACKEND" \
    --locked \
    --isolated \
    --extra dev \
    --no-env-file \
    bash "$0" "$LOCKED_RUNTIME_FLAG"
fi

cd "$BACKEND"
"$PY" -m ruff check .
"$PY" -m pytest

HEADS="$("$PY" -m alembic heads | sed '/^[[:space:]]*$/d')"
HEAD_COUNT="$(printf '%s\n' "$HEADS" | wc -l | tr -d ' ')"
if [ "$HEAD_COUNT" -ne 1 ] || [[ "$HEADS" != *"(head)"* ]]; then
  echo "Expected exactly one Alembic head, found:" >&2
  printf '%s\n' "$HEADS" >&2
  exit 1
fi
printf 'Alembic migration head: %s\n' "$HEADS"
