import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, extname, relative, resolve } from "node:path";
import { format, resolveConfig } from "prettier";

import {
  subpackageSharedDestination,
  subpackageSharedFiles,
} from "./subpackage-shared-manifest.mjs";

const projectRoot = resolve(import.meta.dirname, "..");
const appRoot = resolve(projectRoot, "miniprogram");
const prettierOptions = (await resolveConfig(appRoot)) ?? {};
const destinationToLogicalSource = new Map();
const logicalSourceToEntry = new Map(subpackageSharedFiles.map((entry) => [entry.path, entry]));

for (const entry of subpackageSharedFiles) {
  for (const packageName of entry.packages) {
    const destination = `miniprogram/${subpackageSharedDestination(entry.path, packageName)}`;
    destinationToLogicalSource.set(stripKnownExtension(destination), entry.path);
    if (/\/index\.(?:ts|js)$/u.test(destination)) {
      destinationToLogicalSource.set(destination.replace(/\/index\.(?:ts|js)$/u, ""), entry.path);
    }
  }
}

let created = 0;
let existing = 0;
for (const entry of subpackageSharedFiles) {
  const neutralPath = resolve(projectRoot, entry.source);
  if (existsSync(neutralPath)) {
    existing += 1;
    continue;
  }

  const normalizedTargets = [];
  for (const packageName of entry.packages) {
    const destination = subpackageSharedDestination(entry.path, packageName);
    const targetProjectPath = `miniprogram/${destination}`;
    const target = readFileSync(resolve(appRoot, destination), "utf8");
    normalizedTargets.push(
      await formatNeutral(
        rewriteTargetReferences(target, targetProjectPath, entry.source),
        entry.source,
      ),
    );
  }
  const [neutral, ...otherTargets] = normalizedTargets;
  if (neutral === undefined) throw new Error(`Shared entry has no targets: ${entry.path}`);
  if (otherTargets.some((candidate) => candidate !== neutral)) {
    throw new Error(`Existing package targets disagree before neutral migration: ${entry.path}`);
  }
  mkdirSync(dirname(neutralPath), { recursive: true });
  writeFileSync(neutralPath, neutral);
  created += 1;
}

process.stdout.write(
  `Created ${String(created)} neutral shared sources; ${String(existing)} already existed.\n`,
);

async function formatNeutral(content, sourceProjectPath) {
  return [".ts", ".js"].includes(extname(sourceProjectPath))
    ? format(content, {
        ...prettierOptions,
        filepath: resolve(projectRoot, sourceProjectPath),
      })
    : content;
}

function rewriteTargetReferences(content, targetProjectPath, sourceProjectPath) {
  return content.replace(/(["'])(\.{1,2}\/[^"'\s]+)\1/gu, (full, quote, rawSpecifier) => {
    const queryIndex = rawSpecifier.indexOf("?");
    const pathPart = queryIndex >= 0 ? rawSpecifier.slice(0, queryIndex) : rawSpecifier;
    const query = queryIndex >= 0 ? rawSpecifier.slice(queryIndex) : "";
    const resolvedTarget = normalize(`${dirname(targetProjectPath)}/${pathPart}`);
    const logicalSource = resolveLogicalSource(resolvedTarget);
    const targetEntry = logicalSource ? logicalSourceToEntry.get(logicalSource) : undefined;
    let resolvedSource = targetEntry?.source ?? resolvedTarget;
    if (!hasKnownExtension(pathPart)) resolvedSource = stripKnownExtension(resolvedSource);
    let rewritten = relative(dirname(sourceProjectPath), resolvedSource).replaceAll("\\", "/");
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
    const source = destinationToLogicalSource.get(candidate);
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
  return /\.(?:ts|js|json|wxml|wxss)$/u.test(path);
}
