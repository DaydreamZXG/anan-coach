import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const appRoot = resolve(import.meta.dirname, "..");
const deliveryRoot = resolve(appRoot, "design/figma-delivery");
const miniprogramRoot = resolve(appRoot, "miniprogram");
const failures = [];

function read(path) {
  return readFileSync(path);
}

function readText(path) {
  return readFileSync(path, "utf8");
}

function readJson(path) {
  return JSON.parse(readText(path));
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function assertManifestMirror(entry, label) {
  const source = read(resolve(deliveryRoot, entry.figmaSource));
  const runtime = read(resolve(miniprogramRoot, entry.runtimePath));
  if (!source.equals(runtime)) {
    failures.push(`${label} is no longer an exact Design File mirror`);
  }
  if (sha256(source) !== entry.sha256) {
    failures.push(`${label} source hash no longer matches the recorded Design File export`);
  }
  if (sha256(runtime) !== entry.sha256) {
    failures.push(`${label} runtime hash no longer matches the recorded Design File export`);
  }
}

const assetManifest = readJson(resolve(miniprogramRoot, "assets/design-file.manifest.json"));
assertManifestMirror(assetManifest.tokenSnapshot, "Runtime token snapshot");
for (const asset of assetManifest.assets) {
  assertManifestMirror(asset, `Runtime asset ${asset.id}`);
}

const tokenAliases = readText(resolve(miniprogramRoot, "design-system/tokens/index.wxss"));
if (!tokenAliases.startsWith('@import "design-file.tokens.wxss";')) {
  failures.push("Runtime semantic tokens must import the exact Design File token snapshot first");
}

const loginMarkup = readText(resolve(miniprogramRoot, "pages/login/index.wxml"));
const ananMarkup = readText(resolve(miniprogramRoot, "pages/anan/index.wxml"));
if (!loginMarkup.includes("/assets/icons/anan/brain-outline-ink.svg")) {
  failures.push("Login must render the approved Design File logo asset");
}
if (!ananMarkup.includes("/assets/icons/anan/brain-outline-white.svg")) {
  failures.push("Anan console must render the approved white brain asset");
}
if (`${loginMarkup}\n${ananMarkup}`.includes("logo-design-file.png")) {
  failures.push("The retired hand-made gradient logo must not return to production markup");
}

const routeSource = readText(resolve(miniprogramRoot, "app/navigation/routes.ts"));
const routesBlock = routeSource.match(/export const routes = \{([\s\S]*?)\} as const;/u)?.[1] ?? "";
const registeredRouteNames = new Set(
  [...routesBlock.matchAll(/^\s{2}([A-Za-z][A-Za-z0-9]*):\s*"[^"]+",?$/gmu)].map(
    (match) => match[1],
  ),
);
const routeMap = readJson(resolve(deliveryRoot, "route-map.json"));
const disposition = readJson(resolve(deliveryRoot, "route-disposition.json"));
const directlyMapped = new Set(routeMap.mappings.map((mapping) => mapping.routeName));
const dispositionNames = new Set(disposition.dispositions.map((entry) => entry.routeName));

if (registeredRouteNames.size !== disposition.summary.registeredRouteCount) {
  failures.push(
    `Route disposition expected ${disposition.summary.registeredRouteCount} registered routes, found ${registeredRouteNames.size}`,
  );
}
if (directlyMapped.size !== disposition.summary.directlyMappedRouteCount) {
  failures.push(
    `Route disposition expected ${disposition.summary.directlyMappedRouteCount} directly mapped routes, found ${directlyMapped.size}`,
  );
}
if (dispositionNames.size !== disposition.summary.unmappedRouteCount) {
  failures.push(
    `Route disposition expected ${disposition.summary.unmappedRouteCount} unmapped routes, found ${dispositionNames.size}`,
  );
}

for (const routeName of registeredRouteNames) {
  const membership =
    Number(directlyMapped.has(routeName)) + Number(dispositionNames.has(routeName));
  if (membership !== 1) {
    failures.push(`Registered route ${routeName} must have exactly one Design File disposition`);
  }
}

const actualDispositionCounts = Object.fromEntries(
  [
    "reuse_existing_design",
    "merge_into_designed_flow",
    "hide_until_product_activation",
    "new_design_required",
  ].map((kind) => [
    kind,
    disposition.dispositions.filter((entry) => entry.disposition === kind).length,
  ]),
);
const expectedDispositionCounts = {
  reuse_existing_design: disposition.summary.reuseExistingDesignCount,
  merge_into_designed_flow: disposition.summary.mergeIntoDesignedFlowCount,
  hide_until_product_activation: disposition.summary.hideUntilProductActivationCount,
  new_design_required: disposition.summary.newDesignRequiredCount,
};
for (const [kind, expected] of Object.entries(expectedDispositionCounts)) {
  if (actualDispositionCounts[kind] !== expected) {
    failures.push(
      `Disposition ${kind} expected ${expected}, found ${actualDispositionCounts[kind]}`,
    );
  }
}

const profileController = readText(resolve(miniprogramRoot, "pages/me/index.ts"));
for (const hiddenRoute of [
  "membership",
  "communityDraft",
  "communityStructure",
  "communityTrial",
  "communityContributions",
]) {
  if (profileController.includes(`routes.${hiddenRoute}`)) {
    failures.push(`Hidden product route is still exposed from My: ${hiddenRoute}`);
  }
}

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`);
  process.exit(1);
}

process.stdout.write(
  `Validated Design File tokens, ${assetManifest.assets.length} exact assets, ${registeredRouteNames.size} route dispositions, and hidden-entry boundaries.\n`,
);
