import type { DrawCommand, VisualScene } from "./scene";
import { svgArcToCenter } from "./path";

type CanvasContext = WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D;

const uiFont = "-apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
const dataFont = "Maple Mono CN, DIN Alternate, monospace";
const serifFont = "Georgia, Songti SC, serif";
const symbolFont = "Arial Unicode MS, PingFang SC, sans-serif";

export function paintStructuredVisualScene(context: CanvasContext, scene: VisualScene): void {
  context.clearRect(0, 0, scene.width, scene.height);
  context.lineCap = "round";
  context.lineJoin = "round";
  scene.commands.forEach((command) => {
    paintCommand(context, command);
  });
}

function paintCommand(context: CanvasContext, command: DrawCommand): void {
  context.save();
  if (command.opacity !== undefined) context.globalAlpha = command.opacity;
  if (command.matrix) context.transform(...command.matrix);
  context.setLineDash([]);
  paintCommandContent(context, command);
  context.restore();
}

function paintCommandContent(context: CanvasContext, command: DrawCommand): void {
  switch (command.type) {
    case "line":
      applyStroke(context, command);
      context.beginPath();
      context.moveTo(command.from.x, command.from.y);
      context.lineTo(command.to.x, command.to.y);
      context.stroke();
      return;
    case "roundedRect":
      applyStroke(context, command);
      roundedRectPath(context, command.box, command.radius);
      paintFillAndStroke(context, command.fill, command.lineWidth);
      return;
    case "polyline":
      if (command.points.length === 0) return;
      applyStroke(context, command);
      context.beginPath();
      command.points.forEach((point, index) => {
        if (index === 0) context.moveTo(point.x, point.y);
        else context.lineTo(point.x, point.y);
      });
      if (command.closed) context.closePath();
      if (command.fill) {
        context.fillStyle = command.fill;
        context.fill();
      }
      context.stroke();
      return;
    case "rect":
      applyStroke(context, command);
      if (command.fill) {
        context.fillStyle = command.fill;
        context.fillRect(command.box.x, command.box.y, command.box.width, command.box.height);
      }
      if (command.lineWidth > 0) {
        context.strokeRect(command.box.x, command.box.y, command.box.width, command.box.height);
      }
      return;
    case "circle":
      applyStroke(context, command);
      context.beginPath();
      context.arc(command.center.x, command.center.y, command.radius, 0, Math.PI * 2);
      if (command.fill) {
        context.fillStyle = command.fill;
        context.fill();
      }
      if (command.lineWidth > 0) context.stroke();
      return;
    case "ellipse":
      applyStroke(context, command);
      context.beginPath();
      context.ellipse(
        command.center.x,
        command.center.y,
        command.radiusX,
        command.radiusY,
        command.rotationRadians,
        0,
        Math.PI * 2,
      );
      paintFillAndStroke(context, command.fill, command.lineWidth);
      return;
    case "arc":
      applyStroke(context, command);
      context.beginPath();
      context.ellipse(
        command.center.x,
        command.center.y,
        command.radiusX,
        command.radiusY,
        command.rotationRadians,
        command.startAngle,
        command.endAngle,
        command.anticlockwise,
      );
      paintFillAndStroke(context, command.fill, command.lineWidth);
      return;
    case "quadraticBezier":
      applyStroke(context, command);
      context.beginPath();
      context.moveTo(command.from.x, command.from.y);
      context.quadraticCurveTo(command.control.x, command.control.y, command.to.x, command.to.y);
      paintFillAndStroke(context, command.fill, command.lineWidth);
      return;
    case "cubicBezier":
      applyStroke(context, command);
      context.beginPath();
      context.moveTo(command.from.x, command.from.y);
      context.bezierCurveTo(
        command.control1.x,
        command.control1.y,
        command.control2.x,
        command.control2.y,
        command.to.x,
        command.to.y,
      );
      paintFillAndStroke(context, command.fill, command.lineWidth);
      return;
    case "sector": {
      applyStroke(context, command);
      const start = ((command.startAngle - 90) * Math.PI) / 180;
      const end = ((command.endAngle - 90) * Math.PI) / 180;
      context.beginPath();
      context.moveTo(command.center.x, command.center.y);
      context.arc(command.center.x, command.center.y, command.radius, start, end, false);
      context.closePath();
      context.fillStyle = command.fill;
      context.fill();
      context.stroke();
      return;
    }
    case "text":
      context.fillStyle = command.color;
      context.font = `${command.fontStyle ?? "normal"} ${String(command.fontWeight)} ${String(command.fontSize)}px ${fontFamily(command.fontFamily)}`;
      context.direction = command.direction ?? "inherit";
      context.textAlign = command.align;
      context.textBaseline = command.baseline;
      context.fillText(command.text, command.at.x, command.at.y);
      return;
    case "path": {
      context.beginPath();
      let cursorX = 0;
      let cursorY = 0;
      let subpathX = 0;
      let subpathY = 0;
      command.instructions.forEach((instruction) => {
        if (instruction.op === "move") {
          context.moveTo(instruction.x, instruction.y);
          cursorX = instruction.x;
          cursorY = instruction.y;
          subpathX = instruction.x;
          subpathY = instruction.y;
        } else if (instruction.op === "line") {
          context.lineTo(instruction.x, instruction.y);
          cursorX = instruction.x;
          cursorY = instruction.y;
        } else if (instruction.op === "quadratic") {
          context.quadraticCurveTo(
            instruction.controlX,
            instruction.controlY,
            instruction.x,
            instruction.y,
          );
          cursorX = instruction.x;
          cursorY = instruction.y;
        } else if (instruction.op === "cubic") {
          context.bezierCurveTo(
            instruction.control1X,
            instruction.control1Y,
            instruction.control2X,
            instruction.control2Y,
            instruction.x,
            instruction.y,
          );
          cursorX = instruction.x;
          cursorY = instruction.y;
        } else if (instruction.op === "arc") {
          const arc = svgArcToCenter(cursorX, cursorY, instruction);
          if (arc) {
            context.ellipse(
              arc.centerX,
              arc.centerY,
              arc.radiusX,
              arc.radiusY,
              arc.rotationRadians,
              arc.startAngle,
              arc.endAngle,
              arc.anticlockwise,
            );
          } else {
            context.lineTo(instruction.x, instruction.y);
          }
          cursorX = instruction.x;
          cursorY = instruction.y;
        } else {
          context.closePath();
          cursorX = subpathX;
          cursorY = subpathY;
        }
      });
      applyStroke(context, command);
      if (command.fill) {
        context.fillStyle = command.fill;
        context.fill(command.fillRule);
      }
      if (command.lineWidth > 0) context.stroke();
      return;
    }
  }
}

function paintFillAndStroke(
  context: CanvasContext,
  fill: string | undefined,
  lineWidth: number,
): void {
  if (fill) {
    context.fillStyle = fill;
    context.fill();
  }
  if (lineWidth > 0) context.stroke();
}

function roundedRectPath(
  context: CanvasContext,
  box: { x: number; y: number; width: number; height: number },
  radius: number,
): void {
  const right = box.x + box.width;
  const bottom = box.y + box.height;
  context.beginPath();
  context.moveTo(box.x + radius, box.y);
  context.lineTo(right - radius, box.y);
  context.quadraticCurveTo(right, box.y, right, box.y + radius);
  context.lineTo(right, bottom - radius);
  context.quadraticCurveTo(right, bottom, right - radius, bottom);
  context.lineTo(box.x + radius, bottom);
  context.quadraticCurveTo(box.x, bottom, box.x, bottom - radius);
  context.lineTo(box.x, box.y + radius);
  context.quadraticCurveTo(box.x, box.y, box.x + radius, box.y);
  context.closePath();
}

function fontFamily(value: Extract<DrawCommand, { type: "text" }>["fontFamily"]): string {
  if (value === "data" || value === "monospace") return dataFont;
  if (value === "serif") return serifFont;
  if (value === "symbol") return symbolFont;
  return uiFont;
}

function applyStroke(
  context: CanvasContext,
  style: { stroke: string; lineWidth: number; dash?: number[] },
): void {
  context.strokeStyle = style.stroke;
  context.lineWidth = style.lineWidth;
  context.setLineDash(style.dash ?? []);
}
