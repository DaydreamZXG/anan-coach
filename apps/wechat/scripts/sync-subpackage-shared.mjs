import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, extname, relative, resolve } from "node:path";
import { format, resolveConfig } from "prettier";

import {
  subpackageSharedDestination,
  subpackageSharedFiles,
} from "./subpackage-shared-manifest.mjs";

const projectRoot = resolve(import.meta.dirname, "..");
const appRoot = resolve(projectRoot, "miniprogram");
const neutralRoot = resolve(projectRoot, "subpackage-shared-src");
const generatedOwnedRoots = [
  "pages/practice/shared/task-runtime",
  "pages/mock-exam/shared/task-runtime",
  "pages/reflection/shared/task-runtime",
  "pages/essay-task/shared/task-runtime",
];
const prettierOptions = (await resolveConfig(appRoot)) ?? {};
const destinationToLogicalSource = new Map();
const sourcePathToLogicalSource = new Map();
const logicalSourceToEntry = new Map(subpackageSharedFiles.map((entry) => [entry.path, entry]));

for (const entry of subpackageSharedFiles) {
  sourcePathToLogicalSource.set(stripKnownExtension(entry.source), entry.path);
  if (/\/index\.(?:ts|js)$/u.test(entry.source)) {
    sourcePathToLogicalSource.set(entry.source.replace(/\/index\.(?:ts|js)$/u, ""), entry.path);
  }
  for (const packageName of entry.packages) {
    const destination = subpackageSharedDestination(entry.path, packageName);
    destinationToLogicalSource.set(stripKnownExtension(`miniprogram/${destination}`), entry.path);
    if (/\/index\.(?:ts|js)$/u.test(destination)) {
      destinationToLogicalSource.set(
        `miniprogram/${destination.replace(/\/index\.(?:ts|js)$/u, "")}`,
        entry.path,
      );
    }
  }
}

export function generatedTargetPlan(entries = subpackageSharedFiles) {
  return entries.flatMap((entry) =>
    entry.packages.map((packageName, packageIndex) => ({
      entry,
      packageName,
      packageIndex,
      targetRelative: subpackageSharedDestination(entry.path, packageName),
    })),
  );
}

export function generatedTargetIsStale(expected, actual) {
  return expected !== actual;
}

export function subpackageSharedPlanViolations(
  entries = subpackageSharedFiles,
  {
    neutralFiles = listFiles(neutralRoot, "subpackage-shared-src"),
    generatedOwnedTargets = generatedOwnedRoots.flatMap((root) =>
      listFiles(resolve(appRoot, root), root),
    ),
  } = {},
) {
  const violations = [];
  const logicalPaths = new Set();
  const sourcePaths = new Set();
  const destinations = new Set();

  for (const entry of entries) {
    recordUnique(violations, logicalPaths, "logical path", entry.path);
    recordUnique(violations, sourcePaths, "neutral source", entry.source);
    if (!Array.isArray(entry.packages) || entry.packages.length === 0) {
      violations.push(`Manifest entry has no packages: ${entry.path}`);
      continue;
    }
    const packages = new Set();
    for (const packageName of entry.packages) {
      recordUnique(violations, packages, `package for ${entry.path}`, packageName);
      recordUnique(
        violations,
        destinations,
        "generated target",
        subpackageSharedDestination(entry.path, packageName),
      );
    }
  }

  for (const neutralFile of neutralFiles) {
    if (!sourcePaths.has(neutralFile)) {
      violations.push(`Unclaimed neutral source: ${neutralFile}`);
    }
  }
  const plannedTargets = new Set(
    generatedTargetPlan(entries).map(({ targetRelative }) => targetRelative),
  );
  for (const target of generatedOwnedTargets) {
    if (!plannedTargets.has(target)) {
      violations.push(`Unclaimed generated target: ${target}`);
    }
  }

  return [...new Set(violations)].sort();
}

export async function expectedGeneratedTarget(entry, packageName) {
  const sourcePath = resolve(projectRoot, entry.source);
  let source;
  try {
    source = readFileSync(sourcePath, "utf8");
  } catch {
    throw new Error(`Neutral subpackage source is missing: ${entry.source}`);
  }
  const targetRelative = subpackageSharedDestination(entry.path, packageName);
  const targetProjectPath = `miniprogram/${targetRelative}`;
  return formatTarget(
    rewriteNeutralReferences(source, entry.source, targetProjectPath, packageName),
    targetProjectPath,
  );
}

export async function runSubpackageSharedSync({ checkOnly = false } = {}) {
  const mismatches = [];
  const plan = generatedTargetPlan();
  const crossPackageReplicaCount = plan.filter(({ packageIndex }) => packageIndex > 0).length;
  const planViolations = subpackageSharedPlanViolations();
  if (planViolations.length > 0) {
    process.stderr.write(
      `Subpackage shared manifest is invalid:\n${planViolations
        .map((violation) => `- ${violation}`)
        .join("\n")}\n`,
    );
    process.exitCode = 1;
    return {
      mismatches,
      planViolations,
      targetCount: plan.length,
      crossPackageReplicaCount,
    };
  }
  for (const { entry, packageName, targetRelative } of plan) {
    const expected = await expectedGeneratedTarget(entry, packageName);
    const targetPath = resolve(appRoot, targetRelative);
    let actual = "";
    try {
      actual = readFileSync(targetPath, "utf8");
    } catch {
      // A missing generated target is reported below or recreated in write mode.
    }
    if (actual !== expected) {
      if (checkOnly) mismatches.push(targetRelative);
      else {
        mkdirSync(dirname(targetPath), { recursive: true });
        writeFileSync(targetPath, expected);
      }
    }
  }

  if (mismatches.length > 0) {
    process.stderr.write(
      `Subpackage generated targets are stale:\n${mismatches.map((path) => `- ${path}`).join("\n")}\n` +
        "Run npm run subpackage-shared:sync.\n",
    );
    process.exitCode = 1;
    return { mismatches, targetCount: plan.length, crossPackageReplicaCount };
  }

  process.stdout.write(
    `${checkOnly ? "Validated" : "Synchronized"} ${String(plan.length)} generated targets ` +
      `(including ${String(crossPackageReplicaCount)} cross-package replicas).\n`,
  );
  return { mismatches, planViolations, targetCount: plan.length, crossPackageReplicaCount };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await runSubpackageSharedSync({ checkOnly: process.argv.includes("--check") });
}

async function formatTarget(content, targetProjectPath) {
  return [".ts", ".js"].includes(extname(targetProjectPath))
    ? format(content, {
        ...prettierOptions,
        filepath: resolve(projectRoot, targetProjectPath),
      })
    : content;
}

function rewriteNeutralReferences(content, sourceProjectPath, targetProjectPath, packageName) {
  return content.replace(/(["'])(\.{1,2}\/[^"'\s]+)\1/gu, (full, quote, rawSpecifier) => {
    const queryIndex = rawSpecifier.indexOf("?");
    const pathPart = queryIndex >= 0 ? rawSpecifier.slice(0, queryIndex) : rawSpecifier;
    const query = queryIndex >= 0 ? rawSpecifier.slice(queryIndex) : "";
    const resolvedSource = normalize(`${dirname(sourceProjectPath)}/${pathPart}`);
    const logicalSource = resolveLogicalSource(resolvedSource, sourcePathToLogicalSource);
    const targetEntry = logicalSource ? logicalSourceToEntry.get(logicalSource) : undefined;
    const targetPackage = targetEntry?.packages.includes(packageName)
      ? packageName
      : targetEntry?.packages[0];
    let resolvedTarget =
      logicalSource && targetPackage
        ? `miniprogram/${subpackageSharedDestination(logicalSource, targetPackage)}`
        : resolvedSource;
    if (!hasKnownExtension(pathPart)) resolvedTarget = stripKnownExtension(resolvedTarget);
    let rewritten = relative(dirname(targetProjectPath), resolvedTarget).replaceAll("\\", "/");
    if (!rewritten.startsWith(".")) rewritten = `./${rewritten}`;
    return `${quote}${rewritten}${query}${quote}`;
  });
}

function resolveLogicalSource(path, lookup = destinationToLogicalSource) {
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
    const source = lookup.get(candidate);
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

function listFiles(root, projectPrefix) {
  try {
    return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
      const projectPath = `${projectPrefix}/${entry.name}`;
      return entry.isDirectory()
        ? listFiles(resolve(root, entry.name), projectPath)
        : [projectPath];
    });
  } catch {
    return [];
  }
}

function recordUnique(violations, seen, label, value) {
  if (seen.has(value)) violations.push(`Duplicate ${label}: ${value}`);
  else seen.add(value);
}
