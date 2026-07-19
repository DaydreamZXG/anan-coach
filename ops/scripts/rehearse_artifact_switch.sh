#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_root="$(mktemp -d "${TMPDIR:-/tmp}/anan-release-rehearsal.XXXXXX")"
trap 'rm -rf "${work_root}"' EXIT

artifact_dir="${work_root}/artifacts"
release_root="${work_root}/releases"
mkdir -p "${artifact_dir}" "${release_root}/incoming"

build_release() {
  local release_id="$1"
  RELEASE_ID="${release_id}" ARTIFACT_OUTPUT_DIR="${artifact_dir}" \
    "${root}/ops/scripts/build_immutable_artifact.sh" >/dev/null
  cp "${artifact_dir}/anan-${release_id}.tar.gz" "${release_root}/incoming/"
  cp "${artifact_dir}/anan-${release_id}.tar.gz.sha256" "${release_root}/incoming/"
  (
    cd "${release_root}/incoming"
    shasum -a 256 -c "anan-${release_id}.tar.gz.sha256"
  )
  mkdir -p "${release_root}/${release_id}"
  tar -xzf "${release_root}/incoming/anan-${release_id}.tar.gz" \
    -C "${release_root}/${release_id}"
  test -f "${release_root}/${release_id}/backend/pyproject.toml"
  test -f "${release_root}/${release_id}/apps/wechat/project.config.json"
  test -x "${release_root}/${release_id}/ops/scripts/rollback_release.sh"
}

build_release "rehearsal-previous"
build_release "rehearsal-candidate"
ln -sfn "${release_root}/rehearsal-previous" "${release_root}/current"
test "$(readlink "${release_root}/current")" = "${release_root}/rehearsal-previous"
ln -sfn "${release_root}/rehearsal-candidate" "${release_root}/current"
test "$(readlink "${release_root}/current")" = "${release_root}/rehearsal-candidate"
ln -sfn "${release_root}/rehearsal-previous" "${release_root}/current"
test "$(readlink "${release_root}/current")" = "${release_root}/rehearsal-previous"

printf '%s\n' "Immutable artifact checksum, activation switch, and rollback switch rehearsal: OK"
