import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import automator from "miniprogram-automator";

import { invokePageHandler, readPageData, setPageData } from "./page-data-bridge.mjs";
import { interactionEvidenceKinds, pageInteractionMatrix } from "./page-interaction-matrix.mjs";
import { consoleEventText, isActionableRuntimeWarning } from "./runtime-warning-policy.mjs";
import { visualScenarios } from "./visual-scenarios.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDirectory, "..");
const cliPath =
  process.env.WECHAT_DEVTOOLS_CLI ?? "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const automationEndpoint = process.env.WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT?.trim();
const scenarioById = new Map(visualScenarios.map((scenario) => [scenario.id, scenario]));
const pageJourneys = pageInteractionMatrix.map((contract) => [
  contract.id,
  pagePurposeJourney(contract),
]);
const literalDomJourneys = [
  ["deep-today-primary-task", todayPrimaryTask],
  ["deep-today-capture-entry", todayCaptureEntry],
  ["deep-capture-mode-track", captureModeTrack],
  ["deep-capture-restart", captureRestart],
  ["deep-xingce-toolbar", xingceToolbar],
  ["deep-submitted-mock-report", submittedMockReport],
  ["deep-essay-compose-actions", essayComposeActions],
  ["deep-report-return-today", reportReturnToday],
  ["deep-free-cold-start-access", freeColdStartAccess],
  ["deep-profile-menu", profileMenu],
];
const journeys =
  process.env.LITERAL_DOM_AUDIT === "1" ? [...pageJourneys, ...literalDomJourneys] : pageJourneys;
const journeyFilter = process.env.JOURNEY?.trim() ?? "";
const requestedJourneyNames = new Set(
  journeyFilter
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);
const selectedJourneys = journeyFilter
  ? journeys.filter(([name]) => requestedJourneyNames.has(name))
  : journeys;

assert.equal(
  selectedJourneys.length,
  requestedJourneyNames.size || journeys.length,
  `Unknown click journey filter: ${journeyFilter}`,
);

await access(cliPath, constants.X_OK);

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
miniProgram.on("exception", (event) => runtimeFailures.push(String(event?.message ?? event)));
miniProgram.on("console", (event) => {
  const message = consoleEventText(event);
  if (isActionableRuntimeWarning(message)) runtimeWarnings.push(message);
});

try {
  await freezeAuditAnimationClock(miniProgram);
  await installNetworkGuard(miniProgram);
  for (const [name, journey] of selectedJourneys) await runJourney(name, journey);
} finally {
  await withTimeout(setAuthentication(miniProgram, false), 5_000, "Auth fixture did not clear");
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
  `Audited ${String(selectedJourneys.length)} mock-backed page binding/controller journeys; ` +
    "this command is not literal DOM tap, real-backend, or device evidence.\n",
);

function pagePurposeJourney(contract) {
  return async (program) => {
    if (contract.route === "pages/login/index") {
      const loginPage = await openVisualScenario(program, contract.scenarioId);
      await installLoginMocks(program);
      await invokePageHandler(program, loginPage, "login");
      await waitForPath(program, "/pages/today/index", "login did not switch to today");
      await installNetworkGuard(program);
      return;
    }
    const page = await openVisualScenario(program, contract.scenarioId);
    const interaction = contract.primaryInteraction;
    if (interaction.evidence === interactionEvidenceKinds.RUNTIME_READ_ONLY) {
      assert.equal(interaction.handler, null);
      return;
    }
    if (interaction.evidence === interactionEvidenceKinds.PLATFORM_DEVICE) {
      assert.ok(interaction.handler, `${contract.route} platform action is not declared`);
      return;
    }

    const beforePath = normalizeRoutePath(page.path);
    const beforeData = await readPageData(program, page);
    await invokePageHandler(program, page, interaction.handler, interaction.dataset);
    await page.waitFor(1_200);

    const currentPage = await program.currentPage();
    const afterPath = normalizeRoutePath(currentPage?.path);
    const afterData =
      normalizeRoutePath(currentPage?.path) === beforePath
        ? await readPageData(program, page)
        : null;
    if (interaction.observable) {
      assert.ok(
        afterPath !== beforePath || JSON.stringify(afterData) !== JSON.stringify(beforeData),
        `${contract.route} primary interaction ${interaction.handler} produced no observable effect; ` +
          `beforePath=${beforePath}; afterPath=${afterPath}; dataset=${JSON.stringify(interaction.dataset)}; ` +
          `viewTasks=${JSON.stringify(beforeData?.view?.tasks ?? null)}`,
      );
    }
  };
}

async function runJourney(name, journey) {
  process.stdout.write(`[journey] ${name} ... `);
  const failureOffset = runtimeFailures.length;
  const warningOffset = runtimeWarnings.length;
  await withTimeout(journey(miniProgram), 60_000, `${name} did not finish within 60 seconds`);
  const settledPage = await miniProgram.currentPage();
  await settledPage?.waitFor(800);
  assert.deepEqual(
    runtimeFailures.slice(failureOffset),
    [],
    `${name} emitted Mini Program runtime exceptions:\n${runtimeFailures.slice(failureOffset).join("\n")}`,
  );
  assert.deepEqual(
    runtimeWarnings.slice(warningOffset),
    [],
    `${name} emitted Mini Program runtime warnings:\n${runtimeWarnings.slice(warningOffset).join("\n")}`,
  );
  process.stdout.write("ok\n");
}

async function todayPrimaryTask(program) {
  const page = await openVisualScenario(program, "b01-today-ready");
  const data = await readPageData(program, page);
  assert.ok(data.view?.tasks?.length >= 1, "today page exposes no task card");
  await invokePageHandler(program, page, "openTask", { id: data.view.tasks[0].id });
  await waitForPath(program, "/xingce/xingce-task-intro/index", "today task did not open briefing");
}

async function todayCaptureEntry(program) {
  const page = await openVisualScenario(program, "b01-today-ready");
  await invokePageHandler(program, page, "openCaptureImport");
  await waitForPath(program, "/pages/capture-import/index", "capture entry did not open");
}

async function captureModeTrack(program) {
  const page = await openVisualScenario(program, "h01-capture-search-idle");
  await invokePageHandler(program, page, "chooseMode", { mode: "import" });
  await waitForData(page, (data) => data.mode === "import", "capture mode did not switch");
  await invokePageHandler(program, page, "chooseTrack", { value: "essay" });
  await waitForData(
    page,
    (data) => data.state?.learningTrack === "essay",
    "capture track did not switch to essay",
  );
}

async function captureRestart(program) {
  const page = await openVisualScenario(program, "h01-capture-search-idle");
  const current = await readPageData(program, page);
  await setPageData(program, page, {
    state: {
      ...current.state,
      status: "success",
      selectedContentVersionId: "audit-content-version",
    },
  });
  await invokePageHandler(program, page, "restart");
  await waitForData(page, (data) => data.state?.status === "idle", "capture did not restart");
}

async function xingceToolbar(program) {
  await setAuthentication(program, true);
  await installXingceMocks(program);
  const page = await program.reLaunch(
    "/pages/practice/index?mode=targeted&planTaskId=audit-plan-task",
  );
  await waitForData(page, (data) => data.status === "ready", "practice page did not become ready");

  await invokePageHandler(program, page, "toggleMaterial");
  await waitForData(page, (data) => data.materialOpen === true, "material drawer did not open");

  await invokePageHandler(program, page, "toggleAnswerSheet");
  await waitForData(page, (data) => data.answerSheetOpen === true, "answer sheet did not open");

  await invokePageHandler(program, page, "toggleDraft");
  await waitForData(page, (data) => data.draftOpen === true, "draft sheet did not open");

  await invokePageHandler(program, page, "toggleTaskPause");
  await waitForData(page, (data) => data.taskPaused === true, "timer did not pause");
  await installNetworkGuard(program);
}

async function submittedMockReport(program) {
  const page = await openVisualScenario(program, "c06-mock-exam-answering");
  await setPageData(program, page, {
    status: "submitted",
    receipt: null,
    reportSessionId: "visual-mock-session-v1",
  });
  await invokePageHandler(program, page, "openMockReport");
  await waitForPath(program, "/reports/mock-report/index", "submitted mock did not open its report");
}

async function essayComposeActions(program) {
  let page = await openVisualScenario(program, "f11-essay-compose-workbench");
  const firstData = await readPageData(program, page);
  assert.equal(firstData.status, "ready", "essay compose should be ready before opening outline");
  assert.match(firstData.outlineUrl, /taskVersionId=visual-essay-task/);
  assert.match(firstData.outlineUrl, /planTaskId=visual-plan/);
  await invokePageHandler(program, page, "openOutline");
  await waitForPath(program, "/pages/essay-task/index", "outline action did not navigate");

  page = await openVisualScenario(program, "f11-essay-compose-workbench");
  const secondData = await readPageData(program, page);
  assert.equal(secondData.status, "ready", "essay compose should be ready before opening drafting");
  assert.match(secondData.draftingUrl, /taskVersionId=visual-essay-task/);
  assert.match(secondData.draftingUrl, /planTaskId=visual-plan/);
  await invokePageHandler(program, page, "openDrafting");
  await waitForPath(program, "/pages/essay-task/index", "drafting action did not navigate");
}

async function reportReturnToday(program) {
  const page = await openVisualScenario(program, "f02-report-overview-ready");
  const data = await readPageData(program, page);
  assert.ok(data.report?.conclusion?.nextAction, "report overview should expose next action copy");
  await invokePageHandler(program, page, "openNextAction");
  await waitForPath(program, "/pages/today/index", "report did not return to today's plan");
}

async function freeColdStartAccess(program) {
  const page = await openVisualScenario(program, "g01-open-access-ready");
  const data = await readPageData(program, page);
  assert.equal(data.freeColdStart, true, "open-access page is not in free cold-start mode");
  assert.equal(data.dashboard?.catalog?.orderingEnabled, false, "ordering must be disabled");
  assert.equal(data.offer, null, "free mode must not expose a purchasable offer");
  assert.equal(data.paymentEnabled, false, "free mode must not enable payment");
  assert.equal(data.accepted, false, "free mode must not pre-accept paid policies");
}

async function profileMenu(program) {
  const page = await openVisualScenario(program, "f09-profile-ready");
  const data = await readPageData(program, page);
  assert.equal(data.status, "ready", "profile menu should be ready before navigation");
  await invokePageHandler(program, page, "openQualificationProfile");
  await waitForPath(
    program,
    "/pages/profile/archive/index",
    "profile archive link did not navigate",
  );

  const profilePage = await openVisualScenario(program, "f09-profile-ready");
  await invokePageHandler(program, profilePage, "openPrivacy");
  await waitForPath(program, "/account/privacy/index", "profile privacy link did not navigate");
}

async function openVisualScenario(program, id, options = {}) {
  if (!options.keepMocks) await installNetworkGuard(program);
  const scenario = scenarioById.get(id);
  assert.ok(scenario, `Unknown visual scenario ${id}`);
  await setAuthentication(program, scenario.authenticated === true);
  const currentPage = await program.currentPage();
  const routePath = scenario.route.split("?", 1)[0];
  const page =
    normalizeRoutePath(currentPage?.path) === normalizeRoutePath(routePath)
      ? currentPage
      : await program.reLaunch(scenario.route);
  assert.ok(page, `Unable to open ${scenario.route}`);
  await page.waitFor(400);
  await setPageData(program, page, structuredClone(scenario.data));
  if (scenario.scrollTop) await program.pageScrollTo(scenario.scrollTop);
  await page.waitFor(160);
  return page;
}

async function waitForPath(program, expectedPath, message) {
  await waitForPredicate(async () => {
    const page = await program.currentPage();
    return normalizeRoutePath(page?.path) === normalizeRoutePath(expectedPath);
  }, message);
  const settledPage = await program.currentPage();
  await settledPage?.waitFor(1_000);
}

function normalizeRoutePath(routePath) {
  const value = String(routePath ?? "").trim();
  return value ? `/${value.replace(/^\/+/, "")}` : "";
}

async function waitForData(page, predicate, message) {
  await waitForPredicate(async () => predicate(await readPageData(miniProgram, page)), message);
}

async function waitForPredicate(predicate, message) {
  let lastTransientError = null;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      if (await predicate()) return;
      lastTransientError = null;
    } catch (error) {
      lastTransientError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(
    lastTransientError
      ? `${message}; last transient automation error: ${lastTransientError.message}`
      : message,
  );
}

async function installNetworkGuard(program) {
  await program.mockWxMethod("request", {
    data: {
      code: "click_audit_network_guard",
      message: "Click journeys must install explicit API mocks before network access.",
      requestId: "click-audit-network-guard",
    },
    statusCode: 503,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  });
  await program.mockWxMethod("showToast", { errMsg: "showToast:ok" });
  await program.mockWxMethod("showModal", { confirm: true, cancel: false, errMsg: "showModal:ok" });
}

async function installLoginMocks(program) {
  const loginResponse = {
    data: envelope({
      accessToken: "click-audit-access-token",
      expiresAt: "2099-07-18T00:00:00Z",
      onboardingComplete: true,
      onboardingProgress: {
        completed: true,
        completedAt: "2026-07-18T00:00:00Z",
        nextStep: "workspace",
        progressVersion: 9,
        qualificationProfileVersion: 3,
        qualificationReady: true,
        resultAcknowledged: true,
        resultReady: true,
        studyPreferenceReady: true,
        studyPreferenceVersion: 2,
        updatedAt: "2026-07-18T00:00:00Z",
      },
      tokenType: "Bearer",
      user: {
        userId: "click-audit-user",
        learnerKey: "click-audit-learner",
        displayName: "岸岸审计",
        email: null,
      },
    }),
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  };
  await program.evaluate(function installAuditLoginMocks(response) {
    globalThis.__ananClickAuditLoginCalled = false;
    globalThis.__ananClickAuditLoginRequests = [];
    wx.login = function auditLogin(options) {
      globalThis.__ananClickAuditLoginCalled = true;
      options.success?.({ code: "click-audit-login-code", errMsg: "login:ok" });
    };
    wx.request = function auditLoginRequest(options) {
      const url = String(options.url ?? "");
      globalThis.__ananClickAuditLoginRequests.push(url);
      if (url.includes("/api/v2/auth/wechat")) {
        setTimeout(() => {
          options.success?.(response);
          options.complete?.(response);
        }, 0);
        return {
          abort() {},
          onHeadersReceived() {},
          offHeadersReceived() {},
        };
      }
      const blocked = {
        data: {
          code: "click_audit_network_guard",
          message: "Click journeys must install explicit API mocks before network access.",
          requestId: "click-audit-network-guard",
        },
        statusCode: 503,
        header: {},
        cookies: [],
        errMsg: "request:ok",
      };
      setTimeout(() => {
        options.success?.(blocked);
        options.complete?.(blocked);
      }, 0);
      return {
        abort() {},
        onHeadersReceived() {},
        offHeadersReceived() {},
      };
    };
  }, loginResponse);
}

async function installXingceMocks(program) {
  const sessionResponse = {
    data: envelope(createXingceSession()),
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  };
  const leaseResponse = {
    data: envelope(createXingceLease()),
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  };
  await program.evaluate(
    function installAuditXingceMocks(session, lease) {
      globalThis.__ananClickAuditXingceRequests = [];
      wx.request = function auditXingceRequest(options) {
        const url = String(options.url ?? "");
        globalThis.__ananClickAuditXingceRequests.push(url);
        const response = url.includes("/api/v2/task-editing/leases")
          ? lease
          : url.includes("/api/v2/xingce/task-sessions")
            ? session
            : {
                data: {
                  code: "click_audit_network_guard",
                  message: "Click journeys must install explicit API mocks before network access.",
                  requestId: "click-audit-network-guard",
                },
                statusCode: 503,
                header: {},
                cookies: [],
                errMsg: "request:ok",
              };
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
    },
    sessionResponse,
    leaseResponse,
  );
}

async function setAuthentication(program, authenticated) {
  await program.evaluate(function applyClickAuditSession(hasSession) {
    const storageKey = "gongkao.auth.session.v1";
    const app = getApp();
    if (hasSession) {
      const session = { accessToken: "click-audit-access-token", userId: "click-audit-user" };
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

async function freezeAuditAnimationClock(program) {
  await program.evaluate(function installDeterministicAuditClock() {
    if (globalThis.__ananClickAuditOriginalSetInterval) return;
    const originalSetInterval = globalThis.setInterval;
    globalThis.__ananClickAuditOriginalSetInterval = originalSetInterval;
    globalThis.setInterval = function auditSetInterval(callback, delay, ...args) {
      if (delay === 125) return originalSetInterval(function holdAuditAnimationFrame() {}, delay);
      return originalSetInterval(callback, delay, ...args);
    };
  });
}

function createXingceSession() {
  const material = {
    materialId: "audit-material",
    title: "甲乙两地公共服务数据",
    text: "甲地与乙地公共服务数据。",
    html: null,
    assets: [],
    content: [
      {
        type: "paragraph",
        blockId: "audit-material-block-1",
        text: "甲地一般公共预算收入 184.6 亿元，同比增长 6.8%。",
        html: null,
      },
    ],
    anchors: [
      { anchorId: "audit-material-block-1", blockId: "audit-material-block-1", label: "材料 1" },
    ],
  };
  const questions = ["q-1", "q-2", "q-3"].map((questionId, index) => ({
    questionId,
    contentVersionId: `content-${questionId}`,
    materialId: material.materialId,
    moduleName: "资料分析",
    stem: index === 0 ? "根据材料，下列说法正确的是？" : "根据材料可以推出的是？",
    stemHtml: null,
    stemContent: [
      {
        type: "paragraph",
        blockId: `stem-${questionId}`,
        text: index === 0 ? "根据材料，下列说法正确的是？" : "根据材料可以推出的是？",
        html: null,
      },
    ],
    options: ["A", "B", "C", "D"].map((label) => ({
      optionId: label,
      label,
      text: `${label} 项为材料数据推断项。`,
      html: null,
      content: [
        {
          type: "paragraph",
          blockId: `option-${questionId}-${label}`,
          text: `${label} 项为材料数据推断项。`,
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
      bundleType: "shared_material",
      materials: [material],
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
    leaseId: "audit-xingce-lease",
    generation: 1,
    deviceId: "audit-device-id-000000000000",
    deviceLabel: "审计设备",
    serverRevision: 1,
    localBaseRevision: 1,
    acquiredAt: "2026-07-18T00:00:00Z",
    lastSeenAt: "2026-07-18T00:00:00Z",
    takenOverAt: null,
  };
}

function envelope(data) {
  return {
    data,
    requestId: "click-audit",
    generatedAt: "2026-07-18T00:00:00Z",
  };
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
