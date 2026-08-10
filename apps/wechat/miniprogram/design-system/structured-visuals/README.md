# Structured visuals prototype

This is an isolated WeChat Mini Program prototype. It is not registered by a
page, route, manifest, or business feature, and it is not a product-integration
or release claim.

## Component API

```xml
<structured-visual visual="{{structuredPayload}}" bindvalidationchange="handleValidation" />
```

- `visual` is one structured object. The component does no OCR, network request,
  model call, asset lookup, or business inference.
- `validationchange` emits `{ status, schemaVersion, kind, issues, evidence }`.
  `status` is `valid`, `empty`, `needs_review`, or `invalid`. Issues retain a
  stable code and field path; critical values are not silently normalized.
- Dispatch uses only the exact versioned `schemaVersion` in `registry.ts`.
  Unknown versions render an error state.
- Canvas is a thin adapter. Validation and responsive layout are pure functions
  in `schema.ts`, `primitives.ts`, `path.ts`, `primitive-scene.ts`, and
  `scene.ts`.

## Three fidelity layers

### A. Standard semantic bricks

`graphic-primitives-v2` with `fidelityTier: "semantic"` is editable and
computable. It accepts `line`, `polyline`, `polygon`, `rect`, `roundedRect`,
`circle`, `ellipse`, `arc`, `quadraticBezier`, `cubicBezier`, `dot`,
`textGlyph`, `semanticSymbol`, `wireframe`, and `face`.

Standard print Chinese characters, letters, and numbers use `textGlyph` with
content, font category, style, weight, direction, alignment, and relative
position. It preserves a font category, not the identity of a proprietary font
file. `wireframe` is always `geometry_only`; it does not assert an understood
folding relation.

### B. Deterministic vector bricks

`fidelityTier: "deterministic_vector"` may also contain `pathGroup`. A
`pathGroup` stores normalized command objects, never an unchecked SVG string,
and requires `sourceAsset`, extraction method, and `verified: true`. It can
redraw a verified hand-written glyph or life contour without claiming to
understand the contour's puzzle semantics.

The safe SVG normalizer supports absolute and relative `M/L/H/V/C/S/Q/T/A/Z`,
including repeated parameter sets, moveto's implicit lineto, reflected smooth
controls, and elliptical `large-arc`/`sweep` flags. The normalized schema keeps
only `move`, `line`, `quadratic`, `cubic`, `arc`, and `close` instructions.

### C. `needs_review`

When a life outline, distorted glyph, rare contour, or folding relation cannot
be reproduced from verified evidence, the payload must use
`fidelityTier: "needs_review"`, a reason, source asset, source hash, expected
panel/option counts, and an audit decision. The component shows a review state
and does not draw a similar-but-different substitute.

These layers do not mean that all graphic-reasoning images are covered.

## Frontend-owned layout and drawing

The payload describes internal geometry in a bounded coordinate space. The
frontend owns panel frames, missing/option labels, phone/tablet reflow, and the
mapping from relative coordinates to panel bounds. Model coordinates cannot
move or resize the outer question layout.

Canvas supports stroke/fill, line width, dash, opacity, bounded
translate/scale/rotate/origin transforms, quadratic and cubic curves, and
circle/ellipse arcs. Angles in primitive objects are degrees in Canvas's
coordinate orientation; SVG endpoint arcs are converted deterministically.
Images are never used as a Canvas background.

## Hard limits

Validation rejects work before scene construction when it exceeds these
per-question bounds:

- 16 panels, 128 top-level primitives per panel, 512 primitives including
  nested semantic symbols;
- nesting depth 4, 256 points per primitive and 4,096 explicit points total;
- 2,048 instructions per path and 4,096 path instructions total;
- 128 wireframe vertices, 256 edges per wireframe and 4,096 wireframe edges
  total;
- 16 Unicode code points per `textGlyph`, 1,024 glyph code points total, and 256
  characters per metadata string;
- 8,192 estimated Canvas draw commands total;
- coordinate magnitude 10,000 and transform scale magnitude 0.05 through 20;
- raw SVG normalization additionally caps 65,536 characters and 4,096 output
  instructions.

Scene construction and painting are linear in the validated primitives, points,
and path instructions. The component contains no chart library, embedded sample
payload, OCR runtime, network client, or model SDK; future question data is
expected to arrive outside this component.

## Registered schemas

- `grouped-bar-chart-v1`
- `dual-axis-combo-chart-v1`
- `pie-chart-v1`
- `graphic-binary-grid-v1`
- `graphic-line-topology-candidate-v1`
- `graphic-line-art-v1`
- `graphic-primitives-v2`

Explicit `null` chart values remain missing. Out-of-range values, non-closing
pie angles/shares, malformed 9×9 occupancy, invalid path commands, missing
vector provenance, unsafe transforms, and incomplete panel inventories are
rejected rather than guessed or repaired.
