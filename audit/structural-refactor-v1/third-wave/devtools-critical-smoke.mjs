import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import automator from "../../../apps/wechat/node_modules/miniprogram-automator/out/index.js";
import {
  readPageData,
  setPageData,
} from "../../../apps/wechat/e2e/page-data-bridge.mjs";
import { visualScenarios } from "../../../apps/wechat/e2e/visual-scenarios.mjs";

const repositoryRoot = path.resolve(import.meta.dirname, "../../..");
const projectPath = path.join(repositoryRoot, "apps/wechat");
const cliPath = "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const profile = process.env.EVIDENCE_PROFILE ?? "unknown-device";
const outputDirectory = path.resolve(import.meta.dirname, profile);
const fullInteraction = process.env.FULL_INTERACTION === "1";
const scenarioById = new Map(visualScenarios.map((scenario) => [scenario.id, scenario]));
const runtimeFailures = [];
const checks = [];

await mkdir(outputDirectory, { recursive: true });
process.stdout.write(`[third-wave] launching ${profile}\n`);
const wsEndpoint = process.env.AUTOMATION_WS ?? "";
const program = wsEndpoint
  ? await automator.connect({ wsEndpoint })
  : await automator.launch({
      cliPath,
      projectPath,
      timeout: 60_000,
      trustProject: true,
    });
program.on("exception", (event) => runtimeFailures.push(String(event?.message ?? event)));
program.on("error", (event) => runtimeFailures.push(`automation: ${JSON.stringify(event)}`));

try {
  process.stdout.write("[third-wave] launched; reading tool/device info\n");
  const systemInfo = await retryProtocol("systemInfo", () => program.systemInfo());
  const toolInfo = await retryProtocol("toolInfo", () => program.send("Tool.getInfo"));
  await installPlatformMocks(program);

  if (fullInteraction) {
    process.stdout.write("[third-wave] practice interaction start\n");
    await verifyPracticeRecovery(program);
    process.stdout.write("[third-wave] practice interaction passed\n");
  }
  await freezeFixtureTaskClocks(program);
  process.stdout.write("[third-wave] practice compiled fixture start\n");
  await capturePracticeCompiledState(program);
  process.stdout.write("[third-wave] mock material fixture start\n");
  await captureMockMaterialTable(program);
  process.stdout.write("[third-wave] reflection replay fixture start\n");
  await captureReflectionReplay(program);

  assert.deepEqual(runtimeFailures, []);
  const receipt = {
    schemaVersion: 1,
    kind: "local-devtools-structural-smoke",
    commit: process.env.TEST_COMMIT ?? "52568db3d8cf231e09eb3f3dedbfd088deb12a23",
    profile,
    projectPath,
    toolInfo,
    systemInfo,
    networkMode: "deterministic local wx.request mocks only",
    prohibitedCommands: ["preview", "auto-preview", "upload"],
    runtimeFailures,
    checks,
  };
  await writeFile(
    path.join(outputDirectory, "receipt.json"),
    `${JSON.stringify(receipt, null, 2)}\n`,
  );
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
} finally {
  if (wsEndpoint) program.disconnect();
  else await program.close();
}

async function verifyPracticeRecovery(activeProgram) {
  await leaveLoginController(activeProgram);
  await activeProgram.evaluate(function clearSimulatorStorage() {
    wx.clearStorageSync();
  });
  await setAuthentication(activeProgram, true);
  await installXingceMocks(activeProgram, false);
  process.stdout.write("[third-wave] practice relaunch online fixture\n");
  let page = await activeProgram.reLaunch(
    "/pages/practice/index?mode=targeted&planTaskId=audit-plan-task",
  );
  await waitForData(page, (data) => data.status === "ready", "practice did not initialize");

  await invoke(page, "toggleDraft");
  await invoke(page, "inputDraft", { detail: { value: "独立验证：先锁定分母，再对比增速" } });
  const materialStroke = {
    strokeId: "third-wave-material-stroke",
    surface: "material",
    points: [
      { x: 118, y: 220, t: 0 },
      { x: 280, y: 220, t: 80 },
    ],
    color: "#E45B4E",
    width: 3,
    atMs: 208_000,
  };
  await invoke(page, "changeMaterialAnnotations", {
    detail: { strokes: [materialStroke], action: "added" },
  });
  await invoke(page, "onQuestionScroll", { detail: { scrollTop: 360 } });
  let data = await readPageData(activeProgram, page);
  assert.equal(data.questionScrollPosition, 360);
  await invoke(page, "toggleDraft");
  await invoke(page, "onQuestionTouchStart", {
    timeStamp: 100,
    touches: [{ clientX: 320, clientY: 300 }],
    changedTouches: [{ clientX: 320, clientY: 300 }],
  });
  await invoke(page, "onQuestionTouchMove", {
    timeStamp: 180,
    touches: [{ clientX: 140, clientY: 304 }],
    changedTouches: [{ clientX: 140, clientY: 304 }],
  });
  await invoke(page, "onQuestionTouchEnd", {
    timeStamp: 240,
    touches: [],
    changedTouches: [{ clientX: 80, clientY: 304 }],
  });
  await page.waitFor(450);
  data = await readPageData(activeProgram, page);
  assert.equal(data.view.current, 2);
  assert.ok(data.questionScrollTop === 0 || data.questionScrollTop === 0.1);
  await installXingceMocks(activeProgram, true);
  await invoke(page, "toggleDraft");
  await invoke(page, "inputDraft", { detail: { value: "断网恢复草稿：第二题保留计算式" } });
  await invoke(page, "changeMaterialAnnotations", {
    detail: {
      strokes: [{ ...materialStroke, strokeId: "third-wave-material-stroke-q2" }],
      action: "added",
    },
  });
  await page.waitFor(900);
  process.stdout.write("[third-wave] practice offline checkpoint mutations complete\n");
  await activeProgram.screenshot({
    path: path.join(outputDirectory, "practice-before-offline-recovery.png"),
  });

  await activeProgram.reLaunch("/pages/today/index");
  await new Promise((resolve) => setTimeout(resolve, 900));
  page = await activeProgram.reLaunch(
    "/pages/practice/index?mode=targeted&planTaskId=audit-plan-task",
  );
  await waitForData(
    page,
    (next) => next.status === "ready" && next.view?.current === 2,
    "practice did not recover the local checkpoint",
  );
  process.stdout.write("[third-wave] practice offline checkpoint recovered\n");
  await page.waitFor(900);
  data = await readPageData(activeProgram, page);
  assert.equal(data.view.question.draftText, "断网恢复草稿：第二题保留计算式");
  assert.equal(data.hasPendingCheckpoint, true);
  assert.equal(data.view.question.annotations.some((stroke) => stroke.surface === "material"), true);
  await activeProgram.screenshot({
    path: path.join(outputDirectory, "practice-recovered-offline.png"),
  });
  checks.push({
    id: "practice-draft-save-restore-swipe-scroll",
    status: "passed",
    evidence: {
      recoveredQuestion: data.view.current,
      recoveredDraft: data.view.question.draftText,
      pendingCheckpoint: data.hasPendingCheckpoint,
      materialStrokeRecovered: true,
      verticalScrollBeforeSwipe: 360,
      questionScrollResetAfterSwipe: true,
    },
    screenshots: [
      "practice-before-offline-recovery.png",
      "practice-recovered-offline.png",
    ],
  });
}

async function capturePracticeCompiledState(activeProgram) {
  const scenario = scenarioById.get("c13-xingce-annotation");
  assert.ok(scenario);
  const page = await openScenario(activeProgram, scenario);
  const fixture = structuredClone(scenario.data);
  fixture.draftOpen = true;
  fixture.view.question.draftText = "草稿、题面批注与材料批注共用中立运行时";
  fixture.questionScrollPosition = 280;
  fixture.questionContentHeight = 1680;
  fixture.materialAnnotations = fixture.view.question.annotations.map((stroke) => ({
    ...stroke,
    strokeId: `material-${stroke.strokeId}`,
    surface: "material",
  }));
  await setPageData(activeProgram, page, fixture);
  await page.waitFor(650);
  const applied = await readPageData(activeProgram, page);
  assert.equal(applied.draftOpen, true);
  assert.ok(applied.view.question.annotations.length > 0);
  assert.ok(applied.materialAnnotations.length > 0);
  await activeProgram.screenshot({ path: path.join(outputDirectory, "practice-runtime.png") });
  checks.push({
    id: "practice-compiled-draft-question-material-annotation",
    status: "passed",
    screenshot: "practice-runtime.png",
    evidence: {
      draftOpen: true,
      questionStrokes: applied.view.question.annotations.length,
      materialStrokes: applied.materialAnnotations.length,
      questionScrollPosition: applied.questionScrollPosition,
    },
  });
}

async function captureMockMaterialTable(activeProgram) {
  const scenario = scenarioById.get("c06-mock-exam-answering");
  assert.ok(scenario);
  const page = await openScenario(activeProgram, scenario);
  const fixture = structuredClone(scenario.data);
  const table = createWideTable();
  const material = {
    materialId: "third-wave-table-material",
    title: "分地区公共服务数据",
    text: "八列资料分析表",
    html: null,
    assets: [],
    content: [table],
    anchors: [{ anchorId: table.blockId, blockId: table.blockId, label: table.caption }],
  };
  fixture.question.material = material;
  fixture.materialQuestionId = fixture.question.questionId;
  // This scenario is a compiled read-only geometry fixture. Lock mutations while the visual
  // data is injected so material-reader position events cannot autosave against an intentionally
  // absent backend-owned mock-exam runtime.
  fixture.interactionLocked = true;
  fixture.materialOpen = true;
  fixture.annotationOpen = true;
  fixture.annotationSurface = "material";
  fixture.materialReaderState = {
    mode: "question_first",
    position: { anchorId: table.blockId, scrollTop: 140, zoom: 1 },
    preferences: { fontScale: 1, colorMode: "standard", lineSpacing: 1 },
  };
  fixture.materialAnnotations = [
    {
      strokeId: "mock-material-table-stroke",
      surface: "material",
      points: [
        { x: 180, y: 260, t: 0 },
        { x: 540, y: 260, t: 120 },
      ],
      color: "#E45B4E",
      width: 3,
      atMs: 15_000,
    },
  ];
  await setPageData(activeProgram, page, fixture);
  await page.waitFor(900);
  const geometry = await activeProgram.evaluate(function readTableGeometry() {
    const pages = getCurrentPages();
    const current = pages[pages.length - 1];
    const materialReader = current?.selectComponent(".mock-material-pane");
    const renderer = materialReader?.selectComponent("content-renderer");
    renderer?.onTableScroll?.();
    return new Promise((resolve) => {
      setTimeout(() => {
        resolve(
          JSON.stringify({
            horizontalRegions: materialReader?.data?.horizontalRegions ?? [],
            materialPosition: current?.data?.materialReaderState?.position ?? null,
          }),
        );
      }, 250);
    });
  });
  const parsed = JSON.parse(geometry);
  const region = parsed.horizontalRegions[0];
  assert.ok(region, "mock table did not publish a horizontal annotation region");
  assert.ok(region.contentWidth > region.viewportWidth);
  await activeProgram.screenshot({ path: path.join(outputDirectory, "mock-material-table.png") });
  checks.push({
    id: "mock-material-annotation-table-horizontal-scroll",
    status: "passed",
    screenshot: "mock-material-table.png",
    evidence: {
      materialStrokes: fixture.materialAnnotations.length,
      tableColumns: table.columnCount,
      viewportWidth: region.viewportWidth,
      contentWidth: region.contentWidth,
      horizontallyScrollable: region.contentWidth > region.viewportWidth,
      restoredMaterialPosition: parsed.materialPosition,
    },
  });
}

async function captureReflectionReplay(activeProgram) {
  const scenario = scenarioById.get("c16-xingce-replay");
  assert.ok(scenario);
  const page = await openScenario(activeProgram, scenario);
  await invoke(page, "toggleReplayPlayback");
  await page.waitFor(180);
  await invoke(page, "seekReplay", { detail: { value: 42_000 } });
  await page.waitFor(120);
  let data = await readPageData(activeProgram, page);
  assert.equal(data.replayOpen, true);
  assert.equal(data.replayMs, 42_000);
  await activeProgram.screenshot({ path: path.join(outputDirectory, "reflection-replay.png") });
  await activeProgram.reLaunch("/pages/today/index");
  await new Promise((resolve) => setTimeout(resolve, 500));
  const current = await activeProgram.currentPage();
  assert.equal(current.path, "pages/today/index");
  checks.push({
    id: "reflection-replay-seek-unload",
    status: "passed",
    screenshot: "reflection-replay.png",
    evidence: {
      replaySeekMs: data.replayMs,
      replayDurationMs: data.replayDurationMs,
      unloadDestination: current.path,
      runtimeFailuresAfterUnload: runtimeFailures.length,
    },
  });
}

async function openScenario(activeProgram, scenario) {
  await leaveLoginController(activeProgram);
  await setAuthentication(activeProgram, scenario.authenticated === true);
  await installNetworkGuard(activeProgram);
  const page = await activeProgram.reLaunch(scenario.route);
  assert.ok(page);
  await page.waitFor(450);
  await setPageData(activeProgram, page, structuredClone(scenario.data));
  await page.waitFor(350);
  return page;
}

async function leaveLoginController(activeProgram) {
  const page = await activeProgram.currentPage();
  if (page?.path === "pages/login/index") {
    await activeProgram.reLaunch("/account/support/index");
  }
}

async function setAuthentication(activeProgram, authenticated) {
  await activeProgram.evaluate(function applySession(hasSession) {
    const storageKey = "gongkao.auth.session.v1";
    const app = getApp();
    if (hasSession) {
      const session = { accessToken: "third-wave-token", userId: "click-audit-user" };
      wx.setStorageSync(storageKey, session);
      app.globalData.accessToken = session.accessToken;
      app.globalData.userId = session.userId;
    } else {
      wx.removeStorageSync(storageKey);
      delete app.globalData.accessToken;
      delete app.globalData.userId;
    }
  }, authenticated);
}

async function installPlatformMocks(activeProgram) {
  await activeProgram.mockWxMethod("showToast", { errMsg: "showToast:ok" });
  await activeProgram.mockWxMethod("showModal", {
    confirm: true,
    cancel: false,
    errMsg: "showModal:ok",
  });
}

async function freezeFixtureTaskClocks(activeProgram) {
  await activeProgram.evaluate(function installDeterministicFixtureClock() {
    if (globalThis.__thirdWaveOriginalSetInterval) return;
    const originalSetInterval = globalThis.setInterval;
    globalThis.__thirdWaveOriginalSetInterval = originalSetInterval;
    globalThis.setInterval = function thirdWaveFixtureSetInterval(callback, delay, ...args) {
      if (delay === 125 || delay === 1_000) {
        return originalSetInterval(function holdFixtureFrame() {}, delay);
      }
      return originalSetInterval(callback, delay, ...args);
    };
  });
}

async function installNetworkGuard(activeProgram) {
  await activeProgram.evaluate(function guardRequests() {
    wx.request = function blockedRequest(options) {
      const blocked = {
        data: {
          code: "third_wave_network_guard",
          message: "Simulator evidence uses only local deterministic fixtures.",
          requestId: "third-wave-network-guard",
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
      return { abort() {}, onHeadersReceived() {}, offHeadersReceived() {} };
    };
  });
}

async function installXingceMocks(activeProgram, failCheckpoint) {
  const session = responseEnvelope(createXingceSession());
  const lease = responseEnvelope(createXingceLease());
  await activeProgram.evaluate(
    function mockXingce(sessionResponse, leaseResponse, offlineCheckpoint) {
      wx.request = function localXingceRequest(options) {
        const url = String(options.url ?? "");
        const method = String(options.method ?? "GET").toUpperCase();
        let response;
        if (url.includes("/api/v2/task-editing/leases")) {
          response = leaseResponse;
        } else if (url.includes("/api/v2/xingce/task-sessions")) {
          if (offlineCheckpoint && method === "PUT") {
            response = {
              data: {
                code: "offline",
                message: "deterministic offline retry fixture",
                requestId: "third-wave-offline",
              },
              statusCode: 503,
              header: {},
              cookies: [],
              errMsg: "request:ok",
            };
          } else if (method === "PUT") {
            const command = options.data ?? {};
            const original = sessionResponse.data.data;
            response = {
              ...sessionResponse,
              data: {
                ...sessionResponse.data,
                data: {
                  ...original,
                  currentQuestionId: command.currentQuestionId ?? original.currentQuestionId,
                  answers: Array.isArray(command.answers) ? command.answers : original.answers,
                  totalElapsedSeconds:
                    command.totalElapsedSeconds ?? original.totalElapsedSeconds,
                  revision: Number(command.expectedRevision ?? original.revision) + 1,
                },
              },
            };
          } else {
            response = sessionResponse;
          }
        } else {
          response = {
            data: {
              code: "third_wave_network_guard",
              message: "Unexpected request in structural smoke.",
              requestId: "third-wave-network-guard",
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
        return { abort() {}, onHeadersReceived() {}, offHeadersReceived() {} };
      };
    },
    session,
    lease,
    failCheckpoint,
  );
}

async function invoke(page, handler, event = {}) {
  const payload = await program.evaluate(
    function invokeHandler(expectedRoute, methodName, eventJson) {
      const pages = getCurrentPages();
      const current = pages[pages.length - 1];
      if (!current || current.route !== expectedRoute) {
        return JSON.stringify({ error: "route", actual: current?.route ?? null });
      }
      if (typeof current[methodName] !== "function") {
        return JSON.stringify({ error: "handler", methodName });
      }
      const supplied = JSON.parse(eventJson);
      const dataset = supplied.currentTarget?.dataset ?? supplied.dataset ?? {};
      current[methodName]({
        currentTarget: { dataset },
        target: { dataset },
        detail: {},
        ...supplied,
      });
      return JSON.stringify({ invoked: true });
    },
    page.path,
    handler,
    JSON.stringify(event),
  );
  const parsed = JSON.parse(payload);
  assert.equal(parsed.invoked, true, `${page.path}.${handler}: ${JSON.stringify(parsed)}`);
}

async function waitForData(page, predicate, message) {
  let lastData = null;
  for (let index = 0; index < 100; index += 1) {
    const data = await readPageData(program, page);
    lastData = data;
    if (predicate(data)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(`${message}: ${JSON.stringify(lastData)}`);
}

async function retryProtocol(label, operation) {
  let lastError;
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      process.stdout.write(
        `[third-wave] ${label} protocol retry ${String(attempt)}/4: ${String(error?.message ?? error)}\n`,
      );
      await new Promise((resolve) => setTimeout(resolve, 1_500));
    }
  }
  throw lastError;
}

function responseEnvelope(data) {
  return {
    data: { data, requestId: "third-wave", generatedAt: "2026-08-08T00:00:00Z" },
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  };
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
    acquiredAt: "2026-08-08T00:00:00Z",
    lastSeenAt: "2026-08-08T00:00:00Z",
    takenOverAt: null,
  };
}

function createWideTable() {
  const columnCount = 8;
  const rowCount = 4;
  const cells = [];
  for (let row = 0; row < rowCount; row += 1) {
    for (let column = 0; column < columnCount; column += 1) {
      cells.push({
        row,
        column,
        rowSpan: 1,
        columnSpan: 1,
        header: row === 0,
        text: row === 0 ? `指标 ${String(column + 1)}` : `${String(100 + row * 10 + column)}%`,
        html: null,
        align: column === 0 ? "left" : "right",
      });
    }
  }
  return {
    blockId: "third-wave-regional-table",
    type: "table",
    rowCount,
    columnCount,
    frozenHeaderRows: 1,
    horizontallyScrollable: true,
    cells,
    caption: "八列资料分析表",
    columnWidths: [180, 150, 150, 150, 150, 150, 150, 150],
    rowHeights: [64, 56, 56, 56],
  };
}
