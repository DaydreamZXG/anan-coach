import type {
  GraphicPrimitive,
  GraphicPrimitivePanel,
  GraphicPrimitivesVisual,
  PrimitiveStyle,
  PrimitiveTransform,
} from "./primitives";
import type { Box, DrawCommand, Matrix2D, Point, VisualScene } from "./scene";

const colors = {
  ink: "#101828",
  line: "#d1d5dc",
  primary: "#0092b8",
  primarySoft: "#ecfeff",
  surface: "#ffffff",
} as const;

interface PanelPlacement {
  panel: GraphicPrimitivePanel;
  box: Box;
}

/** Builds the frontend-owned panel frame and maps validated coordinates into it. */
export function buildPrimitivePanelsScene(
  visual: GraphicPrimitivesVisual,
  width: number,
  height: number,
): VisualScene {
  const commands: DrawCommand[] = [];
  const placements = placePanels(visual, width, height);
  placements.forEach(({ panel, box }) => {
    drawPanelFrame(commands, box, panel.role === "missing");
    if (panel.role === "missing") {
      commands.push(textCommand("?", center(box), Math.max(20, box.width * 0.3), colors.primary));
    } else {
      const matrix = panelCoordinateMatrix(box, visual.coordinateSpace);
      panel.primitives.forEach((primitive) => {
        appendPrimitive(commands, primitive, matrix, 1);
      });
    }
    if (panel.role === "option") {
      commands.push({
        type: "text",
        text: panel.id,
        at: { x: box.x + box.width / 2, y: box.y + box.height + 6 },
        color: colors.ink,
        fontSize: 12,
        fontWeight: 600,
        fontFamily: "data",
        align: "center",
        baseline: "top",
      });
    } else if (panel.role === "sample" && visual.layoutType === "classification_2x3") {
      commands.push({
        type: "text",
        text: panel.id,
        at: { x: box.x + box.width / 2, y: box.y + box.height + 4 },
        color: colors.ink,
        fontSize: 12,
        fontWeight: 500,
        fontFamily: "data",
        align: "center",
        baseline: "top",
      });
    }
  });
  return { width, height, commands, labelBoxes: [], missingValueCount: 0 };
}

/** Deterministic matrix for the schema's bounded transform object. */
export function primitiveTransformMatrix(transform: PrimitiveTransform): Matrix2D {
  const radians = (transform.rotateDegrees * Math.PI) / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  return multiplyMatrices(
    [
      1,
      0,
      0,
      1,
      transform.translateX + transform.originX,
      transform.translateY + transform.originY,
    ],
    multiplyMatrices(
      [cosine, sine, -sine, cosine, 0, 0],
      multiplyMatrices(
        [transform.scaleX, 0, 0, transform.scaleY, 0, 0],
        [1, 0, 0, 1, -transform.originX, -transform.originY],
      ),
    ),
  );
}

export function multiplyMatrices(left: Matrix2D, right: Matrix2D): Matrix2D {
  return [
    left[0] * right[0] + left[2] * right[1],
    left[1] * right[0] + left[3] * right[1],
    left[0] * right[2] + left[2] * right[3],
    left[1] * right[2] + left[3] * right[3],
    left[0] * right[4] + left[2] * right[5] + left[4],
    left[1] * right[4] + left[3] * right[5] + left[5],
  ];
}

function appendPrimitive(
  commands: DrawCommand[],
  primitive: GraphicPrimitive,
  parentMatrix: Matrix2D,
  parentOpacity: number,
): void {
  const matrix = multiplyMatrices(parentMatrix, primitiveTransformMatrix(primitive.transform));
  const opacity = parentOpacity * primitive.style.opacity;
  const stroke = strokeFields(primitive.style);
  const fill = fillFields(primitive.style);

  switch (primitive.type) {
    case "line":
      commands.push({
        type: "line",
        from: primitive.from,
        to: primitive.to,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "polyline":
    case "polygon":
    case "face":
      commands.push({
        type: "polyline",
        points: primitive.points,
        closed: primitive.type !== "polyline",
        ...fill,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "rect":
      commands.push({ type: "rect", box: primitive.box, ...fill, ...stroke, matrix, opacity });
      return;
    case "roundedRect":
      commands.push({
        type: "roundedRect",
        box: primitive.box,
        radius: primitive.radius,
        ...fill,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "circle":
    case "dot":
      commands.push({
        type: "circle",
        center: primitive.center,
        radius: primitive.radius,
        ...fill,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "ellipse":
      commands.push({
        type: "ellipse",
        center: primitive.center,
        radiusX: primitive.radiusX,
        radiusY: primitive.radiusY,
        rotationRadians: degreesToRadians(primitive.rotationDegrees),
        ...fill,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "arc":
      commands.push({
        type: "arc",
        center: primitive.center,
        radiusX: primitive.radiusX,
        radiusY: primitive.radiusY,
        rotationRadians: degreesToRadians(primitive.rotationDegrees),
        startAngle: degreesToRadians(primitive.startDegrees),
        endAngle: degreesToRadians(primitive.endDegrees),
        anticlockwise: primitive.anticlockwise,
        ...fill,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "quadraticBezier":
      commands.push({
        type: "quadraticBezier",
        from: primitive.from,
        control: primitive.control,
        to: primitive.to,
        ...fill,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "cubicBezier":
      commands.push({
        type: "cubicBezier",
        from: primitive.from,
        control1: primitive.control1,
        control2: primitive.control2,
        to: primitive.to,
        ...fill,
        ...stroke,
        matrix,
        opacity,
      });
      return;
    case "pathGroup":
      primitive.paths.forEach((path) => {
        commands.push({
          type: "path",
          instructions: path.commands,
          ...fill,
          fillRule: path.fillRule,
          ...stroke,
          matrix,
          opacity,
        });
      });
      return;
    case "textGlyph":
      appendTextGlyph(commands, primitive, matrix, opacity);
      return;
    case "semanticSymbol":
      primitive.primitives.forEach((child) => {
        appendPrimitive(commands, child, matrix, opacity);
      });
      return;
    case "wireframe":
      primitive.edges.forEach(([fromIndex, toIndex]) => {
        const from = primitive.vertices[fromIndex];
        const to = primitive.vertices[toIndex];
        if (from && to) {
          commands.push({ type: "line", from, to, ...stroke, matrix, opacity });
        }
      });
      return;
  }
}

function appendTextGlyph(
  commands: DrawCommand[],
  primitive: Extract<GraphicPrimitive, { type: "textGlyph" }>,
  matrix: Matrix2D,
  opacity: number,
): void {
  const characters = Array.from(primitive.content);
  const vertical = primitive.direction === "vertical";
  const parts = vertical ? characters : [primitive.content];
  parts.forEach((text, index) => {
    commands.push({
      type: "text",
      text,
      at: {
        x: primitive.position.x,
        y: primitive.position.y + (vertical ? index * primitive.fontSize * 1.08 : 0),
      },
      color: primitive.style.fill ?? primitive.style.stroke ?? colors.ink,
      fontSize: primitive.fontSize,
      fontWeight: primitive.fontWeight,
      fontFamily: primitive.fontCategory === "sans" ? "ui" : primitive.fontCategory,
      fontStyle: primitive.fontStyle,
      direction: primitive.direction === "rtl" ? "rtl" : "ltr",
      align: primitive.align,
      baseline: "middle",
      matrix,
      opacity,
    });
  });
}

function strokeFields(style: PrimitiveStyle): {
  stroke: string;
  lineWidth: number;
  dash: number[];
} {
  return {
    stroke: style.stroke ?? colors.ink,
    lineWidth: style.stroke === null ? 0 : style.lineWidth,
    dash: style.dash,
  };
}

function fillFields(style: PrimitiveStyle): { fill?: string } {
  return style.fill === null ? {} : { fill: style.fill };
}

function panelCoordinateMatrix(
  panel: Box,
  coordinateSpace: GraphicPrimitivesVisual["coordinateSpace"],
): Matrix2D {
  const inset = Math.max(5, panel.width * 0.07);
  const availableWidth = panel.width - inset * 2;
  const availableHeight = panel.height - inset * 2;
  const scale = Math.min(
    availableWidth / coordinateSpace.width,
    availableHeight / coordinateSpace.height,
  );
  return [
    scale,
    0,
    0,
    scale,
    panel.x + (panel.width - coordinateSpace.width * scale) / 2,
    panel.y + (panel.height - coordinateSpace.height * scale) / 2,
  ];
}

function placePanels(
  visual: GraphicPrimitivesVisual,
  width: number,
  height: number,
): PanelPlacement[] {
  if (visual.layoutType === "classification_2x3") {
    return placeClassification(visual.panels, width, height);
  }
  return placeQuestionPanels(visual.panels, visual.layoutType, width, height);
}

function placeClassification(
  panels: GraphicPrimitivePanel[],
  width: number,
  height: number,
): PanelPlacement[] {
  const margin = 10;
  const gap = width >= 600 ? 16 : 9;
  const columns = 3;
  const rows = 2;
  const labelHeight = 18;
  const size = Math.max(
    54,
    Math.min(
      (width - margin * 2 - gap * (columns - 1)) / columns,
      (height - margin * 2 - gap * (rows - 1) - labelHeight * rows) / rows,
      width >= 600 ? 145 : 110,
    ),
  );
  const left = (width - (columns * size + gap * (columns - 1))) / 2;
  const totalHeight = rows * (size + labelHeight) + gap * (rows - 1);
  const top = Math.max(margin, (height - totalHeight) / 2);
  return [...panels]
    .sort((leftPanel, rightPanel) => panelCell(leftPanel) - panelCell(rightPanel))
    .map((panel) => ({
      panel,
      box: {
        x: left + ((panel.column ?? 1) - 1) * (size + gap),
        y: top + ((panel.row ?? 1) - 1) * (size + labelHeight + gap),
        width: size,
        height: size,
      },
    }));
}

function placeQuestionPanels(
  panels: GraphicPrimitivePanel[],
  layout: "matrix_3x3" | "sequence",
  width: number,
  height: number,
): PanelPlacement[] {
  const margin = 10;
  const gap = width >= 600 ? 12 : 8;
  const sectionGap = 22;
  const labelHeight = 18;
  const options = panels
    .filter((panel) => panel.role === "option")
    .sort((left, right) => (left.position ?? 0) - (right.position ?? 0));
  const stems = panels
    .filter((panel) => panel.role !== "option")
    .sort((left, right) =>
      layout === "matrix_3x3"
        ? panelCell(left) - panelCell(right)
        : (left.position ?? 0) - (right.position ?? 0),
    );
  const stemColumns = layout === "matrix_3x3" ? 3 : width >= 600 ? 6 : 3;
  const optionColumns = width >= 600 ? 4 : 2;
  const stemRows = Math.ceil(stems.length / stemColumns);
  const optionRows = Math.ceil(options.length / optionColumns);
  const totalGaps = Math.max(0, stemRows - 1) + Math.max(0, optionRows - 1);
  const byWidth = Math.min(
    (width - margin * 2 - gap * (stemColumns - 1)) / stemColumns,
    (width - margin * 2 - gap * (optionColumns - 1)) / optionColumns,
  );
  const byHeight =
    (height - margin * 2 - sectionGap - totalGaps * gap - optionRows * labelHeight) /
    Math.max(1, stemRows + optionRows);
  const size = Math.max(52, Math.min(byWidth, byHeight, width >= 600 ? 118 : 102));
  const stemHeight = stemRows * size + Math.max(0, stemRows - 1) * gap;
  return [
    ...placeGrid(stems, stemColumns, size, gap, margin, width, false),
    ...placeGrid(options, optionColumns, size, gap, margin + stemHeight + sectionGap, width, true),
  ];
}

function placeGrid(
  panels: GraphicPrimitivePanel[],
  columns: number,
  size: number,
  gap: number,
  top: number,
  width: number,
  includeLabelGap: boolean,
): PanelPlacement[] {
  const left = (width - (columns * size + gap * (columns - 1))) / 2;
  return panels.map((panel, index) => ({
    panel,
    box: {
      x: left + (index % columns) * (size + gap),
      y: top + Math.floor(index / columns) * (size + gap + (includeLabelGap ? 18 : 0)),
      width: size,
      height: size,
    },
  }));
}

function drawPanelFrame(commands: DrawCommand[], box: Box, missing: boolean): void {
  commands.push({
    type: "rect",
    box,
    fill: missing ? colors.primarySoft : colors.surface,
    stroke: missing ? colors.primary : colors.line,
    lineWidth: missing ? 1.5 : 1,
    dash: missing ? [5, 4] : [],
  });
}

function textCommand(text: string, at: Point, fontSize: number, color: string): DrawCommand {
  return {
    type: "text",
    text,
    at,
    color,
    fontSize,
    fontWeight: 600,
    fontFamily: "data",
    align: "center",
    baseline: "middle",
  };
}

function center(box: Box): Point {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

function panelCell(panel: GraphicPrimitivePanel): number {
  return (panel.row ?? 0) * 10 + (panel.column ?? 0);
}

function degreesToRadians(value: number): number {
  return (value * Math.PI) / 180;
}
