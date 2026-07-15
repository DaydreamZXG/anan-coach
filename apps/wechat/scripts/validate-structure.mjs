import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const miniprogramRoot = resolve(root, "miniprogram");
const appConfig = readJson(resolve(miniprogramRoot, "app.json"));
const routeManifest = readJson(resolve(miniprogramRoot, "app/navigation/routes.manifest.json"));
const failures = [];

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function walkFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? walkFiles(path) : [path];
  });
}

function normalized(value) {
  return JSON.stringify(value);
}

function assertExists(path, message) {
  if (!existsSync(path)) failures.push(`${message}: ${path}`);
}

for (const directory of [
  "api",
  "api/generated",
  "app/navigation",
  "design-system/tokens",
  "design-system/primitives",
  "design-system/companion",
  "domain",
  "pages",
  "task-runtime",
]) {
  assertExists(resolve(miniprogramRoot, directory), "Missing architecture directory");
}

if (existsSync(resolve(miniprogramRoot, "styles"))) {
  failures.push("Legacy miniprogram/styles must not return; use design-system instead");
}

if (normalized(appConfig.pages) !== normalized(routeManifest.registeredPages)) {
  failures.push("app.json pages must exactly match app/navigation/routes.manifest.json");
}

if (normalized(appConfig.tabBar.list) !== normalized(routeManifest.tabs)) {
  failures.push("app.json tabBar must exactly match app/navigation/routes.manifest.json");
}

if (routeManifest.entry !== appConfig.pages[0]) {
  failures.push("The route manifest entry must be the first page in app.json");
}

for (const page of appConfig.pages) {
  for (const extension of ["ts", "json", "wxml", "wxss"]) {
    assertExists(resolve(miniprogramRoot, `${page}.${extension}`), "Missing page artifact");
  }

  const pageConfig = readJson(resolve(miniprogramRoot, `${page}.json`));
  for (const [componentName, componentPath] of Object.entries(pageConfig.usingComponents ?? {})) {
    const componentRoot = resolve(
      miniprogramRoot,
      page.slice(0, page.lastIndexOf("/")),
      componentPath,
    );
    for (const extension of ["ts", "json", "wxml", "wxss"]) {
      assertExists(
        `${componentRoot}.${extension}`,
        `Page component ${componentName} is incomplete`,
      );
    }
  }
}

for (const tab of appConfig.tabBar.list) {
  if (!appConfig.pages.includes(tab.pagePath)) {
    failures.push(`Tab page is not registered: ${tab.pagePath}`);
  }
}

const componentConfigPaths = walkFiles(miniprogramRoot).filter(
  (path) => path.endsWith("index.json") && readJson(path).component === true,
);
for (const configPath of componentConfigPaths) {
  const componentRoot = configPath.slice(0, -".json".length);
  for (const extension of ["ts", "json", "wxml", "wxss"]) {
    assertExists(`${componentRoot}.${extension}`, "Component artifact is incomplete");
  }

  const componentConfig = readJson(configPath);
  for (const [componentName, componentPath] of Object.entries(
    componentConfig.usingComponents ?? {},
  )) {
    const dependencyRoot = resolve(dirname(configPath), componentPath);
    for (const extension of ["ts", "json", "wxml", "wxss"]) {
      assertExists(
        `${dependencyRoot}.${extension}`,
        `Component dependency ${componentName} is incomplete`,
      );
    }
  }
}

const forbiddenRequestFiles = walkFiles(miniprogramRoot).filter(
  (path) =>
    path.endsWith(".ts") &&
    !path.includes("/api/") &&
    /\bwx\.request\s*\(/u.test(readFileSync(path, "utf8")),
);
for (const path of forbiddenRequestFiles) {
  failures.push(`Direct wx.request is forbidden outside miniprogram/api: ${relative(root, path)}`);
}

const routeRegistry = readFileSync(resolve(miniprogramRoot, "app/navigation/routes.ts"), "utf8");
for (const page of appConfig.pages) {
  if (!routeRegistry.includes(`\"/${page}\"`)) {
    failures.push(`Registered page is missing from typed route registry: ${page}`);
  }
}

const appStyles = readFileSync(resolve(miniprogramRoot, "app.wxss"), "utf8");
for (const requiredImport of [
  "design-system/tokens/index.wxss",
  "design-system/primitives/index.wxss",
]) {
  if (!appStyles.includes(requiredImport)) {
    failures.push(`app.wxss must import ${requiredImport}`);
  }
}

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`);
  process.exit(1);
}

const componentCount = componentConfigPaths.length;
process.stdout.write(
  `Validated ${String(appConfig.pages.length)} pages, ${String(appConfig.tabBar.list.length)} tabs, and ${String(componentCount)} shared components.\n`,
);
