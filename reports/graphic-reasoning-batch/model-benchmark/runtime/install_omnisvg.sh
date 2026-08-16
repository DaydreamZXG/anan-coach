#!/usr/bin/env bash
set -euo pipefail

if [[ "${GRAPHIC_REASONING_GPU_READY:-0}" != "1" ]]; then
  echo "blocked: GRAPHIC_REASONING_GPU_READY=1 is required" >&2
  exit 64
fi

runtime_root="${1:?usage: install_omnisvg.sh RUNTIME_ROOT}"
if [[ ! -f "${runtime_root}/GPU_READY" ]]; then
  echo "blocked: ${runtime_root}/GPU_READY is absent" >&2
  exit 64
fi

repo_dir="${runtime_root}/sources/OmniSVG"
venv_dir="${runtime_root}/venvs/omnisvg"
mkdir -p "${runtime_root}/sources" "${runtime_root}/venvs" "${runtime_root}/models"
git clone --depth 1 https://github.com/OmniSVG/OmniSVG.git "${repo_dir}"
python3 -m venv "${venv_dir}"
"${venv_dir}/bin/python" -m pip install --upgrade pip
"${venv_dir}/bin/python" -m pip install -r "${repo_dir}/requirements.txt"
echo "weights remain a separate explicit step: OmniSVG/OmniSVG1.1_4B"
