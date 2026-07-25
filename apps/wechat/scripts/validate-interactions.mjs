import { readdir, readFile } from "node:fs/promises";
import { relative, resolve } from "node:path";

const projectRoot = resolve(import.meta.dirname, "..");
const templateRoot = resolve(projectRoot, "miniprogram");
const templates = (await collectTemplates(templateRoot)).sort();
const failures = [];
let buttonCount = 0;

for (const file of templates) {
  const source = await readFile(file, "utf8");
  for (const tag of findStartTags(source, "button")) {
    buttonCount += 1;
    if (/\bhover-class\s*=\s*["'][^"']+["']/u.test(tag.source)) continue;
    failures.push(`${relative(projectRoot, file)}:${String(tag.line)} 缺少 hover-class`);
  }
}

if (failures.length > 0) {
  console.error("交互反馈校验失败：真实按钮必须声明按压反馈。");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(
    `Validated ${String(buttonCount)} buttons across ${String(templates.length)} templates.`,
  );
}

async function collectTemplates(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(async (entry) => {
      const target = resolve(directory, entry.name);
      if (entry.isDirectory()) return collectTemplates(target);
      return entry.isFile() && entry.name.endsWith(".wxml") ? [target] : [];
    }),
  );
  return nested.flat();
}

function findStartTags(source, tagName) {
  const prefix = `<${tagName}`;
  const tags = [];
  let cursor = 0;

  while ((cursor = source.indexOf(prefix, cursor)) >= 0) {
    const next = source[cursor + prefix.length];
    if (next && !/[\s>]/u.test(next)) {
      cursor += prefix.length;
      continue;
    }
    let quote = "";
    let end = cursor + prefix.length;
    for (; end < source.length; end += 1) {
      const character = source[end];
      if (quote) {
        if (character === quote) quote = "";
      } else if (character === '"' || character === "'") {
        quote = character;
      } else if (character === ">") {
        break;
      }
    }
    if (end >= source.length) {
      throw new Error(`${tagName} tag at offset ${String(cursor)} is not closed`);
    }
    tags.push({
      source: source.slice(cursor, end + 1),
      line: source.slice(0, cursor).split(/\r?\n/u).length,
    });
    cursor = end + 1;
  }
  return tags;
}
