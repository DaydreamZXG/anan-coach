import type { PathInstruction } from "./path";
import type { ValidationIssue } from "./schema";

export const GRAPHIC_PRIMITIVE_LIMITS = {
  maxPanels: 16,
  maxPrimitivesPerPanel: 128,
  maxTotalPrimitives: 512,
  maxNestingDepth: 4,
  maxPointsPerPrimitive: 256,
  maxTotalPoints: 4_096,
  maxPathCommandsPerGroup: 2_048,
  maxTotalPathCommands: 4_096,
  maxGlyphCodePoints: 16,
  maxMetadataStringLength: 256,
  maxWireframeVertices: 128,
  maxWireframeEdges: 256,
  maxTotalWireframeEdges: 4_096,
  maxTotalGlyphCodePoints: 1_024,
  maxEstimatedDrawCommands: 8_192,
  maxCoordinateMagnitude: 10_000,
  maxScale: 20,
  minScale: 0.05,
} as const;

export interface PrimitivePoint {
  x: number;
  y: number;
}

export interface PrimitiveBox extends PrimitivePoint {
  width: number;
  height: number;
}

export interface PrimitiveTransform {
  translateX: number;
  translateY: number;
  scaleX: number;
  scaleY: number;
  rotateDegrees: number;
  originX: number;
  originY: number;
}

export interface PrimitiveStyle {
  stroke: string | null;
  fill: string | null;
  lineWidth: number;
  dash: number[];
  opacity: number;
}

interface PrimitiveBase {
  style: PrimitiveStyle;
  transform: PrimitiveTransform;
}

export type GraphicPrimitive =
  | (PrimitiveBase & { type: "line"; from: PrimitivePoint; to: PrimitivePoint })
  | (PrimitiveBase & { type: "polyline"; points: PrimitivePoint[] })
  | (PrimitiveBase & { type: "polygon"; points: PrimitivePoint[] })
  | (PrimitiveBase & { type: "rect"; box: PrimitiveBox })
  | (PrimitiveBase & { type: "roundedRect"; box: PrimitiveBox; radius: number })
  | (PrimitiveBase & { type: "circle"; center: PrimitivePoint; radius: number })
  | (PrimitiveBase & {
      type: "ellipse";
      center: PrimitivePoint;
      radiusX: number;
      radiusY: number;
      rotationDegrees: number;
    })
  | (PrimitiveBase & {
      type: "arc";
      center: PrimitivePoint;
      radiusX: number;
      radiusY: number;
      rotationDegrees: number;
      startDegrees: number;
      endDegrees: number;
      anticlockwise: boolean;
    })
  | (PrimitiveBase & {
      type: "quadraticBezier";
      from: PrimitivePoint;
      control: PrimitivePoint;
      to: PrimitivePoint;
    })
  | (PrimitiveBase & {
      type: "cubicBezier";
      from: PrimitivePoint;
      control1: PrimitivePoint;
      control2: PrimitivePoint;
      to: PrimitivePoint;
    })
  | (PrimitiveBase & {
      type: "pathGroup";
      paths: Array<{ commands: PathInstruction[]; fillRule: "nonzero" | "evenodd" }>;
      sourceEvidence: VectorSourceEvidence;
    })
  | (PrimitiveBase & { type: "dot"; center: PrimitivePoint; radius: number })
  | (PrimitiveBase & {
      type: "textGlyph";
      content: string;
      position: PrimitivePoint;
      fontCategory: "sans" | "serif" | "monospace" | "symbol";
      fontStyle: "normal" | "italic";
      fontWeight: 400 | 500 | 600 | 700;
      fontSize: number;
      direction: "ltr" | "rtl" | "vertical";
      align: "left" | "center" | "right";
    })
  | (PrimitiveBase & {
      type: "semanticSymbol";
      semanticLabel: string;
      primitives: GraphicPrimitive[];
    })
  | (PrimitiveBase & {
      type: "wireframe";
      vertices: PrimitivePoint[];
      edges: Array<[number, number]>;
      interpretation: "geometry_only";
    })
  | (PrimitiveBase & { type: "face"; points: PrimitivePoint[] });

export interface VectorSourceEvidence {
  sourceAsset: string;
  extractionMethod: "manual_trace" | "verified_vector" | "cv_contour";
  verified: true;
}

export type PrimitivePanelRole = "stem" | "sequence" | "missing" | "option" | "sample";

export interface GraphicPrimitivePanel {
  id: string;
  role: PrimitivePanelRole;
  row?: number;
  column?: number;
  position?: number;
  primitives: GraphicPrimitive[];
}

export interface GraphicPrimitivesVisual {
  schemaVersion: "graphic-primitives-v2";
  kind: "primitivePanels";
  fidelityTier: "semantic" | "deterministic_vector";
  layoutType: "matrix_3x3" | "sequence" | "classification_2x3";
  coordinateSpace: { width: number; height: number };
  panels: GraphicPrimitivePanel[];
  evidence: Record<string, unknown>;
  complexity: {
    primitiveCount: number;
    pathCommandCount: number;
    pointCount: number;
    wireframeEdgeCount: number;
    glyphCodePointCount: number;
    estimatedDrawCommandCount: number;
  };
}

export type GraphicPrimitiveValidationResult =
  | { status: "valid"; visual: GraphicPrimitivesVisual; issues: ValidationIssue[] }
  | { status: "empty"; issues: ValidationIssue[] }
  | { status: "needs_review"; issues: ValidationIssue[] }
  | { status: "invalid"; issues: ValidationIssue[] };

type UnknownRecord = Record<string, unknown>;

interface ComplexityCounter {
  primitiveCount: number;
  pathCommandCount: number;
}

const primitiveTypes = [
  "line",
  "polyline",
  "polygon",
  "rect",
  "roundedRect",
  "circle",
  "ellipse",
  "arc",
  "quadraticBezier",
  "cubicBezier",
  "pathGroup",
  "dot",
  "textGlyph",
  "semanticSymbol",
  "wireframe",
  "face",
] as const;
const safeColorPattern = /^(?:#[\da-f]{3,8}|rgba?\([\d\s.,%]+\))$/iu;

export function validateGraphicPrimitives(
  input: UnknownRecord,
  evidence: Record<string, unknown>,
): GraphicPrimitiveValidationResult {
  const issues: ValidationIssue[] = [];
  if (input.visualGrammar !== "primitive_scene") {
    issues.push(error("visualGrammar", "PRIMITIVE_GRAMMAR_INVALID", "必须为 primitive_scene。"));
  }
  const fidelityTier = literal(
    input.fidelityTier,
    ["semantic", "deterministic_vector", "needs_review"],
    "fidelityTier",
    issues,
  );
  if (fidelityTier === "needs_review") {
    requiredString(input.reviewReason, "reviewReason", issues);
    requiredString(input.sourceAsset, "sourceAsset", issues);
    const audit = isRecord(input.audit) ? input.audit : null;
    if (
      !audit ||
      positiveInteger(audit.expectedPanelCount, "audit.expectedPanelCount", issues) === null ||
      integerInRange(audit.expectedOptionCount, 0, 16, "audit.expectedOptionCount", issues) ===
        null ||
      requiredString(audit.sourceSha256, "audit.sourceSha256", issues) === null ||
      requiredString(audit.decision, "audit.decision", issues) === null
    ) {
      issues.push(
        error("audit", "REVIEW_AUDIT_REQUIRED", "needs_review 必须保留预期面板数与来源审计。"),
      );
    }
    return hasErrors(issues)
      ? { status: "invalid", issues }
      : {
          status: "needs_review",
          issues: [
            ...issues,
            warning(
              "reviewReason",
              "RENDERING_REQUIRES_REVIEW",
              "该样本无法可信还原，组件不会绘制近似图。",
            ),
          ],
        };
  }

  const layoutType = literal(
    input.layoutType,
    ["matrix_3x3", "sequence", "classification_2x3"],
    "layoutType",
    issues,
  );
  const coordinateSpace = validateCoordinateSpace(input.coordinateSpace, issues);
  const rawPanels = recordArray(input.panels, "panels", issues);
  if (rawPanels.length === 0) {
    return hasErrors(issues)
      ? { status: "invalid", issues }
      : {
          status: "empty",
          issues: [warning("panels", "PRIMITIVE_PANELS_EMPTY", "没有可绘制面板。")],
        };
  }
  if (rawPanels.length > GRAPHIC_PRIMITIVE_LIMITS.maxPanels) {
    issues.push(
      error(
        "panels",
        "PRIMITIVE_PANEL_LIMIT",
        `面板数不能超过 ${String(GRAPHIC_PRIMITIVE_LIMITS.maxPanels)}。`,
      ),
    );
  }

  const counter: ComplexityCounter = { primitiveCount: 0, pathCommandCount: 0 };
  const panels: GraphicPrimitivePanel[] = [];
  rawPanels.forEach((panel, panelIndex) => {
    const path = `panels[${String(panelIndex)}]`;
    const id = requiredString(panel.id, `${path}.id`, issues);
    const role = literal(
      panel.role,
      ["stem", "sequence", "missing", "option", "sample"],
      `${path}.role`,
      issues,
    );
    const rawPrimitives = recordArray(panel.primitives, `${path}.primitives`, issues);
    if (rawPrimitives.length > GRAPHIC_PRIMITIVE_LIMITS.maxPrimitivesPerPanel) {
      issues.push(
        error(
          `${path}.primitives`,
          "PRIMITIVE_PANEL_COMPLEXITY_LIMIT",
          `单面板 primitive 不能超过 ${String(GRAPHIC_PRIMITIVE_LIMITS.maxPrimitivesPerPanel)}。`,
        ),
      );
    }
    if (role === "missing" && rawPrimitives.length > 0) {
      issues.push(
        error(`${path}.primitives`, "MISSING_PANEL_HAS_PRIMITIVES", "空缺面板不得包含 primitive。"),
      );
    }
    const primitives = rawPrimitives
      .map((primitive, primitiveIndex) =>
        validatePrimitive(
          primitive,
          `${path}.primitives[${String(primitiveIndex)}]`,
          0,
          counter,
          issues,
        ),
      )
      .filter((primitive): primitive is GraphicPrimitive => primitive !== null);
    const location = validatePanelLocation(panel, role, layoutType, path, issues);
    if (id !== null && role !== null && location !== null) {
      panels.push({ id, role, primitives, ...location });
    }
  });

  if (counter.primitiveCount > GRAPHIC_PRIMITIVE_LIMITS.maxTotalPrimitives) {
    issues.push(
      error(
        "panels",
        "PRIMITIVE_TOTAL_LIMIT",
        `单题 primitive 总数不能超过 ${String(GRAPHIC_PRIMITIVE_LIMITS.maxTotalPrimitives)}。`,
      ),
    );
  }
  if (counter.pathCommandCount > GRAPHIC_PRIMITIVE_LIMITS.maxTotalPathCommands) {
    issues.push(
      error(
        "panels",
        "PATH_TOTAL_LIMIT",
        `单题 path 指令总数不能超过 ${String(GRAPHIC_PRIMITIVE_LIMITS.maxTotalPathCommands)}。`,
      ),
    );
  }
  const measured = measurePanels(panels);
  if (measured.pointCount > GRAPHIC_PRIMITIVE_LIMITS.maxTotalPoints) {
    issues.push(error("panels", "POINT_TOTAL_LIMIT", "单题坐标点总数超过安全上限。"));
  }
  if (measured.wireframeEdgeCount > GRAPHIC_PRIMITIVE_LIMITS.maxTotalWireframeEdges) {
    issues.push(
      error("panels", "WIREFRAME_EDGE_TOTAL_LIMIT", "单题 wireframe 边总数超过安全上限。"),
    );
  }
  if (measured.glyphCodePointCount > GRAPHIC_PRIMITIVE_LIMITS.maxTotalGlyphCodePoints) {
    issues.push(error("panels", "GLYPH_TOTAL_LIMIT", "单题 glyph 字符总数超过安全上限。"));
  }
  if (measured.estimatedDrawCommandCount > GRAPHIC_PRIMITIVE_LIMITS.maxEstimatedDrawCommands) {
    issues.push(
      error("panels", "DRAW_COMMAND_TOTAL_LIMIT", "单题预计 Canvas 指令数超过安全上限。"),
    );
  }
  validatePanelInventory(panels, layoutType, issues);
  if (
    fidelityTier === "semantic" &&
    panels.some((panel) => panel.primitives.some(containsPathGroup))
  ) {
    issues.push(
      error(
        "panels",
        "SEMANTIC_TIER_CONTAINS_PATH_GROUP",
        "A 层语义积木不得包含仅保证轮廓的 pathGroup。",
      ),
    );
  }
  if (
    hasErrors(issues) ||
    fidelityTier === null ||
    layoutType === null ||
    coordinateSpace === null
  ) {
    return { status: "invalid", issues };
  }
  return {
    status: "valid",
    issues,
    visual: {
      schemaVersion: "graphic-primitives-v2",
      kind: "primitivePanels",
      fidelityTier,
      layoutType,
      coordinateSpace,
      panels,
      evidence,
      complexity: { ...counter, ...measured },
    },
  };
}

function validatePrimitive(
  input: UnknownRecord,
  path: string,
  depth: number,
  counter: ComplexityCounter,
  issues: ValidationIssue[],
): GraphicPrimitive | null {
  counter.primitiveCount += 1;
  if (depth > GRAPHIC_PRIMITIVE_LIMITS.maxNestingDepth) {
    issues.push(error(path, "PRIMITIVE_NESTING_LIMIT", "primitive 嵌套层级超过安全上限。"));
    return null;
  }
  const type = literal(input.type, primitiveTypes, `${path}.type`, issues);
  const style = validateStyle(input.style, `${path}.style`, issues);
  const transform = validateTransform(input.transform, `${path}.transform`, issues);
  if (type === null || style === null || transform === null) return null;
  const base = { style, transform };

  switch (type) {
    case "line": {
      const from = validatePoint(input.from, `${path}.from`, issues);
      const to = validatePoint(input.to, `${path}.to`, issues);
      return from && to ? { type, from, to, ...base } : null;
    }
    case "polyline":
    case "polygon":
    case "face": {
      const minimum = type === "polyline" ? 2 : 3;
      const points = validatePoints(input.points, `${path}.points`, minimum, issues);
      return points ? { type, points, ...base } : null;
    }
    case "rect": {
      const box = validateBox(input.box, `${path}.box`, issues);
      return box ? { type, box, ...base } : null;
    }
    case "roundedRect": {
      const box = validateBox(input.box, `${path}.box`, issues);
      const radius = nonNegativeNumber(input.radius, `${path}.radius`, issues);
      if (box && radius !== null && radius <= Math.min(box.width, box.height) / 2) {
        return { type, box, radius, ...base };
      }
      if (box && radius !== null) {
        issues.push(
          error(`${path}.radius`, "ROUNDED_RECT_RADIUS_INVALID", "圆角半径超过边长一半。"),
        );
      }
      return null;
    }
    case "circle":
    case "dot": {
      const center = validatePoint(input.center, `${path}.center`, issues);
      const radius = positiveNumber(input.radius, `${path}.radius`, issues);
      return center && radius !== null ? { type, center, radius, ...base } : null;
    }
    case "ellipse": {
      const center = validatePoint(input.center, `${path}.center`, issues);
      const radiusX = positiveNumber(input.radiusX, `${path}.radiusX`, issues);
      const radiusY = positiveNumber(input.radiusY, `${path}.radiusY`, issues);
      const rotationDegrees = finiteCoordinate(
        input.rotationDegrees,
        `${path}.rotationDegrees`,
        issues,
      );
      return center && radiusX !== null && radiusY !== null && rotationDegrees !== null
        ? { type, center, radiusX, radiusY, rotationDegrees, ...base }
        : null;
    }
    case "arc": {
      const center = validatePoint(input.center, `${path}.center`, issues);
      const radiusX = positiveNumber(input.radiusX, `${path}.radiusX`, issues);
      const radiusY = positiveNumber(input.radiusY, `${path}.radiusY`, issues);
      const rotationDegrees = finiteCoordinate(
        input.rotationDegrees,
        `${path}.rotationDegrees`,
        issues,
      );
      const startDegrees = finiteCoordinate(input.startDegrees, `${path}.startDegrees`, issues);
      const endDegrees = finiteCoordinate(input.endDegrees, `${path}.endDegrees`, issues);
      if (typeof input.anticlockwise !== "boolean") {
        issues.push(error(`${path}.anticlockwise`, "BOOLEAN_REQUIRED", "必须提供布尔值。"));
      }
      return center &&
        radiusX !== null &&
        radiusY !== null &&
        rotationDegrees !== null &&
        startDegrees !== null &&
        endDegrees !== null &&
        typeof input.anticlockwise === "boolean"
        ? {
            type,
            center,
            radiusX,
            radiusY,
            rotationDegrees,
            startDegrees,
            endDegrees,
            anticlockwise: input.anticlockwise,
            ...base,
          }
        : null;
    }
    case "quadraticBezier": {
      const from = validatePoint(input.from, `${path}.from`, issues);
      const control = validatePoint(input.control, `${path}.control`, issues);
      const to = validatePoint(input.to, `${path}.to`, issues);
      return from && control && to ? { type, from, control, to, ...base } : null;
    }
    case "cubicBezier": {
      const from = validatePoint(input.from, `${path}.from`, issues);
      const control1 = validatePoint(input.control1, `${path}.control1`, issues);
      const control2 = validatePoint(input.control2, `${path}.control2`, issues);
      const to = validatePoint(input.to, `${path}.to`, issues);
      return from && control1 && control2 && to
        ? { type, from, control1, control2, to, ...base }
        : null;
    }
    case "pathGroup": {
      const sourceEvidence = validateVectorEvidence(
        input.sourceEvidence,
        `${path}.sourceEvidence`,
        issues,
      );
      const rawPaths = recordArray(input.paths, `${path}.paths`, issues);
      const paths = rawPaths
        .map((entry, pathIndex) => {
          const entryPath = `${path}.paths[${String(pathIndex)}]`;
          const commands = validatePathInstructions(
            entry.commands,
            `${entryPath}.commands`,
            issues,
          );
          const fillRule = literal(
            entry.fillRule,
            ["nonzero", "evenodd"],
            `${entryPath}.fillRule`,
            issues,
          );
          if (commands) counter.pathCommandCount += commands.length;
          return commands && fillRule ? { commands, fillRule } : null;
        })
        .filter(
          (entry): entry is { commands: PathInstruction[]; fillRule: "nonzero" | "evenodd" } =>
            entry !== null,
        );
      if (counter.pathCommandCount > GRAPHIC_PRIMITIVE_LIMITS.maxTotalPathCommands) {
        issues.push(error(path, "PATH_TOTAL_LIMIT", "path 指令总数超过安全上限。"));
      }
      return sourceEvidence && paths.length === rawPaths.length && paths.length > 0
        ? { type, sourceEvidence, paths, ...base }
        : null;
    }
    case "textGlyph": {
      const content = requiredString(input.content, `${path}.content`, issues);
      const position = validatePoint(input.position, `${path}.position`, issues);
      const fontCategory = literal(
        input.fontCategory,
        ["sans", "serif", "monospace", "symbol"],
        `${path}.fontCategory`,
        issues,
      );
      const fontStyle = literal(input.fontStyle, ["normal", "italic"], `${path}.fontStyle`, issues);
      const fontWeight = literal(
        input.fontWeight,
        [400, 500, 600, 700],
        `${path}.fontWeight`,
        issues,
      );
      const fontSize = positiveNumber(input.fontSize, `${path}.fontSize`, issues);
      const direction = literal(
        input.direction,
        ["ltr", "rtl", "vertical"],
        `${path}.direction`,
        issues,
      );
      const align = literal(input.align, ["left", "center", "right"], `${path}.align`, issues);
      const contentLength = content ? Array.from(content).length : 0;
      if (content && contentLength > GRAPHIC_PRIMITIVE_LIMITS.maxGlyphCodePoints) {
        issues.push(error(`${path}.content`, "TEXT_GLYPH_LIMIT", "textGlyph 字符数超过安全上限。"));
      }
      return content &&
        contentLength <= GRAPHIC_PRIMITIVE_LIMITS.maxGlyphCodePoints &&
        position &&
        fontCategory &&
        fontStyle &&
        fontWeight &&
        fontSize !== null &&
        direction &&
        align
        ? {
            type,
            content,
            position,
            fontCategory,
            fontStyle,
            fontWeight,
            fontSize,
            direction,
            align,
            ...base,
          }
        : null;
    }
    case "semanticSymbol": {
      const semanticLabel = requiredString(input.semanticLabel, `${path}.semanticLabel`, issues);
      const rawPrimitives = recordArray(input.primitives, `${path}.primitives`, issues);
      const primitives = rawPrimitives
        .map((entry, index) =>
          validatePrimitive(
            entry,
            `${path}.primitives[${String(index)}]`,
            depth + 1,
            counter,
            issues,
          ),
        )
        .filter((entry): entry is GraphicPrimitive => entry !== null);
      return semanticLabel && primitives.length === rawPrimitives.length && primitives.length > 0
        ? { type, semanticLabel, primitives, ...base }
        : null;
    }
    case "wireframe": {
      const vertices = validatePoints(input.vertices, `${path}.vertices`, 2, issues);
      const rawEdges = Array.isArray(input.edges) ? input.edges : [];
      if (!Array.isArray(input.edges)) {
        issues.push(error(`${path}.edges`, "EDGE_ARRAY_REQUIRED", "wireframe edges 必须是数组。"));
      }
      if (rawEdges.length > GRAPHIC_PRIMITIVE_LIMITS.maxWireframeEdges) {
        issues.push(error(`${path}.edges`, "WIREFRAME_EDGE_LIMIT", "wireframe 边数超过上限。"));
      }
      const edges: Array<[number, number]> = [];
      rawEdges.forEach((edge, edgeIndex) => {
        if (
          !Array.isArray(edge) ||
          edge.length !== 2 ||
          !edge.every((value) => typeof value === "number" && Number.isInteger(value))
        ) {
          issues.push(
            error(
              `${path}.edges[${String(edgeIndex)}]`,
              "WIREFRAME_EDGE_INVALID",
              "边必须是两个顶点索引。",
            ),
          );
          return;
        }
        const from = edge[0] as number;
        const to = edge[1] as number;
        if (!vertices || from < 0 || to < 0 || from >= vertices.length || to >= vertices.length) {
          issues.push(
            error(
              `${path}.edges[${String(edgeIndex)}]`,
              "WIREFRAME_EDGE_OUT_OF_RANGE",
              "边引用了不存在的顶点。",
            ),
          );
          return;
        }
        edges.push([from, to]);
      });
      if (input.interpretation !== "geometry_only") {
        issues.push(
          error(
            `${path}.interpretation`,
            "WIREFRAME_INTERPRETATION_INVALID",
            "空间 wireframe 只能声明 geometry_only。",
          ),
        );
      }
      return vertices &&
        edges.length === rawEdges.length &&
        input.interpretation === "geometry_only"
        ? { type, vertices, edges, interpretation: "geometry_only", ...base }
        : null;
    }
  }
}

function validatePathInstructions(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): PathInstruction[] | null {
  if (!Array.isArray(value) || value.length === 0) {
    issues.push(error(path, "PATH_COMMANDS_REQUIRED", "pathGroup 必须包含结构化 commands。"));
    return null;
  }
  if (value.length > GRAPHIC_PRIMITIVE_LIMITS.maxPathCommandsPerGroup) {
    issues.push(error(path, "PATH_GROUP_LIMIT", "单个 pathGroup 指令数超过上限。"));
  }
  const instructions: PathInstruction[] = [];
  value.forEach((entry, index) => {
    const instructionPath = `${path}[${String(index)}]`;
    if (!isRecord(entry)) {
      issues.push(error(instructionPath, "PATH_COMMAND_INVALID", "path command 必须是对象。"));
      return;
    }
    const op = literal(
      entry.op,
      ["move", "line", "quadratic", "cubic", "arc", "close"],
      `${instructionPath}.op`,
      issues,
    );
    if (op === "close") {
      instructions.push({ op });
      return;
    }
    if (!op) return;
    const x = finiteCoordinate(entry.x, `${instructionPath}.x`, issues);
    const y = finiteCoordinate(entry.y, `${instructionPath}.y`, issues);
    if (x === null || y === null) return;
    if (op === "move" || op === "line") {
      instructions.push({ op, x, y });
    } else if (op === "quadratic") {
      const controlX = finiteCoordinate(entry.controlX, `${instructionPath}.controlX`, issues);
      const controlY = finiteCoordinate(entry.controlY, `${instructionPath}.controlY`, issues);
      if (controlX !== null && controlY !== null) {
        instructions.push({ op, controlX, controlY, x, y });
      }
    } else if (op === "cubic") {
      const values = ["control1X", "control1Y", "control2X", "control2Y"].map((key) =>
        finiteCoordinate(entry[key], `${instructionPath}.${key}`, issues),
      );
      if (values.every((number): number is number => number !== null)) {
        instructions.push({
          op,
          control1X: values[0] ?? 0,
          control1Y: values[1] ?? 0,
          control2X: values[2] ?? 0,
          control2Y: values[3] ?? 0,
          x,
          y,
        });
      }
    } else {
      const radiusX = positiveNumber(entry.radiusX, `${instructionPath}.radiusX`, issues);
      const radiusY = positiveNumber(entry.radiusY, `${instructionPath}.radiusY`, issues);
      const rotation = finiteCoordinate(entry.rotation, `${instructionPath}.rotation`, issues);
      if (typeof entry.largeArc !== "boolean" || typeof entry.sweep !== "boolean") {
        issues.push(error(instructionPath, "PATH_ARC_FLAG_INVALID", "arc 标志必须是布尔值。"));
      }
      if (
        radiusX !== null &&
        radiusY !== null &&
        rotation !== null &&
        typeof entry.largeArc === "boolean" &&
        typeof entry.sweep === "boolean"
      ) {
        instructions.push({
          op,
          radiusX,
          radiusY,
          rotation,
          largeArc: entry.largeArc,
          sweep: entry.sweep,
          x,
          y,
        });
      }
    }
  });
  if (instructions[0]?.op !== "move") {
    issues.push(error(path, "PATH_MUST_START_WITH_MOVE", "pathGroup 必须以 move 开始。"));
  }
  return instructions.length === value.length &&
    instructions.length <= GRAPHIC_PRIMITIVE_LIMITS.maxPathCommandsPerGroup
    ? instructions
    : null;
}

function validateStyle(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): PrimitiveStyle | null {
  if (value === undefined) {
    return { stroke: "#101828", fill: null, lineWidth: 1.5, dash: [], opacity: 1 };
  }
  if (!isRecord(value)) {
    issues.push(error(path, "PRIMITIVE_STYLE_INVALID", "style 必须是对象。"));
    return null;
  }
  const stroke = nullableColor(value.stroke, `${path}.stroke`, issues);
  const fill = nullableColor(value.fill, `${path}.fill`, issues);
  const lineWidth = nonNegativeNumber(value.lineWidth, `${path}.lineWidth`, issues);
  const opacity = finiteNumber(value.opacity, `${path}.opacity`, issues);
  const dash = numberArray(value.dash, `${path}.dash`, issues);
  if (stroke === null && fill === null) {
    issues.push(error(path, "PRIMITIVE_STYLE_INVISIBLE", "stroke 与 fill 不能同时为空。"));
  }
  if (lineWidth !== null && lineWidth > 12) {
    issues.push(error(`${path}.lineWidth`, "LINE_WIDTH_LIMIT", "线宽超过安全上限。"));
  }
  if (opacity !== null && (opacity < 0 || opacity > 1)) {
    issues.push(error(`${path}.opacity`, "OPACITY_INVALID", "透明度必须在 0 到 1 之间。"));
  }
  if (dash.some((part) => part <= 0) || dash.length > 8) {
    issues.push(error(`${path}.dash`, "DASH_INVALID", "虚线长度必须为正且最多 8 段。"));
  }
  return lineWidth !== null &&
    lineWidth <= 12 &&
    opacity !== null &&
    opacity >= 0 &&
    opacity <= 1 &&
    dash.every((part) => part > 0) &&
    dash.length <= 8
    ? { stroke, fill, lineWidth, dash, opacity }
    : null;
}

function validateTransform(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): PrimitiveTransform | null {
  if (value === undefined) {
    return {
      translateX: 0,
      translateY: 0,
      scaleX: 1,
      scaleY: 1,
      rotateDegrees: 0,
      originX: 0,
      originY: 0,
    };
  }
  if (!isRecord(value)) {
    issues.push(error(path, "PRIMITIVE_TRANSFORM_INVALID", "transform 必须是对象。"));
    return null;
  }
  const keys = [
    "translateX",
    "translateY",
    "scaleX",
    "scaleY",
    "rotateDegrees",
    "originX",
    "originY",
  ] as const;
  const numbers = keys.map((key) => finiteCoordinate(value[key], `${path}.${key}`, issues));
  if (!numbers.every((number): number is number => number !== null)) return null;
  const translateX = numbers[0] as number;
  const translateY = numbers[1] as number;
  const scaleX = numbers[2] as number;
  const scaleY = numbers[3] as number;
  const rotateDegrees = numbers[4] as number;
  const originX = numbers[5] as number;
  const originY = numbers[6] as number;
  if (
    Math.abs(scaleX) < GRAPHIC_PRIMITIVE_LIMITS.minScale ||
    Math.abs(scaleY) < GRAPHIC_PRIMITIVE_LIMITS.minScale ||
    Math.abs(scaleX) > GRAPHIC_PRIMITIVE_LIMITS.maxScale ||
    Math.abs(scaleY) > GRAPHIC_PRIMITIVE_LIMITS.maxScale
  ) {
    issues.push(error(path, "PRIMITIVE_SCALE_LIMIT", "transform scale 超过受控范围。"));
    return null;
  }
  return { translateX, translateY, scaleX, scaleY, rotateDegrees, originX, originY };
}

function validateVectorEvidence(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): VectorSourceEvidence | null {
  if (!isRecord(value)) {
    issues.push(error(path, "VECTOR_EVIDENCE_REQUIRED", "pathGroup 必须包含来源证据。"));
    return null;
  }
  const sourceAsset = requiredString(value.sourceAsset, `${path}.sourceAsset`, issues);
  const extractionMethod = literal(
    value.extractionMethod,
    ["manual_trace", "verified_vector", "cv_contour"],
    `${path}.extractionMethod`,
    issues,
  );
  if (value.verified !== true) {
    issues.push(error(`${path}.verified`, "VECTOR_NOT_VERIFIED", "pathGroup 必须经过显式验证。"));
  }
  return sourceAsset && extractionMethod && value.verified === true
    ? { sourceAsset, extractionMethod, verified: true }
    : null;
}

function validateCoordinateSpace(
  value: unknown,
  issues: ValidationIssue[],
): { width: number; height: number } | null {
  if (!isRecord(value)) {
    issues.push(error("coordinateSpace", "COORDINATE_SPACE_REQUIRED", "必须提供面板坐标空间。"));
    return null;
  }
  const width = positiveNumber(value.width, "coordinateSpace.width", issues);
  const height = positiveNumber(value.height, "coordinateSpace.height", issues);
  return width !== null && height !== null ? { width, height } : null;
}

function validatePanelLocation(
  panel: UnknownRecord,
  role: PrimitivePanelRole | null,
  layoutType: GraphicPrimitivesVisual["layoutType"] | null,
  path: string,
  issues: ValidationIssue[],
): { row?: number; column?: number; position?: number } | null {
  if (!role || !layoutType) return null;
  if (layoutType === "matrix_3x3" || layoutType === "classification_2x3") {
    const maxRow = layoutType === "matrix_3x3" ? 3 : 2;
    const row = integerInRange(panel.row, 1, maxRow, `${path}.row`, issues);
    const column = integerInRange(panel.column, 1, 3, `${path}.column`, issues);
    if (role === "option") {
      const position = integerInRange(panel.position, 1, 4, `${path}.position`, issues);
      return position === null ? null : { position };
    }
    return row === null || column === null ? null : { row, column };
  }
  const position = integerInRange(panel.position, 1, 16, `${path}.position`, issues);
  return position === null ? null : { position };
}

function validatePanelInventory(
  panels: GraphicPrimitivePanel[],
  layoutType: GraphicPrimitivesVisual["layoutType"] | null,
  issues: ValidationIssue[],
): void {
  if (!layoutType) return;
  const ids = new Set(panels.map((panel) => panel.id));
  if (ids.size !== panels.length) {
    issues.push(error("panels", "PRIMITIVE_PANEL_ID_DUPLICATE", "面板 id 必须唯一。"));
  }
  if (layoutType === "classification_2x3") {
    const cells = new Set(
      panels
        .filter((panel) => panel.role === "sample")
        .map((panel) => `${String(panel.row)}:${String(panel.column)}`),
    );
    if (panels.length !== 6 || cells.size !== 6) {
      issues.push(
        error("panels", "CLASSIFICATION_INVENTORY_INVALID", "分类图必须包含 2×3 的 6 个样本面板。"),
      );
    }
  } else if (layoutType === "matrix_3x3") {
    const cells = new Set(
      panels
        .filter((panel) => panel.role !== "option")
        .map((panel) => `${String(panel.row)}:${String(panel.column)}`),
    );
    const options = new Set(
      panels.filter((panel) => panel.role === "option").map((panel) => panel.position),
    );
    if (cells.size !== 9 || options.size !== 4) {
      issues.push(
        error("panels", "MATRIX_INVENTORY_INVALID", "矩阵图必须包含 9 个题干面板和 4 个选项。"),
      );
    }
  } else {
    const positions = new Set(panels.map((panel) => panel.position));
    const options = panels.filter((panel) => panel.role === "option");
    const missing = panels.filter((panel) => panel.role === "missing");
    if (positions.size !== panels.length || options.length !== 4 || missing.length !== 1) {
      issues.push(
        error(
          "panels",
          "SEQUENCE_INVENTORY_INVALID",
          "序列图必须有唯一位置、1 个空缺和 4 个选项。",
        ),
      );
    }
  }
}

function validatePoint(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): PrimitivePoint | null {
  if (!isRecord(value)) {
    issues.push(error(path, "POINT_REQUIRED", "坐标点必须是对象。"));
    return null;
  }
  const x = finiteCoordinate(value.x, `${path}.x`, issues);
  const y = finiteCoordinate(value.y, `${path}.y`, issues);
  return x !== null && y !== null ? { x, y } : null;
}

function validatePoints(
  value: unknown,
  path: string,
  minimum: number,
  issues: ValidationIssue[],
): PrimitivePoint[] | null {
  if (!Array.isArray(value) || value.length < minimum) {
    issues.push(error(path, "POINT_ARRAY_INVALID", `至少需要 ${String(minimum)} 个坐标点。`));
    return null;
  }
  if (value.length > GRAPHIC_PRIMITIVE_LIMITS.maxPointsPerPrimitive) {
    issues.push(error(path, "POINT_ARRAY_LIMIT", "单个 primitive 的点数超过上限。"));
  }
  if (value.length > GRAPHIC_PRIMITIVE_LIMITS.maxWireframeVertices && path.endsWith(".vertices")) {
    issues.push(error(path, "WIREFRAME_VERTEX_LIMIT", "wireframe 顶点数超过上限。"));
  }
  const points = value
    .map((point, index) => validatePoint(point, `${path}[${String(index)}]`, issues))
    .filter((point): point is PrimitivePoint => point !== null);
  return points.length === value.length &&
    points.length <= GRAPHIC_PRIMITIVE_LIMITS.maxPointsPerPrimitive &&
    (!path.endsWith(".vertices") || points.length <= GRAPHIC_PRIMITIVE_LIMITS.maxWireframeVertices)
    ? points
    : null;
}

function validateBox(value: unknown, path: string, issues: ValidationIssue[]): PrimitiveBox | null {
  if (!isRecord(value)) {
    issues.push(error(path, "BOX_REQUIRED", "矩形 box 必须是对象。"));
    return null;
  }
  const x = finiteCoordinate(value.x, `${path}.x`, issues);
  const y = finiteCoordinate(value.y, `${path}.y`, issues);
  const width = positiveNumber(value.width, `${path}.width`, issues);
  const height = positiveNumber(value.height, `${path}.height`, issues);
  return x !== null && y !== null && width !== null && height !== null
    ? { x, y, width, height }
    : null;
}

function containsPathGroup(primitive: GraphicPrimitive): boolean {
  return (
    primitive.type === "pathGroup" ||
    (primitive.type === "semanticSymbol" && primitive.primitives.some(containsPathGroup))
  );
}

function measurePanels(panels: GraphicPrimitivePanel[]): {
  pointCount: number;
  wireframeEdgeCount: number;
  glyphCodePointCount: number;
  estimatedDrawCommandCount: number;
} {
  const measured = {
    pointCount: 0,
    wireframeEdgeCount: 0,
    glyphCodePointCount: 0,
    estimatedDrawCommandCount: 0,
  };
  const visit = (primitive: GraphicPrimitive): void => {
    if (primitive.type === "semanticSymbol") {
      primitive.primitives.forEach(visit);
      return;
    }
    if (primitive.type === "wireframe") {
      measured.pointCount += primitive.vertices.length;
      measured.wireframeEdgeCount += primitive.edges.length;
      measured.estimatedDrawCommandCount += primitive.edges.length;
      return;
    }
    if (primitive.type === "pathGroup") {
      measured.estimatedDrawCommandCount += primitive.paths.length;
      return;
    }
    if (primitive.type === "textGlyph") {
      const glyphLength = Array.from(primitive.content).length;
      measured.glyphCodePointCount += glyphLength;
      measured.estimatedDrawCommandCount += primitive.direction === "vertical" ? glyphLength : 1;
      return;
    }
    if (
      primitive.type === "polyline" ||
      primitive.type === "polygon" ||
      primitive.type === "face"
    ) {
      measured.pointCount += primitive.points.length;
    }
    measured.estimatedDrawCommandCount += 1;
  };
  panels.forEach((panel) => {
    panel.primitives.forEach(visit);
  });
  return measured;
}

function nullableColor(value: unknown, path: string, issues: ValidationIssue[]): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !safeColorPattern.test(value)) {
    issues.push(error(path, "COLOR_INVALID", "颜色必须是受控的十六进制或 rgb(a) 值。"));
    return null;
  }
  return value;
}

function numberArray(value: unknown, path: string, issues: ValidationIssue[]): number[] {
  if (!Array.isArray(value)) {
    issues.push(error(path, "NUMBER_ARRAY_REQUIRED", "必须提供数值数组。"));
    return [];
  }
  const numbers: number[] = [];
  value.forEach((entry, index) => {
    const number = finiteNumber(entry, `${path}[${String(index)}]`, issues);
    if (number !== null) numbers.push(number);
  });
  return numbers;
}

function finiteCoordinate(value: unknown, path: string, issues: ValidationIssue[]): number | null {
  const number = finiteNumber(value, path, issues);
  if (number !== null && Math.abs(number) > GRAPHIC_PRIMITIVE_LIMITS.maxCoordinateMagnitude) {
    issues.push(error(path, "COORDINATE_LIMIT", "坐标超过安全范围。"));
    return null;
  }
  return number;
}

function finiteNumber(value: unknown, path: string, issues: ValidationIssue[]): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    issues.push(error(path, "FINITE_NUMBER_REQUIRED", "必须提供有限数值。"));
    return null;
  }
  return value;
}

function positiveNumber(value: unknown, path: string, issues: ValidationIssue[]): number | null {
  const number = finiteCoordinate(value, path, issues);
  if (number !== null && number <= 0) {
    issues.push(error(path, "POSITIVE_NUMBER_REQUIRED", "数值必须大于 0。"));
    return null;
  }
  return number;
}

function nonNegativeNumber(value: unknown, path: string, issues: ValidationIssue[]): number | null {
  const number = finiteCoordinate(value, path, issues);
  if (number !== null && number < 0) {
    issues.push(error(path, "NON_NEGATIVE_NUMBER_REQUIRED", "数值不能小于 0。"));
    return null;
  }
  return number;
}

function positiveInteger(value: unknown, path: string, issues: ValidationIssue[]): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    issues.push(error(path, "POSITIVE_INTEGER_REQUIRED", "必须提供正整数。"));
    return null;
  }
  return value;
}

function integerInRange(
  value: unknown,
  min: number,
  max: number,
  path: string,
  issues: ValidationIssue[],
): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    issues.push(
      error(path, "INTEGER_OUT_OF_RANGE", `必须是 ${String(min)} 到 ${String(max)} 的整数。`),
    );
    return null;
  }
  return value;
}

function requiredString(value: unknown, path: string, issues: ValidationIssue[]): string | null {
  if (typeof value !== "string" || value.trim().length === 0) {
    issues.push(error(path, "STRING_REQUIRED", "必须提供非空字符串。"));
    return null;
  }
  if (value.length > GRAPHIC_PRIMITIVE_LIMITS.maxMetadataStringLength) {
    issues.push(error(path, "STRING_LENGTH_LIMIT", "字符串超过结构化元数据长度上限。"));
    return null;
  }
  return value;
}

function literal<const T extends readonly (string | number)[]>(
  value: unknown,
  options: T,
  path: string,
  issues: ValidationIssue[],
): T[number] | null {
  if (!options.some((option) => option === value)) {
    issues.push(error(path, "LITERAL_INVALID", `必须是 ${options.join(" / ")} 之一。`));
    return null;
  }
  return value as T[number];
}

function recordArray(value: unknown, path: string, issues: ValidationIssue[]): UnknownRecord[] {
  if (!Array.isArray(value)) {
    issues.push(error(path, "OBJECT_ARRAY_REQUIRED", "必须提供对象数组。"));
    return [];
  }
  const records: UnknownRecord[] = [];
  value.forEach((entry, index) => {
    if (!isRecord(entry)) {
      issues.push(error(`${path}[${String(index)}]`, "OBJECT_REQUIRED", "数组项必须是对象。"));
    } else {
      records.push(entry);
    }
  });
  return records;
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasErrors(issues: ValidationIssue[]): boolean {
  return issues.some((issue) => issue.severity === "error");
}

function error(path: string, code: string, message: string): ValidationIssue {
  return { severity: "error", path, code, message };
}

function warning(path: string, code: string, message: string): ValidationIssue {
  return { severity: "warning", path, code, message };
}
