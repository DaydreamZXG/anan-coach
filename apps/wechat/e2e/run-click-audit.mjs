import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import automator from "miniprogram-automator";

import { visualScenarios } from "./visual-scenarios.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDirectory, "..");
const cliPath =
  process.env.WECHAT_DEVTOOLS_CLI ?? "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const automationEndpoint = process.env.WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT?.trim();
const scenarioById = new Map(visualScenarios.map((scenario) => [scenario.id, scenario]));
const journeys = [
  ["login-success", loginSuccess],
  ["today-primary-task", todayPrimaryTask],
  ["today-capture-entry", todayCaptureEntry],
  ["capture-mode-track", captureModeTrack],
  ["capture-restart", captureRestart],
  ["xingce-toolbar", xingceToolbar],
  ["submitted-mock-report", submittedMockReport],
  ["essay-compose-actions", essayComposeActions],
  ["report-return-today", reportReturnToday],
  ["membership-virtual-payment", membershipVirtualPayment],
  ["profile-menu", profileMenu],
];
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
  if (
    /received type-uncompatible value/u.test(message) ||
    /wx\.(?:saveFile|removeSavedFile) 即将废弃/u.test(message) ||
    /\b(?:GET|POST|PUT|DELETE)\b.+\/api\/v2\/.+\b(?:401|404)\b/u.test(message)
  ) {
    runtimeWarnings.push(message);
  }
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
process.stdout.write(`Audited ${String(selectedJourneys.length)} click journeys.\n`);

async function runJourney(name, journey) {
  process.stdout.write(`[journey] ${name} ... `);
  await withTimeout(journey(miniProgram), 60_000, `${name} did not finish within 60 seconds`);
  const settledPage = await miniProgram.currentPage();
  await settledPage?.waitFor(800);
  process.stdout.write("ok\n");
}

async function loginSuccess(program) {
  await setAuthentication(program, false);
  const page = await program.reLaunch("/pages/login/index");
  await waitForData(
    page,
    (data) => data.submitting === false && data.restoring === false,
    "login page did not become interactive",
  );
  await installLoginMocks(program);
  const button = await requiredElement(page, ".wechat-button");
  await button.tap();
  try {
    await waitForPath(program, "/pages/today/index", "login did not switch to today");
  } catch (error) {
    const currentPage = await program.currentPage();
    const data = await page.data();
    const audit = await program.evaluate(function readLoginAudit() {
      return {
        loginCalled: Boolean(globalThis.__ananClickAuditLoginCalled),
        requestUrls: globalThis.__ananClickAuditLoginRequests ?? [],
      };
    });
    assert.fail(
      `${error.message}; currentPath=${currentPage?.path ?? "none"}; data=${JSON.stringify(data)}; audit=${JSON.stringify(audit)}`,
    );
  }
  await installNetworkGuard(program);
}

async function todayPrimaryTask(program) {
  const page = await openVisualScenario(program, "b01-today-ready");
  const tasks = await page.$$(".task");
  assert.ok(tasks.length >= 1, "today page exposes no tappable task card");
  await tasks[0].tap();
  await waitForPath(program, "/pages/xingce-task-intro/index", "today task did not open briefing");
}

async function todayCaptureEntry(program) {
  const page = await openVisualScenario(program, "b01-today-ready");
  await (await requiredElement(page, ".today-capture")).tap();
  await waitForPath(program, "/pages/capture-import/index", "capture entry did not open");
}

async function captureModeTrack(program) {
  const page = await openVisualScenario(program, "h01-capture-search-idle");
  const modes = await page.$$(".capture-mode");
  assert.equal(modes.length, 2, "capture page should expose search/import mode controls");
  await modes[1].tap();
  await waitForData(page, (data) => data.mode === "import", "capture mode did not switch");
  const tracks = await page.$$(".capture-track");
  assert.equal(tracks.length, 2, "capture page should expose xingce/essay track controls");
  await tracks[1].tap();
  await waitForData(
    page,
    (data) => data.state?.learningTrack === "essay",
    "capture track did not switch to essay",
  );
}

async function captureRestart(program) {
  const page = await openVisualScenario(program, "h01-capture-search-idle");
  const current = await page.data();
  await page.setData({
    state: {
      ...current.state,
      status: "success",
      selectedContentVersionId: "audit-content-version",
    },
  });
  const actions = await page.$$(".capture-secondary");
  assert.equal(actions.length, 2, "capture result should expose answer entry and next capture");
  await actions[1].tap();
  await waitForData(page, (data) => data.state?.status === "idle", "capture did not restart");
}

async function xingceToolbar(program) {
  await setAuthentication(program, true);
  await installXingceMocks(program);
  const page = await program.reLaunch(
    "/pages/practice/index?mode=targeted&planTaskId=audit-plan-task",
  );
  await waitForData(page, (data) => data.status === "ready", "practice page did not become ready");
  const taskShell = await requiredElement(page, "task-shell");

  await (await requiredElement(taskShell, ".task-header-button--material")).tap();
  await waitForData(page, (data) => data.materialOpen === true, "material drawer did not open");

  await (await requiredElement(taskShell, ".task-question-control")).tap();
  await waitForData(page, (data) => data.answerSheetOpen === true, "answer sheet did not open");

  await (await requiredElement(taskShell, ".task-header-button--draft")).tap();
  await waitForData(page, (data) => data.draftOpen === true, "draft sheet did not open");

  await (await requiredElement(taskShell, ".task-timer-control")).tap();
  await waitForData(page, (data) => data.taskPaused === true, "timer did not pause");
  await installNetworkGuard(program);
}

async function submittedMockReport(program) {
  const page = await openVisualScenario(program, "c06-mock-exam-answering");
  await page.setData({ status: "submitted", receipt: null });
  await (await requiredElement(page, ".page-state .primary-button")).tap();
  await waitForPath(program, "/pages/mock-report/index", "submitted mock did not open its report");
}

async function essayComposeActions(program) {
  let page = await openVisualScenario(program, "f11-essay-compose-workbench");
  const firstActions = await page.$$(".compose-action-grid .workbench-action");
  assert.equal(firstActions.length, 2, "essay compose should expose outline and drafting actions");
  await firstActions[0].tap();
  await waitForPath(program, "/pages/essay-task/index", "outline action did not navigate");

  page = await openVisualScenario(program, "f11-essay-compose-workbench");
  const secondActions = await page.$$(".compose-action-grid .workbench-action");
  await secondActions[1].tap();
  await waitForPath(program, "/pages/essay-task/index", "drafting action did not navigate");
}

async function reportReturnToday(program) {
  const page = await openVisualScenario(program, "f02-report-overview-ready");
  await (await requiredElement(page, ".report-next-action")).tap();
  await waitForPath(program, "/pages/today/index", "report did not return to today's plan");
}

async function membershipVirtualPayment(program) {
  await setAuthentication(program, true);
  await installCommerceMocks(program);
  const page = await openVisualScenario(program, "g01-membership-ready", { keepMocks: true });
  await (await requiredElement(page, ".accept-row")).tap();
  await waitForData(page, (data) => data.accepted === true, "membership policy acceptance failed");
  await (await requiredElement(page, ".s9-primary-button")).tap();
  await waitForData(page, (data) => data.paying === false, "membership payment did not settle");
  const audit = await program.evaluate(function readCommerceAudit() {
    return {
      virtualPaymentCalled: Boolean(globalThis.__ananClickAuditVirtualPayment),
    };
  });
  assert.equal(audit.virtualPaymentCalled, true, "virtual payment was not invoked");
  await installNetworkGuard(program);
}

async function profileMenu(program) {
  let page = await openVisualScenario(program, "f09-profile-ready");
  let rows = await page.$$(".profile-menu-row");
  assert.ok(rows.length >= 4, "profile menu should expose primary account links");
  await rows[2].tap();
  await waitForPath(program, "/pages/membership/index", "profile membership link did not navigate");

  page = await openVisualScenario(program, "f09-profile-ready");
  rows = await page.$$(".profile-menu-row");
  await rows[3].tap();
  await waitForPath(program, "/pages/privacy/index", "profile privacy link did not navigate");
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
  await page.setData(structuredClone(scenario.data));
  if (scenario.scrollTop) await program.pageScrollTo(scenario.scrollTop);
  await page.waitFor(160);
  return page;
}

async function requiredElement(page, selector) {
  const element = await page.$(selector);
  assert.ok(element, `Missing clickable selector ${selector} on ${page.path}`);
  return element;
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
  await waitForPredicate(async () => predicate(await page.data()), message);
}

async function waitForPredicate(predicate, message) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(message);
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
  await restoreWxMethodIfMocked(program, "request");
  await restoreWxMethodIfMocked(program, "login");
  await program.mockWxMethod("request", {
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
  });
  await program.evaluate(function installAuditLoginMocks() {
    globalThis.__ananClickAuditLoginCalled = false;
    wx.login = function auditLogin(options) {
      globalThis.__ananClickAuditLoginCalled = true;
      options.success?.({ code: "click-audit-login-code", errMsg: "login:ok" });
    };
  });
}

async function installXingceMocks(program) {
  await restoreWxMethodIfMocked(program, "request");
  await program.mockWxMethod("request", {
    data: envelope(createXingceSession()),
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  });
}

async function installCommerceMocks(program) {
  await restoreWxMethodIfMocked(program, "request");
  await restoreWxMethodIfMocked(program, "login");
  await restoreWxMethodIfMocked(program, "getDeviceInfo");
  await restoreWxMethodIfMocked(program, "canIUse");
  await restoreWxMethodIfMocked(program, "requestVirtualPayment");
  await program.mockWxMethod("request", {
    data: envelope(createCommerceAuditResponse()),
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  });
  await program.evaluate(function installAuditCommerceMocks() {
    globalThis.__ananClickAuditVirtualPayment = false;
    wx.getDeviceInfo = function auditGetDeviceInfo() {
      return { platform: "android" };
    };
    wx.login = function auditLogin(options) {
      options.success?.({ code: "click-audit-payment-code", errMsg: "login:ok" });
    };
    wx.canIUse = function auditCanIUse(capability) {
      return capability === "requestVirtualPayment";
    };
    wx.requestVirtualPayment = function auditVirtualPayment(options) {
      globalThis.__ananClickAuditVirtualPayment = true;
      globalThis.__ananClickAuditVirtualPaymentPayload = options;
      options.success?.({ errMsg: "requestVirtualPayment:ok" });
    };
  });
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

async function restoreWxMethodIfMocked(program, method) {
  try {
    await program.restoreWxMethod(method);
  } catch {
    // The method may not have been mocked by miniprogram-automator in this run.
  }
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

function createCommerceAuditResponse() {
  const offer = {
    offerVersionId: "founder-30d-v1",
    title: "岸岸创始体验",
    amountMinor: 9900,
    cohortRemaining: 18,
    recruitmentState: "open",
    featureHighlights: ["AI 学习大脑与每日计划", "岗位资格与分差判断", "申论证据化评阅"],
    policies: [
      { policyKind: "service_terms", policyVersion: "service@v1", title: "服务规则" },
      { policyKind: "refund", policyVersion: "refund@v1", title: "30 天退款保障" },
      { policyKind: "process_reward", policyVersion: "reward@v1", title: "学习过程奖励" },
      { policyKind: "marketing", policyVersion: "marketing@v1", title: "不承诺提分或上岸" },
    ],
  };
  const awaitingOrder = {
    orderId: "click-audit-order",
    state: "awaiting_payment",
    paidAt: null,
    paymentChannel: "wechat_virtual_payment",
    clientPlatform: "android",
    virtualPayment: {
      signData: '{"goodsId":"founder-30d-v1"}',
      paySig: "pay-sig",
      signature: "signature",
      mode: "short_series_goods",
    },
  };
  return {
    entitlement: { tier: "free", subscriptionEnd: null },
    catalog: { offers: [offer] },
    orders: [],
    rewardProgress: { effectiveDays: 7, weeklyReflections: 2, coreCompletionRatio: 0.68 },
    refundEligibility: null,
    order: awaitingOrder,
    ...awaitingOrder,
    state: "paid",
    paidAt: "2026-07-18T00:00:00Z",
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

function consoleEventText(event) {
  if (typeof event === "string") return event;
  try {
    return JSON.stringify(event);
  } catch {
    return String(event);
  }
}
