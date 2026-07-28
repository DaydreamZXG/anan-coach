import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import {
  access,
  readFile,
} from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const captureScriptPath = fileURLToPath(import.meta.url);
const scriptDirectory = path.dirname(captureScriptPath);
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const safetyModulePath = path.resolve(scriptDirectory, "capture-safety.mjs");
const toolGitEvidence = await readToolGitEvidence([
  captureScriptPath,
  safetyModulePath,
]);
const {
  assertDisjointTrees,
  assertRuntimeIdentityMatches,
  assertSafeOutputTarget,
  assertSourceGitEvidenceUnchanged,
  parseLoopbackWebSocketEndpoint,
  parseRuntimeIdentity,
  readCleanSourceGitEvidence,
  requiredFullCommit,
  requiredSafeLabel,
  reserveProfileOutputs,
} = await import(pathToFileURL(safetyModulePath).href);
const sourceRepositoryRoot = path.resolve(
  process.env.WECHAT_AUDIT_SOURCE_REPOSITORY ?? repositoryRoot,
);
const declaredSourceCommit = requiredFullCommit(
  process.env.WECHAT_AUDIT_SOURCE_COMMIT,
  "WECHAT_AUDIT_SOURCE_COMMIT",
);
const wechatSourceRoot = path.resolve(sourceRepositoryRoot, "apps/wechat");
const runtimeProjectRoot = path.resolve(
  process.env.WECHAT_AUDIT_PROJECT ??
    path.resolve(repositoryRoot, "audit/subagents/c/runtime-project"),
);
const auditRoot = path.resolve(
  process.env.WECHAT_AUDIT_OUTPUT_ROOT ??
    path.resolve(repositoryRoot, "audit/subagents/c"),
);
const allowedAuditRoot = path.resolve(repositoryRoot, "audit");
assertAllowedAuditRoot(auditRoot);
assertDisjointTrees(runtimeProjectRoot, auditRoot, {
  first: "Audit runtime",
  second: "audit output",
});

const sourceGitEvidence = await readCleanSourceGitEvidence(
  sourceRepositoryRoot,
  declaredSourceCommit,
);
const sourceCommit = sourceGitEvidence.head;
const runtimeSourceMarker = (
  await readFile(
    path.resolve(runtimeProjectRoot, ".audit-source-commit"),
    "utf8",
  )
).trim();
assert.equal(
  runtimeSourceMarker,
  sourceCommit,
  "Audit runtime was not prepared from the declared source commit",
);
const runtimeIdentity = parseRuntimeIdentity(
  await readFile(
    path.resolve(runtimeProjectRoot, ".audit-runtime-identity.json"),
    "utf8",
  ),
  sourceCommit,
);
const runtimeEnvironmentSource = await readFile(
  path.resolve(runtimeProjectRoot, "miniprogram/config/environment.ts"),
  "utf8",
);
const runtimeHttpUrls = [
  ...new Set(runtimeEnvironmentSource.match(/https?:\/\/[^"'\s]+/gu) ?? []),
].sort();
assert.deepEqual(
  runtimeHttpUrls,
  ["http://127.0.0.1:9"],
  "Audit runtime environment contains a non-local URL",
);

const [
  { default: automator },
  { readPageData, setPageData },
  { consoleEventText, isActionableRuntimeWarning },
  { visualDeliveryContracts },
  { visualScenarios },
] = await Promise.all([
  importSourceModule("node_modules/miniprogram-automator/out/index.js"),
  importSourceModule("e2e/page-data-bridge.mjs"),
  importSourceModule("e2e/runtime-warning-policy.mjs"),
  importSourceModule("e2e/visual-delivery-contracts.mjs"),
  importSourceModule("e2e/visual-scenarios.mjs"),
]);

const cliPath =
  process.env.WECHAT_DEVTOOLS_CLI ??
  "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const profile = requiredSafeLabel(process.env.AUDIT_PROFILE ?? "standard");
const expectedWidth = optionalPositiveInteger(process.env.AUDIT_EXPECT_WIDTH);
const expectedHeight = optionalPositiveInteger(process.env.AUDIT_EXPECT_HEIGHT);
const expectedOrientation = optionalOrientation(
  process.env.AUDIT_EXPECT_ORIENTATION,
);
const scenarioFilter = new Set(
  (process.env.AUDIT_SCENARIOS ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);

const packageAssetScenarios = [
  "thinking",
  "reminder",
  "encouragement",
  "rest",
  "deprioritize",
  "growth",
].map((kind) => ({
  id: `z-anan-${kind}-asset-entry`,
  route: `/packages/anan-${kind}/asset-entry/index`,
  authenticated: false,
  skipControllerSettlement: true,
  data: {},
  auditSupplement: true,
}));

const allScenarios = [...visualScenarios, ...packageAssetScenarios];
for (const scenario of allScenarios) {
  requiredSafeLabel(scenario.id, "Audit scenario");
}
const selectedScenarios = scenarioFilter.size
  ? allScenarios.filter((scenario) => scenarioFilter.has(scenario.id))
  : allScenarios;
assert.equal(
  selectedScenarios.length,
  scenarioFilter.size || allScenarios.length,
  `Unknown scenario filter: ${[...scenarioFilter].filter((id) => !allScenarios.some((scenario) => scenario.id === id)).join(", ")}`,
);

const deliveryByScenario = new Map(
  visualDeliveryContracts.map((contract) => [contract.scenarioId, contract]),
);
const automationEndpoint = parseLoopbackWebSocketEndpoint(
  process.env.WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT,
);
await access(
  path.resolve(runtimeProjectRoot, "project.config.json"),
  constants.R_OK,
);
if (automationEndpoint === null) await access(cliPath, constants.X_OK);

const startedAt = new Date().toISOString();
const ownsProgram = automationEndpoint === null;
let program;
let runtimeIdentityEvidence;
let outputReservation;
try {
  program = automationEndpoint
    ? await connectWithRetry(automationEndpoint, 30_000)
    : await automator.launch({
        cliPath,
        projectPath: runtimeProjectRoot,
        timeout: 120_000,
        trustProject: true,
      });
  const runningRuntimeIdentity = parseRuntimeIdentity(
    await readRunningRuntimeIdentity(program),
    sourceCommit,
  );
  runtimeIdentityEvidence = assertRuntimeIdentityMatches(
    runtimeIdentity,
    runningRuntimeIdentity,
  );
  outputReservation = await reserveProfileOutputs({
    allowedRoot: allowedAuditRoot,
    auditRoot,
    profile,
  });
} catch (error) {
  await releaseProgram(program, ownsProgram);
  throw error;
}

const {
  profileScreenshotDirectory,
  profileLogPath,
  profileManifestPath,
  logHandle: profileLogHandle,
  manifestHandle: profileManifestHandle,
} = outputReservation;

const runtimeFailures = [];
const actionableWarnings = [];
const allConsoleEvents = [];
const records = [];
let guardEvidence = null;
program.on("exception", (event) => {
  runtimeFailures.push({
    at: new Date().toISOString(),
    message: String(event?.message ?? event),
  });
});
program.on("error", (event) => {
  runtimeFailures.push({
    at: new Date().toISOString(),
    message: `automation error event: ${safeJson(event)}`,
  });
});
program.on("console", (event) => {
  const message = consoleEventText(event);
  const record = { at: new Date().toISOString(), message };
  allConsoleEvents.push(record);
  if (isActionableRuntimeWarning(message)) actionableWarnings.push(record);
});

let systemInfo;
let chromeMetrics;
let fatalError = null;
let sourceIntegrity;

try {
  systemInfo = await withTimeout(
    program.systemInfo(),
    20_000,
    "systemInfo timed out",
  );
  chromeMetrics = await readSystemChromeMetrics(program);
  validateExpectedProfile(systemInfo);
  await freezeVisualAnimationClock(program);
  guardEvidence = await installNetworkAndPlatformGuards(program);

  for (const [index, scenario] of selectedScenarios.entries()) {
    const failureOffset = runtimeFailures.length;
    const warningOffset = actionableWarnings.length;
    const consoleOffset = allConsoleEvents.length;
    const record = {
      id: scenario.id,
      route: scenario.route,
      routePath: routePath(scenario.route),
      authenticated: scenario.authenticated === true,
      source: scenario.auditSupplement
        ? "audit-supplement"
        : "existing-visual-scenario",
      profile,
      status: "pending",
      stage: "pending",
      screenshot: "",
      screenshotWidth: null,
      screenshotHeight: null,
      screenshotNativeChromeInset: null,
      activeRoute: "",
      routeSystemInfo: null,
      semantic: null,
      layoutEvidence: null,
      viewport: null,
      runtimeFailures: [],
      actionableWarnings: [],
      consoleEvents: [],
      error: "",
      startedAt: new Date().toISOString(),
      completedAt: "",
    };
    process.stdout.write(
      `[${String(index + 1).padStart(2, "0")}/${String(selectedScenarios.length)}] ${scenario.id} ... `,
    );
    let abortAfterRecord = false;
    try {
      await withTimeout(
        captureScenario(program, scenario, record),
        60_000,
        `${scenario.id} did not finish within 60 seconds`,
      );
      record.status = "captured";
      process.stdout.write("captured\n");
    } catch (error) {
      record.status = "failed";
      record.error = `${errorText(error)}\nlastStage=${record.stage}`;
      abortAfterRecord = record.error.includes("did not finish within");
      process.stdout.write(`failed: ${record.error}\n`);
    }
    record.runtimeFailures = runtimeFailures.slice(failureOffset);
    record.actionableWarnings = actionableWarnings.slice(warningOffset);
    record.consoleEvents = allConsoleEvents.slice(consoleOffset);
    if (record.runtimeFailures.length > 0 && record.status === "captured") {
      record.status = "captured-with-runtime-error";
    }
    if (record.actionableWarnings.length > 0 && record.status === "captured") {
      record.status = "captured-with-warning";
    }
    record.completedAt = new Date().toISOString();
    records.push(record);
    await appendJsonLine(profileLogHandle, { type: "scenario", ...record });
    if (abortAfterRecord) {
      fatalError = `Aborted after non-cancellable scenario timeout: ${scenario.id}`;
      break;
    }
  }
} catch (error) {
  fatalError = errorText(error);
  process.exitCode = 1;
} finally {
  try {
    await withTimeout(
      setAuthentication(program, false),
      5_000,
      "Auth fixture cleanup timed out",
    );
  } catch (error) {
    runtimeFailures.push({
      at: new Date().toISOString(),
      message: `cleanup: ${errorText(error)}`,
    });
  }
  try {
    await withTimeout(
      releaseProgram(program, ownsProgram),
      15_000,
      "DevTools automation close timed out",
    );
  } catch (error) {
    runtimeFailures.push({
      at: new Date().toISOString(),
      message: `close: ${errorText(error)}`,
    });
  }
}

try {
  const finalSourceGitEvidence = await readCleanSourceGitEvidence(
    sourceRepositoryRoot,
    declaredSourceCommit,
  );
  sourceIntegrity = assertSourceGitEvidenceUnchanged(
    sourceGitEvidence,
    finalSourceGitEvidence,
  );
} catch (error) {
  const sourceIntegrityError = errorText(error);
  sourceIntegrity = {
    validation: "failed",
    initial: sourceGitEvidence,
    final: null,
    error: sourceIntegrityError,
  };
  fatalError = fatalError
    ? `${fatalError}\nSource integrity failure: ${sourceIntegrityError}`
    : `Source integrity failure: ${sourceIntegrityError}`;
  process.exitCode = 1;
}

const manifest = {
  schemaVersion: 2,
  auditKind: "wechat-devtools-real-render",
  captureTool: toolGitEvidence,
  projectSource: displayPath(runtimeProjectRoot),
  sourceRepository: displayPath(sourceRepositoryRoot),
  sourceCommit,
  sourceGitEvidence,
  sourceIntegrity,
  runtimeSourceMarker,
  runtimeIdentity: runtimeIdentityEvidence,
  automationBinding: {
    mode: ownsProgram ? "launched-prepared-runtime" : "connected-loopback",
    endpoint: automationEndpoint,
    runtimeIdentityValidation: runtimeIdentityEvidence.validation,
  },
  profile,
  startedAt,
  completedAt: new Date().toISOString(),
  devtoolsCli: cliPath,
  systemInfo: systemInfo ?? null,
  systemChrome: chromeMetrics ?? null,
  expectedViewport:
    expectedWidth || expectedHeight || expectedOrientation
      ? {
          width: expectedWidth ?? null,
          height: expectedHeight ?? null,
          orientation: expectedOrientation,
        }
      : null,
  guards: {
    startupRuntimeConfiguration: "local-only",
    allowedHttpUrls: runtimeHttpUrls,
    installation: guardEvidence,
    evidenceBoundary:
      "Local-only runtime plus installed platform mocks; this is prevention evidence, not packet capture.",
  },
  scenarioCount: selectedScenarios.length,
  capturedCount: records.filter((record) =>
    record.status.startsWith("captured"),
  ).length,
  failedCount: records.filter((record) => record.status === "failed").length,
  uniqueRouteCount: new Set(records.map((record) => record.routePath)).size,
  runtimeFailureCount: runtimeFailures.length,
  actionableWarningCount: actionableWarnings.length,
  fatalError,
  records,
};
try {
  await profileLogHandle.sync();
  await profileManifestHandle.writeFile(
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );
  await profileManifestHandle.sync();
} finally {
  await Promise.allSettled([
    profileLogHandle.close(),
    profileManifestHandle.close(),
  ]);
}
process.stdout.write(
  `Manifest: ${path.relative(repositoryRoot, profileManifestPath)}; ` +
    `captured ${manifest.capturedCount}/${manifest.scenarioCount}; ` +
    `routes ${manifest.uniqueRouteCount}; failures ${manifest.failedCount}.\n`,
);
if (
  fatalError ||
  manifest.failedCount > 0 ||
  runtimeFailures.length > 0 ||
  actionableWarnings.length > 0
)
  process.exitCode = 1;

async function captureScenario(miniProgram, scenario, record) {
  record.stage = "leave-login-controller";
  await leaveLoginControllerBeforeAuthFixture(miniProgram);
  record.stage = "set-authentication";
  await setAuthentication(miniProgram, scenario.authenticated === true);
  record.stage = "relaunch";
  const page = await miniProgram.reLaunch(scenario.route);
  assert.ok(page, `Unable to open ${scenario.route}`);
  record.stage = "controller-settlement";
  await waitForControllerSettlement(miniProgram, page, scenario);

  const fixtureData = structuredClone(scenario.data);
  record.stage = "set-fixture-initial";
  await setPageData(miniProgram, page, fixtureData);
  record.stage = "layout-settlement";
  await page.waitFor(700);
  if (scenario.scrollTop) {
    record.stage = "scroll";
    await miniProgram.pageScrollTo(scenario.scrollTop);
    await page.waitFor(250);
  }
  record.stage = "set-fixture-final";
  await setPageData(miniProgram, page, fixtureData);
  record.stage = "final-frame";
  await page.waitFor(150);

  record.stage = "active-route";
  record.activeRoute = await activeRoute(miniProgram);
  assert.equal(
    record.activeRoute,
    routePath(scenario.route).replace(/^\//u, ""),
    `${scenario.id} navigated to ${record.activeRoute}`,
  );
  record.stage = "viewport-evidence";
  record.viewport = await readViewportEvidence(miniProgram, page);
  record.stage = "semantic-evidence";
  record.semantic = await readSemanticEvidence(miniProgram, page, scenario);
  record.stage = "route-system-info";
  record.routeSystemInfo = await withTimeout(
    miniProgram.systemInfo(),
    10_000,
    `${scenario.id} route systemInfo timed out`,
  );
  validateExpectedProfile(record.routeSystemInfo, scenario.id);
  record.stage = "supplemental-layout-evidence";
  record.layoutEvidence = await readSupplementalLayoutEvidence(
    miniProgram,
    page,
    scenario,
    record.routeSystemInfo,
  );

  const screenshotPath = path.resolve(
    profileScreenshotDirectory,
    `${requiredSafeLabel(scenario.id, "Audit scenario")}.png`,
  );
  assertAllowedOutput(screenshotPath);
  await assertSafeOutputTarget(allowedAuditRoot, screenshotPath);
  await assertSafeOutputTarget(profileScreenshotDirectory, screenshotPath);
  await assertPathAbsent(screenshotPath);
  record.stage = "screenshot";
  await miniProgram.screenshot({ path: screenshotPath });
  record.stage = "screenshot-dimensions";
  const dimensions = await readPngDimensions(screenshotPath);
  const orientedSystemViewport = orientedViewport(record.routeSystemInfo);
  record.screenshot = path.relative(repositoryRoot, screenshotPath);
  record.screenshotWidth = dimensions.width;
  record.screenshotHeight = dimensions.height;
  record.screenshotNativeChromeInset =
    orientedSystemViewport.height - dimensions.height;
  assert.equal(
    dimensions.width,
    orientedSystemViewport.width,
    `${scenario.id} screenshot width differs from DevTools systemInfo`,
  );
  if (expectedWidth !== null) {
    assert.equal(
      dimensions.width,
      expectedWidth,
      `AUDIT_PROFILE=${profile} ${scenario.id} expected rendered width ${expectedWidth}, got ${String(dimensions.width)}`,
    );
  }
  assert.ok(
    record.screenshotNativeChromeInset >= 0 &&
      record.screenshotNativeChromeInset <= 120,
    `${scenario.id} screenshot height has unexpected native chrome inset: ` +
      `${String(record.screenshotNativeChromeInset)}px`,
  );
  record.stage = "completed";
}

async function readSemanticEvidence(miniProgram, page, scenario) {
  const contract = deliveryByScenario.get(scenario.id);
  if (!contract) return null;
  const semanticGeometry = await readSelectorGeometry(
    miniProgram,
    page,
    contract.semanticSelector,
  );
  const primaryGeometry = contract.primarySelector
    ? await readSelectorGeometry(miniProgram, page, contract.primarySelector)
    : null;
  const orientedSystemViewport = orientedViewport(systemInfo);
  return {
    figmaNodeId: contract.figmaNodeId,
    routeState: contract.routeState ?? null,
    semanticSelector: contract.semanticSelector,
    semanticGeometry,
    primarySelector: contract.primarySelector ?? null,
    primaryGeometry,
    primaryTouchHeightPass:
      primaryGeometry === null ? null : Number(primaryGeometry.height) >= 44,
    semanticHorizontalOverflow:
      semanticGeometry === null
        ? null
        : semanticGeometry.left < -1 ||
          semanticGeometry.left + semanticGeometry.width >
            orientedSystemViewport.width + 1,
    primaryHorizontalOverflow:
      primaryGeometry === null
        ? null
        : primaryGeometry.left < -1 ||
          primaryGeometry.left + primaryGeometry.width >
            orientedSystemViewport.width + 1,
  };
}

async function readSelectorGeometry(miniProgram, page, selector) {
  const payload = await miniProgram.evaluate(
    function queryCurrentPageGeometry(expectedRoute, targetSelector) {
      return new Promise((resolve) => {
        const pages = getCurrentPages();
        const current = pages[pages.length - 1];
        if (!current || current.route !== expectedRoute) {
          resolve(
            JSON.stringify({
              error: "route_mismatch",
              actualRoute: current?.route ?? null,
              expectedRoute,
            }),
          );
          return;
        }
        current
          .createSelectorQuery()
          .select(targetSelector)
          .fields(
            {
              rect: true,
              size: true,
              computedStyle: ["display", "visibility", "opacity"],
            },
            (result) => resolve(JSON.stringify({ result: result ?? null })),
          )
          .exec();
      });
    },
    page.path,
    selector,
  );
  const parsed = JSON.parse(String(payload));
  if (parsed.error || !parsed.result) return null;
  if (
    parsed.result.display === "none" ||
    parsed.result.visibility === "hidden" ||
    parsed.result.opacity === "0"
  ) {
    return null;
  }
  return {
    left: numericCssValue(parsed.result.left),
    top: numericCssValue(parsed.result.top),
    width: numericCssValue(parsed.result.width),
    height: numericCssValue(parsed.result.height),
  };
}

async function readSelectorGeometries(miniProgram, page, selector) {
  const payload = await miniProgram.evaluate(
    function queryCurrentPageGeometries(expectedRoute, targetSelector) {
      return new Promise((resolve) => {
        const pages = getCurrentPages();
        const current = pages[pages.length - 1];
        if (!current || current.route !== expectedRoute) {
          resolve(
            JSON.stringify({
              error: "route_mismatch",
              actualRoute: current?.route ?? null,
              expectedRoute,
            }),
          );
          return;
        }
        current
          .createSelectorQuery()
          .selectAll(targetSelector)
          .fields(
            {
              rect: true,
              size: true,
              computedStyle: ["display", "visibility", "opacity"],
            },
            (results) => resolve(JSON.stringify({ results: results ?? [] })),
          )
          .exec();
      });
    },
    page.path,
    selector,
  );
  const parsed = JSON.parse(String(payload));
  if (parsed.error) return [];
  return parsed.results
    .filter(
      (result) =>
        result.display !== "none" &&
        result.visibility !== "hidden" &&
        result.opacity !== "0",
    )
    .map((result) => ({
      left: numericCssValue(result.left),
      top: numericCssValue(result.top),
      width: numericCssValue(result.width),
      height: numericCssValue(result.height),
    }));
}

async function readSupplementalLayoutEvidence(
  miniProgram,
  page,
  scenario,
  routeSystemInfo,
) {
  if (scenario.id === "h17-capture-camera-init-error") {
    const selectorEntries = await Promise.all(
      [
        ["error", ".camera-error"],
        ["controls", ".camera-controls"],
        ["shutter", ".camera-shutter"],
        ["privacy", ".camera-privacy"],
        ["alternateEntry", ".capture-channel-entry"],
      ].map(async ([name, selector]) => [
        name,
        await readSelectorGeometry(miniProgram, page, selector),
      ]),
    );
    const selectors = Object.fromEntries(selectorEntries);
    return {
      kind: "capture-error-recovery-layout",
      selectors,
      overlaps: {
        errorWithControls: rectanglesOverlap(
          selectors.error,
          selectors.controls,
        ),
        errorWithShutter: rectanglesOverlap(selectors.error, selectors.shutter),
      },
      withinViewport: Object.fromEntries(
        Object.entries(selectors).map(([name, rectangle]) => [
          name,
          rectangleWithinViewport(rectangle, routeSystemInfo),
        ]),
      ),
    };
  }
  if (scenario.id === "f12-essay-grounding-evidence") {
    const [title, phrases] = await Promise.all([
      readSelectorGeometry(miniProgram, page, ".essay-evidence-title"),
      readSelectorGeometries(
        miniProgram,
        page,
        ".essay-evidence-title__phrase",
      ),
    ]);
    return {
      kind: "essay-semantic-title-wrap",
      title,
      phrases,
      phraseCount: phrases.length,
      titleWithinViewport: rectangleWithinViewport(title, routeSystemInfo),
      phrasesWithinViewport: phrases.map((rectangle) =>
        rectangleWithinViewport(rectangle, routeSystemInfo),
      ),
    };
  }
  return null;
}

async function readViewportEvidence(miniProgram, page) {
  const payload = await miniProgram.evaluate(function queryViewport(
    expectedRoute,
  ) {
    return new Promise((resolve) => {
      const pages = getCurrentPages();
      const current = pages[pages.length - 1];
      if (!current || current.route !== expectedRoute) {
        resolve(
          JSON.stringify({
            error: "route_mismatch",
            actualRoute: current?.route ?? null,
            expectedRoute,
          }),
        );
        return;
      }
      current
        .createSelectorQuery()
        .selectViewport()
        .scrollOffset((result) =>
          resolve(JSON.stringify({ result: result ?? null })),
        )
        .exec();
    });
  }, page.path);
  const parsed = JSON.parse(String(payload));
  return parsed.error ? { error: parsed.error } : parsed.result;
}

async function leaveLoginControllerBeforeAuthFixture(miniProgram) {
  const route = await activeRoute(miniProgram);
  if (route !== "pages/login/index") return;
  const page = await miniProgram.reLaunch("/pages/support/index");
  assert.ok(
    page,
    "Unable to unload login controller before changing auth fixture",
  );
}

async function activeRoute(miniProgram) {
  return miniProgram.evaluate(function readActiveRoute() {
    const pages = getCurrentPages();
    return pages[pages.length - 1]?.route ?? "";
  });
}

async function waitForControllerSettlement(miniProgram, page, scenario) {
  if (
    scenario.authenticated !== true ||
    scenario.skipControllerSettlement === true
  )
    return;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const data = await readPageData(miniProgram, page);
    if (controllerHasSettled(scenario.route, data)) return;
    await page.waitFor(100);
  }
  assert.fail(`${scenario.id} controller did not settle within 10 seconds`);
}

function controllerHasSettled(route, data) {
  const pathName = routePath(route);
  if (pathName === "/pages/anan/index") return true;
  if (pathName.startsWith("/pages/community-"))
    return remoteStatusHasSettled(data.pageState);
  if (pathName === "/pages/onboarding/index")
    return data.loadingProfile === false;
  if (pathName === "/pages/positions/index") {
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
  return true;
}

function remoteStatusHasSettled(status) {
  return (
    typeof status === "string" &&
    !["idle", "loading", "previewing"].includes(status)
  );
}

async function installNetworkAndPlatformGuards(miniProgram) {
  await miniProgram.mockWxMethod("request", {
    data: {
      code: "ui_audit_network_blocked",
      message: "Read-only UI audit blocks all network requests.",
      requestId: "ui-audit-network-guard",
    },
    statusCode: 503,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  });
  await miniProgram.mockWxMethod("uploadFile", {
    errMsg: "uploadFile:fail ui audit blocked",
  });
  await miniProgram.mockWxMethod("downloadFile", {
    errMsg: "downloadFile:fail ui audit blocked",
    statusCode: 503,
    tempFilePath: "",
  });
  await miniProgram.mockWxMethod("requestPayment", {
    errMsg: "requestPayment:fail ui audit blocked",
  });
  await miniProgram.mockWxMethod("requestVirtualPayment", {
    errMsg: "requestVirtualPayment:fail ui audit blocked",
  });
  const requestProbePayload = await miniProgram.evaluate(
    function probeAuditRequestGuard() {
      return new Promise((resolve) => {
        wx.request({
          url: "http://127.0.0.1:9/audit-network-guard-probe",
          success: (response) =>
            resolve(
              JSON.stringify({
                callback: "success",
                statusCode: response.statusCode,
                code: response.data?.code ?? null,
              }),
            ),
          fail: (error) =>
            resolve(
              JSON.stringify({
                callback: "fail",
                errMsg: error?.errMsg ?? String(error),
              }),
            ),
        });
      });
    },
  );
  const requestProbe = JSON.parse(String(requestProbePayload));
  assert.deepEqual(requestProbe, {
    callback: "success",
    statusCode: 503,
    code: "ui_audit_network_blocked",
  });
  return {
    wxRequest: "mocked-503-and-probed",
    wxUploadFile: "mock-installed",
    wxDownloadFile: "mock-installed",
    wxRequestPayment: "mock-installed",
    wxRequestVirtualPayment: "mock-installed",
    requestProbe,
  };
}

async function setAuthentication(miniProgram, authenticated) {
  await miniProgram.evaluate(function applyAuditSession(hasSession) {
    const storageKey = "gongkao.auth.session.v1";
    const app = getApp();
    if (hasSession) {
      const session = {
        accessToken: "ui-audit-access-token",
        userId: "ui-audit-user",
      };
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

async function freezeVisualAnimationClock(miniProgram) {
  await miniProgram.evaluate(function installAuditVisualClock() {
    if (globalThis.__uiAuditOriginalSetInterval) return;
    const originalSetInterval = globalThis.setInterval;
    globalThis.__uiAuditOriginalSetInterval = originalSetInterval;
    globalThis.setInterval = function uiAuditSetInterval(
      callback,
      delay,
      ...args
    ) {
      if (delay === 125 || delay === 1_000) {
        return originalSetInterval(function holdAuditFrame() {}, delay);
      }
      return originalSetInterval(callback, delay, ...args);
    };
  });
}

async function readSystemChromeMetrics(miniProgram) {
  return miniProgram.evaluate(function readChrome() {
    const system = wx.getSystemInfoSync();
    const menu = wx.getMenuButtonBoundingClientRect();
    return {
      statusBarHeight: system.statusBarHeight ?? 0,
      windowWidth: system.windowWidth,
      windowHeight: system.windowHeight,
      safeArea: system.safeArea ?? null,
      menuButton: {
        left: menu.left,
        right: menu.right,
        top: menu.top,
        bottom: menu.bottom,
        width: menu.width,
        height: menu.height,
      },
    };
  });
}

function validateExpectedProfile(info, scenarioId = "initial-session") {
  const orientation = String(info.deviceOrientation ?? "portrait");
  if (expectedOrientation !== null) {
    assert.equal(
      orientation,
      expectedOrientation,
      `AUDIT_PROFILE=${profile} ${scenarioId} expected orientation ${expectedOrientation}, got ${orientation} (${String(info.model)})`,
    );
  }
  const expectedSystemWidth =
    expectedOrientation === "landscape" ? expectedHeight : expectedWidth;
  const expectedSystemHeight =
    expectedOrientation === "landscape" ? expectedWidth : expectedHeight;
  if (expectedSystemWidth !== null) {
    assert.equal(
      Number(info.screenWidth ?? info.windowWidth),
      expectedSystemWidth,
      `AUDIT_PROFILE=${profile} ${scenarioId} expected DevTools base screen width ${String(expectedSystemWidth)}, got ${String(info.screenWidth ?? info.windowWidth)} (${String(info.model)}; orientation=${orientation})`,
    );
  }
  if (expectedSystemHeight !== null) {
    assert.equal(
      Number(info.screenHeight ?? info.windowHeight),
      expectedSystemHeight,
      `AUDIT_PROFILE=${profile} ${scenarioId} expected DevTools base screen height ${String(expectedSystemHeight)}, got ${String(info.screenHeight ?? info.windowHeight)} (${String(info.model)}; orientation=${orientation})`,
    );
  }
}

function orientedViewport(info) {
  const width = Number(info.windowWidth);
  const height = Number(info.windowHeight);
  return String(info.deviceOrientation ?? "portrait") === "landscape"
    ? { width: height, height: width }
    : { width, height };
}

async function readPngDimensions(filename) {
  const header = await readFile(filename);
  assert.ok(header.length >= 24, `${filename} is not a complete PNG`);
  assert.equal(header.toString("ascii", 1, 4), "PNG", `${filename} is not PNG`);
  return {
    width: header.readUInt32BE(16),
    height: header.readUInt32BE(20),
  };
}

async function appendJsonLine(fileHandle, value) {
  await fileHandle.appendFile(`${JSON.stringify(value)}\n`, "utf8");
}

function routePath(route) {
  return route.split("?", 1)[0];
}

function numericCssValue(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function optionalPositiveInteger(value) {
  if (value === undefined || value === "") return null;
  const parsed = Number(value);
  assert.ok(
    Number.isInteger(parsed) && parsed > 0,
    `Expected a positive integer, got ${value}`,
  );
  return parsed;
}

function optionalOrientation(value) {
  if (value === undefined || value === "") return null;
  assert.ok(
    value === "portrait" || value === "landscape",
    `Expected portrait or landscape, got ${value}`,
  );
  return value;
}

function assertAllowedOutput(filename) {
  const relative = path.relative(auditRoot, filename);
  assert.ok(
    relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative),
    `Refusing output outside audit root: ${filename}`,
  );
}

async function assertPathAbsent(filename) {
  try {
    await access(filename);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  assert.fail(`Refusing to overwrite existing audit evidence: ${filename}`);
}

function assertAllowedAuditRoot(filename) {
  const relative = path.relative(allowedAuditRoot, filename);
  assert.ok(
    relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative),
    `Refusing audit root outside ${allowedAuditRoot}: ${filename}`,
  );
}

function importSourceModule(relativePath) {
  const filename = path.resolve(wechatSourceRoot, relativePath);
  const relative = path.relative(wechatSourceRoot, filename);
  assert.ok(
    relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative),
    `Refusing source module outside apps/wechat: ${filename}`,
  );
  return import(pathToFileURL(filename).href);
}

function displayPath(filename) {
  const relative = path.relative(repositoryRoot, filename);
  return relative !== "" &&
    !relative.startsWith("..") &&
    !path.isAbsolute(relative)
    ? relative
    : filename;
}

function rectanglesOverlap(first, second) {
  if (!first || !second) return null;
  return (
    first.left < second.left + second.width &&
    first.left + first.width > second.left &&
    first.top < second.top + second.height &&
    first.top + first.height > second.top
  );
}

function rectangleWithinViewport(rectangle, routeSystemInfo) {
  if (!rectangle) return null;
  const { width, height } = orientedViewport(routeSystemInfo);
  return (
    rectangle.left >= -1 &&
    rectangle.top >= -1 &&
    rectangle.left + rectangle.width <= width + 1 &&
    rectangle.top + rectangle.height <= height + 1
  );
}

function safeJson(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function errorText(error) {
  return String(error?.stack ?? error?.message ?? error);
}

async function readToolGitEvidence(filenames) {
  const { stdout: headOutput } = await execFileAsync(
    "git",
    ["-C", repositoryRoot, "rev-parse", "--verify", "HEAD"],
    { encoding: "utf8" },
  );
  const commit = headOutput.trim();
  assert.match(
    commit,
    /^[0-9a-f]{40}$/u,
    "Capture tool repository HEAD must be a full Git commit",
  );
  const files = [];
  for (const filename of filenames) {
    const relativePath = path.relative(repositoryRoot, filename);
    assert.ok(
      relativePath !== "" &&
        !relativePath.startsWith("..") &&
        !path.isAbsolute(relativePath),
      `Capture tool file is outside its repository: ${filename}`,
    );
    const [
      { stdout: treeBlobOutput },
      { stdout: workingBlobOutput },
      { stdout: statusOutput },
    ] = await Promise.all([
      execFileAsync(
        "git",
        ["-C", repositoryRoot, "rev-parse", `HEAD:${relativePath}`],
        { encoding: "utf8" },
      ),
      execFileAsync("git", ["-C", repositoryRoot, "hash-object", filename], {
        encoding: "utf8",
      }),
      execFileAsync(
        "git",
        [
          "-C",
          repositoryRoot,
          "status",
          "--porcelain=v1",
          "--untracked-files=normal",
          "--",
          relativePath,
        ],
        { encoding: "utf8" },
      ),
    ]);
    const treeBlob = treeBlobOutput.trim();
    const workingBlob = workingBlobOutput.trim();
    const status = statusOutput.trim();
    assert.equal(
      workingBlob,
      treeBlob,
      `Capture tool working blob differs from HEAD:${relativePath}`,
    );
    assert.equal(
      status,
      "",
      `Capture tool path is dirty: ${relativePath}\n${status}`,
    );
    files.push({
      path: relativePath,
      treeBlob,
      workingBlob,
      clean: true,
    });
  }
  return {
    repository: repositoryRoot,
    commit,
    files,
  };
}

async function readRunningRuntimeIdentity(miniProgram) {
  const payload = await withTimeout(
    miniProgram.evaluate(function readAuditRuntimeIdentity() {
      const app = getApp();
      return JSON.stringify(
        app?.globalData?.__wechatAuditRuntimeIdentity ?? null,
      );
    }),
    10_000,
    "Audit runtime identity probe timed out",
  );
  return JSON.parse(String(payload));
}

async function releaseProgram(miniProgram, ownsSession) {
  if (!miniProgram) return;
  if (ownsSession) {
    await miniProgram.close();
    return;
  }
  miniProgram.disconnect();
}

async function connectWithRetry(wsEndpoint, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      return await automator.connect({ wsEndpoint });
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw new Error(
    `Failed connecting to ${wsEndpoint} within ${String(timeoutMs)}ms: ${errorText(lastError)}`,
  );
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
