export const STRUCTURED_VISUAL_REGISTRY = {
  "grouped-bar-chart-v1": {
    kind: "groupedBar",
    accessibleName: "分组柱状图",
  },
  "dual-axis-combo-chart-v1": {
    kind: "barLine",
    accessibleName: "柱线组合图",
  },
  "pie-chart-v1": {
    kind: "pie",
    accessibleName: "饼图",
  },
  "graphic-binary-grid-v1": {
    kind: "binaryGrid",
    accessibleName: "二值网格图形推理",
  },
  "graphic-line-topology-candidate-v1": {
    kind: "pathPanels",
    accessibleName: "路径图形推理",
  },
  "graphic-line-art-v1": {
    kind: "pathPanels",
    accessibleName: "路径图形推理",
  },
  "graphic-primitives-v2": {
    kind: "primitivePanels",
    accessibleName: "结构化图形推理",
  },
} as const;

export type StructuredVisualSchemaVersion = keyof typeof STRUCTURED_VISUAL_REGISTRY;
export type StructuredVisualKind =
  (typeof STRUCTURED_VISUAL_REGISTRY)[StructuredVisualSchemaVersion]["kind"];

export interface StructuredVisualRegistration {
  schemaVersion: StructuredVisualSchemaVersion;
  kind: StructuredVisualKind;
  accessibleName: string;
}

/**
 * The registry deliberately dispatches on the exact versioned discriminator.
 * Field-shape guessing would risk drawing malformed or future schemas as the
 * wrong visual.
 */
export function selectStructuredVisual(
  schemaVersion: unknown,
): StructuredVisualRegistration | null {
  if (typeof schemaVersion !== "string" || !(schemaVersion in STRUCTURED_VISUAL_REGISTRY)) {
    return null;
  }
  const version = schemaVersion as StructuredVisualSchemaVersion;
  const registration = STRUCTURED_VISUAL_REGISTRY[version];
  return {
    schemaVersion: version,
    kind: registration.kind,
    accessibleName: registration.accessibleName,
  };
}
