import { cp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const sourceRoot = path.resolve(repositoryRoot, "apps/wechat");
const runtimeRoot = path.resolve(repositoryRoot, "audit/subagents/a/runtime-project");
const outputRoot = path.resolve(repositoryRoot, "audit/subagents/a");

if (!runtimeRoot.startsWith(`${outputRoot}${path.sep}`)) {
  throw new Error(`Refusing to replace runtime project outside audit output: ${runtimeRoot}`);
}

await rm(runtimeRoot, { recursive: true, force: true });
await mkdir(runtimeRoot, { recursive: true });

for (const filename of [
  "project.config.json",
  "project.private.config.json",
  "cloudbaserc.json",
  "package.json",
  "tsconfig.json",
]) {
  await cp(path.resolve(sourceRoot, filename), path.resolve(runtimeRoot, filename));
}
await cp(path.resolve(sourceRoot, "miniprogram"), path.resolve(runtimeRoot, "miniprogram"), {
  recursive: true,
});
await cp(path.resolve(sourceRoot, "cloudfunctions"), path.resolve(runtimeRoot, "cloudfunctions"), {
  recursive: true,
  filter(source) {
    return !source.includes(`${path.sep}node_modules${path.sep}`);
  },
});
await symlink(path.resolve(sourceRoot, "node_modules"), path.resolve(runtimeRoot, "node_modules"), "dir");

const environmentPath = path.resolve(runtimeRoot, "miniprogram/config/environment.ts");
let environmentSource = await readFile(environmentPath, "utf8");
const originalEnvironmentSource = environmentSource;
environmentSource = environmentSource
  .replace(
    'apiBaseUrl: "https://kg.xingguoxian.cn",',
    'apiBaseUrl: "https://127.0.0.1:9",',
  )
  .replace(
    'mapleMonoCnRegularUrl: "https://kg.xingguoxian.cn/assets/fonts/maple-mono-cn-regular-v7.9.woff",',
    "mapleMonoCnRegularUrl: null,",
  )
  .replace(
    'mapleMonoCnBoldUrl: "https://kg.xingguoxian.cn/assets/fonts/maple-mono-cn-bold-v7.9.woff",',
    "mapleMonoCnBoldUrl: null,",
  )
  .replace('ownedHttpsOrigins: ["https://kg.xingguoxian.cn"],', "ownedHttpsOrigins: [],");
if (environmentSource === originalEnvironmentSource) {
  throw new Error("Audit runtime network guard did not change environment.ts");
}
if (environmentSource.includes("kg.xingguoxian.cn")) {
  throw new Error("Audit runtime project still contains the production hostname in environment.ts");
}
await writeFile(environmentPath, environmentSource);

const projectConfigPath = path.resolve(runtimeRoot, "project.config.json");
const projectConfig = JSON.parse(await readFile(projectConfigPath, "utf8"));
projectConfig.projectname = "gongkao-audit-route-smoke";
projectConfig.description = "READ-ONLY AUDIT COPY; fail-closed network";
projectConfig.setting = {
  ...(projectConfig.setting ?? {}),
  urlCheck: false,
};
await writeFile(projectConfigPath, `${JSON.stringify(projectConfig, null, 2)}\n`);

const privateConfigPath = path.resolve(runtimeRoot, "project.private.config.json");
const privateConfig = JSON.parse(await readFile(privateConfigPath, "utf8"));
privateConfig.projectname = "gongkao-audit-route-smoke";
privateConfig.setting = {
  ...(privateConfig.setting ?? {}),
  urlCheck: false,
  compileHotReLoad: false,
};
await writeFile(privateConfigPath, `${JSON.stringify(privateConfig, null, 2)}\n`);

const manifest = {
  generatedAt: new Date().toISOString(),
  sourceRoot,
  runtimeRoot,
  safeguards: {
    apiBaseUrl: "https://127.0.0.1:9",
    fontAssetsDisabled: true,
    productionHostnameAbsentFromRuntimeEnvironment: true,
    sourceProjectUnmodified: true,
  },
};
await writeFile(
  path.resolve(outputRoot, "runtime-project-manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`);
