#!/usr/bin/env bash
set -euo pipefail

if [[ "${GRAPHIC_REASONING_GPU_READY:-0}" != "1" ]]; then
  echo "blocked: GRAPHIC_REASONING_GPU_READY=1 is required" >&2
  exit 64
fi

runtime_root="${1:?usage: run_omnisvg_candidate.sh RUNTIME_ROOT INPUT_PNG OUTPUT_DIR MODEL_PATH}"
input_png="${2:?missing input PNG}"
output_dir="${3:?missing output directory}"
model_path="${4:?missing model path}"
if [[ ! -f "${runtime_root}/GPU_READY" ]]; then
  echo "blocked: ${runtime_root}/GPU_READY is absent" >&2
  exit 64
fi
mkdir -p "${output_dir}/input"
cp "${input_png}" "${output_dir}/input/panel.png"
"${runtime_root}/venvs/omnisvg/bin/python" "${runtime_root}/sources/OmniSVG/inference.py" \
  --task image-to-svg --input "${output_dir}/input" --output "${output_dir}" \
  --model-size 4B --model-path "${model_path}" --num-candidates 1 --save-all-candidates --save-png
