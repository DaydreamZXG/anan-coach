import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import {
  access,
  constants,
  lstat,
  mkdir,
  readFile,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const scriptPath = fileURLToPath(import.meta.url);
const repositoryRoot = path.resolve(path.dirname(scriptPath), "../../..");
const auditRoot = path.resolve(repositoryRoot, "audit");
const safetyModulePath = path.resolve(path.dirname(scriptPath), "capture-safety.mjs");
const {
  assertDisjointTrees,
  assertRuntimeIdentityMatches,
  assertSafeOutputTarget,
  assertSourceGitEvidenceUnchanged,
  parseLoopbackWebSocketEndpoint,
  parseRuntimeIdentity,
  readCleanSourceGitEvidence,
  requiredFullCommit,
} = await import(pathToFileURL(safetyModulePath).href);

const sourceRepository = path.resolve(requiredEnv("WECHAT_AUDIT_SOURCE_REPOSITORY"));
const sourceCommit = requiredFullCommit(
  requiredEnv("WECHAT_AUDIT_SOURCE_COMMIT"),
  "WECHAT_AUDIT_SOURCE_COMMIT",
);
const runtimeRoot = path.resolve(requiredEnv("WECHAT_AUDIT_PROJECT"));
const endpoint = parseLoopbackWebSocketEndpoint(
  requiredEnv("WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT"),
);
assert.ok(endpoint, "A loopback DevTools endpoint is required");
const outputDirectory = path.resolve(requiredEnv("WECHAT_AUDIT_PROBE_OUTPUT"));

assertDisjointTrees(runtimeRoot, outputDirectory, {
  first: "Prepared runtime",
  second: "Probe output",
});
assertDisjointTrees(sourceRepository, outputDirectory, {
  first: "Formal source",
  second: "Probe output",
});
await assertSafeOutputTarget(auditRoot, outputDirectory);
await access(path.resolve(runtimeRoot, "project.config.json"), constants.R_OK);

const sourceInitial = await readCleanSourceGitEvidence(
  sourceRepository,
  sourceCommit,
);
const runtimeSourceMarker = (
  await readFile(path.resolve(runtimeRoot, ".audit-source-commit"), "utf8")
).trim();
assert.equal(runtimeSourceMarker, sourceCommit, "Prepared runtime source marker drifted");
const rawDiskIdentity = JSON.parse(
  await readFile(path.resolve(runtimeRoot, ".audit-runtime-identity.json"), "utf8"),
);
const diskIdentity = parseRuntimeIdentity(rawDiskIdentity, sourceCommit);
const runtimeEnvironmentEvidence = await readRuntimeEnvironmentEvidence(runtimeRoot);

const [
  automatorModule,
  warningPolicy,
] = await Promise.all([
  import(
    pathToFileURL(
      path.resolve(
        sourceRepository,
        "apps/wechat/node_modules/miniprogram-automator/out/index.js",
      ),
    ).href
  ),
  import(
    pathToFileURL(
      path.resolve(
        sourceRepository,
        "apps/wechat/e2e/runtime-warning-policy.mjs",
      ),
    ).href
  ),
]);
const automator = automatorModule.default;
const { consoleEventText, isActionableRuntimeWarning } = warningPolicy;
const program = await withTimeout(
  automator.connect({ wsEndpoint: endpoint }),
  30_000,
  "DevTools automation connection timed out",
);
const runtimeFailures = [];
const actionableWarnings = [];
const consoleEvents = [];
program.on("exception", (event) => {
  runtimeFailures.push(String(event?.message ?? event));
});
program.on("error", (event) => {
  runtimeFailures.push(`automation error: ${String(event?.message ?? event)}`);
});
program.on("console", (event) => {
  const message = consoleEventText(event);
  consoleEvents.push(message);
  if (isActionableRuntimeWarning(message)) actionableWarnings.push(message);
});

let controllerPage = null;
let resultWritten = false;
let cleanupDone = false;
let cleanupEvidence = null;
try {
  const liveIdentity = parseRuntimeIdentity(
    JSON.parse(
      String(
        await withTimeout(
          program.evaluate(function readAuditRuntimeIdentity() {
            return JSON.stringify(
              getApp()?.globalData?.__wechatAuditRuntimeIdentity ?? null,
            );
          }),
          10_000,
          "Runtime identity evaluation timed out",
        ),
      ),
    ),
    sourceCommit,
  );
  const runtimeIdentityEvidence = assertRuntimeIdentityMatches(
    diskIdentity,
    liveIdentity,
  );

  // Validate the selected profile before changing auth, route or audit output.
  const systemInfo = await withTimeout(
    program.systemInfo(),
    10_000,
    "System info timed out",
  );
  assert.equal(systemInfo.model, "HUAWEI nova 14 Ultra");
  assert.equal(systemInfo.windowWidth, 363);
  assert.equal(systemInfo.windowHeight, 817);
  assert.equal(systemInfo.deviceOrientation, "portrait");

  await installPlatformGuards(program);
  const guardProbe = await installControllerRequestRouter(program);
  assert.deepEqual(guardProbe, {
    callback: "success",
    statusCode: 503,
    code: "ui_audit_probe_blocked",
  });
  await setAuthentication(program, true);

  controllerPage = await withTimeout(
    program.reLaunch(
      "/pages/practice/index?mode=targeted&planTaskId=audit-plan-task",
    ),
    20_000,
    "Practice controller did not launch",
  );
  assert.ok(controllerPage, "Practice controller page is unavailable");
  await waitForState(
    program,
    (state) =>
      state.route === "pages/practice/index" &&
      state.status === "ready" &&
      state.sessionState === "answering" &&
      state.runtimeHydrated === true &&
      state.materialAvailable === false &&
      state.interactionLocked === false,
    "Mock-backed practice controller did not hydrate an editable no-material session",
  );

  const beforeState = await readState(program);
  assert.equal(beforeState.toolsOpen, false);
  assert.deepEqual(beforeState.query, {
    mode: "targeted",
    planTaskId: "audit-plan-task",
  });
  assert.equal(beforeState.favoriteStatus, "ready");
  assert.equal(beforeState.favoriteQuestionId, "q-1");

  // Reserve output only after source/runtime/profile/controller ownership is proven.
  await mkdir(outputDirectory);
  await assertSafeOutputTarget(auditRoot, outputDirectory, {
    targetMayNotExist: false,
  });
  const beforeScreenshot = path.resolve(outputDirectory, "before-real-tap.png");
  const afterScreenshot = path.resolve(outputDirectory, "after-real-tap.png");
  await Promise.all([
    assertSafeOutputTarget(auditRoot, beforeScreenshot),
    assertSafeOutputTarget(auditRoot, afterScreenshot),
  ]);

  const button = await withTimeout(
    controllerPage.$(".question-tool--more"),
    10_000,
    "More-tools selector lookup timed out",
  );
  assert.ok(button, "Could not locate .question-tool--more");
  const [buttonClass, buttonAriaLabel, buttonDisabled, buttonSize, buttonOffset] =
    await Promise.all([
      button.attribute("class"),
      button.attribute("aria-label"),
      button.attribute("disabled"),
      button.size(),
      button.offset(),
    ]);
  assert.match(String(buttonClass), /question-tool--more/u);
  assert.equal(buttonAriaLabel, "打开更多做题工具");
  assert.notEqual(String(buttonDisabled), "true");
  assert.ok(buttonSize.width > 0 && buttonSize.height > 0);
  assert.ok(buttonOffset.left >= 0 && buttonOffset.top >= 0);
  assert.ok(buttonOffset.left + buttonSize.width <= 363);
  assert.ok(buttonOffset.top + buttonSize.height <= 817);

  await withTimeout(
    program.screenshot({ path: beforeScreenshot }),
    15_000,
    "Before-tap screenshot timed out",
  );
  await withTimeout(button.tap(), 10_000, "Real more-tools tap timed out");
  await waitForState(
    program,
    (state) =>
      state.route === "pages/practice/index" &&
      state.toolsOpen === true &&
      state.runtimeHydrated === true,
    "Real Element.tap did not open the more-tools sheet",
  );
  await controllerPage.waitFor(300);
  const afterState = await readState(program);
  await withTimeout(
    program.screenshot({ path: afterScreenshot }),
    15_000,
    "After-tap screenshot timed out",
  );
  const finalState = await readState(program);
  assert.deepEqual(finalState, afterState, "Page state drifted while taking final screenshot");
  assert.deepEqual(runtimeFailures, [], "Interaction probe emitted runtime failures");
  assert.deepEqual(actionableWarnings, [], "Interaction probe emitted actionable warnings");

  const requestEvidence = JSON.parse(
    String(
      await program.evaluate(function readProbeRequestEvidence() {
        return JSON.stringify({
          requests: globalThis.__ananMoreToolsProbeRequests ?? [],
          blocked: globalThis.__ananMoreToolsProbeBlocked ?? [],
        });
      }),
    ),
  );
  assert.deepEqual(requestEvidence.blocked, [
    { method: "GET", pathname: "/__audit_probe__" },
  ]);
  assert.deepEqual(
    [...new Set(requestEvidence.requests.map(({ method, pathname }) => `${method} ${pathname}`))].sort(),
    [
      "GET /api/v2/xingce/question-favorites/q-1",
      "POST /api/v2/task-editing/leases",
      "POST /api/v2/xingce/task-sessions",
    ],
    "Controller emitted a request outside the exact audit fixture allowlist",
  );

  cleanupEvidence = await cleanupProbeState(program);
  cleanupDone = true;

  const sourceFinal = await readCleanSourceGitEvidence(
    sourceRepository,
    sourceCommit,
  );
  const sourceIntegrity = assertSourceGitEvidenceUnchanged(
    sourceInitial,
    sourceFinal,
  );
  const screenshotEvidence = {
    before: await readPngEvidence(beforeScreenshot),
    after: await readPngEvidence(afterScreenshot),
  };
  assert.deepEqual(
    {
      width: screenshotEvidence.before.width,
      height: screenshotEvidence.before.height,
    },
    { width: 363, height: 817 },
  );
  assert.deepEqual(
    {
      width: screenshotEvidence.after.width,
      height: screenshotEvidence.after.height,
    },
    { width: 363, height: 817 },
  );

  const result = {
    schemaVersion: 2,
    probe: "more-tools-real-element-tap",
    result: "pass",
    evidenceBoundary:
      "Mock-backed local controller hydration plus real miniprogram-automator Element.tap; " +
      "not a real-backend, physical-device or production E2E test.",
    sourceCommit,
    sourceIntegrity,
    runtimeSourceMarker,
    runtimeIdentity: runtimeIdentityEvidence,
    runtimePreparation: {
      prepareToolCommit: rawDiskIdentity.prepareToolCommit ?? null,
      prepareToolBlob: rawDiskIdentity.prepareToolBlob ?? null,
    },
    endpoint,
    systemInfo,
    guardEvidence: {
      runtimeEnvironment: runtimeEnvironmentEvidence,
      requestRouter: "in-process method + exact-path allowlist; all unmatched URLs return 503",
      platformMethods: [
        "uploadFile",
        "downloadFile",
        "requestPayment",
        "requestVirtualPayment",
      ],
      guardProbe,
    },
    fixture: {
      owner: "audit-only mock-backed controller",
      mode: "targeted",
      sharedMaterial: false,
      sessionId: "audit-session",
      planTaskId: "audit-plan-task",
      questionId: "q-1",
    },
    requestEvidence,
    route: controllerPage.path,
    beforeState,
    button: {
      className: buttonClass,
      ariaLabel: buttonAriaLabel,
      disabled: buttonDisabled ?? null,
      size: buttonSize,
      offset: buttonOffset,
    },
    afterState,
    screenshots: screenshotEvidence,
    runtimeFailures,
    actionableWarnings,
    cleanup: cleanupEvidence,
    consoleEventCount: consoleEvents.length,
    script: await readScriptProvenance(),
    completedAt: new Date().toISOString(),
  };
  await writeFile(
    path.resolve(outputDirectory, "result.json"),
    `${JSON.stringify(result, null, 2)}\n`,
    { flag: "wx" },
  );
  resultWritten = true;
  process.stdout.write(`${JSON.stringify(result)}\n`);
} finally {
  try {
    if (!cleanupDone) {
      cleanupEvidence = await cleanupProbeState(program);
    }
  } finally {
    program.disconnect();
  }
  const sourceAtExit = await readCleanSourceGitEvidence(
    sourceRepository,
    sourceCommit,
  );
  assertSourceGitEvidenceUnchanged(sourceInitial, sourceAtExit);
  if (resultWritten) {
    await assertSafeOutputTarget(
      auditRoot,
      path.resolve(outputDirectory, "result.json"),
      { targetMayNotExist: false },
    );
  }
}

async function installPlatformGuards(program) {
  await program.mockWxMethod("uploadFile", {
    data: {
      code: "ui_audit_upload_blocked",
      message: "Upload blocked by audit probe.",
    },
    statusCode: 503,
    errMsg: "uploadFile:ok",
  });
  await program.mockWxMethod("downloadFile", {
    statusCode: 503,
    tempFilePath: "",
    errMsg: "downloadFile:ok",
  });
  await program.mockWxMethod("requestPayment", {
    errMsg: "requestPayment:fail ui_audit_payment_blocked",
  });
  await program.mockWxMethod("requestVirtualPayment", {
    errMsg: "requestVirtualPayment:fail ui_audit_payment_blocked",
  });
}

async function cleanupProbeState(program) {
  await installTerminalNetworkGuard(program);
  await setAuthentication(program, false);
  const current = await program.currentPage();
  const unloadedRoute = current?.path ?? null;
  if (current?.path === "pages/practice/index") {
    const terminalPage = await withTimeout(
      program.reLaunch("/pages/login/index"),
      20_000,
      "Practice controller teardown did not unload the page",
    );
    assert.ok(terminalPage, "Login teardown page is unavailable");
    await terminalPage.waitFor(500);
  }
  const terminalPage = await program.currentPage();
  assert.notEqual(
    terminalPage?.path,
    "pages/practice/index",
    "Practice controller remained mounted after teardown",
  );
  const storage = JSON.parse(
    String(
      await program.evaluate(function clearAuditOwnerStorage() {
        const ownerPrefix =
          "gongkao.xingce.real-attempt.pending.v1." +
          encodeURIComponent("more-tools-probe-user") +
          ".";
        const before = wx
          .getStorageInfoSync()
          .keys.filter((key) => key.startsWith(ownerPrefix));
        for (const key of before) wx.removeStorageSync(key);
        wx.removeStorageSync("gongkao.auth.session.v1");
        wx.removeStorageSync("gongkao.auth.accessToken");
        const app = getApp();
        delete app.globalData.accessToken;
        delete app.globalData.userId;
        delete app.globalData.displayName;
        delete app.globalData.generation;
        const after = wx
          .getStorageInfoSync()
          .keys.filter((key) => key.startsWith(ownerPrefix));
        return JSON.stringify({ ownerPrefix, removed: before, remaining: after });
      }),
    ),
  );
  assert.deepEqual(storage.remaining, [], "Audit fake-user pending storage survived teardown");
  return {
    unloadedRoute,
    terminalRoute: terminalPage?.path ?? null,
    practiceUnmounted: terminalPage?.path !== "pages/practice/index",
    fakeOwnerStorage: storage,
    terminalRequestGuardInstalled: true,
    authenticationCleared: true,
  };
}

async function installControllerRequestRouter(program) {
  const sessionResponse = apiSuccess(createXingceSession());
  const leaseResponse = apiSuccess(createXingceLease());
  const favoriteResponse = apiSuccess({
    questionId: "q-1",
    favoriteState: "not_favorited",
    favoritedAt: null,
    savedContentVersionId: null,
    currentContentVersionId: "b".repeat(64),
    availability: "available",
    contentChanged: false,
    revision: 0,
    updatedAt: null,
  });
  return JSON.parse(
    String(
      await program.evaluate(
        async function installMoreToolsProbeRouter(session, lease, favorite) {
          globalThis.__ananMoreToolsProbeRequests = [];
          globalThis.__ananMoreToolsProbeBlocked = [];
          wx.request = function moreToolsProbeRequest(options) {
            const url = String(options.url ?? "");
            const method = String(options.method ?? "GET").toUpperCase();
            const pathname = url
              .replace(/^https?:\/\/[^/]+/, "")
              .split("?", 1)[0];
            const request = { method, pathname };
            let response;
            if (method === "POST" && pathname === "/api/v2/task-editing/leases") {
              globalThis.__ananMoreToolsProbeRequests.push(request);
              response = lease;
            } else if (method === "POST" && pathname === "/api/v2/xingce/task-sessions") {
              globalThis.__ananMoreToolsProbeRequests.push(request);
              response = session;
            } else if (
              method === "GET" &&
              pathname === "/api/v2/xingce/question-favorites/q-1"
            ) {
              globalThis.__ananMoreToolsProbeRequests.push(request);
              response = favorite;
            } else {
              globalThis.__ananMoreToolsProbeBlocked.push(request);
              response = {
                data: {
                  code: "ui_audit_probe_blocked",
                  message: "Request blocked by local interaction probe.",
                  requestId: "ui-audit-more-tools-probe",
                },
                statusCode: 503,
                header: {},
                cookies: [],
                errMsg: "request:ok",
              };
            }
            setTimeout(() => {
              options.success?.(response);
              options.complete?.(response);
            }, 0);
            return {
              abort() {},
              onHeadersReceived() {},
              offHeadersReceived() {},
            };
          };
          return await new Promise((resolve) => {
            wx.request({
              url: "http://127.0.0.1:9/__audit_probe__",
              success(response) {
                resolve(
                  JSON.stringify({
                    callback: "success",
                    statusCode: response.statusCode,
                    code: response.data?.code ?? null,
                  }),
                );
              },
              fail(error) {
                resolve(
                  JSON.stringify({
                    callback: "fail",
                    statusCode: null,
                    code: error?.errMsg ?? null,
                  }),
                );
              },
            });
          });
        },
        sessionResponse,
        leaseResponse,
        favoriteResponse,
      ),
    ),
  );
}

async function installTerminalNetworkGuard(program) {
  await program.mockWxMethod("request", {
    data: {
      code: "ui_audit_network_blocked",
      message: "Audit interaction is complete; requests remain blocked.",
      requestId: "ui-audit-more-tools-terminal",
    },
    statusCode: 503,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  });
}

async function setAuthentication(program, authenticated) {
  await program.evaluate(function applyProbeSession(hasSession) {
    const storageKey = "gongkao.auth.session.v1";
    const app = getApp();
    if (hasSession) {
      const session = {
        accessToken: "more-tools-probe-token",
        userId: "more-tools-probe-user",
      };
      wx.setStorageSync(storageKey, session);
      app.globalData.accessToken = session.accessToken;
      app.globalData.userId = session.userId;
      return true;
    }
    wx.removeStorageSync(storageKey);
    wx.removeStorageSync("gongkao.auth.accessToken");
    delete app.globalData.accessToken;
    delete app.globalData.userId;
    delete app.globalData.displayName;
    delete app.globalData.generation;
    return true;
  }, authenticated);
}

async function readRuntimeEnvironmentEvidence(runtimeRoot) {
  const filename = path.resolve(runtimeRoot, "miniprogram/config/environment.ts");
  const source = await readFile(filename, "utf8");
  assert.match(source, /apiBaseUrl:\s*"http:\/\/127\.0\.0\.1:9"/u);
  assert.match(source, /supportGroupQrImageUrl:\s*null/u);
  assert.match(source, /virtualPaymentEnabled:\s*false/u);
  assert.match(source, /releaseChannel:\s*"development"/u);
  return {
    path: path.relative(repositoryRoot, filename),
    sha256: createHash("sha256").update(source).digest("hex"),
    assertions: {
      apiBaseUrl: "http://127.0.0.1:9",
      supportGroupQrImageUrl: null,
      virtualPaymentEnabled: false,
      releaseChannel: "development",
    },
  };
}

async function waitForState(program, predicate, message) {
  let latest = null;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    latest = await readState(program);
    if (predicate(latest)) return latest;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(`${message}; latest=${JSON.stringify(latest)}`);
}

async function readState(program) {
  return JSON.parse(
    String(
      await withTimeout(
        program.evaluate(function readPracticeProbeState() {
          const pages = getCurrentPages();
          const current = pages[pages.length - 1];
          return JSON.stringify({
            route: current?.route ?? null,
            query: current?.options ?? null,
            status: current?.data?.status ?? null,
            sessionState: current?.data?.sessionState ?? null,
            runtimeHydrated:
              current?.data?.view?.question?.questionId === "q-1" &&
              current?.data?.view?.total === 3,
            materialAvailable:
              current?.data?.view?.materialAvailable ?? null,
            interactionLocked: current?.data?.interactionLocked ?? null,
            toolsOpen: current?.data?.toolsOpen ?? null,
            favoriteStatus: current?.data?.favoriteStatus ?? null,
            favoriteQuestionId: current?.data?.favoriteQuestionId ?? null,
          });
        }),
        10_000,
        "Page-state evaluation timed out",
      ),
    ),
  );
}

function createXingceSession() {
  const questions = ["q-1", "q-2", "q-3"].map((questionId, index) => ({
    questionId,
    contentVersionId: (index === 0 ? "b" : "c").repeat(64),
    materialId: null,
    moduleName: "资料分析",
    stem: index === 0 ? "根据题意，下列说法正确的是？" : "下列判断符合题意的是？",
    stemHtml: null,
    stemContent: [
      {
        type: "paragraph",
        blockId: `stem-${questionId}`,
        text: index === 0 ? "根据题意，下列说法正确的是？" : "下列判断符合题意的是？",
        html: null,
      },
    ],
    options: ["A", "B", "C", "D"].map((label) => ({
      optionId: label,
      label,
      text: `${label} 项为题目判断项。`,
      html: null,
      content: [
        {
          type: "paragraph",
          blockId: `option-${questionId}-${label}`,
          text: `${label} 项为题目判断项。`,
          html: null,
        },
      ],
    })),
  }));
  return {
    sessionId: "audit-session",
    planTaskId: "audit-plan-task",
    revision: 1,
    state: "answering",
    mode: "targeted",
    bundle: {
      bundleVersionId: "a".repeat(64),
      bundleType: "single",
      materials: [],
      questions,
    },
    currentQuestionId: "q-1",
    answers: [],
    totalElapsedSeconds: 208,
    result: null,
    verificationOutcome: null,
    review: null,
    completion: null,
  };
}

function createXingceLease() {
  return {
    state: "owned",
    resourceType: "xingce_task_session",
    resourceId: "audit-session",
    leaseId: "audit-more-tools-lease",
    generation: 1,
    deviceId: "audit-device-id-000000000000",
    deviceLabel: "审计设备",
    serverRevision: 1,
    localBaseRevision: 1,
    acquiredAt: "2026-07-29T00:00:00Z",
    lastSeenAt: "2026-07-29T00:00:00Z",
    takenOverAt: null,
  };
}

function apiSuccess(data) {
  return {
    data: {
      data,
      requestId: "more-tools-probe",
      generatedAt: "2026-07-29T00:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  };
}

async function readPngEvidence(filename) {
  const bytes = await readFile(filename);
  assert.equal(bytes.subarray(1, 4).toString("ascii"), "PNG");
  return {
    path: path.relative(repositoryRoot, filename),
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
}

async function readScriptProvenance() {
  const relativePath = path.relative(repositoryRoot, scriptPath);
  const [
    { stdout: commitOutput },
    { stdout: workingBlobOutput },
    { stdout: statusOutput },
  ] = await Promise.all([
    execFileAsync("git", ["-C", repositoryRoot, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }),
    execFileAsync("git", ["-C", repositoryRoot, "hash-object", scriptPath], {
      encoding: "utf8",
    }),
    execFileAsync(
      "git",
      ["-C", repositoryRoot, "status", "--porcelain=v1", "--", relativePath],
      { encoding: "utf8" },
    ),
  ]);
  let treeBlob = null;
  try {
    const result = await execFileAsync(
      "git",
      ["-C", repositoryRoot, "rev-parse", `HEAD:${relativePath}`],
      { encoding: "utf8" },
    );
    treeBlob = result.stdout.trim();
  } catch {
    treeBlob = null;
  }
  const bytes = await readFile(scriptPath);
  return {
    path: relativePath,
    repositoryCommit: commitOutput.trim(),
    treeBlob,
    workingBlob: workingBlobOutput.trim(),
    status: statusOutput.trim(),
    sha256: createHash("sha256").update(bytes).digest("hex"),
    provenanceBoundary:
      treeBlob === workingBlobOutput.trim()
        ? "working blob matches repository HEAD"
        : "audit-only tool is not part of the final source commit; content is bound by blob and SHA-256",
  };
}

function requiredEnv(name) {
  const value = process.env[name]?.trim();
  assert.ok(value, `${name} is required`);
  return value;
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
