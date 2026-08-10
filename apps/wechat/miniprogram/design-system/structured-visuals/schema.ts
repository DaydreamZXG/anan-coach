import { parseLinearSvgPath, type PathInstruction } from "./path";
import {
  selectStructuredVisual,
  type StructuredVisualKind,
  type StructuredVisualRegistration,
  type StructuredVisualSchemaVersion,
} from "./registry";

export type ValidationIssueSeverity = "error" | "warning";

export interface ValidationIssue {
  severity: ValidationIssueSeverity;
  code: string;
  path: string;
  message: string;
}

export interface NumericAxis {
  min: number;
  max: number;
  ticks: number[];
  unit: string;
}

export interface GroupedBarSeries {
  id: string;
  label: string;
  style: {
    fill: string;
    stroke: string;
    dash: number[];
  };
  values: Array<number | null>;
}

export interface GroupedBarVisual {
  schemaVersion: "grouped-bar-chart-v1";
  kind: "groupedBar";
  title: string;
  unit: string;
  categories: string[];
  series: GroupedBarSeries[];
  axis: NumericAxis & { step: number };
  evidence: Record<string, unknown>;
}

export interface ComboSeries {
  name: string;
  renderAs: "bar" | "line";
  axis: "left" | "right";
  unit: string;
  values: Array<number | null>;
}

export interface BarLineVisual {
  schemaVersion: "dual-axis-combo-chart-v1";
  kind: "barLine";
  title: string;
  categories: string[];
  series: ComboSeries[];
  leftAxis: NumericAxis;
  rightAxis: NumericAxis;
  evidence: Record<string, unknown>;
}

export interface PieSlice {
  label: string;
  startAngle: number;
  endAngle: number;
  sweepAngle: number;
  share: number;
}

export interface PieVisual {
  schemaVersion: "pie-chart-v1";
  kind: "pie";
  rotationConvention: "top_clockwise";
  labelsClockwise: string[];
  boundariesDegrees: number[];
  slices: PieSlice[];
  evidence: Record<string, unknown>;
}

export type GraphicPanelRole = "stem" | "sequence" | "missing" | "option";

export interface GraphicPanelLocation {
  row?: number;
  column?: number;
  position?: number;
}

export interface BinaryGridPanel extends GraphicPanelLocation {
  id: string;
  role: "stem" | "missing" | "option";
  occupancy: number[][] | null;
}

export interface BinaryGridVisual {
  schemaVersion: "graphic-binary-grid-v1";
  kind: "binaryGrid";
  layoutType: "matrix_3x3";
  items: BinaryGridPanel[];
  evidence: Record<string, unknown>;
}

export interface ParsedGraphicPath {
  instructions: PathInstruction[];
  fillRule: "nonzero" | "evenodd";
}

export interface PathPanel extends GraphicPanelLocation {
  id: string;
  role: GraphicPanelRole;
  viewBox: [number, number, number, number];
  paths: ParsedGraphicPath[];
}

export interface PathPanelsVisual {
  schemaVersion: "graphic-line-topology-candidate-v1" | "graphic-line-art-v1";
  kind: "pathPanels";
  layoutType: "matrix_3x3" | "sequence";
  items: PathPanel[];
  hasTrailingMissingPanel: boolean;
  evidence: Record<string, unknown>;
}

export type ValidatedStructuredVisual =
  GroupedBarVisual | BarLineVisual | PieVisual | BinaryGridVisual | PathPanelsVisual;

export type StructuredVisualValidation =
  | {
      status: "valid";
      registration: StructuredVisualRegistration;
      visual: ValidatedStructuredVisual;
      issues: ValidationIssue[];
      evidence: Record<string, unknown>;
    }
  | {
      status: "empty";
      registration: StructuredVisualRegistration;
      issues: ValidationIssue[];
      evidence: Record<string, unknown>;
    }
  | {
      status: "invalid";
      registration: StructuredVisualRegistration | null;
      issues: ValidationIssue[];
      evidence: Record<string, unknown>;
    };

type UnknownRecord = Record<string, unknown>;

const ANGLE_TOLERANCE = 0.2;
const SHARE_TOLERANCE = 0.001;
const colorPattern = /^(?:#[\da-f]{3,8}|rgba?\([\d\s.,%]+\))$/iu;

export function validateStructuredVisual(input: unknown): StructuredVisualValidation {
  const evidence = collectEvidence(input);
  if (!isRecord(input)) {
    return invalid(null, evidence, [
      issue("error", "INPUT_NOT_OBJECT", "$", "结构化视觉输入必须是对象。"),
    ]);
  }

  const registration = selectStructuredVisual(input.schemaVersion);
  if (!registration) {
    return invalid(null, evidence, [
      issue(
        "error",
        "UNKNOWN_SCHEMA_VERSION",
        "schemaVersion",
        "无法识别 schemaVersion，已停止渲染以避免画错。",
      ),
    ]);
  }

  switch (registration.schemaVersion) {
    case "grouped-bar-chart-v1":
      return validateGroupedBar(input, registration, evidence);
    case "dual-axis-combo-chart-v1":
      return validateBarLine(input, registration, evidence);
    case "pie-chart-v1":
      return validatePie(input, registration, evidence);
    case "graphic-binary-grid-v1":
      return validateBinaryGrid(input, registration, evidence);
    case "graphic-line-topology-candidate-v1":
      return validateTopologyPaths(input, registration, evidence);
    case "graphic-line-art-v1":
      return validateLineArtPaths(input, registration, evidence);
  }
}

function validateGroupedBar(
  input: UnknownRecord,
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
): StructuredVisualValidation {
  const issues: ValidationIssue[] = [];
  expectLiteral(input.chartType, "groupedBar", "chartType", issues);
  const title = requiredString(input.title, "title", issues);
  const unit = requiredString(input.unit, "unit", issues);
  const categories = stringArray(input.categories, "categories", issues);
  const rawSeries = recordArray(input.series, "series", issues);
  const axis = groupedAxis(input.yAxis, "yAxis", issues);

  if (categories.length === 0 || rawSeries.length === 0) {
    return empty(registration, evidence, issues, "CHART_HAS_NO_DATA", "图表没有分类或系列数据。");
  }

  const series: GroupedBarSeries[] = [];
  const ids = new Set<string>();
  rawSeries.forEach((entry, seriesIndex) => {
    const path = `series[${String(seriesIndex)}]`;
    const id = requiredString(entry.id, `${path}.id`, issues);
    const label = requiredString(entry.label, `${path}.label`, issues);
    if (id !== null && ids.has(id)) {
      issues.push(issue("error", "DUPLICATE_SERIES_ID", `${path}.id`, `系列 id ${id} 重复。`));
    }
    if (id !== null) ids.add(id);
    const style = groupedStyle(entry.style, `${path}.style`, issues);
    const values = nullableNumberArray(entry.values, `${path}.values`, issues);
    if (values.length !== categories.length) {
      issues.push(
        issue(
          "error",
          "CATEGORY_VALUE_LENGTH_MISMATCH",
          `${path}.values`,
          `数值数量 ${String(values.length)} 与分类数量 ${String(categories.length)} 不一致。`,
        ),
      );
    }
    warnNullValues(values, `${path}.values`, issues);
    if (id !== null && label !== null && style !== null) series.push({ id, label, style, values });
  });

  if (axis) {
    series.forEach((entry, seriesIndex) => {
      validateValuesInAxis(entry.values, axis, `series[${String(seriesIndex)}].values`, issues);
    });
  }
  if (!hasNumericValues(series)) {
    return empty(registration, evidence, issues, "CHART_VALUES_EMPTY", "图表数值全部为空。");
  }
  if (hasErrors(issues) || title === null || unit === null || axis === null) {
    return invalid(registration, evidence, issues);
  }
  return valid(registration, evidence, issues, {
    schemaVersion: "grouped-bar-chart-v1",
    kind: "groupedBar",
    title,
    unit,
    categories,
    series,
    axis,
    evidence,
  });
}

function validateBarLine(
  input: UnknownRecord,
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
): StructuredVisualValidation {
  const issues: ValidationIssue[] = [];
  expectLiteral(input.chartType, "barLine", "chartType", issues);
  const title = requiredString(input.title, "title", issues);
  const categories = stringArray(input.categories, "categories", issues);
  const rawSeries = recordArray(input.series, "series", issues);
  const leftAxis = numericAxis(input.leftAxis, "leftAxis", issues);
  const rightAxis = numericAxis(input.rightAxis, "rightAxis", issues);

  if (categories.length === 0 || rawSeries.length === 0) {
    return empty(registration, evidence, issues, "CHART_HAS_NO_DATA", "图表没有分类或系列数据。");
  }

  const series: ComboSeries[] = [];
  rawSeries.forEach((entry, seriesIndex) => {
    const path = `series[${String(seriesIndex)}]`;
    const name = requiredString(entry.name, `${path}.name`, issues);
    const renderAs = literalUnion(entry.renderAs, ["bar", "line"], `${path}.renderAs`, issues);
    const axis = literalUnion(entry.axis, ["left", "right"], `${path}.axis`, issues);
    const unit = requiredString(entry.unit, `${path}.unit`, issues);
    const values = nullableNumberArray(entry.values, `${path}.values`, issues);
    if (values.length !== categories.length) {
      issues.push(
        issue(
          "error",
          "CATEGORY_VALUE_LENGTH_MISMATCH",
          `${path}.values`,
          `数值数量 ${String(values.length)} 与分类数量 ${String(categories.length)} 不一致。`,
        ),
      );
    }
    warnNullValues(values, `${path}.values`, issues);
    if (renderAs === "bar" && axis !== null && axis !== "left") {
      issues.push(issue("error", "BAR_AXIS_MISMATCH", `${path}.axis`, "柱系列必须使用 left 轴。"));
    }
    if (renderAs === "line" && axis !== null && axis !== "right") {
      issues.push(
        issue("error", "LINE_AXIS_MISMATCH", `${path}.axis`, "线系列必须使用 right 轴。"),
      );
    }
    if (name !== null && renderAs !== null && axis !== null && unit !== null) {
      series.push({ name, renderAs, axis, unit, values });
    }
  });

  if (!series.some((entry) => entry.renderAs === "bar")) {
    issues.push(issue("error", "BAR_SERIES_MISSING", "series", "柱线组合图缺少柱系列。"));
  }
  if (!series.some((entry) => entry.renderAs === "line")) {
    issues.push(issue("error", "LINE_SERIES_MISSING", "series", "柱线组合图缺少线系列。"));
  }
  if (leftAxis && rightAxis) {
    series.forEach((entry, seriesIndex) => {
      validateValuesInAxis(
        entry.values,
        entry.axis === "left" ? leftAxis : rightAxis,
        `series[${String(seriesIndex)}].values`,
        issues,
      );
    });
  }
  if (!hasNumericValues(series)) {
    return empty(registration, evidence, issues, "CHART_VALUES_EMPTY", "图表数值全部为空。");
  }
  if (hasErrors(issues) || title === null || leftAxis === null || rightAxis === null) {
    return invalid(registration, evidence, issues);
  }
  return valid(registration, evidence, issues, {
    schemaVersion: "dual-axis-combo-chart-v1",
    kind: "barLine",
    title,
    categories,
    series,
    leftAxis,
    rightAxis,
    evidence,
  });
}

function validatePie(
  input: UnknownRecord,
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
): StructuredVisualValidation {
  const issues: ValidationIssue[] = [];
  expectLiteral(input.chartType, "pie", "chartType", issues);
  expectLiteral(input.rotationConvention, "top_clockwise", "rotationConvention", issues);
  const labels = stringArray(input.labelsClockwise, "labelsClockwise", issues);
  const boundaries = numberArray(input.boundariesDegrees, "boundariesDegrees", issues);
  const rawSlices = recordArray(input.slices, "slices", issues);
  if (rawSlices.length === 0) {
    return empty(registration, evidence, issues, "PIE_HAS_NO_SLICES", "饼图没有扇区数据。");
  }

  const slices: PieSlice[] = [];
  rawSlices.forEach((entry, sliceIndex) => {
    const path = `slices[${String(sliceIndex)}]`;
    const label = requiredString(entry.label, `${path}.label`, issues);
    const startAngle = requiredNumber(entry.startAngle, `${path}.startAngle`, issues);
    const endAngle = requiredNumber(entry.endAngle, `${path}.endAngle`, issues);
    const sweepAngle = requiredNumber(entry.sweepAngle, `${path}.sweepAngle`, issues);
    const share = requiredNumber(entry.share, `${path}.share`, issues);
    if (
      label !== null &&
      startAngle !== null &&
      endAngle !== null &&
      sweepAngle !== null &&
      share !== null
    ) {
      if (sweepAngle <= 0 || share <= 0) {
        issues.push(issue("error", "PIE_SLICE_NOT_POSITIVE", path, "扇区角度与占比必须大于 0。"));
      }
      if (Math.abs(endAngle - startAngle - sweepAngle) > ANGLE_TOLERANCE) {
        issues.push(issue("error", "PIE_SWEEP_MISMATCH", path, "扇区起止角与 sweepAngle 不一致。"));
      }
      if (Math.abs(share - sweepAngle / 360) > SHARE_TOLERANCE) {
        issues.push(issue("error", "PIE_SHARE_MISMATCH", path, "扇区占比与角度不一致。"));
      }
      slices.push({ label, startAngle, endAngle, sweepAngle, share });
    }
  });

  if (labels.length !== slices.length || boundaries.length !== slices.length + 1) {
    issues.push(
      issue("error", "PIE_METADATA_LENGTH_MISMATCH", "slices", "标签、边界与扇区数量不一致。"),
    );
  }
  slices.forEach((slice, index) => {
    if (labels[index] !== slice.label) {
      issues.push(
        issue(
          "error",
          "PIE_LABEL_ORDER_MISMATCH",
          `labelsClockwise[${String(index)}]`,
          "顺时针标签顺序与扇区不一致。",
        ),
      );
    }
    if (
      !approximately(boundaries[index], slice.startAngle, ANGLE_TOLERANCE) ||
      !approximately(boundaries[index + 1], slice.endAngle, ANGLE_TOLERANCE)
    ) {
      issues.push(
        issue(
          "error",
          "PIE_BOUNDARY_MISMATCH",
          `boundariesDegrees[${String(index)}]`,
          "边界角与扇区起止角不一致。",
        ),
      );
    }
  });
  const angleTotal = slices.reduce((total, slice) => total + slice.sweepAngle, 0);
  const shareTotal = slices.reduce((total, slice) => total + slice.share, 0);
  if (Math.abs(angleTotal - 360) > ANGLE_TOLERANCE) {
    issues.push(
      issue(
        "error",
        "PIE_ANGLE_NOT_CLOSED",
        "slices",
        `扇区角度合计 ${formatNumber(angleTotal)}°，未闭合为 360°。`,
      ),
    );
  }
  if (Math.abs(shareTotal - 1) > SHARE_TOLERANCE) {
    issues.push(
      issue(
        "error",
        "PIE_SHARE_NOT_CLOSED",
        "slices",
        `扇区占比合计 ${formatNumber(shareTotal)}，未闭合为 1。`,
      ),
    );
  }
  if (
    !approximately(boundaries[0], 0, ANGLE_TOLERANCE) ||
    !approximately(boundaries.at(-1), 360, ANGLE_TOLERANCE)
  ) {
    issues.push(
      issue("error", "PIE_BOUNDARIES_NOT_CLOSED", "boundariesDegrees", "边界必须从 0° 到 360°。"),
    );
  }
  if (hasErrors(issues)) return invalid(registration, evidence, issues);
  return valid(registration, evidence, issues, {
    schemaVersion: "pie-chart-v1",
    kind: "pie",
    rotationConvention: "top_clockwise",
    labelsClockwise: labels,
    boundariesDegrees: boundaries,
    slices,
    evidence,
  });
}

function validateBinaryGrid(
  input: UnknownRecord,
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
): StructuredVisualValidation {
  const issues: ValidationIssue[] = [];
  expectLiteral(input.layoutType, "matrix_3x3", "layoutType", issues);
  expectLiteral(input.visualGrammar, "binary_grid", "visualGrammar", issues);
  const rawItems = recordArray(input.items, "items", issues);
  if (rawItems.length === 0) {
    return empty(registration, evidence, issues, "GRAPHIC_ITEMS_EMPTY", "图推面板为空。");
  }
  const items: BinaryGridPanel[] = [];
  rawItems.forEach((entry, itemIndex) => {
    const path = `items[${String(itemIndex)}]`;
    const id = requiredString(entry.id, `${path}.id`, issues);
    const role = literalUnion(entry.role, ["stem", "missing", "option"], `${path}.role`, issues);
    const location = graphicLocation(entry, role, path, issues);
    let occupancy: number[][] | null = null;
    if (role === "missing") {
      if (entry.occupancy !== null) {
        issues.push(
          issue(
            "error",
            "MISSING_PANEL_HAS_DATA",
            `${path}.occupancy`,
            "缺失面板的 occupancy 必须为 null。",
          ),
        );
      }
    } else {
      occupancy = binaryOccupancy(entry.occupancy, `${path}.occupancy`, issues);
    }
    if (id !== null && role !== null && location !== null) {
      items.push({ id, role, occupancy, ...location });
    }
  });
  validateMatrixInventory(items, issues);
  if (hasErrors(issues)) return invalid(registration, evidence, issues);
  return valid(registration, evidence, issues, {
    schemaVersion: "graphic-binary-grid-v1",
    kind: "binaryGrid",
    layoutType: "matrix_3x3",
    items,
    evidence,
  });
}

function validateTopologyPaths(
  input: UnknownRecord,
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
): StructuredVisualValidation {
  const issues: ValidationIssue[] = [];
  expectLiteral(input.layoutType, "matrix_3x3", "layoutType", issues);
  expectLiteral(input.visualGrammar, "line_art", "visualGrammar", issues);
  const rawItems = recordArray(input.items, "items", issues);
  if (rawItems.length === 0) {
    return empty(registration, evidence, issues, "GRAPHIC_ITEMS_EMPTY", "图推面板为空。");
  }
  const items: PathPanel[] = [];
  rawItems.forEach((entry, itemIndex) => {
    const path = `items[${String(itemIndex)}]`;
    const id = requiredString(entry.id, `${path}.id`, issues);
    const role = literalUnion(entry.role, ["stem", "missing", "option"], `${path}.role`, issues);
    const location = graphicLocation(entry, role, path, issues);
    const viewBox = viewBoxArray(entry.viewBox, `${path}.viewBox`, issues);
    const paths: ParsedGraphicPath[] = [];
    if (role === "missing") {
      if (entry.path !== null) {
        issues.push(
          issue("error", "MISSING_PANEL_HAS_PATH", `${path}.path`, "缺失面板的 path 必须为 null。"),
        );
      }
    } else {
      const parsed = parsedPath(entry.path, `${path}.path`, "evenodd", issues);
      if (parsed) paths.push(parsed);
    }
    if (id !== null && role !== null && location !== null && viewBox !== null) {
      items.push({ id, role, viewBox, paths, ...location });
    }
  });
  validateMatrixInventory(items, issues);
  if (hasErrors(issues)) return invalid(registration, evidence, issues);
  return valid(registration, evidence, issues, {
    schemaVersion: "graphic-line-topology-candidate-v1",
    kind: "pathPanels",
    layoutType: "matrix_3x3",
    items,
    hasTrailingMissingPanel: false,
    evidence,
  });
}

function validateLineArtPaths(
  input: UnknownRecord,
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
): StructuredVisualValidation {
  const issues: ValidationIssue[] = [];
  const canvas = isRecord(input.canvas) ? input.canvas : null;
  if (
    !canvas ||
    requiredNumber(canvas.width, "canvas.width", issues) === null ||
    requiredNumber(canvas.height, "canvas.height", issues) === null
  ) {
    issues.push(issue("error", "GRAPHIC_CANVAS_INVALID", "canvas", "原型 canvas 元数据不完整。"));
  }
  const rawItems = recordArray(input.items, "items", issues);
  if (rawItems.length === 0) {
    return empty(registration, evidence, issues, "GRAPHIC_ITEMS_EMPTY", "图推面板为空。");
  }
  const items: PathPanel[] = [];
  rawItems.forEach((entry, itemIndex) => {
    const path = `items[${String(itemIndex)}]`;
    const id = requiredString(entry.id, `${path}.id`, issues);
    const role = literalUnion(entry.role, ["sequence", "option"], `${path}.role`, issues);
    const position = integerInRange(
      entry.position,
      1,
      role === "option" ? 4 : 12,
      `${path}.position`,
      issues,
    );
    const viewBox = viewBoxString(entry.viewBox, `${path}.viewBox`, issues);
    const rawPaths = recordArray(entry.paths, `${path}.paths`, issues);
    const paths: ParsedGraphicPath[] = [];
    if (rawPaths.length === 0) {
      issues.push(
        issue("error", "GRAPHIC_PATHS_EMPTY", `${path}.paths`, "非空缺面板必须包含内部路径。"),
      );
    }
    rawPaths.forEach((pathEntry, pathIndex) => {
      const fillRule = pathEntry.fillRule === "evenodd" ? "evenodd" : "nonzero";
      const parsed = parsedPath(
        pathEntry.d,
        `${path}.paths[${String(pathIndex)}].d`,
        fillRule,
        issues,
      );
      if (parsed) paths.push(parsed);
    });
    if (id !== null && role !== null && position !== null && viewBox !== null) {
      items.push({ id, role, position, viewBox, paths });
    }
  });
  validateUniquePositions(items, "sequence", issues);
  validateUniquePositions(items, "option", issues);
  const decorations = isRecord(input.decorations) ? input.decorations : null;
  const hasTrailingMissingPanel = Boolean(decorations && isRecord(decorations.missingCell));
  if (!hasTrailingMissingPanel) {
    issues.push(
      issue(
        "error",
        "MISSING_CELL_DECORATION_ABSENT",
        "decorations.missingCell",
        "序列图推缺少明确的空缺面板证据。",
      ),
    );
  }
  if (hasErrors(issues)) return invalid(registration, evidence, issues);
  return valid(registration, evidence, issues, {
    schemaVersion: "graphic-line-art-v1",
    kind: "pathPanels",
    layoutType: "sequence",
    items,
    hasTrailingMissingPanel,
    evidence,
  });
}

function numericAxis(value: unknown, path: string, issues: ValidationIssue[]): NumericAxis | null {
  if (!isRecord(value)) {
    issues.push(issue("error", "AXIS_NOT_OBJECT", path, "坐标轴必须是对象。"));
    return null;
  }
  const min = requiredNumber(value.min, `${path}.min`, issues);
  const max = requiredNumber(value.max, `${path}.max`, issues);
  const unit = requiredString(value.unit, `${path}.unit`, issues);
  const ticks = numberArray(value.ticks, `${path}.ticks`, issues);
  if (min === null || max === null || unit === null) return null;
  if (min >= max)
    issues.push(issue("error", "AXIS_RANGE_INVALID", path, "坐标轴 min 必须小于 max。"));
  if (
    ticks.some((tick) => tick < min || tick > max) ||
    ticks.some((tick, index) => index > 0 && tick <= (ticks[index - 1] ?? tick))
  ) {
    issues.push(
      issue("error", "AXIS_TICKS_INVALID", `${path}.ticks`, "坐标轴刻度必须递增且位于范围内。"),
    );
  }
  return { min, max, unit, ticks };
}

function groupedAxis(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): (NumericAxis & { step: number }) | null {
  if (!isRecord(value)) {
    issues.push(issue("error", "AXIS_NOT_OBJECT", path, "坐标轴必须是对象。"));
    return null;
  }
  const min = requiredNumber(value.min, `${path}.min`, issues);
  const max = requiredNumber(value.max, `${path}.max`, issues);
  const step = requiredNumber(value.step, `${path}.step`, issues);
  if (min === null || max === null || step === null) return null;
  if (min >= max || step <= 0) {
    issues.push(issue("error", "AXIS_RANGE_INVALID", path, "坐标轴范围或步长无效。"));
  }
  const ticks: number[] = [];
  if (step > 0 && max > min && (max - min) / step <= 40) {
    for (let tick = min; tick <= max + step / 1000; tick += step) ticks.push(tick);
  } else {
    issues.push(issue("error", "AXIS_TICK_COUNT_INVALID", path, "坐标轴刻度数量无效。"));
  }
  return { min, max, step, ticks, unit: "" };
}

function groupedStyle(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): GroupedBarSeries["style"] | null {
  if (!isRecord(value)) {
    issues.push(issue("error", "STYLE_NOT_OBJECT", path, "系列样式必须是对象。"));
    return null;
  }
  const fill = safeColor(value.fill, `${path}.fill`, issues);
  const stroke = safeColor(value.stroke, `${path}.stroke`, issues);
  let dash: number[] = [];
  if (value.dash !== null) {
    if (typeof value.dash !== "string") {
      issues.push(
        issue("error", "DASH_INVALID", `${path}.dash`, "虚线样式必须是数字字符串或 null。"),
      );
    } else {
      dash = value.dash.trim().split(/\s+/u).map(Number);
      if (dash.length === 0 || dash.some((part) => !Number.isFinite(part) || part <= 0)) {
        issues.push(issue("error", "DASH_INVALID", `${path}.dash`, "虚线样式包含无效长度。"));
      }
    }
  }
  return fill === null || stroke === null ? null : { fill, stroke, dash };
}

function parsedPath(
  value: unknown,
  path: string,
  fillRule: "nonzero" | "evenodd",
  issues: ValidationIssue[],
): ParsedGraphicPath | null {
  if (typeof value !== "string") {
    issues.push(issue("error", "PATH_NOT_STRING", path, "SVG path 必须是字符串。"));
    return null;
  }
  const parsed = parseLinearSvgPath(value);
  if (!parsed.ok) {
    issues.push(issue("error", "PATH_UNSUPPORTED", path, parsed.message));
    return null;
  }
  return { instructions: parsed.instructions, fillRule };
}

function viewBoxArray(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): [number, number, number, number] | null {
  const numbers = numberArray(value, path, issues);
  if (numbers.length !== 4 || (numbers[2] ?? 0) <= 0 || (numbers[3] ?? 0) <= 0) {
    issues.push(issue("error", "VIEWBOX_INVALID", path, "viewBox 必须包含 4 个数且宽高为正。"));
    return null;
  }
  return [numbers[0] ?? 0, numbers[1] ?? 0, numbers[2] ?? 0, numbers[3] ?? 0];
}

function viewBoxString(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): [number, number, number, number] | null {
  if (typeof value !== "string") {
    issues.push(issue("error", "VIEWBOX_INVALID", path, "viewBox 必须是数字字符串。"));
    return null;
  }
  const numbers = value.trim().split(/\s+/u).map(Number);
  if (numbers.length !== 4 || numbers.some((number) => !Number.isFinite(number))) {
    issues.push(issue("error", "VIEWBOX_INVALID", path, "viewBox 必须包含 4 个数。"));
    return null;
  }
  return viewBoxArray(numbers, path, issues);
}

function binaryOccupancy(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): number[][] | null {
  if (!Array.isArray(value) || value.length !== 9) {
    issues.push(issue("error", "BINARY_GRID_NOT_9X9", path, "occupancy 必须是 9×9 数组。"));
    return null;
  }
  const rows: number[][] = [];
  value.forEach((row, rowIndex) => {
    if (!Array.isArray(row) || row.length !== 9 || row.some((cell) => cell !== 0 && cell !== 1)) {
      issues.push(
        issue(
          "error",
          "BINARY_GRID_CELL_INVALID",
          `${path}[${String(rowIndex)}]`,
          "每行必须恰好包含 9 个二值单元。",
        ),
      );
      return;
    }
    rows.push(row as number[]);
  });
  return rows.length === 9 ? rows : null;
}

function graphicLocation(
  entry: UnknownRecord,
  role: "stem" | "missing" | "option" | null,
  path: string,
  issues: ValidationIssue[],
): GraphicPanelLocation | null {
  if (role === "option") {
    const position = integerInRange(entry.position, 1, 4, `${path}.position`, issues);
    return position === null ? null : { position };
  }
  if (role === "stem" || role === "missing") {
    const row = integerInRange(entry.row, 1, 3, `${path}.row`, issues);
    const column = integerInRange(entry.column, 1, 3, `${path}.column`, issues);
    return row === null || column === null ? null : { row, column };
  }
  return null;
}

function validateMatrixInventory(
  items: Array<BinaryGridPanel | PathPanel>,
  issues: ValidationIssue[],
): void {
  const cells = new Set<string>();
  const options = new Set<number>();
  const ids = new Set(items.map((item) => item.id));
  let missingCount = 0;
  items.forEach((item) => {
    if (item.role === "option") {
      if (item.position !== undefined) options.add(item.position);
      return;
    }
    if (item.role === "missing") missingCount += 1;
    if (item.row !== undefined && item.column !== undefined) {
      cells.add(`${String(item.row)}:${String(item.column)}`);
    }
  });
  if (cells.size !== 9 || options.size !== 4 || missingCount !== 1) {
    issues.push(
      issue(
        "error",
        "MATRIX_INVENTORY_INVALID",
        "items",
        "3×3 题干必须有 9 个唯一面板（含 1 个空缺）和 4 个唯一选项。",
      ),
    );
  }
  if (ids.size !== items.length) {
    issues.push(issue("error", "GRAPHIC_ID_DUPLICATE", "items", "图推面板 id 必须唯一。"));
  }
}

function validateUniquePositions(
  items: PathPanel[],
  role: "sequence" | "option",
  issues: ValidationIssue[],
): void {
  const matching = items.filter((item) => item.role === role);
  const positions = new Set(matching.map((item) => item.position));
  if (matching.length === 0) {
    issues.push(issue("error", "GRAPHIC_GROUP_EMPTY", "items", `${role} 面板不能为空。`));
  }
  if (positions.size !== matching.length) {
    issues.push(issue("error", "GRAPHIC_POSITION_DUPLICATE", "items", `${role} 面板位置重复。`));
  }
  if (role === "option" && positions.size !== 4) {
    issues.push(
      issue("error", "GRAPHIC_OPTIONS_INCOMPLETE", "items", "图推必须包含 4 个唯一选项。"),
    );
  }
}

function validateValuesInAxis(
  values: Array<number | null>,
  axis: Pick<NumericAxis, "min" | "max">,
  path: string,
  issues: ValidationIssue[],
): void {
  values.forEach((value, index) => {
    if (value !== null && (value < axis.min || value > axis.max)) {
      issues.push(
        issue(
          "error",
          "VALUE_OUTSIDE_AXIS",
          `${path}[${String(index)}]`,
          `数值 ${formatNumber(value)} 超出坐标轴范围，已拒绝裁切。`,
        ),
      );
    }
  });
}

function collectEvidence(input: unknown): Record<string, unknown> {
  if (!isRecord(input)) return {};
  const evidence: Record<string, unknown> = {};
  for (const key of [
    "schemaVersion",
    "sourceQuestionId",
    "sourceAsset",
    "verification",
    "extraction",
    "validation",
  ]) {
    if (key in input) evidence[key] = input[key];
  }
  return evidence;
}

function requiredString(value: unknown, path: string, issues: ValidationIssue[]): string | null {
  if (typeof value !== "string" || value.trim().length === 0) {
    issues.push(issue("error", "STRING_REQUIRED", path, "必须提供非空字符串。"));
    return null;
  }
  return value;
}

function requiredNumber(value: unknown, path: string, issues: ValidationIssue[]): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    issues.push(issue("error", "FINITE_NUMBER_REQUIRED", path, "必须提供有限数值。"));
    return null;
  }
  return value;
}

function safeColor(value: unknown, path: string, issues: ValidationIssue[]): string | null {
  if (typeof value !== "string" || !colorPattern.test(value)) {
    issues.push(issue("error", "COLOR_INVALID", path, "颜色必须是安全的十六进制或 rgb(a) 值。"));
    return null;
  }
  return value;
}

function nullableNumberArray(
  value: unknown,
  path: string,
  issues: ValidationIssue[],
): Array<number | null> {
  if (!Array.isArray(value)) {
    issues.push(issue("error", "NUMBER_ARRAY_REQUIRED", path, "必须提供数值数组。"));
    return [];
  }
  return value.map((entry, index) => {
    if (entry === null) return null;
    if (typeof entry !== "number" || !Number.isFinite(entry)) {
      issues.push(
        issue(
          "error",
          "NUMBER_OR_NULL_REQUIRED",
          `${path}[${String(index)}]`,
          "数值只能是有限数字或显式 null。",
        ),
      );
      return null;
    }
    return entry;
  });
}

function numberArray(value: unknown, path: string, issues: ValidationIssue[]): number[] {
  if (!Array.isArray(value)) {
    issues.push(issue("error", "NUMBER_ARRAY_REQUIRED", path, "必须提供数值数组。"));
    return [];
  }
  const numbers: number[] = [];
  value.forEach((entry, index) => {
    const number = requiredNumber(entry, `${path}[${String(index)}]`, issues);
    if (number !== null) numbers.push(number);
  });
  return numbers;
}

function stringArray(value: unknown, path: string, issues: ValidationIssue[]): string[] {
  if (!Array.isArray(value)) {
    issues.push(issue("error", "STRING_ARRAY_REQUIRED", path, "必须提供字符串数组。"));
    return [];
  }
  const strings: string[] = [];
  value.forEach((entry, index) => {
    const string = requiredString(entry, `${path}[${String(index)}]`, issues);
    if (string !== null) strings.push(string);
  });
  return strings;
}

function recordArray(value: unknown, path: string, issues: ValidationIssue[]): UnknownRecord[] {
  if (!Array.isArray(value)) {
    issues.push(issue("error", "OBJECT_ARRAY_REQUIRED", path, "必须提供对象数组。"));
    return [];
  }
  const records: UnknownRecord[] = [];
  value.forEach((entry, index) => {
    if (!isRecord(entry)) {
      issues.push(
        issue("error", "OBJECT_REQUIRED", `${path}[${String(index)}]`, "数组项必须是对象。"),
      );
    } else {
      records.push(entry);
    }
  });
  return records;
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
      issue(
        "error",
        "INTEGER_OUT_OF_RANGE",
        path,
        `必须是 ${String(min)} 到 ${String(max)} 的整数。`,
      ),
    );
    return null;
  }
  return value;
}

function literalUnion<const T extends readonly string[]>(
  value: unknown,
  options: T,
  path: string,
  issues: ValidationIssue[],
): T[number] | null {
  if (typeof value !== "string" || !options.includes(value)) {
    issues.push(issue("error", "LITERAL_INVALID", path, `必须是 ${options.join(" / ")} 之一。`));
    return null;
  }
  return value;
}

function expectLiteral(
  value: unknown,
  expected: string,
  path: string,
  issues: ValidationIssue[],
): void {
  if (value !== expected) {
    issues.push(issue("error", "DISCRIMINATOR_MISMATCH", path, `必须为 ${expected}。`));
  }
}

function warnNullValues(
  values: Array<number | null>,
  path: string,
  issues: ValidationIssue[],
): void {
  const count = values.filter((value) => value === null).length;
  if (count > 0) {
    issues.push(
      issue(
        "warning",
        "NULL_VALUES_PRESERVED",
        path,
        `${String(count)} 个缺失值保持为 null，未按 0 绘制。`,
      ),
    );
  }
}

function hasNumericValues(series: Array<{ values: Array<number | null> }>): boolean {
  return series.some((entry) => entry.values.some((value) => value !== null));
}

function approximately(left: number | undefined, right: number, tolerance: number): boolean {
  return left !== undefined && Math.abs(left - right) <= tolerance;
}

function formatNumber(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(4).replace(/0+$/u, "").replace(/\.$/u, "");
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasErrors(issues: ValidationIssue[]): boolean {
  return issues.some((entry) => entry.severity === "error");
}

function issue(
  severity: ValidationIssueSeverity,
  code: string,
  path: string,
  message: string,
): ValidationIssue {
  return { severity, code, path, message };
}

function valid(
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
  issues: ValidationIssue[],
  visual: ValidatedStructuredVisual,
): StructuredVisualValidation {
  return { status: "valid", registration, evidence, issues, visual };
}

function empty(
  registration: StructuredVisualRegistration,
  evidence: Record<string, unknown>,
  issues: ValidationIssue[],
  code: string,
  message: string,
): StructuredVisualValidation {
  if (hasErrors(issues)) return invalid(registration, evidence, issues);
  return {
    status: "empty",
    registration,
    evidence,
    issues: [...issues, issue("warning", code, "$", message)],
  };
}

function invalid(
  registration: StructuredVisualRegistration | null,
  evidence: Record<string, unknown>,
  issues: ValidationIssue[],
): StructuredVisualValidation {
  const normalizedIssues = hasErrors(issues)
    ? issues
    : [...issues, issue("error", "INVALID_INPUT", "$", "结构化视觉输入无效。")];
  return { status: "invalid", registration, evidence, issues: normalizedIssues };
}

export function schemaKindOf(visual: ValidatedStructuredVisual): StructuredVisualKind {
  return visual.kind;
}

export function schemaVersionOf(visual: ValidatedStructuredVisual): StructuredVisualSchemaVersion {
  return visual.schemaVersion;
}
