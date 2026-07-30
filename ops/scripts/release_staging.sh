#!/usr/bin/env bash
set -euo pipefail

: "${STAGING_HOST:?STAGING_HOST is required}"
: "${STAGING_RELEASE_ROOT:?STAGING_RELEASE_ROOT is required}"
: "${STAGING_RELEASE_TRUST_PUBLIC_KEY:?STAGING_RELEASE_TRUST_PUBLIC_KEY is required}"
: "${RELEASE_SIGNING_PRIVATE_KEY:?RELEASE_SIGNING_PRIVATE_KEY is required}"
: "${EXPECTED_GIT_COMMIT:?EXPECTED_GIT_COMMIT is required}"
: "${WECHAT_UPLOAD_RECEIPT:?WECHAT_UPLOAD_RECEIPT is required}"
: "${WECHAT_UPLOAD_RECEIPT_SIGNATURE:?WECHAT_UPLOAD_RECEIPT_SIGNATURE is required}"
: "${WECHAT_UPLOAD_INFO_OUTPUT:?WECHAT_UPLOAD_INFO_OUTPUT is required}"
: "${WECHAT_UPLOAD_RAW_LOG:?WECHAT_UPLOAD_RAW_LOG is required}"
: "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY:?WECHAT_UPLOADER_TRUST_PUBLIC_KEY is required}"
: "${STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY:?STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY is required}"
: "${DATA_RELEASE_RECEIPT:?DATA_RELEASE_RECEIPT is required}"
: "${DATA_RELEASE_RECEIPT_SIGNATURE:?DATA_RELEASE_RECEIPT_SIGNATURE is required}"
: "${STAGING_DATA_RELEASE_TRUST_PUBLIC_KEY:?STAGING_DATA_RELEASE_TRUST_PUBLIC_KEY is required}"
: "${DEPLOYMENT_ENVIRONMENT:?DEPLOYMENT_ENVIRONMENT is required}"
: "${ROLLBACK_RELEASE_ID:?ROLLBACK_RELEASE_ID is required}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
staging_current_link="${STAGING_CURRENT_LINK:-/srv/gongkao-dazi/current}"
staging_config_root="${STAGING_CONFIG_ROOT:-/etc/gongkao-dazi}"
if [[ ! "${STAGING_HOST}" =~ ^[A-Za-z0-9._@:-]+$ ]]; then
  printf '%s\n' "STAGING_HOST contains unsupported characters" >&2
  exit 2
fi
if [[ ! "${STAGING_RELEASE_ROOT}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  printf '%s\n' "STAGING_RELEASE_ROOT must be a safe absolute path" >&2
  exit 2
fi
for trust_key in \
  "${STAGING_RELEASE_TRUST_PUBLIC_KEY}" \
  "${STAGING_DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  "${STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY}"; do
  if [[ ! "${trust_key}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "staging trust keys must be safe absolute paths" >&2
    exit 2
  fi
done
for trusted_path in "${staging_current_link}" "${staging_config_root}"; do
  if [[ ! "${trusted_path}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "staging identity paths must be safe absolute paths" >&2
    exit 2
  fi
done
if [[ ! "${EXPECTED_GIT_COMMIT}" =~ ^[0-9a-f]{40,64}$ ]]; then
  printf '%s\n' "EXPECTED_GIT_COMMIT must be a full hexadecimal commit id" >&2
  exit 2
fi
case "${ROLLBACK_RELEASE_ID}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "ROLLBACK_RELEASE_ID contains unsupported characters" >&2
    exit 2
    ;;
esac
case "${DEPLOYMENT_ENVIRONMENT}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "DEPLOYMENT_ENVIRONMENT contains unsupported characters" >&2
    exit 2
    ;;
esac
if [[ "$(git -C "${root}" rev-parse HEAD)" != "${EXPECTED_GIT_COMMIT}" ]]; then
  printf '%s\n' "checked-out commit does not match EXPECTED_GIT_COMMIT" >&2
  exit 1
fi
test -f "${RELEASE_SIGNING_PRIVATE_KEY}"
"${root}/backend/.venv/bin/python" \
  "${root}/ops/scripts/release_manifest.py" \
  validate-signing-key \
  --path "${RELEASE_SIGNING_PRIVATE_KEY}" >/dev/null
test -f "${WECHAT_UPLOAD_RECEIPT}"
test -f "${WECHAT_UPLOAD_RECEIPT_SIGNATURE}"
test -f "${WECHAT_UPLOAD_INFO_OUTPUT}"
test -f "${WECHAT_UPLOAD_RAW_LOG}"
test -f "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}"
test ! -L "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}"
test -z "$(find "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}" -perm /022 -print -quit)"
openssl dgst -sha256 \
  -verify "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}" \
  -signature "${WECHAT_UPLOAD_RECEIPT_SIGNATURE}" \
  "${WECHAT_UPLOAD_RECEIPT}"
test -f "${DATA_RELEASE_RECEIPT}"
test -f "${DATA_RELEASE_RECEIPT_SIGNATURE}"
release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
case "${release_id}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "release id contains unsupported characters" >&2
    exit 2
    ;;
esac
export RELEASE_ID="$release_id"
export RELEASE_MANIFEST_MODE=deployable
artifact="$(${root}/ops/scripts/build_immutable_artifact.sh)"
manifest="${artifact%.tar.gz}.release.json"
signature="${manifest}.sig"
verifier="${root}/ops/scripts/release_manifest.py"
test ! -e "${signature}"
openssl dgst -sha256 \
  -sign "${RELEASE_SIGNING_PRIVATE_KEY}" \
  -out "${signature}" \
  "${manifest}"
test -s "${signature}"
verifier_sha256="$(shasum -a 256 "${verifier}" | awk '{print $1}')"
ssh_args=()
if [[ -n "${STAGING_SSH_IDENTITY_FILE:-}" ]]; then
  test -f "${STAGING_SSH_IDENTITY_FILE}"
  ssh_args=(-i "${STAGING_SSH_IDENTITY_FILE}")
fi
scp "${ssh_args[@]}" \
  "${artifact}" "${artifact}.sha256" \
  "${manifest}" "${manifest}.sha256" "${signature}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/"
scp "${ssh_args[@]}" \
  "${verifier}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/anan-${release_id}.verifier.py"
scp "${ssh_args[@]}" \
  "${WECHAT_UPLOAD_RECEIPT}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/anan-${release_id}.wechat-upload-receipt.json"
scp "${ssh_args[@]}" \
  "${WECHAT_UPLOAD_RECEIPT_SIGNATURE}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/anan-${release_id}.wechat-upload-receipt.json.sig"
scp "${ssh_args[@]}" \
  "${WECHAT_UPLOAD_INFO_OUTPUT}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/anan-${release_id}.wechat-upload-info.json"
scp "${ssh_args[@]}" \
  "${WECHAT_UPLOAD_RAW_LOG}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/anan-${release_id}.wechat-upload.log"
scp "${ssh_args[@]}" \
  "${DATA_RELEASE_RECEIPT}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/anan-${release_id}.data-release-receipt.json"
scp "${ssh_args[@]}" \
  "${DATA_RELEASE_RECEIPT_SIGNATURE}" \
  "${STAGING_HOST}:${STAGING_RELEASE_ROOT}/incoming/anan-${release_id}.data-release-receipt.json.sig"
ssh "${ssh_args[@]}" "${STAGING_HOST}" \
  "set -eu; cd '${STAGING_RELEASE_ROOT}/incoming'; \
   shasum -a 256 -c 'anan-${release_id}.tar.gz.sha256'; \
   shasum -a 256 -c 'anan-${release_id}.release.json.sha256'; \
   controller=\$(mktemp /tmp/anan-activate.XXXXXX); \
   trap 'rm -f \"\${controller}\"' EXIT; \
   printf '%s  %s\n' '${verifier_sha256}' \
     'anan-${release_id}.verifier.py' | shasum -a 256 -c -; \
   for trust_key in \
     '${STAGING_RELEASE_TRUST_PUBLIC_KEY}' \
     '${STAGING_DATA_RELEASE_TRUST_PUBLIC_KEY}' \
     '${STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY}'; do \
     test -f \"\${trust_key}\"; \
     test \"\$(stat -c '%u' \"\${trust_key}\")\" = 0; \
     test -z \"\$(find \"\${trust_key}\" -perm /022 -print -quit)\"; \
   done; \
   openssl dgst -sha256 \
     -verify '${STAGING_RELEASE_TRUST_PUBLIC_KEY}' \
     -signature 'anan-${release_id}.release.json.sig' \
     'anan-${release_id}.release.json'; \
   openssl dgst -sha256 \
     -verify '${STAGING_DATA_RELEASE_TRUST_PUBLIC_KEY}' \
     -signature 'anan-${release_id}.data-release-receipt.json.sig' \
     'anan-${release_id}.data-release-receipt.json'; \
   openssl dgst -sha256 \
     -verify '${STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY}' \
     -signature 'anan-${release_id}.wechat-upload-receipt.json.sig' \
     'anan-${release_id}.wechat-upload-receipt.json'; \
   environment_file='${staging_config_root}/release-environment'; \
   test -f \"\${environment_file}\"; \
   test \"\$(stat -c '%u' \"\${environment_file}\")\" = 0; \
   test -z \"\$(find \"\${environment_file}\" -perm /022 -print -quit)\"; \
   actual_environment=\$(tr -d '\\r\\n' < \"\${environment_file}\"); \
   case \"\${actual_environment}\" in \
     *[!A-Za-z0-9._-]*|'') exit 71 ;; \
   esac; \
   actual_previous=\$(readlink -f '${staging_current_link}'); \
   case \"\${actual_previous}\" in \
     '${STAGING_RELEASE_ROOT}'/*) ;; \
     *) exit 72 ;; \
   esac; \
   actual_rollback=\$(basename \"\${actual_previous}\"); \
   test \"\${actual_rollback}\" = '${ROLLBACK_RELEASE_ID}'; \
   previous_manifest=\"\${actual_previous}/release-authority/release.json\"; \
   previous_signature=\"\${actual_previous}/release-authority/release.json.sig\"; \
   test -f \"\${previous_manifest}\"; \
   test -f \"\${previous_signature}\"; \
   openssl dgst -sha256 \
     -verify '${STAGING_RELEASE_TRUST_PUBLIC_KEY}' \
     -signature \"\${previous_signature}\" \
     \"\${previous_manifest}\"; \
   python3 -c \
     'import json,sys; authority=json.load(open(sys.argv[1], encoding=\"utf-8\")); assert authority.get(\"releaseId\") == sys.argv[2]' \
     \"\${previous_manifest}\" \"\${actual_rollback}\"; \
   previous_commit=\$(python3 -c \
     'import json,sys; authority=json.load(open(sys.argv[1], encoding=\"utf-8\")); print(authority[\"source\"][\"gitCommit\"])' \
     \"\${previous_manifest}\"); \
   case \"\${previous_commit}\" in \
     *[!0-9a-f]*|'') exit 73 ;; \
   esac; \
   test \"\${#previous_commit}\" -ge 40; \
   test \"\${#previous_commit}\" -le 64; \
   python3 'anan-${release_id}.verifier.py' verify \
     --manifest 'anan-${release_id}.release.json' \
     --artifact 'anan-${release_id}.tar.gz' \
     --expected-commit '${EXPECTED_GIT_COMMIT}' \
     --expected-release-id '${release_id}' \
     --expected-environment \"\${actual_environment}\" \
     --expected-rollback-release-id \"\${actual_rollback}\" \
     --expected-compatible-commit '${EXPECTED_GIT_COMMIT}' \
     --expected-compatible-commit \"\${previous_commit}\" \
     --wechat-upload-receipt \
       'anan-${release_id}.wechat-upload-receipt.json' \
     --wechat-upload-receipt-signature \
       'anan-${release_id}.wechat-upload-receipt.json.sig' \
     --wechat-upload-info-output \
       'anan-${release_id}.wechat-upload-info.json' \
     --wechat-upload-raw-log 'anan-${release_id}.wechat-upload.log' \
     --wechat-uploader-trust-public-key \
       '${STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY}' \
     --data-release-receipt \
       'anan-${release_id}.data-release-receipt.json' \
     --require-deployable; \
   tar -xOf 'anan-${release_id}.tar.gz' ops/scripts/activate_release.sh > \"\${controller}\"; \
   chmod 0700 \"\${controller}\"; \
   EXPECTED_DEPLOYMENT_ENVIRONMENT=\"\${actual_environment}\" \
   EXPECTED_ROLLBACK_RELEASE_ID=\"\${actual_rollback}\" \
   EXPECTED_GIT_COMMIT='${EXPECTED_GIT_COMMIT}' \
   EXPECTED_ROLLBACK_GIT_COMMIT=\"\${previous_commit}\" \
   EXPECTED_VERIFIER_SHA256='${verifier_sha256}' \
   RELEASE_TRUST_PUBLIC_KEY='${STAGING_RELEASE_TRUST_PUBLIC_KEY}' \
   DATA_RELEASE_TRUST_PUBLIC_KEY='${STAGING_DATA_RELEASE_TRUST_PUBLIC_KEY}' \
   WECHAT_UPLOADER_TRUST_PUBLIC_KEY='${STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY}' \
   STAGING_RELEASE_ROOT='${STAGING_RELEASE_ROOT}' \
   GONGKAO_CURRENT_LINK='${staging_current_link}' \
   GONGKAO_CONFIG_ROOT='${staging_config_root}' \
     \"\${controller}\" '${release_id}'"
