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

const files = (await Promise.all(scanRoots.map((directory) => collectStylesheets(directory))))
  .flat()
  .sort();
const failures = [];
let exemptionCount = 0;

for (const file of files) {
  const source = await readFile(file, "utf8");
  const lines = source.split(/\r?\n/u);
  let insideComment = false;

  for (const [index, line] of lines.entries()) {
    const exemption = !insideComment ? line.match(exemptionPattern) : null;
    const stripped = stripBlockComments(line, insideComment);
    insideComment = stripped.insideComment;
    const colors = stripped.code.match(hexColorPattern) ?? [];

    if (exemption && colors.length === 0) {
      failures.push(
        `${relative(projectRoot, file)}:${String(index + 1)} 的 token-exempt 没有对应色值`,
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
  }
}

if (failures.length > 0) {
  console.error("Token 校验失败：页面样式不得直接写十六进制颜色。");
  for (const failure of failures) console.error(`- ${failure}`);
  console.error("请改用设计令牌；确需保留时，在该行末尾写 /* token-exempt: 具体理由 */。");
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
