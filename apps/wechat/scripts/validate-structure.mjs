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

if (existsSync(resolve(miniprogramRoot, "data/prototype-data.ts"))) {
  failures.push("Prototype fixtures must stay under tests and never ship in the mini-program");
}

if (/\bdataMode\b/u.test(readFileSync(resolve(miniprogramRoot, "config/environment.ts"), "utf8"))) {
  failures.push("Runtime environment must have one server-backed data path; dataMode is forbidden");
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
    const componentRoot = componentPath.startsWith("/")
      ? resolve(miniprogramRoot, `.${componentPath}`)
      : resolve(miniprogramRoot, page.slice(0, page.lastIndexOf("/")), componentPath);
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
for (const page of appConfig.pages) {
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

const componentSelectorTagPattern = new RegExp(
  "(^|[\\s>+~(])[a-z][a-z0-9-]*(?=$|[\\s>+~.#:[(])",
  "u",
);
const componentStylePaths = walkFiles(miniprogramRoot).filter(
  (path) =>
    path.endsWith(".wxss") &&
    path !== resolve(miniprogramRoot, "app.wxss") &&
    path !== resolve(miniprogramRoot, "design-system/tokens/index.wxss") &&
    path !== resolve(miniprogramRoot, "design-system/tokens/design-file.tokens.wxss") &&
    path !== resolve(miniprogramRoot, "design-system/responsive/index.wxss"),
);
for (const stylePath of componentStylePaths) {
  const stylesheet = readFileSync(stylePath, "utf8")
    .replace(/\/\*[\s\S]*?\*\//gu, "")
    .replace(/@import\s+[^;]+;/gu, "");
  for (const rule of stylesheet.matchAll(/([^{}]+)\{/gu)) {
    const prelude = rule[1]?.trim() ?? "";
    if (!prelude || prelude.startsWith("@")) continue;
    for (const selector of prelude.split(",").map((value) => value.trim())) {
      if (
        selector.includes("[") ||
        /(^|[\s>+~])#[A-Za-z_-]/u.test(selector) ||
        componentSelectorTagPattern.test(selector)
      ) {
        failures.push(
          `Component WXSS must use class selectors only: ${relative(root, stylePath)} -> ${selector}`,
        );
      }
    }
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
  `Validated ${String(appConfig.pages.length)} pages, ${String(appConfig.tabBar.list.length)} tabs, and ${String(componentCount)} shared components.\n`,
);
