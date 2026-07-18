#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
REGULAR_NAME="maple-mono-cn-regular-v7.9.woff"
BOLD_NAME="maple-mono-cn-bold-v7.9.woff"
REGULAR_SHA256="fd4a84f153836e20742a02f44611b29f006fce1864efd7bf43522f1b4e1f07fa"
BOLD_SHA256="64bc17d6e159d3265f55c38cfbb8267b771391d6808416595a9a713bb6f84b1b"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
python3 "$ROOT/scripts/build_maple_cn_web_fonts.py" --output-dir "$work"

scp -i "$SSH_KEY" \
  "$work/$REGULAR_NAME" \
  "$work/$BOLD_NAME" \
  "$ROOT/third_party/maple-font/OFL.txt" \
  "root@$HOST:/tmp/"

ssh -i "$SSH_KEY" -o BatchMode=yes "root@$HOST" \
  "REGULAR_NAME='$REGULAR_NAME' BOLD_NAME='$BOLD_NAME' bash -s" <<'REMOTE'
set -euo pipefail

install -d -m 0755 /srv/gongkao-dazi-public/fonts
install -m 0644 "/tmp/$REGULAR_NAME" "/srv/gongkao-dazi-public/fonts/$REGULAR_NAME"
install -m 0644 "/tmp/$BOLD_NAME" "/srv/gongkao-dazi-public/fonts/$BOLD_NAME"
install -m 0644 /tmp/OFL.txt /srv/gongkao-dazi-public/fonts/MapleMono-OFL-1.1.txt
rm -f "/tmp/$REGULAR_NAME" "/tmp/$BOLD_NAME" /tmp/OFL.txt
REMOTE

for public_name in "$REGULAR_NAME" "$BOLD_NAME"; do
  asset_url="https://kg.xingguoxian.cn/assets/fonts/$public_name"
  headers="$(curl --fail --silent --show-error --head "$asset_url")"
  grep -qi '^content-type: font/woff' <<<"$headers"
  grep -qi '^access-control-allow-origin: \*' <<<"$headers"
  grep -qi '^cache-control: .*public' <<<"$headers"
  grep -qi '^cache-control: .*max-age=31536000' <<<"$headers"
  grep -qi '^cache-control: .*immutable' <<<"$headers"
  if [[ "$public_name" == "$REGULAR_NAME" ]]; then
    expected_sha256="$REGULAR_SHA256"
  else
    expected_sha256="$BOLD_SHA256"
  fi
  curl --fail --silent --show-error "$asset_url" --output "$work/verify-$public_name"
  actual_sha256="$(shasum -a 256 "$work/verify-$public_name" | awk '{print $1}')"
  [[ "$actual_sha256" == "$expected_sha256" ]]
  printf 'Published Maple Mono CN font: %s\n' "$asset_url"
done
