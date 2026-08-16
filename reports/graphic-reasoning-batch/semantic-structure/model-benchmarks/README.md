# Semantic model benchmarks

Each subdirectory is an isolated extraction and solver-evaluation run. Do not
compare schema validity with factual quality: schema-valid candidates still
remain `repair_pending` until the visible panel facts are checked.

- `qwen3-vl-30b-a3b/` is the preserved five-question baseline from 2026-08-11.
  It scored 1/5 when solving from semantic JSON and 1/5 from the original image.
- `gemma-4-31b-it-whole-question-v1/` preserves the rejected one-shot Gemma
  baseline. Its first item took several minutes, omitted important option lines,
  and scored 0/1 from both semantic JSON and the original image.
- `gemma-4-31b-it/` is written only by the focused per-panel compact extractor.

The original source image and SHA-256 stay authoritative in every run. Model
output is semantic evidence, never coordinate or vector authority.
