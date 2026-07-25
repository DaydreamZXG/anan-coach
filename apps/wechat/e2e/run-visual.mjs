import assert from "node:assert/strict";
import { access, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import automator from "miniprogram-automator";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

import { readPageData, setPageData } from "./page-data-bridge.mjs";
import { consoleEventText, isActionableRuntimeWarning } from "./runtime-warning-policy.mjs";
import { visualScenarios } from "./visual-scenarios.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDirectory, "..");
const baselineDirectory = path.resolve(projectRoot, "tests/visual/baselines");
const baselineProfilePath = path.resolve(projectRoot, "tests/visual/baseline-profile.json");
const artifactDirectory = path.resolve(projectRoot, "tests/visual/artifacts");
const cliPath =
  process.env.WECHAT_DEVTOOLS_CLI ?? "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const updateBaselines = process.argv.includes("--update");
const runtimeOnly = process.env.VISUAL_RUNTIME_ONLY === "1";
const captureOnly = process.env.VISUAL_CAPTURE_ONLY === "1";
const maximumDifferentPixelRatio = 0.003;
const scenarioFilter = process.env.VISUAL_SCENARIO?.trim() ?? "";
const requestedScenarioIds = new Set(
  scenarioFilter
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);
const selectedScenarios = scenarioFilter
  ? visualScenarios.filter((scenario) => requestedScenarioIds.has(scenario.id))
  : visualScenarios;

assert.equal(
  selectedScenarios.length,
  requestedScenarioIds.size || visualScenarios.length,
  `Unknown visual scenario filter: ${scenarioFilter}`,
);

await access(cliPath, constants.X_OK);
await mkdir(baselineDirectory, { recursive: true });
await rm(artifactDirectory, { recursive: true, force: true });
await mkdir(artifactDirectory, { recursive: true });

const automationEndpoint = process.env.WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT?.trim();
const miniProgram = automationEndpoint
  ? await automator.connect({ wsEndpoint: automationEndpoint })
  : await automator.launch({
      cliPath,
      projectPath: projectRoot,
      timeout: 60_000,
      trustProject: true,
    });
const runtimeFailures = [];
const runtimeWarnings = [];
const captureRecords = [];
miniProgram.on("exception", (event) => runtimeFailures.push(String(event?.message ?? event)));
if (runtimeOnly || captureOnly) {
  miniProgram.on("console", (event) => {
    const message = consoleEventText(event);
    if (isActionableRuntimeWarning(message)) runtimeWarnings.push(message);
  });
}
if (!runtimeOnly && !captureOnly) await assertBaselineDeviceProfile(miniProgram);
await freezeVisualAnimationClock(miniProgram);
await miniProgram.mockWxMethod("request", {
  data: {
    code: "visual_test_request_blocked",
    message: "Visual scenarios own their deterministic page state.",
    requestId: "visual-network-guard",
  },
  statusCode: 503,
  header: {},
  cookies: [],
  errMsg: "request:ok",
});

try {
  for (const scenario of selectedScenarios) {
    process.stdout.write(`[audit] ${scenario.id} ... `);
    await withTimeout(
      captureScenario(miniProgram, scenario),
      45_000,
      `${scenario.id} did not settle within 45 seconds`,
    );
    process.stdout.write("ok\n");
  }
  if (captureOnly) {
    await writeFile(
      path.resolve(artifactDirectory, "audit-manifest.json"),
      `${JSON.stringify(
        {
          systemInfo: await miniProgram.systemInfo(),
          scenarios: captureRecords,
        },
        null,
        2,
      )}\n`,
    );
  }
} finally {
  await withTimeout(
    setAuthentication(miniProgram, false),
    5_000,
    "Visual authentication fixture did not clear cleanly",
  );
  await withTimeout(miniProgram.close(), 10_000, "Automation connection did not close cleanly");
}

assert.deepEqual(
  runtimeFailures,
  [],
  `Mini Program runtime exceptions:\n${runtimeFailures.join("\n")}`,
);
assert.deepEqual(
  runtimeWarnings,
  [],
  `Mini Program runtime warnings:\n${runtimeWarnings.join("\n")}`,
);
process.stdout.write(
  `${runtimeOnly ? "Audited" : captureOnly ? "Captured" : updateBaselines ? "Updated" : "Verified"} ${String(selectedScenarios.length)} ${runtimeOnly ? "runtime" : "visual"} scenarios.\n`,
);

async function captureScenario(program, scenario) {
  assert.match(scenario.id, /^[a-z0-9][a-z0-9-]+$/u);
  assert.ok(scenario.route.startsWith("/pages/"));
  await setAuthentication(program, scenario.authenticated === true);
  const page = await program.reLaunch(scenario.route);
  assert.ok(page, `Unable to open ${scenario.route}`);
  await waitForControllerSettlement(page, scenario);
  const fixtureData = structuredClone(scenario.data);
  await setPageData(program, page, fixtureData);
  await page.waitFor(800);
  if (scenario.scrollTop) {
    await program.pageScrollTo(scenario.scrollTop);
    await page.waitFor(200);
  }
  // Page timers may keep rendering after the controller has settled. The scenario owns
  // deterministic screenshot data, so restore it once after layout/scroll stabilization.
  await setPageData(program, page, fixtureData);
  // automator resolves setData after the page data commit, while WXML/component layout can
  // still be one render behind. Wait for that final deterministic frame before asserting and
  // capturing, otherwise a completed network-error frame can be photographed with fixture data.
  await page.waitFor(120);
  const appliedData = await readPageData(program, page);
  if (!runtimeOnly) {
    for (const [key, expected] of Object.entries(scenario.data)) {
      assert.deepEqual(
        appliedData[key],
        expected,
        `${scenario.id} fixture was overwritten: ${key}`,
      );
    }
  }

  if (runtimeOnly) return;
  const currentPath = path.resolve(artifactDirectory, `${scenario.id}.png`);
  const baselinePath = path.resolve(baselineDirectory, `${scenario.id}.png`);
  await program.screenshot({ path: currentPath });
  if (captureOnly) {
    const current = await readPng(currentPath);
    captureRecords.push({
      id: scenario.id,
      route: scenario.route,
      width: current.width,
      height: current.height,
      artifact: path.relative(projectRoot, currentPath),
    });
    return;
  }
  if (updateBaselines) {
    const current = await readFile(currentPath);
    await writeFile(baselinePath, current);
    return;
  }

  await access(baselinePath, constants.R_OK);
  const [baseline, current] = await Promise.all([readPng(baselinePath), readPng(currentPath)]);
  assert.equal(current.width, baseline.width, `${scenario.id} width changed`);
  assert.equal(current.height, baseline.height, `${scenario.id} height changed`);

  const diff = new PNG({ width: current.width, height: current.height });
  const differentPixels = pixelmatch(
    baseline.data,
    current.data,
    diff.data,
    current.width,
    current.height,
    { threshold: 0.12, includeAA: false },
  );
  const ratio = differentPixels / (current.width * current.height);
  if (ratio > maximumDifferentPixelRatio) {
    const diffPath = path.resolve(artifactDirectory, `${scenario.id}.diff.png`);
    await writePng(diffPath, diff);
    assert.fail(
      `${scenario.id} visual difference ${(ratio * 100).toFixed(3)}% exceeds ${(maximumDifferentPixelRatio * 100).toFixed(3)}%; inspect ${diffPath}`,
    );
  }
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

async function freezeVisualAnimationClock(program) {
  await program.evaluate(function installDeterministicVisualClock() {
    if (globalThis.__gongkaoVisualOriginalSetInterval) return;
    const originalSetInterval = globalThis.setInterval;
    globalThis.__gongkaoVisualOriginalSetInterval = originalSetInterval;
    globalThis.setInterval = function visualSetInterval(callback, delay, ...args) {
      // 岸岸 core animation is contractually fixed at 8 fps (125 ms). Visual tests
      // keep frame zero while leaving page polling/countdown timers operational.
      if (delay === 125) return originalSetInterval(function holdVisualFrame() {}, delay);
      return originalSetInterval(callback, delay, ...args);
    };
  });
}

async function waitForControllerSettlement(page, scenario) {
  if (scenario.authenticated !== true || scenario.skipControllerSettlement === true) return;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const data = await readPageData(miniProgram, page);
    if (controllerHasSettled(scenario.route, data)) return;
    await page.waitFor(100);
  }
  assert.fail(`${scenario.id} did not settle its page controller before fixture injection`);
}

function controllerHasSettled(route, data) {
  const path = route.split("?", 1)[0];
  if (path === "/pages/anan/index") return true;
  if (path.startsWith("/pages/community-")) {
    return remoteStatusHasSettled(data.pageState);
  }
  if (path === "/pages/onboarding/index") return data.loadingProfile === false;
  if (path === "/pages/positions/index") {
    return (
      remoteStatusHasSettled(data.workspace?.status) &&
      !["idle", "loading"].includes(data.workspace?.qualificationStatus)
    );
  }
  if (Object.prototype.hasOwnProperty.call(data, "status")) {
    return remoteStatusHasSettled(data.status);
  }
  if (
    typeof data.state === "object" &&
    data.state !== null &&
    Object.prototype.hasOwnProperty.call(data.state, "status")
  ) {
    return remoteStatusHasSettled(data.state.status);
  }
  // Static and platform-only pages do not own an asynchronous controller state.
  return true;
}

function remoteStatusHasSettled(status) {
  return typeof status === "string" && !["idle", "loading", "previewing"].includes(status);
}

async function assertBaselineDeviceProfile(program) {
  const [profileSource, systemInfo] = await Promise.all([
    readFile(baselineProfilePath, "utf8"),
    program.systemInfo(),
  ]);
  const profile = JSON.parse(profileSource);
  assert.equal(profile.schemaVersion, 1, "Unsupported visual baseline profile schema");
  for (const field of [
    "model",
    "platform",
    "windowWidth",
    "windowHeight",
    "pixelRatio",
    "statusBarHeight",
    "fontSizeSetting",
    "language",
    "deviceOrientation",
    "SDKVersion",
  ]) {
    assert.equal(
      systemInfo[field],
      profile[field],
      `Visual baseline device mismatch for ${field}; select the certified profile before capture`,
    );
  }
}

async function setAuthentication(program, authenticated) {
  await program.evaluate(function applyVisualSession(hasSession) {
    const storageKey = "gongkao.auth.session.v1";
    const app = getApp();
    if (hasSession) {
      const session = { accessToken: "visual-access-token", userId: "visual-user" };
      wx.setStorageSync(storageKey, session);
      app.globalData.accessToken = session.accessToken;
      app.globalData.userId = session.userId;
      return;
    }
    wx.removeStorageSync(storageKey);
    delete app.globalData.accessToken;
    delete app.globalData.userId;
  }, authenticated);
}

async function readPng(filename) {
  const source = await readFile(filename);
  return PNG.sync.read(source);
}

async function writePng(filename, image) {
  await writeFile(filename, PNG.sync.write(image));
}
