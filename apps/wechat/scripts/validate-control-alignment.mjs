import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

import postcss from "postcss";

const projectRoot = path.resolve(import.meta.dirname, "..");
const runtimeRoot = path.join(projectRoot, "miniprogram");
const styleFiles = await collectFiles(runtimeRoot, ".wxss");
const templateFiles = await collectFiles(runtimeRoot, ".wxml");
const classDeclarations = await readClassDeclarations(styleFiles);
const failures = [];
let fixedHeightFlatControls = 0;

for (const file of templateFiles) {
  const source = await readFile(file, "utf8");
  for (const button of pairedButtonTags(source)) {
    if (isStructuredContent(button.body)) continue;
    const classValue = attributeValue(button.startTag, "class");
    if (!classValue) continue;
    const classNames = classValue.split(/\s+/u).filter((value) => value && !value.includes("{{"));
    const declarations = Object.assign(
      {},
      ...classNames.map((className) => classDeclarations.get(className) ?? {}),
    );
    const fixedHeight = declarations.height ?? declarations["min-height"];
    if (!fixedHeight) continue;
    fixedHeightFlatControls += 1;
    if (classNames.includes("control-center") || ownsVerticalCentering(declarations, fixedHeight)) {
      continue;
    }
    failures.push(
      `${path.relative(projectRoot, file)}:${String(button.line)} ` +
        `固定高度纯文字按钮缺少 control-center（${classNames.join(" ")}）`,
    );
  }
}

if (failures.length > 0) {
  console.error("按钮文字对齐校验失败：固定高度纯文字控件必须声明垂直居中契约。");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(
    `Validated vertical centering for ${String(fixedHeightFlatControls)} fixed-height flat controls.`,
  );
}

async function collectFiles(directory, extension) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(async (entry) => {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) return collectFiles(target, extension);
      return entry.isFile() && entry.name.endsWith(extension) ? [target] : [];
    }),
  );
  return nested.flat();
}

async function readClassDeclarations(files) {
  const index = new Map();
  for (const file of files) {
    const root = postcss.parse(await readFile(file, "utf8"), { from: file });
    root.walkRules((rule) => {
      const declarations = Object.fromEntries(
        rule.nodes
          .filter((node) => node.type === "decl")
          .map((declaration) => [declaration.prop, declaration.value]),
      );
      for (const selector of rule.selectors ?? [rule.selector]) {
        for (const match of selector.matchAll(/\.([a-zA-Z_][\w-]*)/gu)) {
          const className = match[1];
          index.set(className, { ...(index.get(className) ?? {}), ...declarations });
        }
      }
    });
  }
  return index;
}

function pairedButtonTags(source) {
  const tokenPattern = /<\/?button\b/gu;
  const stack = [];
  const buttons = [];
  for (const match of source.matchAll(tokenPattern)) {
    const closing = source.startsWith("</button", match.index);
    const tagEnd = findTagEnd(source, match.index);
    if (closing) {
      const start = stack.pop();
      if (!start) throw new Error("Encountered an unmatched closing button tag");
      buttons.push({
        startTag: source.slice(start.offset, start.tagEnd + 1),
        body: source.slice(start.tagEnd + 1, match.index),
        line: source.slice(0, start.offset).split(/\r?\n/u).length,
      });
      continue;
    }
    stack.push({ offset: match.index, tagEnd });
  }
  if (stack.length > 0) throw new Error("Encountered an unclosed button tag");
  return buttons;
}

function findTagEnd(source, offset) {
  let quote = "";
  for (let cursor = offset; cursor < source.length; cursor += 1) {
    const character = source[cursor];
    if (quote) {
      if (character === quote) quote = "";
    } else if (character === '"' || character === "'") {
      quote = character;
    } else if (character === ">") {
      return cursor;
    }
  }
  throw new Error(`Button tag at offset ${String(offset)} is not closed`);
}

function attributeValue(tag, name) {
  const match = tag.match(new RegExp(`(?:^|\\s)${name}=(["'])(.*?)\\1`, "u"));
  return match?.[2] ?? "";
}

function isStructuredContent(body) {
  return /<(?:view|image|input|textarea|picker|scroll-view|block|ui-[\w-]+)\b/u.test(body);
}

function ownsVerticalCentering(declarations, fixedHeight) {
  const display = declarations.display ?? "";
  if (/^(?:inline-)?(?:flex|grid)$/u.test(display) && declarations["align-items"] === "center") {
    return true;
  }
  const height = absoluteLength(fixedHeight);
  const lineHeight = absoluteLength(declarations["line-height"]);
  return height !== null && lineHeight !== null && Math.abs(height - lineHeight) < 0.01;
}

function absoluteLength(value) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^(\d+(?:\.\d+)?)(rpx|px)$/u);
  return match ? Number(match[1]) : null;
}
