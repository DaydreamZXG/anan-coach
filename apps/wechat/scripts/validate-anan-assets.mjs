import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const appRoot = resolve(root, "miniprogram");
const componentRoot = resolve(appRoot, "design-system/companion/anan-companion");
const manifest = readJson(resolve(componentRoot, "runtime-manifest.json"));
const appConfig = readJson(resolve(appRoot, "app.json"));
const failures = [];

const coreBytes = manifest.core.assets.reduce((total, relativePath) => {
  const path = resolve(componentRoot, relativePath);
  if (!isFile(path)) {
    failures.push(`Missing core asset: ${path}`);
    return total;
  }
  return total + statSync(path).size;
}, 0);
if (coreBytes !== manifest.core.actualBytes) {
  failures.push(`Core bytes ${coreBytes} do not match manifest ${manifest.core.actualBytes}`);
}
if (coreBytes > manifest.core.budgetBytes) {
  failures.push(`Core assets exceed ${manifest.core.budgetBytes} bytes: ${coreBytes}`);
}

const packageNames = new Set((appConfig.subPackages ?? []).map((item) => item.name));
for (const [intent, release] of Object.entries(manifest.intents)) {
  const animationPath =
    intent === "companion"
      ? resolve(componentRoot, "assets/v1.0.0/core/spritesheet.webp")
      : resolve(appRoot, `packages/anan-${intent}/assets/v1.0.0/spritesheet.webp`);
  validateAsset(animationPath, release.animation.bytes, release.animation.sha256);
  if (release.animation.bytes > 225_280) {
    failures.push(`${intent} animation exceeds 220 KiB: ${release.animation.bytes}`);
  }
  if (intent === "companion") continue;
  const packageName = `anan-${intent}`;
  if (!packageNames.has(packageName)) failures.push(`Missing subpackage: ${packageName}`);
  const packageRoot = resolve(appRoot, `packages/${packageName}`);
  validatePackageEntry(packageName, packageRoot);
  for (const asset of [
    "inline-64@2x.webp",
    "card-160@2x.webp",
    "hero-240@2x.webp",
    "spritesheet.webp",
  ]) {
    if (!isFile(resolve(packageRoot, `assets/v1.0.0/${asset}`))) {
      failures.push(`Missing ${intent} asset: ${asset}`);
    }
  }
  for (const extension of ["ts", "json", "wxml", "wxss"]) {
    if (!isFile(resolve(packageRoot, `components/visual/index.${extension}`))) {
      failures.push(`Incomplete ${intent} async visual component: index.${extension}`);
    }
  }
}

if (!packageNames.has(manifest.growth.package)) {
  failures.push(`Missing growth subpackage: ${manifest.growth.package}`);
}
validatePackageEntry("anan-growth", resolve(appRoot, "packages/anan-growth"));
const growthRoot = resolve(appRoot, "packages/anan-growth/assets/v1.0.0");
for (const asset of [manifest.growth.default, ...Object.values(manifest.growth.stages)]) {
  if (!isFile(resolve(growthRoot, asset))) failures.push(`Missing growth asset: ${asset}`);
}

for (const path of allFiles(resolve(appRoot, "pages")).filter((item) => item.endsWith(".wxml"))) {
  const source = readFileSync(path, "utf8");
  if (/(?:spritesheet|inline-64|card-160|hero-240)[^\s"']*\.webp/u.test(source)) {
    failures.push(`Business page references a concrete Anan asset: ${path}`);
  }
}

const componentMarkup = readFileSync(resolve(componentRoot, "index.wxml"), "utf8");
const componentStyles = readFileSync(resolve(componentRoot, "index.wxss"), "utf8");
for (const obsoleteMarker of ["anan-ear", "anan-eye", "anan-muzzle", "anan-core"]) {
  if (componentMarkup.includes(obsoleteMarker) || componentStyles.includes(obsoleteMarker)) {
    failures.push(`Obsolete CSS mascot marker remains: ${obsoleteMarker}`);
  }
}

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`);
  process.exit(1);
}
process.stdout.write(
  `Validated Anan ${manifest.releaseVersion}: ${coreBytes} core bytes and ${Object.keys(manifest.intents).length} intents.\n`,
);

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function isFile(path) {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function validateAsset(path, expectedBytes, expectedHash) {
  if (!isFile(path)) {
    failures.push(`Missing animation: ${path}`);
    return;
  }
  const bytes = readFileSync(path);
  if (bytes.length !== expectedBytes) {
    failures.push(`Unexpected animation bytes for ${path}: ${bytes.length}`);
  }
  const hash = createHash("sha256").update(bytes).digest("hex");
  if (hash !== expectedHash) failures.push(`Unexpected animation hash for ${path}: ${hash}`);
}

function validatePackageEntry(packageName, packageRoot) {
  const packageConfig = (appConfig.subPackages ?? []).find((item) => item.name === packageName);
  if (!packageConfig || packageConfig.pages.length === 0) {
    failures.push(`Subpackage has no explicit asset entry page: ${packageName}`);
    return;
  }
  for (const extension of ["ts", "json", "wxml", "wxss"]) {
    const pagePath = resolve(packageRoot, `${packageConfig.pages[0]}.${extension}`);
    if (!isFile(pagePath)) failures.push(`Incomplete ${packageName} entry: ${pagePath}`);
  }
}

function allFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? allFiles(path) : [path];
  });
}
