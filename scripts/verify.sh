#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/check_architecture.py"
"$ROOT/scripts/verify_backend.sh"
"$ROOT/scripts/verify_wechat.sh"

echo "Commercial foundation verification: OK"
