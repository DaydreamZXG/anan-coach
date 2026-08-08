import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import automator from "../../../apps/wechat/node_modules/miniprogram-automator/out/index.js";

const repositoryRoot = path.resolve(import.meta.dirname, "../../..");
const projectPath = path.join(repositoryRoot, "apps/wechat");
const cliPath = "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const outputDirectory = path.resolve(import.meta.dirname, process.env.EVIDENCE_PROFILE ?? "probe");

await mkdir(outputDirectory, { recursive: true });
const program = await automator.launch({
  cliPath,
  projectPath,
  timeout: 60_000,
  trustProject: true,
});
try {
  const systemInfo = await program.systemInfo();
  const toolInfo = await program.send("Tool.getInfo");
  await program.screenshot({ path: path.join(outputDirectory, "launch.png") });
  await writeFile(
    path.join(outputDirectory, "receipt.json"),
    `${JSON.stringify(
      {
        schemaVersion: 1,
        kind: "local-devtools-automator-launch",
        commit: process.env.TEST_COMMIT ?? "660546761aa3",
        projectPath,
        prohibitedCommands: ["preview", "auto-preview", "upload"],
        systemInfo,
        toolInfo,
      },
      null,
      2,
    )}\n`,
  );
  process.stdout.write(`${JSON.stringify({ systemInfo, toolInfo }, null, 2)}\n`);
} finally {
  await program.close();
}
