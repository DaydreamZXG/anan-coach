# WeChat structured-visuals real-sample acceptance

This report is evidence for an isolated rendering prototype, not a claim that a
question page is integrated or that all graphic-reasoning images are supported.
Each directory contains the real source, the structured input, a redraw and
comparison, or an explicit `needs_review` record explaining why redraw was
withheld. `acceptance.json` checks panel count, option count, outer frame, major
topology, text, and key relative positions instead of relying on “looks close”.

Question-bank knowledge points were not used as visual grammar. The rows below
come from directly observing source pixels and the actual structured JSON.

## Coverage matrix

| Visual grammar              | Real sample evidence                                                                       | Result       | What is proved                                                                                                                          |
| --------------------------- | ------------------------------------------------------------------------------------------ | ------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| Straight-line topology      | `straight-topology/`, source question 16070178                                             | verified     | 3×3 stem, four options, straight-segment connectivity and relative endpoints                                                            |
| Curves and arcs             | `curve-arrow-wireframe/`, source question 15833882; `rare-vector/`                         | verified A/B | ellipse, bottom elliptical arc, and verified vector contours can be redrawn                                                             |
| Circle / ellipse / sector   | `curve-arrow-wireframe/`                                                                   | partial      | circles and ellipse are proved by a real graphic sample; sector has renderer tests but no accepted graphic-reasoning sample in this set |
| Dot array / grid            | `binary-grid/`, source question 15833795                                                   | verified     | exact 9×9 occupancy, filled cells, matrix positions and four options                                                                    |
| Fill / shade                | `binary-grid/`; `shaded-polygons-needs-review/`, source question 16070043                  | partial      | solid fill is proved; fill/opacity is renderable; six irregular gray faces are not yet vector-verified                                  |
| Arrow and direction         | `curve-arrow-wireframe/` panel 3                                                           | verified     | semantic right arrow plus primitives preserves the observed direction and topology                                                      |
| Chinese / letters / numbers | `chinese-glyph-sequence/`, source question 14077; `glyph-sequence/`, source question 48335 | verified     | exact print content/order and centered relative positions through `textGlyph`                                                           |
| Life symbols / contours     | `life-outline-needs-review/`, source question 16593051                                     | needs_review | panel inventory and labels are audited; rocket/ladybug/bird contours are deliberately not approximated                                  |
| 3D / folding / spatial      | `curve-arrow-wireframe/` panels 4–5; `folding-needs-review/`, source question 16070182     | partial      | wireframe geometry is proved; cube-net folding correspondence is not claimed                                                            |
| Complex rare vector         | `rare-vector/`, source question `cq_09fc9cc57c86e57c553944c2`                              | verified B   | contour topology is deterministically redrawable; semantic interpretation is not claimed                                                |

## Layer verdicts

- A — standard semantic bricks: proved here for line, polygon, rect, circle,
  ellipse, arc, semantic arrow, wireframe geometry, and standard Chinese,
  Latin, Greek and numeric glyphs.
- B — deterministic vectors: proved by the existing real rare-vector sample.
  New v2 `pathGroup` requires structured commands and explicit source evidence.
- C — `needs_review`: life outlines, unverified irregular shaded polygons, and
  folding semantics remain here. Their comparison pages intentionally contain
  no redraw.

The code additionally tests rounded rectangles, quadratic/cubic Béziers,
polyline, dot, face, dash, opacity, transform, RTL/vertical glyph metadata, and
all SVG M/L/H/V/C/S/Q/T/A/Z normalization rules. These are renderer capabilities
unless a real sample row above proves them.

## Evidence provenance

Copied `source.png` files retain SHA-256 values in their structured or audit
JSON. The binary-grid, straight-topology, and rare-vector source/structured/
redrawn/comparison artifacts are copied from the read-only
`reports/graphic-reasoning-structure-test/` audit so this report is standalone.
No generated question or invented option is presented as a real sample.
