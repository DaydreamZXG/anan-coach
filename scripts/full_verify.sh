#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/verify.sh"

cd "$ROOT"
xcodebuild build \
  -project "$ROOT/apps/ios/Gongkao/Gongkao.xcodeproj" \
  -scheme Gongkao \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  CODE_SIGNING_ALLOWED=NO

xcodebuild build \
  -project "$ROOT/apps/ios/Gongkao/Gongkao.xcodeproj" \
  -scheme Gongkao \
  -destination 'platform=iOS Simulator,name=iPad Pro 13-inch (M5)' \
  CODE_SIGNING_ALLOWED=NO

echo "Full Apple-platform verification: OK"
