export type PathInstruction =
  { op: "move"; x: number; y: number } | { op: "line"; x: number; y: number } | { op: "close" };

export type PathParseResult =
  { ok: true; instructions: PathInstruction[] } | { ok: false; message: string };

const tokenPattern = /[MLHVZmlhvz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/giu;
const commandPattern = /^[MLHVZmlhvz]$/u;

/**
 * Parses the small, deterministic SVG path subset used by the audited graphic
 * reasoning prototypes. Curves and arcs are rejected instead of approximated.
 */
export function parseLinearSvgPath(path: string): PathParseResult {
  const tokens = path.match(tokenPattern) ?? [];
  const remainder = path.replace(tokenPattern, "").replace(/[\s,]/gu, "");
  if (remainder.length > 0) {
    return { ok: false, message: `包含不支持的 SVG path 指令：${remainder.slice(0, 12)}` };
  }
  if (tokens.length === 0) return { ok: false, message: "SVG path 为空。" };

  const instructions: PathInstruction[] = [];
  let cursorX = 0;
  let cursorY = 0;
  let subpathX = 0;
  let subpathY = 0;
  let index = 0;
  let command = "";

  const nextNumber = (): number | null => {
    const token = tokens[index];
    if (token === undefined || commandPattern.test(token)) return null;
    index += 1;
    const value = Number(token);
    return Number.isFinite(value) ? value : null;
  };

  while (index < tokens.length) {
    const token = tokens[index];
    if (token !== undefined && commandPattern.test(token)) {
      command = token;
      index += 1;
    } else if (command === "") {
      return { ok: false, message: "SVG path 必须从指令开始。" };
    }

    const upper = command.toUpperCase();
    const relative = command !== upper;
    if (upper === "Z") {
      instructions.push({ op: "close" });
      cursorX = subpathX;
      cursorY = subpathY;
      command = "";
      continue;
    }

    if (upper === "H" || upper === "V") {
      const value = nextNumber();
      if (value === null) return { ok: false, message: `${command} 指令缺少坐标。` };
      if (upper === "H") cursorX = relative ? cursorX + value : value;
      else cursorY = relative ? cursorY + value : value;
      instructions.push({ op: "line", x: cursorX, y: cursorY });
      continue;
    }

    if (upper !== "M" && upper !== "L") {
      return { ok: false, message: `不支持 SVG path 指令 ${command}。` };
    }
    const x = nextNumber();
    const y = nextNumber();
    if (x === null || y === null) {
      return { ok: false, message: `${command} 指令缺少坐标对。` };
    }
    cursorX = relative ? cursorX + x : x;
    cursorY = relative ? cursorY + y : y;
    if (upper === "M") {
      instructions.push({ op: "move", x: cursorX, y: cursorY });
      subpathX = cursorX;
      subpathY = cursorY;
      command = relative ? "l" : "L";
    } else {
      instructions.push({ op: "line", x: cursorX, y: cursorY });
    }
  }

  if (instructions[0]?.op !== "move") {
    return { ok: false, message: "SVG path 必须以移动指令开始。" };
  }
  return { ok: true, instructions };
}
