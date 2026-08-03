import assert from "node:assert/strict";
import { access, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import automator from "miniprogram-automator";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

import { readPageData, setComponentData, setPageData } from "./page-data-bridge.mjs";
import { consoleEventText, isActionableRuntimeWarning } from "./runtime-warning-policy.mjs";
import { visualDeliveryContracts } from "./visual-delivery-contracts.mjs";
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
const deliveryContractByScenarioId = new Map(
  visualDeliveryContracts.map((contract) => [contract.scenarioId, contract]),
);
const systemChromeContracts = new Map([
  ["/pages/ability/index", pageChrome(".ability-content")],
  ["/pages/anan/index", pageChrome(".coach-content")],
  ["/pages/capture-import/index", workbenchChrome()],
  ["/pages/daily-settlement/index", workbenchChrome()],
  ["/pages/day-adjustment/index", workbenchChrome()],
  ["/pages/essay-material/index", workbenchChrome()],
  ["/pages/essay-review/index", workbenchChrome()],
  ["/pages/me/index", pageChrome(".profile-content")],
  [
    "/pages/onboarding/ability-baseline/index",
    componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title"),
  ],
  ["/pages/onboarding/entry/index", workbenchChrome()],
  [
    "/pages/onboarding/index",
    componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title"),
  ],
  [
    "/pages/onboarding/position-preference/index",
    componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title"),
  ],
  [
    "/pages/onboarding/preparation-experience/index",
    componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title"),
  ],
  ["/pages/onboarding/result/index", workbenchChrome()],
  [
    "/pages/onboarding/study-preference/index",
    componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title"),
  ],
  ["/pages/plan-basis/index", workbenchChrome()],
  ["/pages/positions/compare/index", workbenchChrome()],
  ["/pages/positions/detail/index", workbenchChrome()],
  ["/pages/positions/goal/index", workbenchChrome()],
  ["/pages/positions/index", workbenchChrome()],
  ["/pages/positions/search/index", workbenchChrome()],
  ["/pages/private-knowledge/detail/index", workbenchChrome()],
  ["/pages/private-knowledge/index", workbenchChrome()],
  ["/pages/profile/archive/index", workbenchChrome()],
  ["/pages/onboarding/profile/index", workbenchChrome()],
  ["/pages/reflection/index", workbenchChrome()],
  ["/pages/today/index", pageChrome(".today-content")],
  ["/pages/weekly-plan/index", workbenchChrome()],
  [
    "/pages/practice/index",
    componentChrome(
      "#runtime-task-shell",
      ".task-header-button--back",
      ".task-header-title",
      ".task-header-actions",
    ),
  ],
  [
    "/pages/mock-exam/index",
    componentChrome(
      "#runtime-task-shell",
      ".task-header-button--back",
      ".task-header-title",
      ".task-header-actions",
    ),
  ],
  [
    "/pages/essay-task/index",
    componentChrome(
      "#runtime-task-shell",
      ".task-header-button--back",
      ".task-header-title",
      ".task-header-actions",
    ),
  ],
  [
    "/pages/answer-ingestion/index",
    componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title"),
  ],
  [
    "/pages/learning-packets/index",
    componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title"),
  ],
]);

assert.equal(
  selectedScenarios.length,
  requestedScenarioIds.size || visualScenarios.length,
  `Unknown visual scenario filter: ${scenarioFilter}`,
);

await access(cliPath, constants.X_OK);
await mkdir(baselineDirectory, { recursive: true });
await rm(artifactDirectory, { recursive: true, force: true });
await mkdir(artifactDirectory, { recursive: true });

// 启动期死线：模拟器里没有可用 app 时（如 DevTools 编译缓存脏了导致
// app.json 报错），connect/systemInfo 会无限等待——场景循环前由这里兜底。
const startupDeadline = setTimeout(() => {
  console.error("[harness] startup did not complete within 180s, aborting");
  process.exit(1);
}, 180_000);
startupDeadline.unref();

const automationEndpoint = process.env.WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT?.trim();
const miniProgram = automationEndpoint
  ? await automator.connect({ wsEndpoint: automationEndpoint })
  : await automator.launch({
      cliPath,
      projectPath: projectRoot,
      timeout: 60_000,
      trustProject: true,
    });
const runtimeSystemInfo = await miniProgram.systemInfo();
const runtimeChromeMetrics = await readSystemChromeMetrics(miniProgram);
assertSystemChromeMetrics(runtimeSystemInfo, runtimeChromeMetrics);
const runtimeFailures = [];
const runtimeWarnings = [];
const captureRecords = [];
miniProgram.on("exception", (event) => runtimeFailures.push(String(event?.message ?? event)));
// 协议层若下发 "error" 事件，EventEmitter 无监听会直接抛出并击杀进程
// （表现为 Uncaught [object Object]）。这里吸收并记录，让失败落到具体场景上。
miniProgram.on("error", (event) => {
  runtimeFailures.push(`automation error event: ${JSON.stringify(event ?? null)}`);
});
process.on("uncaughtException", (error) => {
  // 连接层的漏网异常：不炸整轮，但必须计为失败——绝不允许假绿。
  runtimeFailures.push(`uncaught: ${String(error)}`);
  process.exitCode = 1;
  console.error(`[harness] uncaught exception (recorded as failure): ${String(error)}`);
});
process.on("unhandledRejection", (reason) => {
  // 顶层 await 链断裂（如启动期断言失败）后，进程只剩一条空转的自动化连接，
  // 会以僵尸态挂住整条采集链——记账后立即散场，不做任何等待。
  runtimeFailures.push(`unhandled rejection: ${String(reason)}`);
  console.error(`[harness] fatal rejection, aborting run: ${String(reason)}`);
  process.exitCode = 1;
  setTimeout(() => process.exit(1), 3_000).unref();
  void miniProgram.close().finally(() => process.exit(1));
});
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

// 死水看门狗：单场景最长 90s+90s 重试，300s 无任何进展只可能是挂机
//（协议静默、连接半死等），此时任何等待都是浪费，直接判死并散场。
clearTimeout(startupDeadline);
let lastProgressAt = Date.now();
const zombieWatchdog = setInterval(() => {
  if (Date.now() - lastProgressAt <= 300_000) return;
  console.error("[harness] no scenario progress for 300s, aborting zombie run");
  process.exitCode = 1;
  setTimeout(() => process.exit(1), 3_000).unref();
  void miniProgram.close().finally(() => process.exit(1));
}, 30_000);
zombieWatchdog.unref();

try {
  for (const scenario of selectedScenarios) {
    process.stdout.write(`[audit] ${scenario.id} ... `);
    // 单场景允许重试一次：偶发的连接抖动不再让整轮陪葬。
    // 例外：超时不重试——withTimeout 无法取消底层任务，超时后原任务仍在跑，
    // 立刻重试会与残留任务并发打架（reLaunch 互踩），只会把失败搞得更糊。
    try {
      await withTimeout(
        captureScenario(miniProgram, scenario),
        90_000,
        `${scenario.id} did not settle within 90 seconds`,
      );
    } catch (firstError) {
      const message = String(firstError?.message ?? firstError);
      if (message.includes("did not settle")) throw firstError;
      process.stdout.write(`retry (${message}) ... `);
      await withTimeout(
        captureScenario(miniProgram, scenario),
        90_000,
        `${scenario.id} did not settle within 90 seconds on retry`,
      );
    }
    process.stdout.write("ok\n");
    lastProgressAt = Date.now();
  }
  if (captureOnly) {
    await writeFile(
      path.resolve(artifactDirectory, "audit-manifest.json"),
      `${JSON.stringify(
        {
          systemInfo: runtimeSystemInfo,
          systemChrome: runtimeChromeMetrics,
          scenarios: captureRecords,
        },
        null,
        2,
      )}\n`,
    );
  }
} finally {
  clearInterval(zombieWatchdog);
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
  await leaveLoginControllerBeforeAuthFixture(program);
  await setAuthentication(program, scenario.authenticated === true);
  const page = await program.reLaunch(scenario.route);
  assert.ok(page, `Unable to open ${scenario.route}`);
  await waitForControllerSettlement(page, scenario);
  const fixtureData = structuredClone(scenario.data);
  await setPageData(program, page, fixtureData);
  await page.waitFor(800);
  await applyComponentFixtures(program, page, scenario);
  if (scenario.scrollTop) {
    await program.pageScrollTo(scenario.scrollTop);
    await page.waitFor(200);
  }
  // Page timers may keep rendering after the controller has settled. The scenario owns
  // deterministic screenshot data, so restore it once after layout/scroll stabilization.
  await setPageData(program, page, fixtureData);
  await applyComponentFixtures(program, page, scenario);
  // automator resolves setData after the page data commit, while WXML/component layout can
  // still be one render behind. Wait for that final deterministic frame before asserting and
  // capturing, otherwise a completed network-error frame can be photographed with fixture data.
  await page.waitFor(120);
  await assertActiveScenarioRoute(program, scenario);
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
  const deliveryEvidence = await assertDeliveryContract(miniProgram, page, scenario);
  const systemChromeEvidence = await assertSystemChromeClearance(miniProgram, page, scenario);

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
      delivery: deliveryEvidence,
      systemChrome: systemChromeEvidence,
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

async function applyComponentFixtures(program, page, scenario) {
  for (const fixture of scenario.componentFixtures ?? []) {
    await setComponentData(program, page, fixture.selector, structuredClone(fixture.data));
  }
}

async function leaveLoginControllerBeforeAuthFixture(program) {
  const activeRoute = await program.evaluate(function readRouteBeforeVisualAuthFixture() {
    const pages = getCurrentPages();
    return pages[pages.length - 1]?.route ?? "";
  });
  if (activeRoute !== "pages/login/index") return;

  // The login controller owns an asynchronous session restore. Mutating storage while that
  // controller is still alive creates a race where its late completion can clear the visual
  // fixture or navigate away from the scenario. Unload it on a static, side-effect-free page
  // before applying deterministic authentication.
  const neutralPage = await program.reLaunch("/pages/support/index");
  assert.ok(neutralPage, "Unable to unload the login controller before visual authentication");
}

async function assertActiveScenarioRoute(program, scenario) {
  const expectedRoute = scenario.route.split("?", 1)[0].replace(/^\//u, "");
  const actualRoute = await program.evaluate(function readActiveVisualRoute() {
    const pages = getCurrentPages();
    return pages[pages.length - 1]?.route ?? "";
  });
  assert.equal(
    actualRoute,
    expectedRoute,
    `${scenario.id} navigated away from ${expectedRoute} to ${String(actualRoute)}`,
  );
}

function pageChrome(...selectors) {
  return { scope: "page", selectors };
}

function componentChrome(componentSelector, ...selectors) {
  return { scope: "component", componentSelector, selectors };
}

function workbenchChrome() {
  return componentChrome("#runtime-workbench-nav", ".workbench-nav__back", ".workbench-nav__title");
}

async function assertSystemChromeClearance(program, page, scenario) {
  const route = `/${page.path}`;
  const contract = systemChromeContracts.get(route);
  if (!contract) return null;
  const nodes = await readChromeGeometry(program, page, contract);
  assert.ok(nodes.length > 0, `${scenario.id} did not expose its system-chrome layout contract`);

  const menu = runtimeChromeMetrics.menuButton;
  const protectedMenu = {
    left: menu.left - 8,
    right: menu.right,
    top: menu.top - 4,
    bottom: menu.bottom,
  };
  for (const node of nodes) {
    assertHorizontalGeometry(
      scenario.id,
      node.selector,
      node.geometry,
      Number(runtimeSystemInfo.windowWidth),
    );
    assert.ok(
      !rectanglesOverlap(node.geometry, protectedMenu),
      `${scenario.id} ${node.selector} overlaps the protected WeChat capsule area: ` +
        `${JSON.stringify({ node: node.geometry, capsule: protectedMenu })}`,
    );
  }
  return {
    contract,
    protectedMenu,
    nodes,
  };
}

async function readChromeGeometry(program, page, contract) {
  const payload = await program.evaluate(
    function querySystemChromeNodes(expectedRoute, scope, componentSelector, targetSelectors) {
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
        const context =
          scope === "component" ? current.selectComponent(componentSelector) : current;
        if (!context) {
          resolve(JSON.stringify({ error: "component_missing", componentSelector }));
          return;
        }
        const query = wx.createSelectorQuery().in(context);
        const results = [];
        for (const selector of targetSelectors) {
          query.selectAll(selector).fields(
            {
              rect: true,
              size: true,
              computedStyle: ["display", "visibility", "opacity"],
            },
            (items) => results.push({ selector, items: items ?? [] }),
          );
        }
        query.exec(() => resolve(JSON.stringify({ results })));
      });
    },
    page.path,
    contract.scope,
    contract.componentSelector ?? "",
    contract.selectors,
  );
  assert.equal(typeof payload, "string", `${page.path} returned invalid chrome geometry`);
  const parsed = JSON.parse(payload);
  assert.equal(
    parsed.error,
    undefined,
    `${page.path} system chrome query failed: ${parsed.error ?? "unknown"}`,
  );
  return parsed.results.flatMap(({ selector, items }) =>
    items
      .filter(
        (item) =>
          item.display !== "none" &&
          item.visibility !== "hidden" &&
          item.opacity !== "0" &&
          numericCssValue(item.width) > 0 &&
          numericCssValue(item.height) > 0,
      )
      .map((item) => ({
        selector,
        geometry: {
          left: numericCssValue(item.left),
          top: numericCssValue(item.top),
          width: numericCssValue(item.width),
          height: numericCssValue(item.height),
        },
      })),
  );
}

function rectanglesOverlap(node, protectedArea) {
  const right = node.left + node.width;
  const bottom = node.top + node.height;
  return (
    node.left < protectedArea.right &&
    right > protectedArea.left &&
    node.top < protectedArea.bottom &&
    bottom > protectedArea.top
  );
}

async function assertDeliveryContract(program, page, scenario) {
  const contract = deliveryContractByScenarioId.get(scenario.id);
  if (!contract) return null;
  const semanticGeometry = await readSelectorGeometry(
    program,
    page,
    contract.semanticSelector,
    contract.componentSelector,
  );
  assert.ok(
    semanticGeometry,
    `${scenario.id} did not render Figma ${contract.figmaNodeId} state ${contract.semanticSelector}`,
  );
  assertHorizontalGeometry(
    scenario.id,
    contract.semanticSelector,
    semanticGeometry,
    Number(runtimeSystemInfo.windowWidth),
  );

  let primaryGeometry = null;
  if (contract.primarySelector) {
    const primary = await readSelectorGeometry(
      program,
      page,
      contract.primarySelector,
      contract.componentSelector,
    );
    assert.ok(
      primary,
      `${scenario.id} is missing primary action ${contract.primarySelector} for Figma ${contract.figmaNodeId}`,
    );
    primaryGeometry = primary;
    assertHorizontalGeometry(
      scenario.id,
      contract.primarySelector,
      primaryGeometry,
      Number(runtimeSystemInfo.windowWidth),
    );
    assert.ok(
      primaryGeometry.height >= 44,
      `${scenario.id} primary action ${contract.primarySelector} is ${String(primaryGeometry.height)}px high; minimum touch target is 44px`,
    );
  }

  return {
    figmaNodeId: contract.figmaNodeId,
    routeState: contract.routeState ?? null,
    semanticSelector: contract.semanticSelector,
    semanticGeometry,
    primarySelector: contract.primarySelector ?? null,
    primaryGeometry,
    requiredText: contract.requiredText,
  };
}

/**
 * Developer Tools 2.01.2510290 can leave the legacy automator Element channel
 * waiting forever even while App.callFunction remains healthy. Query layout
 * through the page's supported SelectorQuery runtime API, matching the data
 * bridge used elsewhere in this audit.
 */
async function readSelectorGeometry(program, page, selector, componentSelector) {
  const payload = await program.evaluate(
    function queryCurrentPageGeometry(expectedRoute, targetSelector, targetComponentSelector) {
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
        const context = targetComponentSelector
          ? current.selectComponent(targetComponentSelector)
          : current;
        if (!context) {
          resolve(JSON.stringify({ error: "component_missing", targetComponentSelector }));
          return;
        }
        context
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
    componentSelector ?? "",
  );
  assert.equal(typeof payload, "string", `${page.path} ${selector} returned invalid geometry`);
  const parsed = JSON.parse(payload);
  assert.equal(
    parsed.error,
    undefined,
    parsed.error === "component_missing"
      ? `${page.path} selector query did not find ${parsed.targetComponentSelector}`
      : `${page.path} selector query expected ${page.path} but found ${parsed.actualRoute ?? "no page"}`,
  );
  if (!parsed.result) return null;
  assert.notEqual(parsed.result.display, "none", `${page.path} ${selector} is display:none`);
  assert.notEqual(parsed.result.visibility, "hidden", `${page.path} ${selector} is hidden`);
  assert.notEqual(parsed.result.opacity, "0", `${page.path} ${selector} is transparent`);
  return {
    left: numericCssValue(parsed.result.left),
    top: numericCssValue(parsed.result.top),
    width: numericCssValue(parsed.result.width),
    height: numericCssValue(parsed.result.height),
  };
}

function assertHorizontalGeometry(scenarioId, selector, geometry, viewportWidth) {
  assert.ok(
    geometry.width > 0 && geometry.height > 0,
    `${scenarioId} ${selector} has empty geometry ${JSON.stringify(geometry)}`,
  );
  assert.ok(
    geometry.left >= -1,
    `${scenarioId} ${selector} starts outside the viewport: ${JSON.stringify(geometry)}`,
  );
  assert.ok(
    geometry.left + geometry.width <= viewportWidth + 1,
    `${scenarioId} ${selector} overflows the ${String(viewportWidth)}px viewport: ${JSON.stringify(geometry)}`,
  );
}

function numericCssValue(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

async function readSystemChromeMetrics(program) {
  return program.evaluate(function readVisualSystemChrome() {
    const system = wx.getSystemInfoSync();
    const menu = wx.getMenuButtonBoundingClientRect();
    return {
      statusBarHeight: system.statusBarHeight ?? 0,
      windowWidth: system.windowWidth,
      windowHeight: system.windowHeight,
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

function assertSystemChromeMetrics(systemInfo, chrome) {
  assert.equal(chrome.windowWidth, systemInfo.windowWidth, "System chrome width is inconsistent");
  assert.equal(
    chrome.windowHeight,
    systemInfo.windowHeight,
    "System chrome height is inconsistent",
  );
  assert.ok(chrome.statusBarHeight > 0, "Status bar height was not measured");
  assert.ok(chrome.menuButton.width > 0 && chrome.menuButton.height > 0, "Menu capsule is empty");
  assert.ok(chrome.menuButton.left >= 0, "Menu capsule starts outside the viewport");
  assert.ok(
    chrome.menuButton.right <= chrome.windowWidth,
    "Menu capsule ends outside the viewport",
  );
  assert.ok(
    chrome.menuButton.top >= chrome.statusBarHeight,
    "Menu capsule overlaps the status bar",
  );
  assert.ok(
    chrome.menuButton.bottom <= chrome.windowHeight,
    "Menu capsule ends below the viewport",
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

async function freezeVisualAnimationClock(program) {
  await program.evaluate(function installDeterministicVisualClock() {
    if (globalThis.__gongkaoVisualOriginalSetInterval) return;
    const originalSetInterval = globalThis.setInterval;
    globalThis.__gongkaoVisualOriginalSetInterval = originalSetInterval;
    globalThis.setInterval = function visualSetInterval(callback, delay, ...args) {
      // 岸岸 core animation is contractually fixed at 8 fps (125 ms), while task clocks
      // repaint every second. Visual scenarios own both displayed states, so keep their
      // injected frame stable without touching request promises or one-shot controller work.
      if (delay === 125 || delay === 1_000) {
        return originalSetInterval(function holdVisualFrame() {}, delay);
      }
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
