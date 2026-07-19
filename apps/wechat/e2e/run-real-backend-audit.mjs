import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import automator from "miniprogram-automator";

import { invokePageHandler, readPageData } from "./page-data-bridge.mjs";
import { consoleEventText, isActionableRuntimeWarning } from "./runtime-warning-policy.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDirectory, "..");
const cliPath =
  process.env.WECHAT_DEVTOOLS_CLI ?? "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const accessToken = requiredEnvironment("WECHAT_REAL_BACKEND_ACCESS_TOKEN");
const userId = requiredEnvironment("WECHAT_REAL_BACKEND_USER_ID");
const expectedBaseUrl = requiredEnvironment("WECHAT_REAL_BACKEND_BASE_URL").replace(/\/$/u, "");
const testAccountAcknowledged = process.env.WECHAT_REAL_BACKEND_TEST_ACCOUNT === "acknowledged";
const allowWrites = process.env.WECHAT_REAL_BACKEND_ALLOW_WRITES === "1";

assert.ok(
  testAccountAcknowledged,
  "Set WECHAT_REAL_BACKEND_TEST_ACCOUNT=acknowledged only for a dedicated E2E account.",
);
if (allowWrites) {
  assert.equal(
    process.env.WECHAT_REAL_BACKEND_WRITE_ACK,
    "notification-preferences-may-be-versioned",
    "Real writes require an explicit notification preference versioning acknowledgement.",
  );
}

const environmentSource = await readFile(
  path.resolve(projectRoot, "miniprogram/config/environment.ts"),
  "utf8",
);
const compiledBaseUrl = environmentSource.match(/apiBaseUrl:\s*["']([^"']+)["']/u)?.[1];
assert.equal(
  compiledBaseUrl,
  expectedBaseUrl,
  "The requested real-backend origin differs from the Mini Program compiled origin.",
);
await access(cliPath, constants.X_OK);

const miniProgram = await automator.launch({
  cliPath,
  projectPath: projectRoot,
  timeout: 60_000,
  trustProject: true,
});
const runtimeFailures = [];
const runtimeWarnings = [];
miniProgram.on("exception", (event) => runtimeFailures.push(String(event?.message ?? event)));
miniProgram.on("console", (event) => {
  const message = consoleEventText(event);
  if (isActionableRuntimeWarning(message)) runtimeWarnings.push(message);
});

try {
  await setAuthentication(miniProgram, true);
  await auditReadyPage(miniProgram, "/pages/today/index", (data) => data.status === "ready");
  await auditReadyPage(
    miniProgram,
    "/pages/positions/index",
    (data) => data.workspace?.status === "ready" || data.workspace?.status === "empty",
  );
  await auditReadyPage(miniProgram, "/pages/profile/index", (data) => data.status === "ready");
  await auditReadyPage(
    miniProgram,
    "/pages/learning-profile/index",
    (data) => data.status === "ready" && Boolean(data.profile),
  );
  await auditReadyPage(
    miniProgram,
    "/pages/report-center/index",
    (data) => data.status === "ready" && Boolean(data.center),
  );
  await auditReadyPage(
    miniProgram,
    "/pages/notification-settings/index",
    (data) => data.status === "ready" && Boolean(data.draft),
  );
  if (allowWrites) await auditNotificationPreferenceWrite(miniProgram);
} finally {
  await withTimeout(setAuthentication(miniProgram, false), 5_000, "Real E2E auth did not clear");
  await withTimeout(miniProgram.close(), 10_000, "Real E2E connection did not close");
}

assert.deepEqual(
  runtimeFailures,
  [],
  `Real-backend runtime exceptions:\n${runtimeFailures.join("\n")}`,
);
assert.deepEqual(
  runtimeWarnings,
  [],
  `Real-backend runtime warnings:\n${runtimeWarnings.join("\n")}`,
);
process.stdout.write(
  `Verified 6 unmocked real-backend page reads${allowWrites ? " and 1 preference write" : ""}.\n`,
);

async function auditReadyPage(program, route, predicate) {
  process.stdout.write(`[real-backend] ${route} ... `);
  const page = await program.reLaunch(route);
  assert.ok(page, `Unable to open ${route}`);
  await waitForPredicate(
    async () => predicate(await readPageData(program, page)),
    `${route} did not reach ready`,
  );
  process.stdout.write("ok\n");
}

async function auditNotificationPreferenceWrite(program) {
  const page = await program.reLaunch("/pages/notification-settings/index");
  assert.ok(page);
  await waitForPredicate(
    async () => (await readPageData(program, page)).status === "ready",
    "Notification settings did not load before write",
  );
  const before = await readPageData(program, page);
  await invokePageHandler(program, page, "save");
  await waitForPredicate(async () => {
    const data = await readPageData(program, page);
    return data.saving === false && typeof data.message === "string" && data.message.length > 0;
  }, "Notification preference write did not complete");
  const after = await readPageData(program, page);
  assert.ok(
    after.history?.length >= before.history?.length,
    "Notification write lost existing delivery history",
  );
}

async function setAuthentication(program, authenticated) {
  await program.evaluate(
    function applyRealBackendSession(hasSession, token, stableUserId) {
      const storageKey = "gongkao.auth.session.v1";
      const app = getApp();
      if (hasSession) {
        const session = { accessToken: token, userId: stableUserId };
        wx.setStorageSync(storageKey, session);
        app.globalData.accessToken = token;
        app.globalData.userId = stableUserId;
        return;
      }
      wx.removeStorageSync(storageKey);
      delete app.globalData.accessToken;
      delete app.globalData.userId;
    },
    authenticated,
    accessToken,
    userId,
  );
}

async function waitForPredicate(predicate, message) {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(message);
}

async function withTimeout(promise, timeoutMs, message) {
  let timeoutHandle;
  const timeout = new Promise((_, reject) => {
    timeoutHandle = setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timeoutHandle);
  }
}

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  assert.ok(value, `${name} is required; mock credentials are rejected by this command.`);
  return value;
}
