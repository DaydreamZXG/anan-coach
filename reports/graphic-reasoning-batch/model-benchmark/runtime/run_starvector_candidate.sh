#!/usr/bin/env bash
set -euo pipefail

if [[ "${GRAPHIC_REASONING_GPU_READY:-0}" != "1" ]]; then
  echo "blocked: GRAPHIC_REASONING_GPU_READY=1 is required" >&2
  exit 64
fi

runtime_root="${1:?usage: run_starvector_candidate.sh RUNTIME_ROOT INPUT_PNG OUTPUT_DIR VERIFIED_RUNNER}"
input_png="${2:?missing input PNG}"
output_dir="${3:?missing output directory}"
verified_runner="${4:?missing verified StarVector runner}"
if [[ ! -f "${runtime_root}/GPU_READY" ]]; then
  echo "blocked: ${runtime_root}/GPU_READY is absent" >&2
  exit 64
fi
if [[ ! -x "${verified_runner}" ]]; then
  echo "blocked: a locally inspected executable StarVector runner is required" >&2
  exit 65
fi

mkdir -p "${output_dir}/input" "${output_dir}/raw"
cp "${input_png}" "${output_dir}/input/panel.png"
"${verified_runner}" \
  --model "starvector/starvector-1b-im2svg" \
  --input "${output_dir}/input/panel.png" \
  --output "${output_dir}/raw/candidate.svg" \
  --num-candidates 1
