#!/usr/bin/env bash
set -euo pipefail

if [[ "${GRAPHIC_REASONING_GPU_READY:-0}" != "1" ]]; then
  echo "blocked: GRAPHIC_REASONING_GPU_READY=1 is required" >&2
  exit 64
fi

runtime_root="${1:?usage: install_starvector.sh RUNTIME_ROOT}"
if [[ ! -f "${runtime_root}/GPU_READY" ]]; then
  echo "blocked: ${runtime_root}/GPU_READY is absent" >&2
  exit 64
fi

repo_dir="${runtime_root}/sources/star-vector"
venv_dir="${runtime_root}/venvs/starvector"
mkdir -p "${runtime_root}/sources" "${runtime_root}/venvs" "${runtime_root}/models"
git clone --depth 1 https://github.com/joanrod/star-vector.git "${repo_dir}"
python3 -m venv "${venv_dir}"
"${venv_dir}/bin/python" -m pip install --upgrade pip
"${venv_dir}/bin/python" -m pip install -e "${repo_dir}"
echo "weights remain a separate explicit step: starvector/starvector-1b-im2svg"
