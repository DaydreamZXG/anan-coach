import { spawnSync } from "node:child_process";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const wechatRoot = path.resolve(repositoryRoot, "apps/wechat");
const outputRoot = path.resolve(repositoryRoot, "audit/subagents/a/logs");

const commands = [
  ["navigation-validate", "npm", ["run", "navigation:validate"]],
  ["interaction-bindings-validate", "npm", ["run", "interactions:validate"]],
  ["structure-validate", "node", ["scripts/validate-structure.mjs"]],
  ["visual-coverage-validate", "node", ["scripts/validate-visual-coverage.mjs"]],
  [
    "interaction-coverage-validate",
    "node",
    ["scripts/validate-interaction-coverage.mjs"],
  ],
];

const results = [];
for (const [id, command, arguments_] of commands) {
  const startedAt = new Date().toISOString();
  const result = spawnSync(command, arguments_, {
    cwd: wechatRoot,
    encoding: "utf8",
    timeout: 120_000,
    env: { ...process.env, CI: "1" },
  });
  results.push({
    id,
    command: [command, ...arguments_],
    startedAt,
    finishedAt: new Date().toISOString(),
    status: result.status,
    signal: result.signal,
    error: result.error ? String(result.error.stack ?? result.error) : null,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  });
}

await writeFile(
  path.resolve(outputRoot, "route-validation-results.json"),
  `${JSON.stringify({ generatedAt: new Date().toISOString(), results }, null, 2)}\n`,
);
await writeFile(
  path.resolve(outputRoot, "route-validation-results.log"),
  `${results
    .map(
      (result) =>
        `$ ${result.command.join(" ")}\nexit=${String(result.status)} signal=${String(
          result.signal,
        )}\n${result.stdout}${result.stderr}`,
    )
    .join("\n\n")}\n`,
);

const weappLogDirectory =
  "/Users/zxg/Library/Application Support/微信开发者工具/50a7d9210159a32f006158795f893857/WeappLog/logs";
const logFilenames = await readdir(weappLogDirectory);
const recentLogs = (
  await Promise.all(
    logFilenames.map(async (filename) => {
      const absolute = path.resolve(weappLogDirectory, filename);
      const metadata = await stat(absolute);
      return { absolute, modifiedAt: metadata.mtimeMs };
    }),
  )
)
  .sort((left, right) => right.modifiedAt - left.modifiedAt)
  .slice(0, 8);
const evidencePattern =
  /(simulator launch success|finish load user code|webview loaded|webview page ready|routeTo appLaunch timeout|triggerAppRouteDone timeout|start cli server error|pages\/login\/index|runtime-project)/u;
const devtoolsEvidence = [];
for (const log of recentLogs) {
  const source = await readFile(log.absolute, "utf8");
  const matchingLines = source
    .split("\n")
    .filter((line) => evidencePattern.test(line))
    .slice(-160);
  if (matchingLines.length > 0) {
    devtoolsEvidence.push({
      source: log.absolute,
      matchingLines,
    });
  }
}
await writeFile(
  path.resolve(outputRoot, "devtools-app-launch-excerpt.json"),
  `${JSON.stringify(
    { generatedAt: new Date().toISOString(), logs: devtoolsEvidence },
    null,
    2,
  )}\n`,
);
await writeFile(
  path.resolve(outputRoot, "devtools-app-launch-excerpt.log"),
  `${devtoolsEvidence
    .map(({ source, matchingLines }) => `# ${source}\n${matchingLines.join("\n")}`)
    .join("\n\n")}\n`,
);

process.stdout.write(
  `${JSON.stringify(
    {
      commandResults: results.map(({ id, status }) => ({ id, status })),
      devtoolsLogsWithEvidence: devtoolsEvidence.length,
    },
    null,
    2,
  )}\n`,
);
