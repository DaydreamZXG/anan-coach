import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";

import {
  assertRegisteredPageArtifacts,
  assertRegisteredPageComponents,
  routeInventory,
} from "./app-pages.mjs";

const root = resolve(import.meta.dirname, "..");
const miniprogramRoot = resolve(root, "miniprogram");
const appConfig = readJson(resolve(miniprogramRoot, "app.json"));
const routeManifest = readJson(resolve(miniprogramRoot, "app/navigation/routes.manifest.json"));
let inventory;
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
  "features/companion",
  "domain",
  "pages",
  "task-runtime",
]) {
  assertExists(resolve(miniprogramRoot, directory), "Missing architecture directory");
}

if (existsSync(resolve(miniprogramRoot, "styles"))) {
  failures.push("Legacy miniprogram/styles must not return; use design-system instead");
}

if (existsSync(resolve(miniprogramRoot, "data/prototype-data.ts"))) {
  failures.push("Prototype fixtures must stay under tests and never ship in the mini-program");
}

if (/\bdataMode\b/u.test(readFileSync(resolve(miniprogramRoot, "config/environment.ts"), "utf8"))) {
  failures.push("Runtime environment must have one server-backed data path; dataMode is forbidden");
}

try {
  inventory = routeInventory(appConfig, routeManifest);
} catch (error) {
  failures.push(error.message);
}

if (normalized(appConfig.tabBar.list) !== normalized(routeManifest.tabs)) {
  failures.push("app.json tabBar must exactly match app/navigation/routes.manifest.json");
}

if (routeManifest.entry !== appConfig.pages[0]) {
  failures.push("The route manifest entry must be the first page in app.json");
}

if (inventory) {
  try {
    assertRegisteredPageArtifacts(inventory, (route, extension) =>
      existsSync(resolve(miniprogramRoot, `${route}.${extension}`)),
    );
  } catch (error) {
    failures.push(error.message);
  }

  try {
    assertRegisteredPageComponents(inventory, (page) => {
      const pageConfigPath = resolve(miniprogramRoot, `${page}.json`);
      if (!existsSync(pageConfigPath)) return [];
      const pageConfig = readJson(pageConfigPath);
      return Object.entries(pageConfig.usingComponents ?? {}).map(
        ([componentName, componentPath]) => {
          const componentRoot = componentPath.startsWith("/")
            ? resolve(miniprogramRoot, `.${componentPath}`)
            : resolve(miniprogramRoot, page.slice(0, page.lastIndexOf("/")), componentPath);
          return {
            name: componentName,
            root: componentRoot,
            missingExtensions: ["ts", "json", "wxml", "wxss"].filter(
              (extension) => !existsSync(`${componentRoot}.${extension}`),
            ),
          };
        },
      );
    });
  } catch (error) {
    failures.push(error.message);
  }
}

for (const tab of appConfig.tabBar.list) {
  if (!inventory?.businessRoutes.includes(tab.pagePath)) {
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
    const dependencyRoot = componentPath.startsWith("/")
      ? resolve(miniprogramRoot, `.${componentPath}`)
      : resolve(dirname(configPath), componentPath);
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

for (const typeScriptPath of walkFiles(miniprogramRoot).filter((path) => path.endsWith(".ts"))) {
  const shadowJavaScriptPath = typeScriptPath.slice(0, -".ts".length) + ".js";
  if (existsSync(shadowJavaScriptPath)) {
    failures.push(
      `TypeScript runtime is shadowed by a same-name JavaScript file: ${relative(
        root,
        shadowJavaScriptPath,
      )}`,
    );
  }
}

const routeRegistry = readFileSync(resolve(miniprogramRoot, "app/navigation/routes.ts"), "utf8");
for (const page of inventory?.businessRoutes ?? []) {
  if (!routeRegistry.includes(`"/${page}"`)) {
    failures.push(`Registered page is missing from typed route registry: ${page}`);
  }
}

const appStyles = readFileSync(resolve(miniprogramRoot, "app.wxss"), "utf8");
for (const requiredImport of [
  "design-system/tokens/index.wxss",
  "design-system/primitives/index.wxss",
  "design-system/responsive/index.wxss",
]) {
  if (!appStyles.includes(requiredImport)) {
    failures.push(`app.wxss must import ${requiredImport}`);
  }
}

if (appConfig.window?.pageOrientation !== "auto") {
  failures.push("app.json window.pageOrientation must remain auto for tablet rotation support");
}

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`);
  process.exit(1);
}

const componentCount = componentConfigPaths.length;
process.stdout.write(
  `Validated ${String(inventory?.allRoutes.length ?? 0)} registered routes: ` +
    `${String(inventory?.businessRoutes.length ?? 0)} business, no internal asset hosts; ` +
    `${String(appConfig.tabBar.list.length)} tabs and ${String(componentCount)} shared components.\n`,
);
