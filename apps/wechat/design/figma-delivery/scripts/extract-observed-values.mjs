#!/usr/bin/env node

import { readdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const deliveryRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const contextsRoot = resolve(deliveryRoot, "contexts");

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await filesUnder(path)));
    if (entry.isFile() && entry.name.endsWith(".tsx")) files.push(path);
  }
  return files;
}

function countedMatches(text, pattern, normalize = (value) => value) {
  const counts = new Map();
  for (const match of text.matchAll(pattern)) {
    const value = normalize(match[1] ?? match[0]);
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort(
      (left, right) =>
        right[1] - left[1] || String(left[0]).localeCompare(String(right[0])),
    )
    .map(([value, count]) => ({ value, count }));
}

async function main() {
  const contextFiles = await filesUnder(contextsRoot);
  const context = (
    await Promise.all(contextFiles.map((path) => readFile(path, "utf8")))
  ).join("\n");

  const observed = {
    schemaVersion: 1,
    generatedFrom: {
      screenCount: contextFiles.length,
      source: "contexts/**/*.tsx",
    },
    colors: countedMatches(context, /(#[0-9a-fA-F]{6,8})\b/g, (value) =>
      value.toLowerCase(),
    ),
    rgba: countedMatches(context, /(rgba\([^)]*\))/g),
    fontSizesPx: countedMatches(context, /text-\[([0-9.]+)px\]/g, Number),
    radiiPx: countedMatches(context, /rounded-\[([0-9.]+)px\]/g, Number),
    shadows: countedMatches(context, /shadow-\[([^\]]+)\]/g),
    gradients: countedMatches(context, /(linear-gradient\([^"]+\))/g),
  };

  await writeFile(
    resolve(deliveryRoot, "tokens", "observed-values.json"),
    `${JSON.stringify(observed, null, 2)}\n`,
  );
  process.stdout.write(
    `Observed values written from ${contextFiles.length} Figma screen contexts.\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
});
