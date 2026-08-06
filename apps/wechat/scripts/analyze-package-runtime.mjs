import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, relative, resolve, sep } from "node:path";
import ts from "typescript";

const appRoot = resolve(import.meta.dirname, "..", "miniprogram");
const appConfig = JSON.parse(readFileSync(resolve(appRoot, "app.json"), "utf8"));
const codeFiles = allFiles(appRoot).filter((path) => [".ts", ".js"].includes(extname(path)));
const codeSet = new Set(codeFiles);
const subpackages = (appConfig.subPackages ?? appConfig.subpackages ?? []).map((entry) => ({
  name: entry.name ?? entry.root,
  root: entry.root,
  absoluteRoot: resolve(appRoot, entry.root),
  entries: entry.pages.map((page) => resolveCode(resolve(appRoot, entry.root, page))),
}));
const edges = new Map(codeFiles.map((path) => [path, runtimeDependencies(path)]));

for (const jsonPath of allFiles(appRoot).filter((path) => extname(path) === ".json")) {
  if (jsonPath === resolve(appRoot, "app.json")) continue;
  const ownerCode = resolveCode(jsonPath.slice(0, -5));
  if (!ownerCode) continue;
  const config = JSON.parse(readFileSync(jsonPath, "utf8"));
  for (const componentPath of Object.values(config.usingComponents ?? {})) {
    if (typeof componentPath !== "string" || componentPath.startsWith("plugin://")) continue;
    const component = componentPath.startsWith("/")
      ? resolveCode(resolve(appRoot, componentPath.slice(1)))
      : resolveCode(resolve(dirname(jsonPath), componentPath));
    if (component) edges.get(ownerCode)?.add(component);
  }
}

const mainEntries = [
  resolveCode(resolve(appRoot, "app")),
  ...appConfig.pages.map((page) => resolveCode(resolve(appRoot, page))),
  resolveCode(resolve(appRoot, "custom-tab-bar", "index")),
].filter(Boolean);
const reachability = new Map([["main", reachableFrom(mainEntries)]]);
for (const subpackage of subpackages) {
  reachability.set(subpackage.name, reachableFrom(subpackage.entries.filter(Boolean)));
}

const findings = codeFiles
  .filter((path) => packageOwner(path) === "main" && !reachability.get("main").has(path))
  .map((path) => ({
    path: relative(appRoot, path),
    bytes: statSync(path).size,
    usedBy: subpackages
      .filter((subpackage) => reachability.get(subpackage.name).has(path))
      .map((subpackage) => subpackage.name),
  }))
  .sort((left, right) => right.bytes - left.bytes || left.path.localeCompare(right.path));

const usedBySubpackages = findings.filter((finding) => finding.usedBy.length > 0);
const unreferenced = findings.filter((finding) => finding.usedBy.length === 0);
const crossPackageRuntime = subpackages.flatMap((subpackage) =>
  [...reachability.get(subpackage.name)]
    .map((path) => ({ path, owner: packageOwner(path) }))
    .filter(({ owner }) => owner !== "main" && owner !== subpackage.name)
    .map(({ path, owner }) => ({
      from: subpackage.name,
      to: owner,
      path: relative(appRoot, path),
    })),
);
if (process.argv.includes("--check")) {
  if (usedBySubpackages.length > 0) {
    process.stderr.write(
      `Main-package runtime files are reachable only from subpackages:\n${usedBySubpackages
        .map((finding) => `- ${finding.path} (${finding.usedBy.join(", ")})`)
        .join("\n")}\n`,
    );
    process.exit(1);
  }
  if (crossPackageRuntime.length > 0) {
    process.stderr.write(
      `Subpackages contain forbidden runtime imports into other subpackages:\n${crossPackageRuntime
        .map((finding) => `- ${finding.from} -> ${finding.to}: ${finding.path}`)
        .join("\n")}\n`,
    );
    process.exit(1);
  }
  process.stdout.write(
    "Validated runtime package placement: 0 subpackage-only files in main, 0 cross-subpackage imports.\n",
  );
  process.exit(0);
}
process.stdout.write(
  `${JSON.stringify(
    {
      summary: {
        mainUnreachableBytes: findings.reduce((total, finding) => total + finding.bytes, 0),
        subpackageOnlyBytes: usedBySubpackages.reduce((total, finding) => total + finding.bytes, 0),
        unreferencedBytes: unreferenced.reduce((total, finding) => total + finding.bytes, 0),
      },
      subpackageOnly: usedBySubpackages,
      unreferenced,
    },
    null,
    2,
  )}\n`,
);

function runtimeDependencies(path) {
  const sourceText = readFileSync(path, "utf8");
  const sourceFile = ts.createSourceFile(path, sourceText, ts.ScriptTarget.Latest, true);
  const dependencies = new Set();
  const add = (specifier) => {
    if (!specifier.startsWith(".")) return;
    const resolved = resolveCode(resolve(dirname(path), specifier));
    if (resolved) dependencies.add(resolved);
  };
  const visit = (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.importClause;
      const named = clause?.namedBindings;
      const hasRuntimeBinding =
        !clause ||
        (!clause.isTypeOnly &&
          (Boolean(clause.name) ||
            ts.isNamespaceImport(named) ||
            (ts.isNamedImports(named) && named.elements.some((element) => !element.isTypeOnly))));
      if (hasRuntimeBinding) add(node.moduleSpecifier.text);
    } else if (
      ts.isExportDeclaration(node) &&
      !node.isTypeOnly &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      add(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node) &&
      node.arguments.length === 1 &&
      ts.isStringLiteral(node.arguments[0]) &&
      ((ts.isIdentifier(node.expression) && node.expression.text === "require") ||
        node.expression.kind === ts.SyntaxKind.ImportKeyword)
    ) {
      add(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return dependencies;
}

function reachableFrom(entries) {
  const reached = new Set();
  const pending = [...entries];
  while (pending.length > 0) {
    const path = pending.pop();
    if (!path || reached.has(path)) continue;
    reached.add(path);
    for (const dependency of edges.get(path) ?? []) pending.push(dependency);
  }
  return reached;
}

function resolveCode(path) {
  const extension = extname(path);
  const base = extension ? path.slice(0, -extension.length) : path;
  for (const candidate of [
    path,
    `${base}.ts`,
    `${base}.js`,
    resolve(path, "index.ts"),
    resolve(path, "index.js"),
  ]) {
    if (existsSync(candidate) && statSync(candidate).isFile() && codeSet.has(candidate))
      return candidate;
  }
  return null;
}

function packageOwner(path) {
  return (
    subpackages
      .filter(
        ({ absoluteRoot }) => path === absoluteRoot || path.startsWith(`${absoluteRoot}${sep}`),
      )
      .sort((left, right) => right.absoluteRoot.length - left.absoluteRoot.length)[0]?.name ??
    "main"
  );
}

function allFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? allFiles(path) : [path];
  });
}
