#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output_dir="${ARTIFACT_OUTPUT_DIR:-${root}/dist}"
release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
artifact_name="anan-${release_id}.tar.gz"
manifest_name="anan-${release_id}.release.json"
release_set="${output_dir}/anan-${release_id}.release-set"

case "${release_id}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "release id contains unsupported characters" >&2
    exit 2
    ;;
esac

if [[ -n "$(git -C "${root}" status --porcelain --untracked-files=normal)" ]]; then
  printf '%s\n' "immutable artifacts require a clean Git source tree" >&2
  exit 1
fi

mkdir -p "${output_dir}"
for output in \
  "${release_set}" \
  "${output_dir}/${artifact_name}" \
  "${output_dir}/${artifact_name}.sha256" \
  "${output_dir}/${manifest_name}" \
  "${output_dir}/${manifest_name}.sha256"; do
  if [[ -e "${output}" || -L "${output}" ]]; then
    printf '%s\n' "refusing to overwrite existing release output ${output}" >&2
    exit 1
  fi
done
staging_directory="$(
  mktemp -d "${output_dir}/.anan-${release_id}.release-set.XXXXXX"
)"
artifact="${staging_directory}/${artifact_name}"
manifest="${staging_directory}/${manifest_name}"
artifact_checksum="${artifact}.sha256"
manifest_checksum="${manifest}.sha256"
cleanup_failed_build() {
  local exit_code=$?
  rm -rf -- "${staging_directory}"
  exit "${exit_code}"
}
trap cleanup_failed_build EXIT
git -C "${root}" archive \
  --format=tar.gz \
  --output="${artifact}" \
  HEAD \
  backend apps/wechat ops scripts .github audit/api-contract/openapi.json
(
  cd "${staging_directory}"
  shasum -a 256 "${artifact_name}" > "${artifact_name}.sha256"
)

manifest_arguments=(
  build
  --project-root "${root}"
  --artifact "${artifact}"
  --output "${manifest}"
  --release-id "${release_id}"
  --mode "${RELEASE_MANIFEST_MODE:-candidate}"
  --wechat-upload-version "${WECHAT_UPLOAD_VERSION:-}"
  --deployment-environment "${DEPLOYMENT_ENVIRONMENT:-}"
  --rollback-release-id "${ROLLBACK_RELEASE_ID:-}"
)
if [[ -n "${WECHAT_UPLOAD_RECEIPT:-}" ]]; then
  manifest_arguments+=(--wechat-upload-receipt "${WECHAT_UPLOAD_RECEIPT}")
fi
if [[ -n "${WECHAT_UPLOAD_RECEIPT_SIGNATURE:-}" ]]; then
  manifest_arguments+=(
    --wechat-upload-receipt-signature
    "${WECHAT_UPLOAD_RECEIPT_SIGNATURE}"
  )
fi
if [[ -n "${WECHAT_UPLOAD_INFO_OUTPUT:-}" ]]; then
  manifest_arguments+=(--wechat-upload-info-output "${WECHAT_UPLOAD_INFO_OUTPUT}")
fi
if [[ -n "${WECHAT_UPLOAD_RAW_LOG:-}" ]]; then
  manifest_arguments+=(--wechat-upload-raw-log "${WECHAT_UPLOAD_RAW_LOG}")
fi
if [[ -n "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY:-}" ]]; then
  manifest_arguments+=(
    --wechat-uploader-trust-public-key
    "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}"
  )
fi
if [[ -n "${DATA_RELEASE_RECEIPT:-}" ]]; then
  manifest_arguments+=(--data-release-receipt "${DATA_RELEASE_RECEIPT}")
fi
python3 \
  "${root}/ops/scripts/release_manifest.py" \
  "${manifest_arguments[@]}" >/dev/null
published_artifact="$(
  python3 \
    "${root}/ops/scripts/release_manifest.py" \
    publish-release-set \
    --staging-directory "${staging_directory}" \
    --destination-directory "${release_set}" \
    --release-id "${release_id}"
)"
trap - EXIT
printf '%s\n' "${published_artifact}"
