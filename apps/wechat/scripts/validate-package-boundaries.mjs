import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { relative, resolve, sep } from "node:path";

const appRoot = resolve(import.meta.dirname, "..", "miniprogram");
const repositoryRoot = resolve(appRoot, "..", "..", "..");
const appConfig = JSON.parse(readFileSync(resolve(appRoot, "app.json"), "utf8"));
const packageLimitBytes = 2 * 1024 * 1024;
const assetPackageNames = [
  "anan-thinking",
  "anan-reminder",
  "anan-encouragement",
  "anan-rest",
  "anan-deprioritize",
  "anan-growth",
];
const failures = [];
const packages = appConfig.subPackages ?? appConfig.subpackages ?? [];
const roots = packages.map((item) => ({
  ...item,
  absoluteRoot: resolve(appRoot, item.root),
}));

for (const name of assetPackageNames) {
  const expectedRoot = `packages/${name}`;
  const matches = roots.filter(({ root }) => root === expectedRoot);
  if (matches.length !== 1) {
    failures.push(`${expectedRoot} must have exactly one subpackage owner`);
    continue;
  }
  const [owner] = matches;
  if (
    owner.name !== name ||
    !Array.isArray(owner.pages) ||
    owner.pages.length !== 0 ||
    owner.entry !== "package-entry.js"
  ) {
    failures.push(`${expectedRoot} must be an asset-only subpackage with package-entry.js`);
    continue;
  }
  const entryPath = resolve(owner.absoluteRoot, owner.entry);
  if (!existsSync(entryPath)) {
    failures.push(`${expectedRoot} package entry is missing`);
    continue;
  }
  if (existsSync(resolve(repositoryRoot, ".git")) && !isTracked(entryPath)) {
    failures.push(`${expectedRoot} package entry is not in the Git delivery authority`);
  }
}

const packageBytes = new Map([["main", 0]]);
const imageBytes = new Map([["main", 0]]);
for (const { root } of roots) {
  packageBytes.set(root, 0);
  imageBytes.set(root, 0);
}

for (const path of allFiles(appRoot)) {
  const owner = packageOwner(path, roots);
  const bytes = statSync(path).size;
  packageBytes.set(owner, (packageBytes.get(owner) ?? 0) + bytes);
  if (/\.(?:gif|jpe?g|png|webp)$/iu.test(path)) {
    imageBytes.set(owner, (imageBytes.get(owner) ?? 0) + bytes);
  }
}

for (const name of assetPackageNames) {
  const root = `packages/${name}`;
  if ((packageBytes.get(root) ?? 0) > packageLimitBytes) {
    failures.push(`${root} raw source exceeds the 2 MiB package limit`);
  }
}
if ((imageBytes.get("main") ?? 0) > packageLimitBytes) {
  failures.push("main-package images alone exceed the 2 MiB package limit");
}

const ananImageBytes = assetPackageNames.reduce(
  (total, name) => total + (imageBytes.get(`packages/${name}`) ?? 0),
  0,
);
if (ananImageBytes === 0) {
  failures.push("Anan visual images were not assigned to their asset subpackages");
}

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`);
  process.exit(1);
}

process.stdout.write(
  [
    `Validated ${assetPackageNames.length} asset-only Anan subpackages.`,
    `Main-package image lower bound: ${imageBytes.get("main") ?? 0} bytes.`,
    `Anan subpackage images: ${ananImageBytes} bytes.`,
    "Final compiled main-package size still requires the official WeChat packaging receipt.",
  ].join(" ") + "\n",
);

function packageOwner(path, candidates) {
  const owners = candidates
    .filter(({ absoluteRoot }) => path === absoluteRoot || path.startsWith(`${absoluteRoot}${sep}`))
    .sort((left, right) => right.absoluteRoot.length - left.absoluteRoot.length);
  return owners[0]?.root ?? "main";
}

function allFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? allFiles(path) : [path];
  });
}

function isTracked(path) {
  try {
    execFileSync(
      "git",
      ["-C", repositoryRoot, "ls-files", "--error-unmatch", "--", relative(repositoryRoot, path)],
      { stdio: "ignore" },
    );
    return true;
  } catch {
    return false;
  }
}
