import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, extname, relative, resolve } from "node:path";
import { format, resolveConfig } from "prettier";

import {
  subpackageSharedDestination,
  subpackageSharedFiles,
} from "./subpackage-shared-manifest.mjs";

const appRoot = resolve(import.meta.dirname, "..", "miniprogram");
const prettierOptions = (await resolveConfig(appRoot)) ?? {};
const checkOnly = process.argv.includes("--check");
const destinationToSource = new Map();
const sourceToPackages = new Map(
  subpackageSharedFiles.map((entry) => [entry.path, entry.packages]),
);

for (const entry of subpackageSharedFiles) {
  for (const packageName of entry.packages) {
    const destination = subpackageSharedDestination(entry.path, packageName);
    destinationToSource.set(stripKnownExtension(destination), entry.path);
    if (/\/index\.(?:ts|js)$/u.test(destination)) {
      destinationToSource.set(destination.replace(/\/index\.(?:ts|js)$/u, ""), entry.path);
    }
  }
}

const mismatches = [];
let replicaCount = 0;
for (const entry of subpackageSharedFiles) {
  if (entry.packages.length < 2) continue;
  const canonicalPackage = entry.packages[0];
  const canonicalRelative = subpackageSharedDestination(entry.path, canonicalPackage);
  const canonical = readFileSync(resolve(appRoot, canonicalRelative), "utf8");
  for (const packageName of entry.packages.slice(1)) {
    const replicaRelative = subpackageSharedDestination(entry.path, packageName);
    const expected = await formatReplica(
      rewriteRelativeReferences(canonical, canonicalRelative, replicaRelative, packageName),
      replicaRelative,
    );
    const replicaPath = resolve(appRoot, replicaRelative);
    let actual = "";
    try {
      actual = readFileSync(replicaPath, "utf8");
    } catch {
      // A missing generated file is reported below or recreated in write mode.
    }
    if (actual !== expected) {
      if (checkOnly) mismatches.push(replicaRelative);
      else {
        mkdirSync(dirname(replicaPath), { recursive: true });
        writeFileSync(replicaPath, expected);
      }
    }
    replicaCount += 1;
  }
}

if (mismatches.length > 0) {
  process.stderr.write(
    `Subpackage shared replicas are stale:\n${mismatches.map((path) => `- ${path}`).join("\n")}\n` +
      "Run npm run subpackage-shared:sync.\n",
  );
  process.exit(1);
}

process.stdout.write(
  `${checkOnly ? "Validated" : "Synchronized"} ${String(replicaCount)} subpackage shared replicas.\n`,
);

async function formatReplica(content, replicaPath) {
  return [".ts", ".js"].includes(extname(replicaPath))
    ? format(content, {
        ...prettierOptions,
        filepath: resolve(appRoot, replicaPath),
      })
    : content;
}

function rewriteRelativeReferences(content, sourcePath, targetPath, packageName) {
  return content.replace(/(["'])(\.{1,2}\/[^"'\s]+)\1/gu, (full, quote, rawSpecifier) => {
    const queryIndex = rawSpecifier.indexOf("?");
    const pathPart = queryIndex >= 0 ? rawSpecifier.slice(0, queryIndex) : rawSpecifier;
    const query = queryIndex >= 0 ? rawSpecifier.slice(queryIndex) : "";
    const resolved = normalize(`${dirname(sourcePath)}/${pathPart}`);
    const logicalSource = resolveLogicalSource(resolved);
    const targetPackages = logicalSource ? sourceToPackages.get(logicalSource) : undefined;
    const targetPackage = targetPackages?.includes(packageName) ? packageName : targetPackages?.[0];
    let resolvedTarget =
      logicalSource && targetPackage
        ? subpackageSharedDestination(logicalSource, targetPackage)
        : resolved;
    if (!hasKnownExtension(pathPart)) resolvedTarget = stripKnownExtension(resolvedTarget);
    let rewritten = relative(dirname(targetPath), resolvedTarget).replaceAll("\\", "/");
    if (!rewritten.startsWith(".")) rewritten = `./${rewritten}`;
    return `${quote}${rewritten}${query}${quote}`;
  });
}

function resolveLogicalSource(path) {
  for (const candidate of [
    path,
    stripKnownExtension(path),
    `${path}.ts`,
    `${path}.js`,
    `${path}.json`,
    `${path}.wxml`,
    `${path}.wxss`,
    `${path}/index.ts`,
    `${path}/index.js`,
  ]) {
    const source = destinationToSource.get(candidate);
    if (source) return source;
  }
  return null;
}

function normalize(path) {
  const parts = [];
  for (const part of path.replaceAll("\\", "/").split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return parts.join("/");
}

function stripKnownExtension(path) {
  return path.replace(/\.(?:ts|js|json|wxml|wxss)$/u, "");
}

function hasKnownExtension(path) {
  return [".ts", ".js", ".json", ".wxml", ".wxss"].includes(extname(path));
}
