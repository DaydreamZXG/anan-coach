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
  ["deep-practice-interaction-release", practiceInteractionRelease],
  ["deep-mock-interaction-release", mockInteractionRelease],
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

const miniProgram = await launchMiniProgram();

const runtimeFailures = [];
const runtimeWarnings = [];
const journeyFailures = [];
const simulatorLimitations = [];
miniProgram.on("exception", (event) => runtimeFailures.push(String(event?.message ?? event)));
miniProgram.on("console", (event) => {
  const message = consoleEventText(event);
  if (isActionableRuntimeWarning(message)) runtimeWarnings.push(message);
});

try {
  await retryTransientAutomation(() => freezeAuditAnimationClock(miniProgram));
  await retryTransientAutomation(() => installNavigationAudit(miniProgram));
  await retryTransientAutomation(() => installNetworkGuard(miniProgram));
  for (const [name, journey] of selectedJourneys) {
    try {
      await runJourney(name, journey);
    } catch (error) {
      journeyFailures.push({ name, message: String(error?.message ?? error) });
      process.stdout.write("failed\n");
      await installNetworkGuard(miniProgram);
    }
  }
} finally {
  await withTimeout(setAuthentication(miniProgram, false), 5_000, "Auth fixture did not clear");
  await withTimeout(miniProgram.close(), 10_000, "Automation connection did not close cleanly");
}

if (simulatorLimitations.length > 0) {
  process.stdout.write(`Simulator limitations:\n- ${simulatorLimitations.join("\n- ")}\n`);
}
if (journeyFailures.length > 0) {
  const failureSummary = journeyFailures
    .map(({ name, message }) => `- ${name}: ${message}`)
    .join("\n");
  assert.fail(
    `Completed all ${String(selectedJourneys.length)} selected journeys with ` +
      `${String(journeyFailures.length)} failure(s):\n${failureSummary}`,
  );
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
  process.env.LITERAL_DOM_AUDIT === "1"
    ? `Audited ${String(selectedJourneys.length)} selected mock-backed journeys, including literal ` +
        "Mini Program DOM taps/touches where declared; this is not real-backend or device evidence.\n"
    : `Audited ${String(selectedJourneys.length)} mock-backed page binding/controller journeys; ` +
        "this command is not literal DOM tap, real-backend, or device evidence.\n",
);

async function launchMiniProgram() {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      return automationEndpoint
        ? await automator.connect({ wsEndpoint: automationEndpoint })
        : await automator.launch({
            cliPath,
            projectPath: projectRoot,
            timeout: 60_000,
            trustProject: true,
          });
    } catch (error) {
      lastError = error;
      const message = String(error?.message ?? error);
      const transient =
        message.includes("timeout waiting for automator response") ||
        message.includes("Connection closed") ||
        message.includes("Failed connecting to");
      if (!transient || attempt === 3) throw error;
      process.stderr.write(
        `[automator] launch attempt ${String(attempt)} failed; retrying: ${message}\n`,
      );
      await new Promise((resolve) => setTimeout(resolve, 2_000));
    }
  }
  throw lastError;
}

function pagePurposeJourney(contract) {
  return async (program) => {
    if (contract.route === "pages/login/index") {
      await setAuthentication(program, true);
      await reLaunchScenarioRoute(program, "/pages/today/index");
      await installLoginMocks(program);
      await setAuthentication(program, false);
      const loginPage = await reLaunchWithRetry(program, "/pages/login/index");
      assert.ok(loginPage, "Unable to open the silent login entry");
      await waitForPath(program, "/pages/today/index", "silent login did not switch to today");
      const loginEvidence = await readLoginAuditEvidence(program);
      assert.equal(loginEvidence.loginCalled, true, "silent entry did not request a WeChat code");
      assert.ok(
        loginEvidence.requestUrls.some((url) => url.includes("/api/v2/auth/wechat")),
        "silent entry did not exchange the WeChat code",
      );
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

    if (Object.keys(interaction.preconditionData).length > 0) {
      await setPageData(program, page, interaction.preconditionData);
    }
    const beforePath = normalizeRoutePath(page.path);
    const beforeData = await readPageData(program, page);
    const beforeNavigation = await readNavigationAudit(program);
    const beforeBlockedRequests = await readNetworkGuardAudit(program);
    await invokePageHandler(program, page, interaction.handler, interaction.dataset);
    await page.waitFor(1_200);

    const currentPage = await currentPageWithRetry(program);
    const afterPath = normalizeRoutePath(currentPage?.path);
    const afterNavigation = await readNavigationAudit(program);
    const afterBlockedRequests = await readNetworkGuardAudit(program);
    const navigationObserved = afterNavigation.length > beforeNavigation.length;
    const newlyBlockedRequests = afterBlockedRequests.slice(beforeBlockedRequests.length);
    const afterData =
      !navigationObserved && normalizeRoutePath(currentPage?.path) === beforePath
        ? await readPageData(program, page)
        : null;
    if (afterData !== null) {
      assertPageDataIsNotError(
        `${contract.route} primary interaction ${interaction.handler}`,
        afterData,
      );
    }
    if (interaction.observable) {
      assert.ok(
        afterPath !== beforePath ||
          JSON.stringify(afterData) !== JSON.stringify(beforeData) ||
          navigationObserved,
        `${contract.route} primary interaction ${interaction.handler} produced no observable effect; ` +
          `beforePath=${beforePath}; afterPath=${afterPath}; dataset=${JSON.stringify(interaction.dataset)}; ` +
          `navigation=${JSON.stringify(afterNavigation.slice(beforeNavigation.length))}; ` +
          `viewTasks=${JSON.stringify(beforeData?.view?.tasks ?? null)}`,
      );
    }
    assert.deepEqual(
      newlyBlockedRequests,
      [],
      `${contract.route} primary interaction ${interaction.handler} reached blocked API request(s); ` +
        `user-visible success is not verified:\n${newlyBlockedRequests
          .map((request) => `${request.method} ${request.url}`)
          .join("\n")}`,
    );
  };
}

async function runJourney(name, journey) {
  process.stdout.write(`[journey] ${name} ... `);
  const failureOffset = runtimeFailures.length;
  const warningOffset = runtimeWarnings.length;
  await withTimeout(journey(miniProgram), 60_000, `${name} did not finish within 60 seconds`);
  const settledPage = await currentPageWithRetry(miniProgram);
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
  const page = await reLaunchScenarioRoute(
    program,
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

async function practiceInteractionRelease(program) {
  await setAuthentication(program, true);
  await installXingceMocks(program);
  const page = await reLaunchScenarioRoute(
    program,
    "/pages/practice/index?mode=targeted&planTaskId=audit-plan-task",
  );
  await waitForData(page, (data) => data.status === "ready", "practice page did not become ready");
  await literalTaskInteractionRelease(page, {
    current: (data) => data.view?.current,
    materialOpenSelector: ".practice-material-action--collapse",
    materialReaderSelector: ".practice-material-reader",
    paneSelector: ".practice-question-pane",
    pauseAvailable: true,
    selected: (data) => data.view?.question?.selectedOptionIds?.length > 0,
  });
  await installNetworkGuard(program);
}

async function mockInteractionRelease(program) {
  await setAuthentication(program, true);
  await installMockExamMocks(program);
  const page = await reLaunchScenarioRoute(
    program,
    "/pages/mock-exam/index?sessionId=audit-mock-session",
  );
  await waitForData(page, (data) => data.status === "ready", "mock page did not become ready");
  await literalTaskInteractionRelease(page, {
    current: (data) => data.current,
    materialOpenSelector: ".material-reader__inline-expand",
    materialReaderSelector: ".mock-material-pane",
    paneSelector: ".mock-question-pane",
    pauseAvailable: false,
    selected: (data) => data.question?.options?.some((option) => option.selected) === true,
  });
  await installNetworkGuard(program);
}

async function literalTaskInteractionRelease(page, contract) {
  let questionPane = await requiredElement(page, contract.paneSelector);

  if (contract.pauseAvailable) {
    const taskShell = await requiredElement(page, "#runtime-task-shell");
    const timerControl = await requiredElement(taskShell, ".task-timer-control");
    await timerControl.tap();
    await waitForData(
      page,
      (data) =>
        data.interactionMode === "paused" &&
        data.taskPaused === true &&
        data.pauseCaptureActive === true &&
        data.captureLayer === "pause" &&
        data.touchOwner === "overlay",
      `${page.path} did not project pause capture ownership`,
    );
    await waitForElement(page, ".pause-mask", `${page.path} did not mount the pause capture mask`);

    await invokePageHandler(miniProgram, page, "onHide");
    await waitForData(
      page,
      (data) =>
        data.taskPaused === true &&
        data.pauseCaptureActive === false &&
        data.captureLayer === "none" &&
        data.touchOwner === "none",
      `${page.path} did not release pause capture on hide`,
    );
    assert.equal(
      await page.$(".pause-mask"),
      null,
      `${page.path} retained the physical pause mask while hidden`,
    );

    await invokePageHandler(miniProgram, page, "onShow");
    await waitForData(
      page,
      (data) => data.pauseCaptureActive === true && data.captureLayer === "pause",
      `${page.path} did not restore paused state on show`,
    );
    const pauseMask = await waitForElement(
      page,
      ".pause-mask",
      `${page.path} did not remount the paused state on show`,
    );
    const resumeButton = await requiredElement(pauseMask, ".pause-resume");
    await resumeButton.tap();
    await waitForData(
      page,
      (data) =>
        data.taskPaused === false &&
        data.pauseCaptureActive === false &&
        data.captureLayer === "none" &&
        data.touchOwner === "question" &&
        data.interactionLocked === false,
      `${page.path} did not release pause capture after resume`,
    );
  }

  const beforeMaterial = Number(contract.current(await readPageData(miniProgram, page)));
  const materialReader = await requiredElement(page, contract.materialReaderSelector);
  const materialOpenRoot = contract.materialOpenSelector.startsWith(".material-reader__")
    ? materialReader
    : page;
  const materialOpenButton = await requiredElement(materialOpenRoot, contract.materialOpenSelector);
  await materialOpenButton.tap();
  await waitForData(
    page,
    (data) =>
      data.interactionMode === "material" &&
      data.materialOpen === true &&
      data.materialCaptureActive === true &&
      data.captureLayer === "material" &&
      data.touchOwner === "material" &&
      data.swipeBlocked === true,
    `${page.path} did not project material capture ownership`,
  );
  await waitForElement(
    materialReader,
    ".material-reader__surface",
    `${page.path} did not physically mount its material capture surface`,
  );
  assert.equal(
    await questionPane.attribute("data-touch-owner"),
    "material",
    `${page.path} did not project material ownership into WXML`,
  );

  await invokePageHandler(miniProgram, page, "onHide");
  await waitForData(
    page,
    (data) =>
      data.materialOpen === true &&
      data.materialCaptureActive === false &&
      data.captureLayer === "none" &&
      data.touchOwner === "none",
    `${page.path} did not release material capture on hide`,
  );
  await assertMaterialCaptureAbsent(page, contract.materialReaderSelector);

  await invokePageHandler(miniProgram, page, "onShow");
  await waitForData(
    page,
    (data) =>
      data.materialCaptureActive === true &&
      data.captureLayer === "material" &&
      data.touchOwner === "material" &&
      data.interactionLocked === false,
    `${page.path} did not restore material capture on show`,
  );
  const restoredMaterialReader = await requiredElement(page, contract.materialReaderSelector);
  await waitForElement(
    restoredMaterialReader,
    ".material-reader__surface",
    `${page.path} did not remount material capture on show`,
  );

  await invokePageHandler(miniProgram, page, "nextQuestion");
  await waitForData(
    page,
    (data) =>
      contract.current(data) === beforeMaterial + 1 &&
      data.materialOpen === false &&
      data.materialCaptureActive === false &&
      data.captureLayer === "none" &&
      data.touchOwner === "question",
    `${page.path} did not release material capture on question change`,
  );
  await assertMaterialCaptureAbsent(page, contract.materialReaderSelector);
  await waitForData(
    page,
    (data) => data.interactionLocked === false,
    `${page.path} did not finish its question-change checkpoint`,
  );
  await invokePageHandler(miniProgram, page, "previousQuestion");
  await waitForData(
    page,
    (data) => contract.current(data) === beforeMaterial && data.touchOwner === "question",
    `${page.path} did not return to the initial question after material release`,
  );

  const closeCheckReader = await requiredElement(page, contract.materialReaderSelector);
  const closeCheckOpenRoot = contract.materialOpenSelector.startsWith(".material-reader__")
    ? closeCheckReader
    : page;
  const closeCheckOpenButton = await requiredElement(
    closeCheckOpenRoot,
    contract.materialOpenSelector,
  );
  await closeCheckOpenButton.tap();
  await waitForData(
    page,
    (data) => data.materialCaptureActive === true && data.touchOwner === "material",
    `${page.path} did not reopen material capture`,
  );
  const closeReader = await requiredElement(page, contract.materialReaderSelector);
  const materialBack = await waitForElement(
    closeReader,
    ".material-reader__back",
    `${page.path} material reader has no return action`,
  );
  await materialBack.tap();
  await waitForData(
    page,
    (data) =>
      data.interactionMode === "question" &&
      data.materialOpen === false &&
      data.materialCaptureActive === false &&
      data.captureLayer === "none" &&
      data.touchOwner === "question",
    `${page.path} did not release material capture through its close action`,
  );
  await assertMaterialCaptureAbsent(page, contract.materialReaderSelector);
  questionPane = await requiredElement(page, contract.paneSelector);

  const draftButton = await requiredElement(page, ".question-tool--draft");
  await draftButton.tap();
  await waitForData(
    page,
    (data) =>
      data.annotationOpen === true &&
      data.annotationSurface === "question" &&
      data.annotationMode === "annotate" &&
      data.touchOwner === "annotation",
    `${page.path} did not give annotation the touch owner`,
  );

  assert.equal(
    await questionPane.attribute("data-touch-owner"),
    "annotation",
    `${page.path} did not project annotation touch ownership into WXML`,
  );
  const penTool = await requiredElementAt(
    page,
    ".annotation-layer__tool",
    1,
    `${page.path} annotation toolbar does not expose a pen tool`,
  );
  await penTool.tap();
  const capture = await waitForElement(
    page,
    ".annotation-layer__capture",
    `${page.path} did not physically mount its drawing capture layer`,
  );
  await capture.touchstart({ touches: [{ clientX: 180, clientY: 260 }] });
  await capture.touchend({ touches: [], changeTouches: [{ clientX: 180, clientY: 260 }] });

  const moreAction = await requiredElementAt(
    page,
    ".annotation-layer__secondary-action",
    2,
    `${page.path} annotation toolbar has no more action`,
  );
  await moreAction.tap();
  await waitForData(
    page,
    (data) =>
      data.annotationOpen === false &&
      data.annotationSurface === "" &&
      data.annotationMode === "static" &&
      data.toolsOpen === true &&
      data.touchOwner === "overlay",
    `${page.path} did not transfer touch ownership from annotation to tools`,
  );
  await assertDrawingCaptureAbsent(page, page.path);
  assert.equal(
    await questionPane.attribute("data-touch-owner"),
    "overlay",
    `${page.path} did not project overlay touch ownership into WXML`,
  );

  const closeButton = await requiredElement(page, ".ui-sheet__close");
  await closeButton.tap();
  await waitForData(
    page,
    (data) => data.toolsOpen === false && data.touchOwner === "question",
    `${page.path} did not release overlay touch ownership`,
  );
  await assertDrawingCaptureAbsent(page, page.path);
  assert.equal(
    await questionPane.attribute("data-touch-owner"),
    "question",
    `${page.path} did not return touch ownership to the question WXML`,
  );

  const firstOption = (await page.$$(".option-card"))[0];
  assert.ok(firstOption, `${page.path} has no literal option card`);
  await firstOption.tap();
  await waitForData(page, contract.selected, `${page.path} option remained blocked after release`);

  const beforeScroll = contract.current(await readPageData(miniProgram, page));
  const paneSize = await questionPane.size();
  const scrollHeight = Number(await questionPane.scrollHeight());
  const viewportHeight = Number(paneSize.height);
  assert.ok(
    scrollHeight > viewportHeight + 1,
    `${page.path} scroll fixture must overflow: ${String(scrollHeight)} <= ${String(viewportHeight)}`,
  );
  await questionPane.scrollTo(0, Math.min(160, scrollHeight - viewportHeight));
  await waitForData(
    page,
    (data) => data.questionScrollPosition > 0,
    `${page.path} question scroll-view remained blocked after release`,
  );
  assert.equal(
    contract.current(await readPageData(miniProgram, page)),
    beforeScroll,
    `${page.path} vertical scrolling changed question`,
  );

  await installSwipeEventAudit(miniProgram, page);
  await questionPane.touchstart({ touches: [{ clientX: 330, clientY: 260 }] });
  await page.waitFor(40);
  await questionPane.touchmove({ touches: [{ clientX: 210, clientY: 262 }] });
  await page.waitFor(40);
  await questionPane.touchend({ touches: [], changeTouches: [{ clientX: 70, clientY: 264 }] });
  await page.waitFor(300);
  const literalSwipeData = await readPageData(miniProgram, page);
  if (contract.current(literalSwipeData) !== Number(beforeScroll) + 1) {
    const swipeEvidence = await readSwipeEventAudit(miniProgram);
    assert.equal(
      hasUsableAutomatorSwipeEvidence(swipeEvidence),
      false,
      `${page.path} horizontal question swipe remained blocked with usable literal event evidence: ` +
        JSON.stringify(swipeEvidence),
    );
    simulatorLimitations.push(
      `${page.path}: DevTools automator emitted unusable touch timing; ` +
        "horizontal swipe was verified through the bound page handlers instead.",
    );
    await invokeTimedSwipeHandlers(miniProgram, page);
  }
  await waitForData(
    page,
    (data) => contract.current(data) === Number(beforeScroll) + 1,
    `${page.path} horizontal question swipe remained blocked after release`,
  );
  await assertDrawingCaptureAbsent(page, page.path);
}

async function installSwipeEventAudit(program, page) {
  await program.evaluate(function installClickAuditSwipeRecorder(expectedRoute) {
    const pages = getCurrentPages();
    const current = pages[pages.length - 1];
    if (!current || current.route !== expectedRoute) return;
    globalThis.__ananClickAuditSwipeEvents = [];
    for (const methodName of [
      "onQuestionTouchStart",
      "onQuestionTouchMove",
      "onQuestionTouchEnd",
    ]) {
      const original = current[methodName];
      if (typeof original !== "function" || original.__ananClickAuditWrapped) continue;
      const wrapped = function auditedSwipeHandler(event) {
        const touches = Array.from(event?.touches ?? event?.changedTouches ?? []).map((touch) => ({
          clientX: touch?.clientX,
          clientY: touch?.clientY,
          pageX: touch?.pageX,
          pageY: touch?.pageY,
        }));
        globalThis.__ananClickAuditSwipeEvents.push({
          methodName,
          timeStamp: event?.timeStamp,
          touches,
        });
        return original.call(this, event);
      };
      wrapped.__ananClickAuditWrapped = true;
      current[methodName] = wrapped;
    }
  }, page.path);
}

async function readSwipeEventAudit(program) {
  return program.evaluate(function readClickAuditSwipeEvents() {
    return Array.isArray(globalThis.__ananClickAuditSwipeEvents)
      ? [...globalThis.__ananClickAuditSwipeEvents]
      : [];
  });
}

function hasUsableAutomatorSwipeEvidence(events) {
  const start = events.find((event) => event.methodName === "onQuestionTouchStart");
  const end = [...events]
    .reverse()
    .find(
      (event) =>
        event.methodName === "onQuestionTouchEnd" || event.methodName === "onQuestionTouchMove",
    );
  return (
    Number.isFinite(start?.timeStamp) &&
    Number.isFinite(end?.timeStamp) &&
    end.timeStamp > start.timeStamp &&
    start.touches.length === 1 &&
    end.touches.length === 1
  );
}

async function invokeTimedSwipeHandlers(program, page) {
  await program.evaluate(function invokeClickAuditTimedSwipe(expectedRoute) {
    const pages = getCurrentPages();
    const current = pages[pages.length - 1];
    if (!current || current.route !== expectedRoute) return;
    current.onQuestionTouchStart({
      type: "touchstart",
      timeStamp: 1_000,
      touches: [{ clientX: 330, clientY: 260 }],
      changedTouches: [],
    });
    current.onQuestionTouchMove({
      type: "touchmove",
      timeStamp: 1_040,
      touches: [{ clientX: 210, clientY: 262 }],
      changedTouches: [],
    });
    current.onQuestionTouchEnd({
      type: "touchend",
      timeStamp: 1_080,
      touches: [],
      changedTouches: [{ clientX: 70, clientY: 264 }],
    });
  }, page.path);
}

async function requiredElement(root, selector) {
  const element = await findElement(root, selector);
  assert.ok(element, `Missing literal Mini Program element ${selector}`);
  return element;
}

async function waitForElement(root, selector, message) {
  let found = null;
  await waitForPredicate(async () => {
    found = await findElement(root, selector);
    return found !== null;
  }, message);
  assert.ok(found, message);
  return found;
}

async function findElement(root, selector) {
  const element = await root.$(selector);
  if (element || typeof root.getElementByXpath !== "function") return element;
  const classMatch = /^\.([a-zA-Z0-9_-]+)$/u.exec(selector);
  if (classMatch) {
    return root.getElementByXpath(`//*[contains(@class, '${classMatch[1]}')]`);
  }
  if (!/^[a-z][a-z0-9-]*$/u.test(selector)) return null;
  return root.getElementByXpath(`//${selector}`);
}

async function requiredElementAt(root, selector, index, message) {
  const elements = await root.$$(selector);
  if (elements[index]) return elements[index];
  assert.equal(typeof root.getElementByXpath, "function", message);
  const classMatch = /^\.([a-zA-Z0-9_-]+)$/u.exec(selector);
  assert.ok(classMatch, message);
  const element = await root.getElementByXpath(
    `(//*[contains(@class, '${classMatch[1]}')])[${String(index + 1)}]`,
  );
  assert.ok(element, message);
  return element;
}

async function assertDrawingCaptureAbsent(root, routePath) {
  const annotation = await findElement(root, "annotation-layer");
  if (!annotation) return;
  assert.equal(
    await annotation.$(".annotation-layer__capture"),
    null,
    `${routePath} retained a physical drawing capture layer outside draw state`,
  );
}

async function assertMaterialCaptureAbsent(page, materialReaderSelector) {
  const materialReader = await page.$(materialReaderSelector);
  if (!materialReader) return;
  assert.equal(
    await materialReader.$(".material-reader__surface"),
    null,
    `${page.path} retained a physical material capture surface outside material ownership`,
  );
}

async function submittedMockReport(program) {
  const page = await openVisualScenario(program, "c06-mock-exam-answering");
  await setPageData(program, page, {
    status: "submitted",
    receipt: null,
    reportSessionId: "visual-mock-session-v1",
  });
  await invokePageHandler(program, page, "openMockReport");
  await waitForPath(
    program,
    "/reports/mock-report/index",
    "submitted mock did not open its report",
  );
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
  await leaveLoginControllerBeforeAuthFixture(program);
  await setAuthentication(program, scenario.authenticated === true);
  const currentPage = await currentPageWithRetry(program);
  const routePath = scenario.route.split("?", 1)[0];
  const page =
    normalizeRoutePath(currentPage?.path) === normalizeRoutePath(routePath)
      ? currentPage
      : await reLaunchScenarioRoute(program, scenario.route);
  assert.ok(page, `Unable to open ${scenario.route}`);
  await waitForControllerSettlement(program, page, scenario);
  const fixtureData = structuredClone(scenario.data);
  await setPageData(program, page, fixtureData);
  await page.waitFor(800);
  if (scenario.scrollTop) {
    await program.pageScrollTo(scenario.scrollTop);
    await page.waitFor(200);
  }
  await setPageData(program, page, fixtureData);
  await page.waitFor(120);
  await assertActiveScenarioRoute(program, scenario);
  const appliedData = await readPageData(program, page);
  for (const [key, expected] of Object.entries(scenario.data)) {
    assert.deepEqual(appliedData[key], expected, `${scenario.id} fixture was overwritten: ${key}`);
  }
  await assertVisibleScenarioIsNotError(program, page, scenario, appliedData);
  return page;
}

async function leaveLoginControllerBeforeAuthFixture(program) {
  const activeRoute = await program.evaluate(function readRouteBeforeClickAuditAuthFixture() {
    const pages = getCurrentPages();
    return pages[pages.length - 1]?.route ?? "";
  });
  if (activeRoute !== "pages/login/index") return;
  const neutralPage = await reLaunchWithRetry(program, "/account/support/index");
  assert.ok(neutralPage, "Unable to unload the login controller before click-audit fixture");
}

async function waitForControllerSettlement(program, page, scenario) {
  if (scenario.authenticated !== true || scenario.skipControllerSettlement === true) return;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const data = await readPageData(program, page);
    if (controllerHasSettled(scenario.route, data)) return;
    await page.waitFor(100);
  }
  assert.fail(`${scenario.id} did not settle its page controller before fixture injection`);
}

function controllerHasSettled(route, data) {
  const path = route.split("?", 1)[0];
  if (path === "/pages/anan/index") return true;
  if (path.startsWith("/pages/community-")) return remoteStatusHasSettled(data.pageState);
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
  return true;
}

function remoteStatusHasSettled(status) {
  return typeof status === "string" && !["idle", "loading", "previewing"].includes(status);
}

async function assertActiveScenarioRoute(program, scenario) {
  const expectedRoute = scenario.route.split("?", 1)[0].replace(/^\//u, "");
  const actualRoute = await program.evaluate(function readActiveClickAuditRoute() {
    const pages = getCurrentPages();
    return pages[pages.length - 1]?.route ?? "";
  });
  assert.equal(
    actualRoute,
    expectedRoute,
    `${scenario.id} navigated away from ${expectedRoute} to ${String(actualRoute)}`,
  );
}

async function assertVisibleScenarioIsNotError(program, page, scenario, data) {
  assertPageDataIsNotError(scenario.id, data);
  assert.equal(
    containsAuditNetworkGuardMarker(data),
    false,
    `${scenario.id} retained click-audit-network-guard in final page data`,
  );
  const payload = await program.evaluate(function readVisibleStateViews(expectedRoute) {
    const pages = getCurrentPages();
    const current = pages[pages.length - 1];
    if (!current || current.route !== expectedRoute) {
      return JSON.stringify({ error: "route_mismatch", actualRoute: current?.route ?? null });
    }
    const components =
      typeof current.selectAllComponents === "function"
        ? current.selectAllComponents("state-view")
        : [];
    return JSON.stringify({
      states: components.map((component) => ({
        state: component?.data?.state ?? "",
        title: component?.data?.title ?? "",
        description: component?.data?.description ?? "",
        requestId: component?.data?.requestId ?? "",
      })),
    });
  }, page.path);
  assert.equal(typeof payload, "string", `${scenario.id} returned invalid visible-state evidence`);
  const parsed = JSON.parse(payload);
  assert.equal(
    parsed.error,
    undefined,
    `${scenario.id} visible-state query found ${parsed.actualRoute ?? "no page"}`,
  );
  const visibleErrors = parsed.states.filter(
    (state) => state.state === "error" || containsAuditNetworkGuardMarker(state),
  );
  assert.deepEqual(
    visibleErrors,
    [],
    `${scenario.id} still renders an error state:\n${JSON.stringify(visibleErrors, null, 2)}`,
  );
}

function assertPageDataIsNotError(label, data) {
  const explicitStates = [
    data?.status,
    data?.pageState,
    typeof data?.state === "object" && data.state !== null ? data.state.status : undefined,
  ].filter((state) => typeof state === "string");
  assert.equal(
    explicitStates.includes("error"),
    false,
    `${label} ended in an error page state: ${JSON.stringify({
      states: explicitStates,
      errorMessage: data?.errorMessage ?? data?.message ?? "",
      requestId: data?.requestId ?? "",
    })}`,
  );
  assert.equal(
    containsAuditNetworkGuardMarker(data),
    false,
    `${label} retained click-audit-network-guard in final page data`,
  );
}

function containsAuditNetworkGuardMarker(value, seen = new Set()) {
  if (typeof value === "string") return value.includes("click-audit-network-guard");
  if (value === null || typeof value !== "object" || seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) {
    return value.some((item) => containsAuditNetworkGuardMarker(item, seen));
  }
  return Object.values(value).some((item) => containsAuditNetworkGuardMarker(item, seen));
}

async function reLaunchScenarioRoute(program, route) {
  const expectedPath = normalizeRoutePath(route.split("?", 1)[0]);
  let actualPath = "";
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const launchedPage = await reLaunchWithRetry(program, route);
    await launchedPage?.waitFor(500);
    const currentPage = await currentPageWithRetry(program);
    actualPath = normalizeRoutePath(currentPage?.path);
    if (actualPath !== expectedPath) continue;
    await currentPage.waitFor(300);
    const settledPage = await currentPageWithRetry(program);
    actualPath = normalizeRoutePath(settledPage?.path);
    if (actualPath === expectedPath) return settledPage;
  }
  assert.fail(`Unable to settle ${route}; current page is ${actualPath || "unavailable"}`);
}

async function waitForPath(program, expectedPath, message) {
  await waitForPredicate(async () => {
    const page = await currentPageWithRetry(program);
    return normalizeRoutePath(page?.path) === normalizeRoutePath(expectedPath);
  }, message);
  const settledPage = await currentPageWithRetry(program);
  await settledPage?.waitFor(1_000);
}

async function currentPageWithRetry(program) {
  return retryTransientAutomation(() => program.currentPage());
}

async function reLaunchWithRetry(program, route) {
  return retryTransientAutomation(() => program.reLaunch(route));
}

async function retryTransientAutomation(operation) {
  let lastError;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      const message = String(error?.message ?? error);
      const transientAutomationError =
        message.includes("getPageMetaByWebviewId") ||
        (message.includes("rawPath") && message.includes("null")) ||
        message.includes("timeout waiting for automator response");
      if (!transientAutomationError || attempt === 4) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw lastError;
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
  const blockedResponse = {
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
  await program.evaluate(function installClickAuditNetworkGuard(response) {
    globalThis.__ananClickAuditBlockedRequests = [];
    wx.request = function blockedClickAuditRequest(options = {}) {
      globalThis.__ananClickAuditBlockedRequests.push({
        method: String(options.method ?? "GET").toUpperCase(),
        url: String(options.url ?? ""),
      });
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
  }, blockedResponse);
  await program.mockWxMethod("showToast", { errMsg: "showToast:ok" });
  await program.mockWxMethod("showModal", { confirm: true, cancel: false, errMsg: "showModal:ok" });
}

async function readNetworkGuardAudit(program) {
  return program.evaluate(function readClickAuditBlockedRequests() {
    return Array.isArray(globalThis.__ananClickAuditBlockedRequests)
      ? [...globalThis.__ananClickAuditBlockedRequests]
      : [];
  });
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

async function readLoginAuditEvidence(program) {
  return program.evaluate(function readAuditLoginEvidence() {
    return {
      loginCalled: globalThis.__ananClickAuditLoginCalled === true,
      requestUrls: Array.isArray(globalThis.__ananClickAuditLoginRequests)
        ? [...globalThis.__ananClickAuditLoginRequests]
        : [],
    };
  });
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
        let response;
        if (url.includes("/api/v2/task-editing/leases")) {
          response = lease;
        } else if (url.includes("/api/v2/xingce/task-sessions")) {
          if (url.endsWith("/checkpoint")) {
            const command = options.data ?? {};
            const savedSession = session.data.data;
            savedSession.revision = Number(command.expectedRevision ?? savedSession.revision) + 1;
            savedSession.currentQuestionId = String(
              command.currentQuestionId ?? savedSession.currentQuestionId,
            );
            savedSession.answers = Array.isArray(command.answers) ? command.answers : [];
          }
          response = session;
        } else {
          response = {
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
    },
    sessionResponse,
    leaseResponse,
  );
}

async function installMockExamMocks(program) {
  const sessionResponse = {
    data: envelope(createMockExamSession()),
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  };
  const leaseResponse = {
    data: envelope(createMockExamLease()),
    statusCode: 200,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  };
  await program.evaluate(
    function installAuditMockExamMocks(session, lease) {
      globalThis.__ananClickAuditMockExamRequests = [];
      wx.request = function auditMockExamRequest(options) {
        const url = String(options.url ?? "");
        globalThis.__ananClickAuditMockExamRequests.push(url);
        let response;
        if (url.includes("/api/v2/task-editing/leases")) {
          response = lease;
        } else if (url.endsWith("/trajectory-events")) {
          response = {
            data: {
              data: null,
              requestId: "click-audit-mock-trajectory",
              generatedAt: "2026-08-09T00:00:00.000Z",
            },
            statusCode: 200,
            header: {},
            cookies: [],
            errMsg: "request:ok",
          };
        } else if (url.includes("/api/v1/mock-exams/audit-mock-session")) {
          const savedSession = session.data.data;
          if (url.endsWith("/checkpoint")) {
            const command = options.data ?? {};
            savedSession.revision = Number(command.expectedRevision ?? savedSession.revision) + 1;
            savedSession.checkpoint = {
              currentQuestionId: String(
                command.currentQuestionId ?? savedSession.checkpoint.currentQuestionId,
              ),
              answers: Array.isArray(command.answers) ? command.answers : [],
            };
            response = {
              ...session,
              data: {
                ...session.data,
                data: {
                  sessionId: savedSession.sessionId,
                  status: "assigned",
                  revision: savedSession.revision,
                  checkpoint: savedSession.checkpoint,
                  checkpointUpdatedAt: "2026-08-09T00:00:00.000Z",
                },
              },
            };
          } else {
            response = session;
          }
        } else {
          response = {
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
    },
    sessionResponse,
    leaseResponse,
  );
}

async function setAuthentication(program, authenticated) {
  await program.evaluate(function applyClickAuditSession(hasSession) {
    const storageKey = "gongkao.auth.session.v1";
    const app = typeof getApp === "function" ? getApp() : undefined;
    if (hasSession) {
      const session = {
        accessToken: "click-audit-access-token",
        userId: "click-audit-user",
        generation: "click-audit-generation",
      };
      wx.setStorageSync(storageKey, session);
      if (app?.globalData) {
        app.globalData.accessToken = session.accessToken;
        app.globalData.userId = session.userId;
        app.globalData.generation = session.generation;
      }
      return;
    }
    wx.removeStorageSync(storageKey);
    if (app?.globalData) {
      delete app.globalData.accessToken;
      delete app.globalData.userId;
      delete app.globalData.displayName;
      delete app.globalData.generation;
    }
  }, authenticated);
}

async function installNavigationAudit(program) {
  await program.evaluate(function installClickAuditNavigationRecorder() {
    if (globalThis.__ananClickAuditNavigationInstalled) return;
    globalThis.__ananClickAuditNavigationInstalled = true;
    globalThis.__ananClickAuditNavigationCalls = [];
    for (const method of ["navigateTo", "redirectTo", "reLaunch", "switchTab"]) {
      const original = wx[method];
      if (typeof original !== "function") continue;
      wx[method] = function auditedNavigation(options = {}) {
        globalThis.__ananClickAuditNavigationCalls.push({
          method,
          url: String(options.url ?? ""),
        });
        return original.call(wx, options);
      };
    }
  });
}

async function readNavigationAudit(program) {
  return program.evaluate(function readClickAuditNavigationCalls() {
    return Array.isArray(globalThis.__ananClickAuditNavigationCalls)
      ? [...globalThis.__ananClickAuditNavigationCalls]
      : [];
  });
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

function createMockExamSession() {
  const material = {
    materialId: "audit-mock-material",
    title: "增长资料",
    text: "2025 年为 120，2024 年为 100。",
    html: null,
    assets: [],
    content: [
      {
        type: "paragraph",
        blockId: "audit-mock-material-paragraph",
        text: "2025 年为 120，2024 年为 100。",
        html: null,
      },
    ],
    anchors: [
      {
        anchorId: "audit-mock-material-start",
        blockId: "audit-mock-material-paragraph",
        label: "材料开头",
      },
    ],
  };
  const questions = ["audit-mock-q-1", "audit-mock-q-2"].map((questionId, index) => ({
    questionId,
    contentVersionId: String(index + 1).repeat(64),
    moduleName: "资料分析",
    materialId: material.materialId,
    stem: "某指标同比增长率为多少？",
    stemHtml: null,
    stemContent: [
      {
        type: "paragraph",
        blockId: `audit-mock-stem-${String(index + 1)}`,
        text: "某指标同比增长率为多少？请结合材料完成计算并选择最合理的答案。",
        html: null,
      },
    ],
    options: ["A", "B", "C", "D"].map((label, optionIndex) => ({
      optionId: label,
      label,
      text: `${String((optionIndex + 1) * 10)}%`,
      html: null,
      content: [
        {
          type: "paragraph",
          blockId: `audit-mock-${String(index + 1)}-${label}`,
          text: `${String((optionIndex + 1) * 10)}% · 根据材料数据计算得出的候选项。`,
          html: null,
        },
      ],
    })),
  }));
  return {
    sessionId: "audit-mock-session",
    planTaskId: null,
    status: "assigned",
    title: "国考行测整卷模考",
    durationMinutes: 120,
    questionCount: questions.length,
    blueprint: [{ moduleName: "资料分析", questionCount: questions.length }],
    bundleVersionId: "b".repeat(64),
    paperVersionId: "00000000-0000-4000-8000-000000000602",
    scoringPolicyVersionId: "mock-balanced-performance-index-v1",
    scoreMetricName: "balanced_performance_index",
    scoreMetricUnit: "index_points",
    scoreMetricDefinition: "五个行测模块各占 20 点，不是官方原始分。",
    nextTrajectoryEventSeq: 1,
    materials: [material],
    questions,
    revision: 3,
    startedAt: "2026-08-09T00:00:00.000Z",
    deadlineAt: "2099-08-09T02:00:00.000Z",
    checkpoint: {
      currentQuestionId: questions[0].questionId,
      answers: questions.map((question) => ({
        questionId: question.questionId,
        selectedAnswer: null,
        eliminatedOptionIds: [],
        marked: false,
        draftText: "",
      })),
    },
  };
}

function createMockExamLease() {
  return {
    state: "owned",
    resourceType: "mock_exam_session",
    resourceId: "audit-mock-session",
    leaseId: "audit-mock-lease",
    generation: 1,
    deviceId: "audit-device-id-000000000000",
    deviceLabel: "审计设备",
    serverRevision: 3,
    localBaseRevision: 3,
    acquiredAt: "2026-08-09T00:00:00.000Z",
    lastSeenAt: "2026-08-09T00:00:00.000Z",
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
