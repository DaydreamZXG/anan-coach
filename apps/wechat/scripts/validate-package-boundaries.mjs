import { readdirSync, readFileSync, statSync } from "node:fs";
import { resolve, sep } from "node:path";

const appRoot = resolve(import.meta.dirname, "..", "miniprogram");
const appConfig = JSON.parse(readFileSync(resolve(appRoot, "app.json"), "utf8"));
const packageLimitBytes = 2 * 1024 * 1024;
const failures = [];
const packages = appConfig.subPackages ?? appConfig.subpackages ?? [];
const roots = packages.map((item) => ({
  ...item,
  absoluteRoot: resolve(appRoot, item.root),
}));

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

for (const { root } of roots) {
  if ((packageBytes.get(root) ?? 0) > packageLimitBytes) {
    failures.push(`${root} raw source exceeds the 2 MiB package limit`);
  }
}
if ((imageBytes.get("main") ?? 0) > packageLimitBytes) {
  failures.push("main-package images alone exceed the 2 MiB package limit");
}

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`);
  process.exit(1);
}

process.stdout.write(
  [
    `Validated ${roots.length} subpackages.`,
    `Main-package image lower bound: ${imageBytes.get("main") ?? 0} bytes.`,
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
