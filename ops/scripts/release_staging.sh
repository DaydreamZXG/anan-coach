#!/usr/bin/env bash
set -euo pipefail

: "${STAGING_HOST:?STAGING_HOST is required}"
: "${STAGING_RELEASE_ROOT:?STAGING_RELEASE_ROOT is required}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export RELEASE_ID="$release_id"
artifact="$(${root}/ops/scripts/build_immutable_artifact.sh)"
scp "${artifact}" "${artifact}.sha256" "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/"
ssh "${STAGING_HOST}" "/srv/gongkao-dazi/ops/scripts/activate_release.sh '${release_id}'"
