#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output_dir="${ARTIFACT_OUTPUT_DIR:-${root}/dist}"
release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
artifact="${output_dir}/anan-${release_id}.tar.gz"

mkdir -p "${output_dir}"
COPYFILE_DISABLE=1 tar \
  --no-xattrs \
  --exclude='.git' \
  --exclude='._*' \
  --exclude='**/._*' \
  --exclude='**/.venv' \
  --exclude='**/node_modules' \
  --exclude='**/__pycache__' \
  --exclude='dist' \
  -czf "${artifact}" -C "${root}" backend apps/wechat ops scripts .github
(
  cd "${output_dir}"
  shasum -a 256 "$(basename "${artifact}")" > "$(basename "${artifact}").sha256"
)
printf '%s\n' "${artifact}"
