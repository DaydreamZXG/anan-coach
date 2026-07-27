#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
manifest="${root}/ops/upstreams/openilink-hub-0.1.6.json"
install_root="${OPENILINK_HUB_INSTALL_ROOT:-/opt/openilink-hub}"

IFS=$'\t' read -r version asset_url expected_sha < <(
  python3 - "${manifest}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
print(
    manifest["version"],
    manifest["assetUrl"],
    manifest["assetSha256"],
    sep="\t",
)
PY
)

case "$(uname -m)" in
  x86_64|amd64) ;;
  *)
    printf '%s\n' "OpeniLink Hub pinned artifact only supports linux amd64" >&2
    exit 2
    ;;
esac

temp_dir="$(mktemp -d)"
trap 'rm -rf "${temp_dir}"' EXIT
archive="${temp_dir}/openilink-hub.tar.gz"
curl --fail --location --silent --show-error "${asset_url}" --output "${archive}"
actual_sha="$(sha256sum "${archive}" | awk '{print $1}')"
if [[ "${actual_sha}" != "${expected_sha}" ]]; then
  printf '%s\n' "OpeniLink Hub artifact checksum mismatch" >&2
  exit 1
fi

tar -xzf "${archive}" -C "${temp_dir}"
binary="$(find "${temp_dir}" -maxdepth 2 -type f -name 'openilink-hub' -print -quit)"
if [[ -z "${binary}" ]]; then
  binary="$(find "${temp_dir}" -maxdepth 2 -type f -name 'oih' -print -quit)"
fi
if [[ -z "${binary}" ]]; then
  printf '%s\n' "OpeniLink Hub binary is missing from the verified artifact" >&2
  exit 1
fi

install -d -m 0755 "${install_root}/${version}"
install -m 0755 "${binary}" "${install_root}/${version}/openilink-hub"
ln -sfn "${install_root}/${version}" "${install_root}/current"
"${install_root}/current/openilink-hub" version
