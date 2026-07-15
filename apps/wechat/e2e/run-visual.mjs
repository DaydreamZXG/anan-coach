import assert from "node:assert/strict";
import { access, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import automator from "miniprogram-automator";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

import { visualScenarios } from "./visual-scenarios.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDirectory, "..");
const baselineDirectory = path.resolve(projectRoot, "tests/visual/baselines");
const artifactDirectory = path.resolve(projectRoot, "tests/visual/artifacts");
const cliPath =
  process.env.WECHAT_DEVTOOLS_CLI ?? "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const updateBaselines = process.argv.includes("--update");
const maximumDifferentPixelRatio = 0.003;
const scenarioFilter = process.env.VISUAL_SCENARIO?.trim() ?? "";
const selectedScenarios = scenarioFilter
  ? visualScenarios.filter((scenario) => scenario.id === scenarioFilter)
  : visualScenarios;

assert.ok(selectedScenarios.length > 0, `Unknown visual scenario: ${scenarioFilter}`);

await access(cliPath, constants.X_OK);
await mkdir(baselineDirectory, { recursive: true });
await rm(artifactDirectory, { recursive: true, force: true });
await mkdir(artifactDirectory, { recursive: true });

const miniProgram = await automator.launch({
  cliPath,
  projectPath: projectRoot,
  timeout: 60_000,
  trustProject: true,
});
const runtimeFailures = [];
miniProgram.on("exception", (event) => runtimeFailures.push(String(event?.message ?? event)));
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
    await captureScenario(miniProgram, scenario);
  }
} finally {
  await miniProgram.close();
}

assert.deepEqual(
  runtimeFailures,
  [],
  `Mini Program runtime exceptions:\n${runtimeFailures.join("\n")}`,
);
process.stdout.write(
  `${updateBaselines ? "Updated" : "Verified"} ${String(selectedScenarios.length)} visual scenarios.\n`,
);

async function captureScenario(program, scenario) {
  assert.match(scenario.id, /^[a-z0-9][a-z0-9-]+$/u);
  assert.ok(scenario.route.startsWith("/pages/"));
  await setAuthentication(program, scenario.authenticated === true);
  const page = await program.reLaunch(scenario.route);
  assert.ok(page, `Unable to open ${scenario.route}`);
  await waitForControllerSettlement(page, scenario);
  const resetData = Object.fromEntries(
    Object.entries(scenario.data)
      .filter(([, value]) => typeof value === "object" && value !== null)
      .map(([key, value]) => [key, Array.isArray(value) ? [] : {}]),
  );
  if (Object.keys(resetData).length > 0) {
    await page.setData(resetData);
  }
  await page.setData(flattenSetData(structuredClone(scenario.data)));
  await page.waitFor(800);
  if (scenario.scrollTop) {
    await program.pageScrollTo(scenario.scrollTop);
    await page.waitFor(200);
  }
  const appliedData = await page.data();
  for (const [key, expected] of Object.entries(scenario.data)) {
    assert.deepEqual(appliedData[key], expected, `${scenario.id} fixture was overwritten: ${key}`);
  }

  const currentPath = path.resolve(artifactDirectory, `${scenario.id}.png`);
  const baselinePath = path.resolve(baselineDirectory, `${scenario.id}.png`);
  await program.screenshot({ path: currentPath });
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

function flattenSetData(data) {
  const result = {};
  for (const [key, value] of Object.entries(data)) flattenValue(key, value, result);
  return result;
}

function flattenValue(path, value, result) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    result[path] = value;
    return;
  }
  const entries = Object.entries(value);
  if (entries.length === 0) {
    result[path] = {};
    return;
  }
  for (const [key, child] of entries) flattenValue(`${path}.${key}`, child, result);
}

async function waitForControllerSettlement(page, scenario) {
  if (scenario.authenticated !== true) return;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const data = await page.data();
    if (controllerHasSettled(scenario.route, data)) return;
    await page.waitFor(100);
  }
  assert.fail(`${scenario.id} did not settle its page controller before fixture injection`);
}

function controllerHasSettled(route, data) {
  const path = route.split("?", 1)[0];
  if (path === "/pages/onboarding/index") return data.loadingProfile === false;
  if (path === "/pages/positions/index") {
    return (
      remoteStatusHasSettled(data.workspace?.status) &&
      !["idle", "loading"].includes(data.workspace?.qualificationStatus)
    );
  }
  return remoteStatusHasSettled(data.state?.status);
}

function remoteStatusHasSettled(status) {
  return typeof status === "string" && !["idle", "loading", "previewing"].includes(status);
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
