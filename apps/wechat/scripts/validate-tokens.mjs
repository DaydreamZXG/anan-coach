import { readdir, readFile } from "node:fs/promises";
import { relative, resolve } from "node:path";

const projectRoot = resolve(import.meta.dirname, "..");
const scanRoots = [
  resolve(projectRoot, "miniprogram/pages"),
  resolve(projectRoot, "miniprogram/custom-tab-bar"),
  resolve(projectRoot, "miniprogram/components"),
  resolve(projectRoot, "miniprogram/features"),
  resolve(projectRoot, "miniprogram/task-runtime"),
  resolve(projectRoot, "miniprogram/design-system/primitives"),
  resolve(projectRoot, "miniprogram/design-system/reporting"),
  resolve(projectRoot, "miniprogram/design-system/stage9"),
];
const hexColorPattern = /#[\da-fA-F]{3,4}\b|#[\da-fA-F]{6}\b|#[\da-fA-F]{8}\b/gu;
const exemptionPattern = /\/\*\s*token-exempt:\s*(.+?)\s*\*\/\s*$/u;
// Locked scale (2026-07-26): these three properties resolve to tokens.
// px literals stay legal for the tablet layer; hairline radii (<= 4rpx),
// 0, 50% and inherit stay literal by design.
const scalePattern = /(?<prop>font-size|border-radius|box-shadow)\s*:\s*(?<value>[^;}]+)/gu;
const scaleMigrationPending = new Set();

// Locked spacing rhythm and font-weight steps (2026-07-26).
// Components <= 4rpx are hairlines, > 96rpx are layout clearances; both stay
// literal. Values containing var()/calc()/env()/max()/min() are exempt.
const spaceLadder = new Set([2, 4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96]);
const weightSteps = new Set(["400", "500", "600", "700", "800"]);

// 已退役的写法（2026-07-26 收敛）：字号只走 --text-*，文字灰只剩 4 档。
const retiredPatterns = [
  {
    pattern: /var\(--figma-font-(?!family)[a-z-]+\)/u,
    message: "字号已统一为 --text-*，不再直接引用 --figma-font-*",
  },
  {
    pattern: /var\(--font-(?:body|caption|section|title)\)/u,
    message: "旧 --font-* 字号别名已退役，改用 --text-*",
  },
  {
    pattern: /var\(--figma-color-ink-tertiary\)/u,
    message: "文字灰已收敛为 4 档，--figma-color-ink-tertiary 并入 --color-ink-secondary",
  },
  {
    pattern: /color:\s*var\(--figma-color-ink-strong\)/u,
    message: "文字色不再使用 --figma-color-ink-strong，并入 --color-ink（暗面结构背景除外）",
  },
  {
    pattern: /var\(--figma-color-primary/u,
    message: "主交互色走语义层 --color-primary*（青色系），不再直连 --figma-color-primary*",
  },
];
const rhythmPattern =
  /(?<![-a-zA-Z])(?<prop>padding(?:-top|-right|-bottom|-left)?|margin(?:-top|-right|-bottom|-left)?|row-gap|column-gap|gap|font-weight)\s*:\s*(?<value>[^;}]+)/gu;

function scaleViolations(code, file) {
  if (scaleMigrationPending.has(file)) return [];
  const found = [];
  for (const match of code.matchAll(scalePattern)) {
    const value = match.groups.value.trim();
    if (value.includes("var(")) continue;
    if (match.groups.prop === "box-shadow") {
      if (value !== "none") found.push(`box-shadow: ${value}`);
      continue;
    }
    const offending = [...value.matchAll(/(-?[\d.]+)rpx/gu)].filter(
      (m) => match.groups.prop === "font-size" || Math.abs(Number(m[1])) > 4,
    );
    if (offending.length > 0) found.push(`${match.groups.prop}: ${value}`);
  }
  for (const match of code.matchAll(rhythmPattern)) {
    const value = match.groups.value.trim().replace(/\s*!important$/u, "");
    if (match.groups.prop === "font-weight") {
      if (/^\d+$/u.test(value) && !weightSteps.has(value)) {
        found.push(`font-weight: ${value}`);
      }
      continue;
    }
    if (/var\(|calc\(|env\(|max\(|min\(/u.test(value)) continue;
    const offending = [...value.matchAll(/(-?[\d.]+)rpx/gu)]
      .map((m) => Math.abs(Number(m[1])))
      .filter((n) => n > 4 && n <= 96 && !spaceLadder.has(n));
    if (offending.length > 0) found.push(`${match.groups.prop}: ${value}`);
  }
  return found;
}

const files = (await Promise.all(scanRoots.map((directory) => collectStylesheets(directory))))
  .flat()
  .sort();
const failures = [];
let exemptionCount = 0;

for (const file of files) {
  if (scaleMigrationPending.has(relative(projectRoot, file))) continue;
  const source = await readFile(file, "utf8");
  const lines = source.split(/\r?\n/u);
  let insideComment = false;

  for (const [index, line] of lines.entries()) {
    const exemption = !insideComment ? line.match(exemptionPattern) : null;
    const stripped = stripBlockComments(line, insideComment);
    insideComment = stripped.insideComment;
    const colors = stripped.code.match(hexColorPattern) ?? [];
    const scaleIssues = scaleViolations(stripped.code, relative(projectRoot, file));
    const retiredIssues = retiredPatterns
      .filter(({ pattern }) => pattern.test(stripped.code))
      .map(({ message }) => message);

    if (
      exemption &&
      colors.length === 0 &&
      scaleIssues.length === 0 &&
      retiredIssues.length === 0
    ) {
      failures.push(
        `${relative(projectRoot, file)}:${String(index + 1)} 的 token-exempt 没有对应违规值`,
      );
      continue;
    }
    if (exemption) {
      exemptionCount += 1;
      continue;
    }
    if (colors.length > 0) {
      failures.push(
        `${relative(projectRoot, file)}:${String(index + 1)} 使用了 ${colors.join(", ")}`,
      );
    }
    for (const issue of scaleIssues) {
      failures.push(`${relative(projectRoot, file)}:${String(index + 1)} 绕过锁定阶梯：${issue}`);
    }
    for (const issue of retiredIssues) {
      failures.push(`${relative(projectRoot, file)}:${String(index + 1)} ${issue}`);
    }
  }
}

if (failures.length > 0) {
  console.error("Token 校验失败：颜色、字号、圆角与阴影必须使用锁定的设计令牌。");
  for (const failure of failures) console.error(`- ${failure}`);
  console.error(
    "字号用 --text-*，圆角用 --radius-*，阴影用 --shadow-*；确需保留时，在该行末尾写 /* token-exempt: 具体理由 */。",
  );
  process.exitCode = 1;
} else {
  console.log(
    `Validated ${String(files.length)} runtime stylesheets; ${String(exemptionCount)} documented token exemption(s).`,
  );
}

async function collectStylesheets(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const filesInDirectory = await Promise.all(
    entries.map(async (entry) => {
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) return collectStylesheets(path);
      return entry.isFile() && entry.name.endsWith(".wxss") ? [path] : [];
    }),
  );
  return filesInDirectory.flat();
}

function stripBlockComments(line, startsInsideComment) {
  let code = "";
  let cursor = 0;
  let insideComment = startsInsideComment;

  while (cursor < line.length) {
    if (insideComment) {
      const commentEnd = line.indexOf("*/", cursor);
      if (commentEnd < 0) return { code, insideComment: true };
      insideComment = false;
      cursor = commentEnd + 2;
      continue;
    }

    const commentStart = line.indexOf("/*", cursor);
    if (commentStart < 0) {
      code += line.slice(cursor);
      break;
    }
    code += line.slice(cursor, commentStart);
    insideComment = true;
    cursor = commentStart + 2;
  }

  return { code, insideComment };
}
