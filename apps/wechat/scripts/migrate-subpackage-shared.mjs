import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, extname, relative, resolve, sep } from "node:path";

import {
  subpackageSharedDestination,
  subpackageSharedFiles,
} from "./subpackage-shared-manifest.mjs";

const appRoot = resolve(import.meta.dirname, "..", "miniprogram");
const appConfig = JSON.parse(readFileSync(resolve(appRoot, "app.json"), "utf8"));
const packages = (appConfig.subPackages ?? appConfig.subpackages ?? []).map((entry) => ({
  name: entry.name ?? entry.root,
  root: entry.root,
}));
const migratable = subpackageSharedFiles
  .map((entry) => ({
    ...entry,
    destination: subpackageSharedDestination(entry.path, entry.packages[0]),
  }))
  .filter((entry) => existsSync(resolve(appRoot, entry.path)));

const movingBySource = new Map(migratable.map((entry) => [entry.path, entry]));
const textExtensions = new Set([".ts", ".js", ".json", ".wxml", ".wxss"]);
const projectFiles = allFiles(appRoot).filter((path) => textExtensions.has(extname(path)));

for (const file of projectFiles.filter((path) => extname(path) === ".ts")) {
  const sourceRelative = relative(appRoot, file).replaceAll(sep, "/");
  const source = readFileSync(file, "utf8");
  const generatedTypes = relative(dirname(sourceRelative), "api/generated/types.gen").replaceAll(
    sep,
    "/",
  );
  const normalizedGeneratedTypes = generatedTypes.startsWith(".")
    ? generatedTypes
    : `./${generatedTypes}`;
  const rewritten = source.replace(
    /(["'])(\.{1,2}\/[^"'\s]*generated\/types\.gen)\1/gu,
    `$1${normalizedGeneratedTypes}$1`,
  );
  if (rewritten !== source) writeFileSync(file, rewritten);
}

const testsRoot = resolve(appRoot, "..", "tests");
for (const file of allFiles(testsRoot).filter((path) => textExtensions.has(extname(path)))) {
  let source = readFileSync(file, "utf8");
  for (const entry of subpackageSharedFiles) {
    const oldPath = `miniprogram/${stripKnownExtension(entry.path)}`;
    const newPath = `miniprogram/${stripKnownExtension(
      subpackageSharedDestination(entry.path, entry.packages[0]),
    )}`;
    source = source.replaceAll(oldPath, newPath);
  }
  writeFileSync(file, source);
}

if (migratable.length === 0) {
  process.stdout.write("Updated shared-package references; no files need migration.\n");
  process.exit(0);
}

for (const file of projectFiles) {
  const sourceRelative = relative(appRoot, file).replaceAll(sep, "/");
  const futureSource = movingBySource.get(sourceRelative)?.destination ?? sourceRelative;
  const source = readFileSync(file, "utf8");
  const rewritten = source.replace(/(["'])(\.{1,2}\/[^"'\s]+)\1/gu, (full, quote, rawSpecifier) => {
    const queryIndex = rawSpecifier.indexOf("?");
    const pathPart = queryIndex >= 0 ? rawSpecifier.slice(0, queryIndex) : rawSpecifier;
    const query = queryIndex >= 0 ? rawSpecifier.slice(queryIndex) : "";
    const target = resolveProjectFile(resolve(appRoot, dirname(sourceRelative), pathPart));
    if (!target) return full;
    const targetRelative = relative(appRoot, target).replaceAll(sep, "/");
    const movingTarget = movingBySource.get(targetRelative);
    let futureTarget = targetRelative;
    if (movingTarget) {
      const owner = packageOwner(futureSource);
      const targetPackage =
        owner && movingTarget.packages.includes(owner) ? owner : movingTarget.packages[0];
      futureTarget = subpackageSharedDestination(movingTarget.path, targetPackage);
    }
    if (!textExtensions.has(extname(pathPart))) {
      futureTarget = futureTarget.replace(/\.(?:ts|js|json|wxml|wxss)$/u, "");
    }
    let nextSpecifier = relative(dirname(futureSource), futureTarget).replaceAll(sep, "/");
    if (!nextSpecifier.startsWith(".")) nextSpecifier = `./${nextSpecifier}`;
    return `${quote}${nextSpecifier}${query}${quote}`;
  });
  if (rewritten !== source) writeFileSync(file, rewritten);
}

for (const entry of migratable) {
  const source = resolve(appRoot, entry.path);
  const destination = resolve(appRoot, entry.destination);
  if (existsSync(destination)) {
    throw new Error(`Destination already exists: ${entry.destination}`);
  }
  mkdirSync(dirname(destination), { recursive: true });
  renameSync(source, destination);
}

process.stdout.write(
  `Migrated ${String(migratable.length)} shared files out of the main package.\n`,
);

function packageOwner(path) {
  return packages
    .filter(({ root }) => path === root || path.startsWith(`${root}/`))
    .sort((left, right) => right.root.length - left.root.length)[0]?.name;
}

function resolveProjectFile(path) {
  const extension = extname(path);
  const base = extension ? path.slice(0, -extension.length) : path;
  for (const candidate of [
    path,
    `${path}.ts`,
    `${path}.js`,
    `${path}.json`,
    `${path}.wxml`,
    `${path}.wxss`,
    `${base}.ts`,
    `${base}.js`,
    `${base}.json`,
    `${base}.wxml`,
    `${base}.wxss`,
    resolve(path, "index.ts"),
    resolve(path, "index.js"),
  ]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function stripKnownExtension(path) {
  return path.replace(/\.(?:ts|js|json|wxml|wxss)$/u, "");
}

function allFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? allFiles(path) : [path];
  });
}
