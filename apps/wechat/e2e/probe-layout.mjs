import automator from "miniprogram-automator";
import { setPageData } from "/Users/zxg/Documents/新项目/apps/wechat/e2e/page-data-bridge.mjs";
import { visualScenarios } from "/Users/zxg/Documents/新项目/apps/wechat/e2e/visual-scenarios.mjs";

const projectRoot = "/Users/zxg/Documents/新项目/apps/wechat";
const cliPath = "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const scenarioId = process.argv[2] ?? "c02-xingce-answering-toolbar";
const scenario = visualScenarios.find((item) => item.id === scenarioId);
if (!scenario) throw new Error(`unknown scenario ${scenarioId}`);

const miniProgram = await automator.launch({
  cliPath,
  projectPath: projectRoot,
  timeout: 60_000,
  trustProject: true,
});

await miniProgram.callWxMethod("setStorageSync", "auth.session", {
  userId: "visual-user-001",
  token: "visual-token",
  expiresAt: new Date(Date.now() + 86_400_000).toISOString(),
});
const page = await miniProgram.reLaunch(scenario.route);
await page.waitFor(600);
await setPageData(page, scenario.data);
await page.waitFor(800);

const report = await page.callMethod("__probeLayout").catch(() => null);
if (report) {
  console.log(JSON.stringify(report, null, 2));
} else {
  // Fall back to element queries.
  const selectors = [
    ".practice-question-pane",
    ".option-list",
    ".option-row",
    ".material-reader-shell",
    ".material-reader__inline-scroll--answering",
    ".task-shell--xingce",
    ".task-content",
    ".task-footer",
  ];
  for (const selector of selectors) {
    const elements = await page.$$(selector);
    const sizes = [];
    for (const element of elements) {
      sizes.push(await element.size().catch(() => null));
    }
    console.log(selector, "count=", elements.length, JSON.stringify(sizes));
  }
}
await miniProgram.close();
