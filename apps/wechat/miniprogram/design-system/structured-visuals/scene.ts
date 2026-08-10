import type { PathInstruction } from "./path";
import type {
  BarLineVisual,
  BinaryGridPanel,
  BinaryGridVisual,
  GroupedBarVisual,
  PathPanel,
  PathPanelsVisual,
  PieVisual,
  ValidatedStructuredVisual,
} from "./schema";
import type { StructuredVisualKind } from "./registry";

export interface Point {
  x: number;
  y: number;
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface StrokeStyle {
  stroke: string;
  lineWidth: number;
  dash?: number[];
}

export type DrawCommand =
  | ({ type: "line"; from: Point; to: Point } & StrokeStyle)
  | ({ type: "polyline"; points: Point[]; closed: boolean; fill?: string } & StrokeStyle)
  | ({ type: "rect"; box: Box; fill?: string } & StrokeStyle)
  | ({ type: "circle"; center: Point; radius: number; fill?: string } & StrokeStyle)
  | ({
      type: "sector";
      center: Point;
      radius: number;
      startAngle: number;
      endAngle: number;
      fill: string;
    } & StrokeStyle)
  | {
      type: "text";
      text: string;
      at: Point;
      color: string;
      fontSize: number;
      fontWeight: 400 | 500 | 600 | 700;
      fontFamily: "ui" | "data";
      align: "left" | "center" | "right";
      baseline: "top" | "middle" | "bottom";
    }
  | {
      type: "path";
      instructions: PathInstruction[];
      fill: string;
      fillRule: "nonzero" | "evenodd";
    };

export interface SceneLabelBox extends Box {
  id: string;
  role: "value" | "slice" | "category";
}

export interface VisualScene {
  width: number;
  height: number;
  commands: DrawCommand[];
  labelBoxes: SceneLabelBox[];
  missingValueCount: number;
}

export interface LabelCandidate {
  id: string;
  text: string;
  x: number;
  preferredY: number;
  fontSize: number;
  priority: number;
  role: SceneLabelBox["role"];
}

export interface ResolvedLabel extends LabelCandidate {
  visible: boolean;
  y: number;
  box: Box;
}

const theme = {
  ink: "#101828",
  inkSecondary: "#364153",
  muted: "#6a7282",
  faint: "#99a1af",
  line: "#f3f4f6",
  lineStrong: "#d1d5dc",
  primary: "#00b8db",
  primaryDark: "#0092b8",
  primarySoft: "#ecfeff",
  warning: "#ff6900",
  surface: "#ffffff",
  surfaceSoft: "#f9fafb",
} as const;

const comboPalette = [theme.primaryDark, "#7c3aed", "#364153", "#d97706"] as const;
const piePalette = ["#ecfeff", "#cefafe", "#e0e7ff", "#f3e8ff", "#fff7ed", "#f3f4f6"];

export function preferredCanvasHeight(kind: StructuredVisualKind, width: number): number {
  const safeWidth = Math.max(240, width);
  switch (kind) {
    case "groupedBar":
      return Math.round(clamp(safeWidth * 0.72, 250, 350));
    case "barLine":
      return Math.round(clamp(safeWidth * 0.78, 270, 370));
    case "pie":
      return Math.round(clamp(safeWidth * 0.78, 250, 360));
    case "binaryGrid":
    case "pathPanels":
      return safeWidth >= 600 ? 520 : Math.round(clamp(safeWidth * 1.5, 430, 540));
  }
}

export function buildStructuredVisualScene(
  visual: ValidatedStructuredVisual,
  width: number,
  height = preferredCanvasHeight(visual.kind, width),
): VisualScene {
  const safeWidth = Math.max(240, width);
  const safeHeight = Math.max(220, height);
  const builders: Record<
    StructuredVisualKind,
    (value: ValidatedStructuredVisual, sceneWidth: number, sceneHeight: number) => VisualScene
  > = {
    groupedBar: (value, sceneWidth, sceneHeight) =>
      buildGroupedBarScene(value as GroupedBarVisual, sceneWidth, sceneHeight),
    barLine: (value, sceneWidth, sceneHeight) =>
      buildBarLineScene(value as BarLineVisual, sceneWidth, sceneHeight),
    pie: (value, sceneWidth, sceneHeight) =>
      buildPieScene(value as PieVisual, sceneWidth, sceneHeight),
    binaryGrid: (value, sceneWidth, sceneHeight) =>
      buildBinaryGridScene(value as BinaryGridVisual, sceneWidth, sceneHeight),
    pathPanels: (value, sceneWidth, sceneHeight) =>
      buildPathPanelsScene(value as PathPanelsVisual, sceneWidth, sceneHeight),
  };
  return builders[visual.kind](visual, safeWidth, safeHeight);
}

export function resolveLabelCollisions(
  candidates: LabelCandidate[],
  bounds: Box,
  gap = 2,
): ResolvedLabel[] {
  const placed: Box[] = [];
  const verticalOffsets = [0, -12, 12, -24, 24, -36, 36, -48, 48];
  const sorted = [...candidates].sort(
    (left, right) =>
      right.priority - left.priority || left.preferredY - right.preferredY || left.x - right.x,
  );
  const resolved = new Map<string, ResolvedLabel>();
  sorted.forEach((candidate) => {
    const estimatedWidth = Math.max(
      candidate.fontSize,
      candidate.text.length * candidate.fontSize * 0.58,
    );
    const estimatedHeight = candidate.fontSize + 3;
    let placement: Box | null = null;
    for (const offset of verticalOffsets) {
      const x = clamp(
        candidate.x - estimatedWidth / 2,
        bounds.x,
        bounds.x + bounds.width - estimatedWidth,
      );
      const y = clamp(
        candidate.preferredY + offset - estimatedHeight / 2,
        bounds.y,
        bounds.y + bounds.height - estimatedHeight,
      );
      const box = { x, y, width: estimatedWidth, height: estimatedHeight };
      if (!placed.some((other) => boxesOverlap(box, other, gap))) {
        placement = box;
        break;
      }
    }
    if (placement) placed.push(placement);
    const box = placement ?? {
      x: candidate.x,
      y: candidate.preferredY,
      width: 0,
      height: 0,
    };
    resolved.set(candidate.id, {
      ...candidate,
      visible: placement !== null,
      y: placement ? placement.y + placement.height / 2 : candidate.preferredY,
      box,
    });
  });
  return candidates.map((candidate) => resolved.get(candidate.id) ?? hiddenLabel(candidate));
}

export function boxesOverlap(left: Box, right: Box, gap = 0): boolean {
  return !(
    left.x + left.width + gap <= right.x ||
    right.x + right.width + gap <= left.x ||
    left.y + left.height + gap <= right.y ||
    right.y + right.height + gap <= left.y
  );
}

function buildGroupedBarScene(
  visual: GroupedBarVisual,
  width: number,
  height: number,
): VisualScene {
  const commands: DrawCommand[] = [];
  const labels: LabelCandidate[] = [];
  const categoryLabels: LabelCandidate[] = [];
  const left = width < 420 ? 42 : 54;
  const right = 10;
  const top = 20;
  const legendHeight = visual.series.length > 2 ? 48 : 28;
  const bottom = 34 + legendHeight;
  const plot = { x: left, y: top, width: width - left - right, height: height - top - bottom };
  drawAxes(commands, plot, visual.axis.ticks, visual.axis.min, visual.axis.max, "left");
  commands.push(
    textCommand(`单位：${visual.unit}`, 4, 2, 10, theme.muted, "left", "top", "ui", 500),
  );

  const categoryStep = plot.width / visual.categories.length;
  const groupWidth = categoryStep * 0.76;
  const barGap = 2;
  const barWidth = Math.max(
    2,
    Math.min(20, (groupWidth - barGap * (visual.series.length - 1)) / visual.series.length),
  );
  const actualGroupWidth = visual.series.length * barWidth + (visual.series.length - 1) * barGap;
  let missingValueCount = 0;
  visual.categories.forEach((category, categoryIndex) => {
    const centerX = plot.x + categoryStep * (categoryIndex + 0.5);
    const startX = centerX - actualGroupWidth / 2;
    visual.series.forEach((series, seriesIndex) => {
      const value = series.values[categoryIndex] ?? null;
      const barCenterX = startX + seriesIndex * (barWidth + barGap) + barWidth / 2;
      if (value === null) {
        missingValueCount += 1;
        commands.push({
          type: "line",
          from: { x: barCenterX - 3, y: plot.y + plot.height - 6 },
          to: { x: barCenterX + 3, y: plot.y + plot.height },
          stroke: theme.warning,
          lineWidth: 1.4,
        });
        labels.push({
          id: `missing-${String(categoryIndex)}-${String(seriesIndex)}`,
          text: "缺",
          x: barCenterX,
          preferredY: plot.y + plot.height - 13,
          fontSize: 9,
          priority: 20,
          role: "value",
        });
        return;
      }
      const y = axisY(value, visual.axis.min, visual.axis.max, plot);
      commands.push({
        type: "rect",
        box: { x: barCenterX - barWidth / 2, y, width: barWidth, height: plot.y + plot.height - y },
        fill: series.style.fill,
        stroke: series.style.stroke,
        lineWidth: 1,
        dash: series.style.dash,
      });
      labels.push({
        id: `bar-${String(categoryIndex)}-${String(seriesIndex)}`,
        text: formatDataValue(value),
        x: barCenterX,
        preferredY: y - 8,
        fontSize: width < 420 ? 9 : 10,
        priority: Math.abs(value),
        role: "value",
      });
    });
    categoryLabels.push({
      id: `category-${String(categoryIndex)}`,
      text: ellipsize(category, width < 420 ? 5 : 8),
      x: centerX,
      preferredY: plot.y + plot.height + 16,
      fontSize: 10,
      priority: visual.categories.length - categoryIndex,
      role: "category",
    });
  });

  const resolved = resolveLabelCollisions(labels, {
    x: plot.x,
    y: plot.y,
    width: plot.width,
    height: plot.height - 4,
  });
  appendResolvedLabels(commands, resolved);
  const resolvedCategories = resolveLabelCollisions(categoryLabels, {
    x: plot.x,
    y: plot.y + plot.height + 4,
    width: plot.width,
    height: 27,
  });
  appendResolvedLabels(commands, resolvedCategories, theme.inkSecondary, 500, "ui");
  drawLegend(
    commands,
    visual.series.map((series) => ({
      label: series.label,
      fill: series.style.fill,
      stroke: series.style.stroke,
      dash: series.style.dash,
      line: false,
    })),
    { x: 8, y: height - legendHeight + 2, width: width - 16, height: legendHeight - 2 },
  );
  return scene(width, height, commands, [...resolved, ...resolvedCategories], missingValueCount);
}

function buildBarLineScene(visual: BarLineVisual, width: number, height: number): VisualScene {
  const commands: DrawCommand[] = [];
  const labels: LabelCandidate[] = [];
  const categoryLabels: LabelCandidate[] = [];
  const left = width < 420 ? 42 : 54;
  const right = width < 420 ? 38 : 48;
  const top = 20;
  const legendHeight = 46;
  const bottom = 34 + legendHeight;
  const plot = { x: left, y: top, width: width - left - right, height: height - top - bottom };
  drawAxes(commands, plot, visual.leftAxis.ticks, visual.leftAxis.min, visual.leftAxis.max, "left");
  drawRightAxis(commands, plot, visual.rightAxis);
  commands.push(textCommand(visual.leftAxis.unit, 4, 2, 10, theme.muted, "left", "top", "ui", 500));
  commands.push(
    textCommand(visual.rightAxis.unit, width - 4, 2, 10, theme.muted, "right", "top", "ui", 500),
  );

  const categoryStep = plot.width / visual.categories.length;
  const barSeries = visual.series.filter((entry) => entry.renderAs === "bar");
  const lineSeries = visual.series.filter((entry) => entry.renderAs === "line");
  const groupWidth = categoryStep * 0.56;
  const barWidth = Math.max(3, Math.min(22, groupWidth / barSeries.length - 2));
  let missingValueCount = 0;

  visual.categories.forEach((category, categoryIndex) => {
    const centerX = plot.x + categoryStep * (categoryIndex + 0.5);
    categoryLabels.push({
      id: `category-${String(categoryIndex)}`,
      text: ellipsize(category, 4),
      x: centerX,
      preferredY: plot.y + plot.height + 16,
      fontSize: 10,
      priority: visual.categories.length - categoryIndex,
      role: "category",
    });
    barSeries.forEach((series, seriesIndex) => {
      const value = series.values[categoryIndex] ?? null;
      const offset = (seriesIndex - (barSeries.length - 1) / 2) * (barWidth + 2);
      const x = centerX + offset;
      if (value === null) {
        missingValueCount += 1;
        labels.push({
          id: `combo-bar-missing-${String(categoryIndex)}-${String(seriesIndex)}`,
          text: "缺",
          x,
          preferredY: plot.y + plot.height - 12,
          fontSize: 9,
          priority: 20,
          role: "value",
        });
        return;
      }
      const y = axisY(value, visual.leftAxis.min, visual.leftAxis.max, plot);
      commands.push({
        type: "rect",
        box: { x: x - barWidth / 2, y, width: barWidth, height: plot.y + plot.height - y },
        fill: comboPalette[seriesIndex % comboPalette.length] ?? theme.primaryDark,
        stroke: theme.surface,
        lineWidth: 0.5,
      });
      labels.push({
        id: `combo-bar-${String(categoryIndex)}-${String(seriesIndex)}`,
        text: formatDataValue(value),
        x,
        preferredY: y - 8,
        fontSize: 9,
        priority: Math.abs(value),
        role: "value",
      });
    });
  });

  lineSeries.forEach((series, seriesIndex) => {
    const color = comboPalette[(barSeries.length + seriesIndex) % comboPalette.length] ?? "#7c3aed";
    let segment: Point[] = [];
    const flush = () => {
      if (segment.length >= 2) {
        commands.push({
          type: "polyline",
          points: segment,
          closed: false,
          stroke: color,
          lineWidth: 2.2,
        });
      }
      segment = [];
    };
    series.values.forEach((value, categoryIndex) => {
      const x = plot.x + categoryStep * (categoryIndex + 0.5);
      if (value === null) {
        missingValueCount += 1;
        flush();
        labels.push({
          id: `combo-line-missing-${String(categoryIndex)}-${String(seriesIndex)}`,
          text: "缺",
          x,
          preferredY: plot.y + plot.height - 12,
          fontSize: 9,
          priority: 21,
          role: "value",
        });
        return;
      }
      const y = axisY(value, visual.rightAxis.min, visual.rightAxis.max, plot);
      segment.push({ x, y });
      commands.push({
        type: "circle",
        center: { x, y },
        radius: 3.2,
        fill: theme.surface,
        stroke: color,
        lineWidth: 2,
      });
      labels.push({
        id: `combo-line-${String(categoryIndex)}-${String(seriesIndex)}`,
        text: formatDataValue(value),
        x,
        preferredY: y - 10,
        fontSize: 10,
        priority: 100_000 + Math.abs(value),
        role: "value",
      });
    });
    flush();
  });

  const resolved = resolveLabelCollisions(labels, {
    x: plot.x,
    y: plot.y,
    width: plot.width,
    height: plot.height - 4,
  });
  appendResolvedLabels(commands, resolved);
  const resolvedCategories = resolveLabelCollisions(categoryLabels, {
    x: plot.x,
    y: plot.y + plot.height + 4,
    width: plot.width,
    height: 27,
  });
  appendResolvedLabels(commands, resolvedCategories, theme.inkSecondary, 500, "ui");
  drawLegend(
    commands,
    visual.series.map((series, index) => ({
      label: series.name,
      fill:
        series.renderAs === "bar"
          ? (comboPalette[index % comboPalette.length] ?? theme.primaryDark)
          : theme.surface,
      stroke: comboPalette[index % comboPalette.length] ?? theme.primaryDark,
      line: series.renderAs === "line",
    })),
    { x: 8, y: height - legendHeight + 4, width: width - 16, height: legendHeight - 4 },
  );
  return scene(width, height, commands, [...resolved, ...resolvedCategories], missingValueCount);
}

function buildPieScene(visual: PieVisual, width: number, height: number): VisualScene {
  const commands: DrawCommand[] = [];
  const labels: LabelCandidate[] = [];
  const radius = Math.min(width * 0.34, height * 0.4, 138);
  const center = { x: width / 2, y: height / 2 };
  visual.slices.forEach((slice, index) => {
    commands.push({
      type: "sector",
      center,
      radius,
      startAngle: slice.startAngle,
      endAngle: slice.endAngle,
      fill: piePalette[index % piePalette.length] ?? theme.surfaceSoft,
      stroke: theme.ink,
      lineWidth: 1.5,
    });
    const middle = slice.startAngle + slice.sweepAngle / 2;
    const labelRadius = radius * (slice.sweepAngle < 30 ? 0.78 : 0.58);
    const point = polarPoint(center, labelRadius, middle);
    labels.push({
      id: `slice-${String(index)}`,
      text: slice.label,
      x: point.x,
      preferredY: point.y,
      fontSize: width < 420 ? 14 : 16,
      priority: slice.sweepAngle,
      role: "slice",
    });
  });
  const resolved = resolveLabelCollisions(labels, {
    x: center.x - radius + 6,
    y: center.y - radius + 6,
    width: radius * 2 - 12,
    height: radius * 2 - 12,
  });
  appendResolvedLabels(commands, resolved, theme.ink, 600);
  return scene(width, height, commands, resolved, 0);
}

function buildBinaryGridScene(
  visual: BinaryGridVisual,
  width: number,
  height: number,
): VisualScene {
  const commands: DrawCommand[] = [];
  for (const placement of graphicPlacements(visual.items, "matrix_3x3", width, height, false)) {
    drawPanelFrame(commands, placement.box, placement.item.role === "missing");
    if (placement.item.occupancy) {
      drawBinaryGrid(commands, placement.box, placement.item);
    }
    if (placement.item.role === "missing") drawMissingPanel(commands, placement.box);
    if (placement.item.role === "option")
      drawOptionLabel(commands, placement.box, placement.item.id);
  }
  return { width, height, commands, labelBoxes: [], missingValueCount: 0 };
}

function buildPathPanelsScene(
  visual: PathPanelsVisual,
  width: number,
  height: number,
): VisualScene {
  const commands: DrawCommand[] = [];
  const items = visual.hasTrailingMissingPanel
    ? [
        ...visual.items,
        {
          id: "missing",
          role: "missing" as const,
          position: visual.items.filter((item) => item.role === "sequence").length + 1,
          viewBox: [0, 0, 1, 1] as [number, number, number, number],
          paths: [],
        },
      ]
    : visual.items;
  for (const placement of graphicPlacements(
    items,
    visual.layoutType,
    width,
    height,
    visual.layoutType === "sequence",
  )) {
    drawPanelFrame(commands, placement.box, placement.item.role === "missing");
    if (placement.item.role === "missing") {
      drawMissingPanel(commands, placement.box);
    } else {
      drawPathPanel(commands, placement.box, placement.item);
    }
    if (placement.item.role === "option")
      drawOptionLabel(commands, placement.box, placement.item.id);
  }
  return { width, height, commands, labelBoxes: [], missingValueCount: 0 };
}

interface GraphicPlacement<T> {
  item: T;
  box: Box;
}

function graphicPlacements<T extends { role: string } & GraphicLocation>(
  items: T[],
  layoutType: "matrix_3x3" | "sequence",
  width: number,
  height: number,
  sequenceLayout: boolean,
): Array<GraphicPlacement<T>> {
  const margin = 10;
  const gap = width >= 600 ? 12 : 8;
  const sectionGap = 20;
  const optionLabelHeight = 18;
  const optionColumns = width >= 600 ? 4 : 2;
  const options = items
    .filter((item) => item.role === "option")
    .sort((left, right) => (left.position ?? 0) - (right.position ?? 0));
  const stems = items
    .filter((item) => item.role !== "option")
    .sort((left, right) => {
      if (sequenceLayout) return (left.position ?? 0) - (right.position ?? 0);
      return (
        (left.row ?? 0) * 10 + (left.column ?? 0) - ((right.row ?? 0) * 10 + (right.column ?? 0))
      );
    });
  const stemColumns = layoutType === "sequence" ? (width >= 600 ? 6 : 3) : 3;
  const stemRows = Math.ceil(stems.length / stemColumns);
  const optionRows = Math.ceil(options.length / optionColumns);
  const verticalGaps = Math.max(0, stemRows - 1) + Math.max(0, optionRows - 1);
  const totalRows = stemRows + optionRows;
  const sizeByWidth = Math.min(
    (width - margin * 2 - gap * (stemColumns - 1)) / stemColumns,
    (width - margin * 2 - gap * (optionColumns - 1)) / optionColumns,
  );
  const sizeByHeight =
    (height - margin * 2 - sectionGap - verticalGaps * gap - optionRows * optionLabelHeight) /
    Math.max(1, totalRows);
  const panelSize = Math.max(54, Math.min(sizeByWidth, sizeByHeight, width >= 600 ? 118 : 102));
  const stemHeight = stemRows * panelSize + Math.max(0, stemRows - 1) * gap;
  const optionTop = margin + stemHeight + sectionGap;
  return [
    ...gridPlacements(stems, stemColumns, panelSize, gap, margin, width),
    ...gridPlacements(options, optionColumns, panelSize, gap, optionTop, width),
  ];
}

interface GraphicLocation {
  row?: number;
  column?: number;
  position?: number;
}

function gridPlacements<T>(
  items: T[],
  columns: number,
  size: number,
  gap: number,
  top: number,
  width: number,
): Array<GraphicPlacement<T>> {
  const gridWidth = columns * size + (columns - 1) * gap;
  const left = (width - gridWidth) / 2;
  return items.map((item, index) => ({
    item,
    box: {
      x: left + (index % columns) * (size + gap),
      y: top + Math.floor(index / columns) * (size + gap + (top > 10 ? 18 : 0)),
      width: size,
      height: size,
    },
  }));
}

function drawBinaryGrid(commands: DrawCommand[], panel: Box, item: BinaryGridPanel): void {
  if (!item.occupancy) return;
  const inset = Math.max(7, panel.width * 0.11);
  const box = {
    x: panel.x + inset,
    y: panel.y + inset,
    width: panel.width - inset * 2,
    height: panel.height - inset * 2,
  };
  const cell = box.width / 9;
  for (let index = 0; index <= 9; index += 1) {
    const offset = index * cell;
    commands.push({
      type: "line",
      from: { x: box.x + offset, y: box.y },
      to: { x: box.x + offset, y: box.y + box.height },
      stroke: theme.lineStrong,
      lineWidth: 0.6,
    });
    commands.push({
      type: "line",
      from: { x: box.x, y: box.y + offset },
      to: { x: box.x + box.width, y: box.y + offset },
      stroke: theme.lineStrong,
      lineWidth: 0.6,
    });
  }
  item.occupancy.forEach((row, rowIndex) => {
    row.forEach((occupied, columnIndex) => {
      if (occupied !== 1) return;
      commands.push({
        type: "rect",
        box: {
          x: box.x + columnIndex * cell + 0.8,
          y: box.y + rowIndex * cell + 0.8,
          width: Math.max(1, cell - 1.6),
          height: Math.max(1, cell - 1.6),
        },
        fill: theme.ink,
        stroke: theme.ink,
        lineWidth: 0,
      });
    });
  });
}

function drawPathPanel(commands: DrawCommand[], panel: Box, item: PathPanel): void {
  const [viewX, viewY, viewWidth, viewHeight] = item.viewBox;
  const inset = Math.max(6, panel.width * 0.08);
  const available = panel.width - inset * 2;
  const scale = Math.min(available / viewWidth, available / viewHeight);
  const offsetX = panel.x + (panel.width - viewWidth * scale) / 2 - viewX * scale;
  const offsetY = panel.y + (panel.height - viewHeight * scale) / 2 - viewY * scale;
  item.paths.forEach((path) => {
    commands.push({
      type: "path",
      instructions: path.instructions.map((instruction): PathInstruction => {
        if (instruction.op === "close") return instruction;
        return {
          op: instruction.op,
          x: offsetX + instruction.x * scale,
          y: offsetY + instruction.y * scale,
        };
      }),
      fill: theme.ink,
      fillRule: path.fillRule,
    });
  });
}

function drawPanelFrame(commands: DrawCommand[], box: Box, missing: boolean): void {
  commands.push({
    type: "rect",
    box,
    fill: missing ? theme.primarySoft : theme.surface,
    stroke: missing ? theme.primaryDark : theme.lineStrong,
    lineWidth: missing ? 1.5 : 1,
    dash: missing ? [5, 4] : [],
  });
}

function drawMissingPanel(commands: DrawCommand[], box: Box): void {
  commands.push(
    textCommand(
      "?",
      box.x + box.width / 2,
      box.y + box.height / 2,
      Math.max(20, box.width * 0.3),
      theme.primaryDark,
      "center",
      "middle",
      "data",
      600,
    ),
  );
}

function drawOptionLabel(commands: DrawCommand[], box: Box, label: string): void {
  commands.push(
    textCommand(
      label,
      box.x + box.width / 2,
      box.y + box.height + 6,
      12,
      theme.ink,
      "center",
      "top",
      "data",
      600,
    ),
  );
}

function drawAxes(
  commands: DrawCommand[],
  plot: Box,
  ticks: number[],
  min: number,
  max: number,
  side: "left",
): void {
  void side;
  ticks.forEach((tick) => {
    const y = axisY(tick, min, max, plot);
    commands.push({
      type: "line",
      from: { x: plot.x, y },
      to: { x: plot.x + plot.width, y },
      stroke: tick === min ? theme.lineStrong : theme.line,
      lineWidth: 1,
    });
    commands.push(
      textCommand(
        formatAxisValue(tick),
        plot.x - 7,
        y,
        9,
        theme.muted,
        "right",
        "middle",
        "data",
        400,
      ),
    );
  });
  commands.push({
    type: "line",
    from: { x: plot.x, y: plot.y },
    to: { x: plot.x, y: plot.y + plot.height },
    stroke: theme.lineStrong,
    lineWidth: 1,
  });
}

function drawRightAxis(commands: DrawCommand[], plot: Box, axis: BarLineVisual["rightAxis"]): void {
  axis.ticks.forEach((tick) => {
    const y = axisY(tick, axis.min, axis.max, plot);
    commands.push(
      textCommand(
        formatAxisValue(tick),
        plot.x + plot.width + 7,
        y,
        9,
        theme.muted,
        "left",
        "middle",
        "data",
        400,
      ),
    );
  });
  commands.push({
    type: "line",
    from: { x: plot.x + plot.width, y: plot.y },
    to: { x: plot.x + plot.width, y: plot.y + plot.height },
    stroke: theme.lineStrong,
    lineWidth: 1,
  });
}

interface LegendItem {
  label: string;
  fill: string;
  stroke: string;
  dash?: number[];
  line: boolean;
}

function drawLegend(commands: DrawCommand[], items: LegendItem[], bounds: Box): void {
  const itemWidths = items.map((item) => Math.min(bounds.width, 28 + item.label.length * 11));
  let x = bounds.x;
  let y = bounds.y + 9;
  items.forEach((item, index) => {
    const itemWidth = itemWidths[index] ?? 80;
    if (x > bounds.x && x + itemWidth > bounds.x + bounds.width) {
      x = bounds.x;
      y += 20;
    }
    if (item.line) {
      commands.push({
        type: "line",
        from: { x, y },
        to: { x: x + 16, y },
        stroke: item.stroke,
        lineWidth: 2,
      });
      commands.push({
        type: "circle",
        center: { x: x + 8, y },
        radius: 2.5,
        fill: theme.surface,
        stroke: item.stroke,
        lineWidth: 1.5,
      });
    } else {
      commands.push({
        type: "rect",
        box: { x, y: y - 5, width: 12, height: 10 },
        fill: item.fill,
        stroke: item.stroke,
        lineWidth: 1,
        dash: item.dash ?? [],
      });
    }
    commands.push(
      textCommand(
        ellipsize(item.label, 12),
        x + 19,
        y,
        10,
        theme.muted,
        "left",
        "middle",
        "ui",
        500,
      ),
    );
    x += itemWidth;
  });
}

function appendResolvedLabels(
  commands: DrawCommand[],
  labels: ResolvedLabel[],
  color: string = theme.inkSecondary,
  weight: 400 | 500 | 600 | 700 = 500,
  fontFamily: "ui" | "data" = "data",
): void {
  labels.forEach((label) => {
    if (!label.visible) return;
    commands.push(
      textCommand(
        label.text,
        label.box.x + label.box.width / 2,
        label.y,
        label.fontSize,
        label.text === "缺" ? theme.warning : color,
        "center",
        "middle",
        fontFamily,
        weight,
      ),
    );
  });
}

function scene(
  width: number,
  height: number,
  commands: DrawCommand[],
  labels: ResolvedLabel[],
  missingValueCount: number,
): VisualScene {
  return {
    width,
    height,
    commands,
    labelBoxes: labels
      .filter((label) => label.visible)
      .map((label) => ({ id: label.id, role: label.role, ...label.box })),
    missingValueCount,
  };
}

function textCommand(
  text: string,
  x: number,
  y: number,
  fontSize: number,
  color: string,
  align: "left" | "center" | "right",
  baseline: "top" | "middle" | "bottom",
  fontFamily: "ui" | "data",
  fontWeight: 400 | 500 | 600 | 700,
): DrawCommand {
  return {
    type: "text",
    text,
    at: { x, y },
    color,
    fontSize,
    fontWeight,
    fontFamily,
    align,
    baseline,
  };
}

function axisY(value: number, min: number, max: number, plot: Box): number {
  return plot.y + plot.height * ((max - value) / (max - min));
}

function polarPoint(center: Point, radius: number, degreesFromTop: number): Point {
  const radians = (degreesFromTop * Math.PI) / 180;
  return {
    x: center.x + Math.sin(radians) * radius,
    y: center.y - Math.cos(radians) * radius,
  };
}

function formatAxisValue(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 10_000) return `${formatNumber(value / 10_000)}万`;
  return formatNumber(value);
}

function formatDataValue(value: number): string {
  return formatNumber(value);
}

function formatNumber(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(2).replace(/0+$/u, "").replace(/\.$/u, "");
}

function ellipsize(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, Math.max(1, maxLength - 1))}…`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function hiddenLabel(candidate: LabelCandidate): ResolvedLabel {
  return {
    ...candidate,
    visible: false,
    y: candidate.preferredY,
    box: { x: candidate.x, y: candidate.preferredY, width: 0, height: 0 },
  };
}
