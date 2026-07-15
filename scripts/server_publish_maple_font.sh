#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
FONT_VERSION="v7.9"
FONT_ARCHIVE="MapleMono-Variable.zip"
FONT_SHA256="b9e57677cc4ec069ce178bd965d66cd14bd26c12e37d89d786ff959339bf8c93"
PUBLIC_NAME="maple-mono-data-v7.9.ttf"

ssh -i "$SSH_KEY" -o BatchMode=yes "root@$HOST" \
  "FONT_VERSION='$FONT_VERSION' FONT_ARCHIVE='$FONT_ARCHIVE' FONT_SHA256='$FONT_SHA256' PUBLIC_NAME='$PUBLIC_NAME' bash -s" <<'REMOTE'
set -euo pipefail

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
archive_url="https://github.com/subframe7536/maple-font/releases/download/${FONT_VERSION}/${FONT_ARCHIVE}"

curl --fail --location --silent --show-error "$archive_url" --output "$work/font.zip"
printf '%s  %s\n' "$FONT_SHA256" "$work/font.zip" | sha256sum --check --status
unzip -q "$work/font.zip" -d "$work/extracted"

install -d -m 0755 /srv/gongkao-dazi-public/fonts
install -m 0644 "$work/extracted/MapleMono[wght].ttf" \
  "/srv/gongkao-dazi-public/fonts/$PUBLIC_NAME"
install -m 0644 "$work/extracted/LICENSE.txt" \
  /srv/gongkao-dazi-public/fonts/MapleMono-OFL-1.1.txt
REMOTE

scp -i "$SSH_KEY" "$ROOT/ops/caddy/Caddyfile" "root@$HOST:/etc/caddy/Caddyfile.next"
ssh -i "$SSH_KEY" -o BatchMode=yes "root@$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail

caddy validate --config /etc/caddy/Caddyfile.next
cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.backup.$(date +%Y%m%d%H%M%S)"
install -m 0644 /etc/caddy/Caddyfile.next /etc/caddy/Caddyfile
rm /etc/caddy/Caddyfile.next
systemctl reload caddy
REMOTE

asset_url="https://kg.xingguoxian.cn/assets/fonts/$PUBLIC_NAME"
headers="$(curl --fail --silent --show-error --head "$asset_url")"
grep -qi '^content-type: font/ttf' <<<"$headers"
grep -qi '^access-control-allow-origin: \*' <<<"$headers"
grep -qi '^cache-control: .*public' <<<"$headers"
grep -qi '^cache-control: .*max-age=31536000' <<<"$headers"
grep -qi '^cache-control: .*immutable' <<<"$headers"

printf 'Published Maple Mono data font: %s\n' "$asset_url"
