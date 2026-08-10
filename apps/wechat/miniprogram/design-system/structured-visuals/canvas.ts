import type { DrawCommand, VisualScene } from "./scene";

type CanvasContext = WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D;

const uiFont = "-apple-system, BlinkMacSystemFont, PingFang SC, sans-serif";
const dataFont = "Maple Mono CN, DIN Alternate, monospace";

export function paintStructuredVisualScene(context: CanvasContext, scene: VisualScene): void {
  context.clearRect(0, 0, scene.width, scene.height);
  context.lineCap = "round";
  context.lineJoin = "round";
  scene.commands.forEach((command) => {
    paintCommand(context, command);
  });
}

function paintCommand(context: CanvasContext, command: DrawCommand): void {
  context.setLineDash([]);
  switch (command.type) {
    case "line":
      applyStroke(context, command);
      context.beginPath();
      context.moveTo(command.from.x, command.from.y);
      context.lineTo(command.to.x, command.to.y);
      context.stroke();
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
      context.font = `${String(command.fontWeight)} ${String(command.fontSize)}px ${command.fontFamily === "data" ? dataFont : uiFont}`;
      context.textAlign = command.align;
      context.textBaseline = command.baseline;
      context.fillText(command.text, command.at.x, command.at.y);
      return;
    case "path":
      context.beginPath();
      command.instructions.forEach((instruction) => {
        if (instruction.op === "move") context.moveTo(instruction.x, instruction.y);
        else if (instruction.op === "line") context.lineTo(instruction.x, instruction.y);
        else context.closePath();
      });
      context.fillStyle = command.fill;
      context.fill(command.fillRule);
      return;
  }
}

function applyStroke(
  context: CanvasContext,
  style: { stroke: string; lineWidth: number; dash?: number[] },
): void {
  context.strokeStyle = style.stroke;
  context.lineWidth = style.lineWidth;
  context.setLineDash(style.dash ?? []);
}
