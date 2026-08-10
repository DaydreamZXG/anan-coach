export const SVG_PATH_LIMITS = {
  maxCharacters: 65_536,
  maxInstructions: 4_096,
  maxCoordinateMagnitude: 100_000,
} as const;

export type PathInstruction =
  | { op: "move"; x: number; y: number }
  | { op: "line"; x: number; y: number }
  | { op: "quadratic"; controlX: number; controlY: number; x: number; y: number }
  | {
      op: "cubic";
      control1X: number;
      control1Y: number;
      control2X: number;
      control2Y: number;
      x: number;
      y: number;
    }
  | {
      op: "arc";
      radiusX: number;
      radiusY: number;
      rotation: number;
      largeArc: boolean;
      sweep: boolean;
      x: number;
      y: number;
    }
  | { op: "close" };

export interface CenterArc {
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
  rotationRadians: number;
  startAngle: number;
  endAngle: number;
  anticlockwise: boolean;
}

export type PathParseResult =
  { ok: true; instructions: PathInstruction[] } | { ok: false; code: string; message: string };

const commandPattern = /^[MLHVCSQTAZmlhvcsqtaz]$/u;
const tokenPattern = /[MLHVCSQTAZmlhvcsqtaz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/giu;
const arity = {
  M: 2,
  L: 2,
  H: 1,
  V: 1,
  C: 6,
  S: 4,
  Q: 4,
  T: 2,
  A: 7,
  Z: 0,
} as const;

type PathCommand = keyof typeof arity;

/**
 * Safe SVG path normalizer for M/L/H/V/C/S/Q/T/A/Z. Relative commands,
 * repeated parameter sets, implicit lineto after moveto and smooth-control
 * reflection are resolved into explicit absolute instructions.
 */
export function parseSvgPath(path: string): PathParseResult {
  if (path.length > SVG_PATH_LIMITS.maxCharacters) {
    return failure("PATH_CHARACTER_LIMIT", "SVG path 超过字符复杂度上限。");
  }
  const tokens = path.match(tokenPattern) ?? [];
  const remainder = path.replace(tokenPattern, "").replace(/[\s,]/gu, "");
  if (remainder.length > 0) {
    return failure(
      "PATH_COMMAND_UNSUPPORTED",
      `包含不支持的 SVG path 指令：${remainder.slice(0, 12)}`,
    );
  }
  if (tokens.length === 0) return failure("PATH_EMPTY", "SVG path 为空。");

  const instructions: PathInstruction[] = [];
  let cursorX = 0;
  let cursorY = 0;
  let subpathX = 0;
  let subpathY = 0;
  let index = 0;
  let commandToken = "";
  let previousCommand: PathCommand | null = null;
  let previousCubicControl: [number, number] | null = null;
  let previousQuadraticControl: [number, number] | null = null;

  while (index < tokens.length) {
    const token = tokens[index];
    if (token !== undefined && commandPattern.test(token)) {
      commandToken = token;
      index += 1;
    } else if (commandToken === "") {
      return failure("PATH_COMMAND_REQUIRED", "SVG path 参数前缺少指令。");
    }

    const command = commandToken.toUpperCase() as PathCommand;
    const relative = commandToken !== command;
    if (command === "Z") {
      instructions.push({ op: "close" });
      cursorX = subpathX;
      cursorY = subpathY;
      previousCommand = command;
      previousCubicControl = null;
      previousQuadraticControl = null;
      commandToken = "";
      if (!withinInstructionLimit(instructions)) {
        return failure("PATH_INSTRUCTION_LIMIT", "SVG path 超过指令复杂度上限。");
      }
      continue;
    }

    const parameterCount = arity[command];
    const parameters = readParameterSet(tokens, index, parameterCount);
    if (!parameters.ok) return parameters.error;
    index += parameterCount;
    if (!parameters.values.every(coordinateWithinLimit)) {
      return failure("PATH_COORDINATE_LIMIT", "SVG path 含非有限数值或坐标超过安全范围。");
    }

    const startX = cursorX;
    const startY = cursorY;
    const point = (x: number, y: number): [number, number] => [
      relative ? startX + x : x,
      relative ? startY + y : y,
    ];

    if (command === "M" || command === "L" || command === "T") {
      const [x, y] = point(parameters.values[0] ?? 0, parameters.values[1] ?? 0);
      if (!coordinateWithinLimit(x) || !coordinateWithinLimit(y)) {
        return failure("PATH_COORDINATE_LIMIT", "相对坐标计算结果超过安全范围。");
      }
      if (command === "M") {
        instructions.push({ op: "move", x, y });
        subpathX = x;
        subpathY = y;
        commandToken = relative ? "l" : "L";
        previousQuadraticControl = null;
      } else if (command === "T") {
        const reflectedControl: [number, number] =
          previousCommand === "Q" || previousCommand === "T"
            ? reflect(previousQuadraticControl, startX, startY)
            : [startX, startY];
        const [controlX, controlY] = reflectedControl;
        instructions.push({ op: "quadratic", controlX, controlY, x, y });
        previousQuadraticControl = [controlX, controlY];
      } else {
        instructions.push({ op: "line", x, y });
        previousQuadraticControl = null;
      }
      cursorX = x;
      cursorY = y;
      previousCubicControl = null;
    } else if (command === "H") {
      const x = relative ? startX + (parameters.values[0] ?? 0) : (parameters.values[0] ?? 0);
      if (!coordinateWithinLimit(x)) {
        return failure("PATH_COORDINATE_LIMIT", "相对坐标计算结果超过安全范围。");
      }
      cursorX = x;
      instructions.push({ op: "line", x, y: cursorY });
      previousCubicControl = null;
      previousQuadraticControl = null;
    } else if (command === "V") {
      const y = relative ? startY + (parameters.values[0] ?? 0) : (parameters.values[0] ?? 0);
      if (!coordinateWithinLimit(y)) {
        return failure("PATH_COORDINATE_LIMIT", "相对坐标计算结果超过安全范围。");
      }
      cursorY = y;
      instructions.push({ op: "line", x: cursorX, y });
      previousCubicControl = null;
      previousQuadraticControl = null;
    } else if (command === "Q") {
      const [controlX, controlY] = point(parameters.values[0] ?? 0, parameters.values[1] ?? 0);
      const [x, y] = point(parameters.values[2] ?? 0, parameters.values[3] ?? 0);
      if (![controlX, controlY, x, y].every(coordinateWithinLimit)) {
        return failure("PATH_COORDINATE_LIMIT", "相对坐标计算结果超过安全范围。");
      }
      instructions.push({ op: "quadratic", controlX, controlY, x, y });
      cursorX = x;
      cursorY = y;
      previousQuadraticControl = [controlX, controlY];
      previousCubicControl = null;
    } else if (command === "C") {
      const [control1X, control1Y] = point(parameters.values[0] ?? 0, parameters.values[1] ?? 0);
      const [control2X, control2Y] = point(parameters.values[2] ?? 0, parameters.values[3] ?? 0);
      const [x, y] = point(parameters.values[4] ?? 0, parameters.values[5] ?? 0);
      if (![control1X, control1Y, control2X, control2Y, x, y].every(coordinateWithinLimit)) {
        return failure("PATH_COORDINATE_LIMIT", "相对坐标计算结果超过安全范围。");
      }
      instructions.push({
        op: "cubic",
        control1X,
        control1Y,
        control2X,
        control2Y,
        x,
        y,
      });
      cursorX = x;
      cursorY = y;
      previousCubicControl = [control2X, control2Y];
      previousQuadraticControl = null;
    } else if (command === "S") {
      const [control1X, control1Y] =
        previousCommand === "C" || previousCommand === "S"
          ? reflect(previousCubicControl, startX, startY)
          : [startX, startY];
      const [control2X, control2Y] = point(parameters.values[0] ?? 0, parameters.values[1] ?? 0);
      const [x, y] = point(parameters.values[2] ?? 0, parameters.values[3] ?? 0);
      if (![control1X, control1Y, control2X, control2Y, x, y].every(coordinateWithinLimit)) {
        return failure("PATH_COORDINATE_LIMIT", "相对坐标计算结果超过安全范围。");
      }
      instructions.push({
        op: "cubic",
        control1X,
        control1Y,
        control2X,
        control2Y,
        x,
        y,
      });
      cursorX = x;
      cursorY = y;
      previousCubicControl = [control2X, control2Y];
      previousQuadraticControl = null;
    } else {
      const radiusX = parameters.values[0] ?? 0;
      const radiusY = parameters.values[1] ?? 0;
      const rotation = parameters.values[2] ?? 0;
      const largeArcFlag = parameters.values[3];
      const sweepFlag = parameters.values[4];
      if (radiusX < 0 || radiusY < 0) {
        return failure("PATH_ARC_RADIUS_INVALID", "椭圆弧半径不能为负数。");
      }
      if ((largeArcFlag !== 0 && largeArcFlag !== 1) || (sweepFlag !== 0 && sweepFlag !== 1)) {
        return failure("PATH_ARC_FLAG_INVALID", "椭圆弧 large-arc 与 sweep 标志只能为 0 或 1。");
      }
      const [x, y] = point(parameters.values[5] ?? 0, parameters.values[6] ?? 0);
      if (![x, y].every(coordinateWithinLimit)) {
        return failure("PATH_COORDINATE_LIMIT", "相对坐标计算结果超过安全范围。");
      }
      if (radiusX === 0 || radiusY === 0) {
        instructions.push({ op: "line", x, y });
      } else if (x !== startX || y !== startY) {
        instructions.push({
          op: "arc",
          radiusX,
          radiusY,
          rotation,
          largeArc: largeArcFlag === 1,
          sweep: sweepFlag === 1,
          x,
          y,
        });
      }
      cursorX = x;
      cursorY = y;
      previousCubicControl = null;
      previousQuadraticControl = null;
    }

    previousCommand = command;
    if (!withinInstructionLimit(instructions)) {
      return failure("PATH_INSTRUCTION_LIMIT", "SVG path 超过指令复杂度上限。");
    }
  }

  if (instructions[0]?.op !== "move") {
    return failure("PATH_MUST_START_WITH_MOVE", "SVG path 必须以移动指令开始。");
  }
  return { ok: true, instructions };
}

/** Backwards-compatible name retained for the audited v1 linear prototypes. */
export const parseLinearSvgPath = parseSvgPath;

/** Converts SVG endpoint arc parameters to Canvas ellipse center parameters. */
export function svgArcToCenter(
  startX: number,
  startY: number,
  instruction: Extract<PathInstruction, { op: "arc" }>,
): CenterArc | null {
  let radiusX = Math.abs(instruction.radiusX);
  let radiusY = Math.abs(instruction.radiusY);
  if (radiusX === 0 || radiusY === 0) return null;
  const rotationRadians = ((instruction.rotation % 360) * Math.PI) / 180;
  const cosRotation = Math.cos(rotationRadians);
  const sinRotation = Math.sin(rotationRadians);
  const halfDeltaX = (startX - instruction.x) / 2;
  const halfDeltaY = (startY - instruction.y) / 2;
  const transformedX = cosRotation * halfDeltaX + sinRotation * halfDeltaY;
  const transformedY = -sinRotation * halfDeltaX + cosRotation * halfDeltaY;
  const radiiScale =
    (transformedX * transformedX) / (radiusX * radiusX) +
    (transformedY * transformedY) / (radiusY * radiusY);
  if (radiiScale > 1) {
    const scale = Math.sqrt(radiiScale);
    radiusX *= scale;
    radiusY *= scale;
  }

  const radiusXSquared = radiusX * radiusX;
  const radiusYSquared = radiusY * radiusY;
  const transformedXSquared = transformedX * transformedX;
  const transformedYSquared = transformedY * transformedY;
  const denominator = radiusXSquared * transformedYSquared + radiusYSquared * transformedXSquared;
  if (denominator === 0) return null;
  const numerator = Math.max(
    0,
    radiusXSquared * radiusYSquared -
      radiusXSquared * transformedYSquared -
      radiusYSquared * transformedXSquared,
  );
  const sign = instruction.largeArc === instruction.sweep ? -1 : 1;
  const coefficient = sign * Math.sqrt(numerator / denominator);
  const transformedCenterX = coefficient * ((radiusX * transformedY) / radiusY);
  const transformedCenterY = coefficient * (-(radiusY * transformedX) / radiusX);
  const centerX =
    cosRotation * transformedCenterX -
    sinRotation * transformedCenterY +
    (startX + instruction.x) / 2;
  const centerY =
    sinRotation * transformedCenterX +
    cosRotation * transformedCenterY +
    (startY + instruction.y) / 2;

  const startVector: [number, number] = [
    (transformedX - transformedCenterX) / radiusX,
    (transformedY - transformedCenterY) / radiusY,
  ];
  const endVector: [number, number] = [
    (-transformedX - transformedCenterX) / radiusX,
    (-transformedY - transformedCenterY) / radiusY,
  ];
  const startAngle = vectorAngle([1, 0], startVector);
  let deltaAngle = vectorAngle(startVector, endVector);
  if (!instruction.sweep && deltaAngle > 0) deltaAngle -= Math.PI * 2;
  if (instruction.sweep && deltaAngle < 0) deltaAngle += Math.PI * 2;
  return {
    centerX,
    centerY,
    radiusX,
    radiusY,
    rotationRadians,
    startAngle,
    endAngle: startAngle + deltaAngle,
    anticlockwise: !instruction.sweep,
  };
}

function readParameterSet(
  tokens: string[],
  start: number,
  count: number,
): { ok: true; values: number[] } | { ok: false; error: Extract<PathParseResult, { ok: false }> } {
  const values: number[] = [];
  for (let offset = 0; offset < count; offset += 1) {
    const token = tokens[start + offset];
    if (token === undefined || commandPattern.test(token)) {
      return {
        ok: false,
        error: failure("PATH_PARAMETER_MISSING", "SVG path 指令缺少完整参数组。"),
      };
    }
    const value = Number(token);
    if (!Number.isFinite(value)) {
      return {
        ok: false,
        error: failure("PATH_NUMBER_INVALID", "SVG path 包含非有限数值。"),
      };
    }
    values.push(value);
  }
  return { ok: true, values };
}

function reflect(
  control: [number, number] | null,
  cursorX: number,
  cursorY: number,
): [number, number] {
  return control ? [cursorX * 2 - control[0], cursorY * 2 - control[1]] : [cursorX, cursorY];
}

function vectorAngle(left: [number, number], right: [number, number]): number {
  const dot = left[0] * right[0] + left[1] * right[1];
  const determinant = left[0] * right[1] - left[1] * right[0];
  return Math.atan2(determinant, dot);
}

function coordinateWithinLimit(value: number): boolean {
  return Number.isFinite(value) && Math.abs(value) <= SVG_PATH_LIMITS.maxCoordinateMagnitude;
}

function withinInstructionLimit(instructions: PathInstruction[]): boolean {
  return instructions.length <= SVG_PATH_LIMITS.maxInstructions;
}

function failure(code: string, message: string): Extract<PathParseResult, { ok: false }> {
  return { ok: false, code, message };
}
