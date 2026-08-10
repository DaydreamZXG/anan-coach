# Runtime and package budget

The component is data-independent: no real-sample JSON or image is imported by
the Mini Program source. Real evidence remains under this report directory.

Runtime work is deterministic and linear after validation. A question is
rejected before scene construction above 16 panels, 512 primitives, 4,096 path
instructions, nesting depth 4, 256 points per primitive, 4,096 explicit points,
128 vertices or 256 edges per wireframe, 4,096 wireframe edges, 1,024 glyph code
points, or 8,192 estimated Canvas commands. Canvas paints the resulting command
list once per bind or resize; there is no animation loop, OCR, network call, or
chart runtime.

Relative to commit `55a9d2d3e`, the component directory grows from 86,726 to
166,095 source bytes (+79,369). Runtime-candidate files (`ts/json/wxml/wxss`)
grow from 85,402 to 161,140 bytes (+75,738, about 74.0 KiB). This is source size,
not an official WeChat compiled-package receipt; `package:validate` explicitly
leaves final compiled main-package measurement to official packaging.

The exact source-byte delta and validation results are recorded in the final
task handoff after formatting and package checks. Node Vitest timings are useful
regression evidence but are not represented as a WeChat-device frame-time SLA.
