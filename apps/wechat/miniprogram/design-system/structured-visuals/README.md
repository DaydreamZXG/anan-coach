# Structured visuals prototype

This directory is an isolated WeChat Mini Program prototype. It is intentionally
not registered by a page or route.

## Component API

```xml
<structured-visual visual="{{structuredPayload}}" bindvalidationchange="handleValidation" />
```

- `visual`: one structured object. The component does no OCR, network request,
  model call, or business lookup.
- `validationchange`: emits `{ status, schemaVersion, kind, issues, evidence }`.
  `status` is `valid`, `empty`, or `invalid`; issues always retain a field path
  and error code.
- Dispatch uses only the exact, versioned `schemaVersion` values in
  `registry.ts`. Unknown versions render an error state.

Supported audited inputs:

- `grouped-bar-chart-v1`
- `dual-axis-combo-chart-v1`
- `pie-chart-v1`
- `graphic-binary-grid-v1`
- `graphic-line-topology-candidate-v1`
- `graphic-line-art-v1`

The validator preserves explicit `null` chart values and reports them; missing
array entries, out-of-range values, non-closing pie angles/shares, non-9×9
occupancy, unsupported path commands, and incomplete graphic inventories are
rejected. The renderer supports only the audited linear SVG path subset
(`M/L/H/V/Z`, including relative forms). Curves, arcs, animation, interaction,
OCR, and product-page integration are outside this prototype.
