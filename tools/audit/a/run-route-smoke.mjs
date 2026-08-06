import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readPageData, setPageData } from "../../../apps/wechat/e2e/page-data-bridge.mjs";
import { consoleEventText } from "../../../apps/wechat/e2e/runtime-warning-policy.mjs";
import { visualScenarios } from "../../../apps/wechat/e2e/visual-scenarios.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const sourceProjectRoot = path.resolve(repositoryRoot, "apps/wechat");
const runtimeProjectRoot = path.resolve(
  repositoryRoot,
  "audit/subagents/a/runtime-project",
);
const outputRoot = path.resolve(repositoryRoot, "audit/subagents/a");
const screenshotsValidRoot = path.resolve(outputRoot, "screenshots/valid");
const screenshotsMissingRoot = path.resolve(outputRoot, "screenshots/missing-params");
const logsRoot = path.resolve(outputRoot, "logs");
const cliPath =
  process.env.WECHAT_DEVTOOLS_CLI ??
  "/Applications/wechatwebdevtools.app/Contents/MacOS/cli";
const automationPort = Number(process.env.AUDIT_AUTOMATION_PORT ?? 9437);

const require = createRequire(import.meta.url);
const automator = require(
  path.resolve(sourceProjectRoot, "node_modules/miniprogram-automator"),
);
const { PNG } = require(path.resolve(sourceProjectRoot, "node_modules/pngjs"));

await Promise.all([
  mkdir(screenshotsValidRoot, { recursive: true }),
  mkdir(screenshotsMissingRoot, { recursive: true }),
  mkdir(logsRoot, { recursive: true }),
]);

const guardedEnvironment = await readFile(
  path.resolve(runtimeProjectRoot, "miniprogram/config/environment.ts"),
  "utf8",
);
assert.ok(
  guardedEnvironment.includes('apiBaseUrl: "https://127.0.0.1:9"'),
  "Audit runtime API must fail closed on loopback",
);
assert.ok(
  !guardedEnvironment.includes("kg.xingguoxian.cn"),
  "Audit runtime must not retain the production hostname",
);

const appConfig = JSON.parse(
  await readFile(path.resolve(runtimeProjectRoot, "miniprogram/app.json"), "utf8"),
);
const registeredPages = [
  ...(appConfig.pages ?? []).map((route) => ({
    route,
    packageName: "main",
    packageRoot: "",
  })),
  ...(appConfig.subPackages ?? appConfig.subpackages ?? []).flatMap((subpackage) =>
    subpackage.pages.map((pageRoute) => ({
      route: `${subpackage.root.replace(/\/$/u, "")}/${pageRoute}`,
      packageName: subpackage.name ?? subpackage.root,
      packageRoot: subpackage.root,
    })),
  ),
];

const firstScenarioByRoute = new Map();
for (const scenario of visualScenarios) {
  const route = scenario.route.split("?", 1)[0].replace(/^\//u, "");
  if (!firstScenarioByRoute.has(route)) firstScenarioByRoute.set(route, scenario);
}

const missingParameterRoutes = new Set([
  "pages/positions/detail/index",
  "pages/positions/compare/index",
  "pages/positions/goal/index",
  "pages/positions/briefing/index",
  "xingce/xingce-task-intro/index",
  "pages/practice/index",
  "pages/reflection/index",
  "community/community-structure/index",
  "pages/essay-task/index",
  "essay/essay-review/index",
  "essay/essay-rewrite/index",
  "essay/essay-material/index",
  "essay/essay-compose/index",
  "essay/essay-evidence/index",
  "reports/ability-module/index",
  "reports/error-pattern/index",
  "pages/private-knowledge/detail/index",
]);
const tabRoutes = new Set((appConfig.tabBar?.list ?? []).map(({ pagePath }) => pagePath));
const runtimeEvents = [];
const run = {
  schemaVersion: 1,
  startedAt: new Date().toISOString(),
  safeguards: {
    sourceProjectRoot,
    runtimeProjectRoot,
    productionHostnameAbsent: true,
    requestMockInstalled: false,
    apiBaseUrl: "https://127.0.0.1:9",
    realLocationBlocked: false,
    automationPort,
  },
  systemInfo: null,
  validSmoke: [],
  missingParameterSmoke: [],
  backNavigationSmoke: [],
  runtimeEvents,
  launchError: null,
  finishedAt: null,
};

let intendedRoute = "startup";
let miniProgram;
try {
  miniProgram = await withTimeout(
    automator.launch({
      cliPath,
      projectPath: runtimeProjectRoot,
      port: automationPort,
      timeout: 120_000,
      trustProject: true,
    }),
    150_000,
    "Wechat DevTools automation launch exceeded 150 seconds",
  );
  run.systemInfo = await withTimeout(
    miniProgram.systemInfo(),
    60_000,
    "Wechat DevTools systemInfo timed out after 60 seconds",
  );

  miniProgram.on("exception", (event) => {
    runtimeEvents.push({
      at: new Date().toISOString(),
      intendedRoute,
      kind: "exception",
      message: String(event?.message ?? event),
      raw: safeJson(event),
    });
  });
  miniProgram.on("error", (event) => {
    runtimeEvents.push({
      at: new Date().toISOString(),
      intendedRoute,
      kind: "automation-error",
      message: String(event?.message ?? safeJson(event)),
      raw: safeJson(event),
    });
  });
  miniProgram.on("console", (event) => {
    runtimeEvents.push({
      at: new Date().toISOString(),
      intendedRoute,
      kind: `console-${String(event?.type ?? "log")}`,
      message: consoleEventText(event),
      raw: safeJson(event),
    });
  });

  await miniProgram.mockWxMethod("request", {
    data: {
      code: "audit_network_blocked",
      message: "Read-only route audit blocks all network requests.",
      requestId: "audit-route-network-guard",
    },
    statusCode: 503,
    header: {},
    cookies: [],
    errMsg: "request:ok",
  });
  run.safeguards.requestMockInstalled = true;
  await miniProgram.mockWxMethod("getLocation", {
    errMsg: "getLocation:fail read-only audit blocks real location",
  });
  run.safeguards.realLocationBlocked = true;
  await miniProgram.mockWxMethod("showToast", { errMsg: "showToast:ok" });
  await miniProgram.mockWxMethod("showModal", {
    confirm: false,
    cancel: true,
    errMsg: "showModal:ok",
  });

  for (let index = 0; index < registeredPages.length; index += 1) {
    const registered = registeredPages[index];
    const scenario = firstScenarioByRoute.get(registered.route);
    const url = scenario?.route ?? `/${registered.route}`;
    const result = await validSmoke(registered, scenario, url, index);
    run.validSmoke.push(result);
    process.stdout.write(
      `[valid ${String(index + 1).padStart(2, "0")}/${String(
        registeredPages.length,
      )}] ${registered.route} -> ${result.status}\n`,
    );
  }

  const missingRoutes = registeredPages.filter(({ route }) =>
    missingParameterRoutes.has(route),
  );
  for (let index = 0; index < missingRoutes.length; index += 1) {
    const result = await missingParameterSmoke(missingRoutes[index], index);
    run.missingParameterSmoke.push(result);
    process.stdout.write(
      `[missing ${String(index + 1).padStart(2, "0")}/${String(
        missingRoutes.length,
      )}] ${missingRoutes[index].route} -> ${result.status}\n`,
    );
  }

  for (let index = 0; index < registeredPages.length; index += 1) {
    const registered = registeredPages[index];
    const scenario = firstScenarioByRoute.get(registered.route);
    const url = scenario?.route ?? `/${registered.route}`;
    const result = await backNavigationSmoke(registered, url);
    run.backNavigationSmoke.push(result);
    process.stdout.write(
      `[back ${String(index + 1).padStart(2, "0")}/${String(
        registeredPages.length,
      )}] ${registered.route} -> ${result.status}\n`,
    );
  }
} catch (error) {
  run.launchError = errorMessage(error);
  process.stderr.write(`[fatal] ${run.launchError}\n`);
  process.exitCode = 1;
} finally {
  run.finishedAt = new Date().toISOString();
  await writeFile(
    path.resolve(logsRoot, "route-smoke-results.json"),
    `${JSON.stringify(run, null, 2)}\n`,
  );
  await writePageSmokeCsv(run);
  if (miniProgram) {
    try {
      intendedRoute = "cleanup";
      await withTimeout(setAuthentication(false), 5_000, "Session cleanup timed out");
    } catch (error) {
      runtimeEvents.push({
        at: new Date().toISOString(),
        intendedRoute,
        kind: "cleanup-error",
        message: errorMessage(error),
      });
    }
    try {
      await withTimeout(miniProgram.close(), 10_000, "Automation close timed out");
    } catch (error) {
      runtimeEvents.push({
        at: new Date().toISOString(),
        intendedRoute,
        kind: "cleanup-error",
        message: errorMessage(error),
      });
    }
  }
  await writeFile(
    path.resolve(logsRoot, "route-smoke-results.json"),
    `${JSON.stringify(run, null, 2)}\n`,
  );
}

setTimeout(() => process.exit(process.exitCode ?? 0), 100);

async function validSmoke(registered, scenario, url, index) {
  intendedRoute = registered.route;
  const eventStart = runtimeEvents.length;
  const screenshotRelative = path.join(
    "screenshots/valid",
    `${String(index + 1).padStart(2, "0")}-${filenameForRoute(registered.route)}.png`,
  );
  const screenshotAbsolute = path.resolve(outputRoot, screenshotRelative);
  const result = {
    route: registered.route,
    packageName: registered.packageName,
    packageRoot: registered.packageRoot,
    url,
    scenarioId: scenario?.id ?? null,
    authenticated: scenario?.authenticated === true,
    expectedRoute: registered.route,
    actualRoute: null,
    status: "blocked",
    reason: "",
    screenshot: null,
    screenshotMetrics: null,
    visibleGeometry: null,
    pageState: null,
    pageStack: null,
    runtimeEvents: [],
    elapsedMs: 0,
  };
  const startedAt = Date.now();
  try {
    await withTimeout(
      setAuthentication(scenario?.authenticated === true),
      5_000,
      `${registered.route} auth fixture timed out`,
    );
    const navigation = await rawRoute("reLaunch", url, registered.route, 8_000);
    result.actualRoute = navigation.actualRoute;
    result.pageStack = navigation.pageStack;
    if (!navigation.matched) {
      result.reason = `reLaunch did not reach ${registered.route}; actual=${navigation.actualRoute}`;
      return finalizeResult(result, eventStart, startedAt);
    }
    const page = await miniProgram.currentPage();
    await page.waitFor(220);
    if (scenario?.data) {
      await withTimeout(
        setPageData(miniProgram, page, structuredClone(scenario.data)),
        5_000,
        `${registered.route} fixture injection timed out`,
      );
      await page.waitFor(180);
    }
    result.actualRoute = await activeRoute();
    result.pageState = await pageStateSummary(page);
    result.visibleGeometry = await visibleGeometrySummary(page);
    await withTimeout(
      miniProgram.screenshot({ path: screenshotAbsolute }),
      10_000,
      `${registered.route} screenshot timed out`,
    );
    result.screenshot = path.relative(repositoryRoot, screenshotAbsolute);
    result.screenshotMetrics = await screenshotMetrics(screenshotAbsolute);
    if (result.actualRoute !== registered.route) {
      result.reason = `Page redirected after render to ${result.actualRoute}`;
    } else if (
      typeof result.visibleGeometry?.visibleCount === "number" &&
      result.visibleGeometry.visibleCount === 0
    ) {
      result.reason = "No visible page-level geometry was measurable";
    } else if (result.screenshotMetrics?.blankSuspect === true) {
      result.reason = "Screenshot is nearly uniform and may be blank";
    } else {
      result.status = "success";
    }
  } catch (error) {
    result.reason = errorMessage(error);
    result.actualRoute = await activeRoute().catch(() => null);
  }
  return finalizeResult(result, eventStart, startedAt);
}

async function missingParameterSmoke(registered, index) {
  intendedRoute = `${registered.route} [missing params]`;
  const eventStart = runtimeEvents.length;
  const screenshotRelative = path.join(
    "screenshots/missing-params",
    `${String(index + 1).padStart(2, "0")}-${filenameForRoute(registered.route)}.png`,
  );
  const screenshotAbsolute = path.resolve(outputRoot, screenshotRelative);
  const result = {
    route: registered.route,
    url: `/${registered.route}`,
    expectedRoute: registered.route,
    actualRoute: null,
    status: "blocked",
    reason: "",
    screenshot: null,
    screenshotMetrics: null,
    pageState: null,
    pageStack: null,
    runtimeEvents: [],
    elapsedMs: 0,
  };
  const startedAt = Date.now();
  try {
    await setAuthentication(true);
    const navigation = await rawRoute(
      "reLaunch",
      `/${registered.route}`,
      registered.route,
      8_000,
      { allowRedirect: true },
    );
    result.actualRoute = navigation.actualRoute;
    result.pageStack = navigation.pageStack;
    const page = await miniProgram.currentPage();
    await page.waitFor(350);
    result.actualRoute = await activeRoute();
    result.pageState = await pageStateSummary(page);
    await withTimeout(
      miniProgram.screenshot({ path: screenshotAbsolute }),
      10_000,
      `${registered.route} missing-parameter screenshot timed out`,
    );
    result.screenshot = path.relative(repositoryRoot, screenshotAbsolute);
    result.screenshotMetrics = await screenshotMetrics(screenshotAbsolute);
    result.status = result.actualRoute === registered.route ? "stayed" : "redirected";
    result.reason =
      result.actualRoute === registered.route
        ? "Missing parameters handled on the page"
        : `Missing parameters navigated to ${result.actualRoute}`;
  } catch (error) {
    result.reason = errorMessage(error);
    result.actualRoute = await activeRoute().catch(() => null);
  }
  return finalizeResult(result, eventStart, startedAt);
}

async function backNavigationSmoke(registered, url) {
  intendedRoute = `${registered.route} [back]`;
  const eventStart = runtimeEvents.length;
  const result = {
    route: registered.route,
    url,
    mode: tabRoutes.has(registered.route) ? "switchTab" : "navigateTo",
    anchorRoute: null,
    openedRoute: null,
    returnedRoute: null,
    status: "blocked",
    reason: "",
    runtimeEvents: [],
    elapsedMs: 0,
  };
  const startedAt = Date.now();
  try {
    await setAuthentication(true);
    if (registered.route === "pages/login/index") {
      result.status = "not-applicable";
      result.reason = "Login is the configured entry/auth redirect page";
      return finalizeResult(result, eventStart, startedAt);
    }

    if (tabRoutes.has(registered.route)) {
      const anchor =
        registered.route === "pages/today/index"
          ? "pages/ability/index"
          : "pages/today/index";
      await rawRoute("reLaunch", `/${anchor}`, anchor, 8_000);
      result.anchorRoute = anchor;
      const opened = await rawRoute(
        "switchTab",
        `/${registered.route}`,
        registered.route,
        8_000,
      );
      result.openedRoute = opened.actualRoute;
      result.returnedRoute = opened.actualRoute;
      if (opened.matched && opened.pageStack?.length === 1) {
        result.status = "success";
        result.reason = "Tab switch reached target and reset stack to one page";
      } else {
        result.reason = `Tab switch mismatch or unexpected stack: ${safeJson(opened)}`;
      }
      return finalizeResult(result, eventStart, startedAt);
    }

    const anchor = "pages/today/index";
    await rawRoute("reLaunch", `/${anchor}`, anchor, 8_000);
    result.anchorRoute = anchor;
    const opened = await rawRoute("navigateTo", url, registered.route, 8_000);
    result.openedRoute = opened.actualRoute;
    if (!opened.matched) {
      result.reason = `navigateTo did not reach target; actual=${opened.actualRoute}`;
      return finalizeResult(result, eventStart, startedAt);
    }
    const returned = await rawRoute("navigateBack", undefined, anchor, 8_000);
    result.returnedRoute = returned.actualRoute;
    if (returned.matched) {
      result.status = "success";
      result.reason = "navigateBack returned to the anchor page";
    } else {
      result.reason = `navigateBack returned to ${returned.actualRoute}`;
    }
  } catch (error) {
    result.reason = errorMessage(error);
    result.returnedRoute = await activeRoute().catch(() => null);
  }
  return finalizeResult(result, eventStart, startedAt);
}

async function rawRoute(method, url, expectedRoute, timeoutMs, options = {}) {
  const callResult =
    method === "navigateBack"
      ? await withTimeout(
          miniProgram.callWxMethod(method),
          5_000,
          `${method} API call timed out`,
        )
      : await withTimeout(
          miniProgram.callWxMethod(method, { url }),
          5_000,
          `${method} ${url} API call timed out`,
        );
  const deadline = Date.now() + timeoutMs;
  let actualRoute = await activeRoute().catch(() => null);
  while (Date.now() < deadline) {
    actualRoute = await activeRoute().catch(() => actualRoute);
    if (actualRoute === expectedRoute) break;
    if (
      options.allowRedirect === true &&
      actualRoute &&
      actualRoute !== expectedRoute &&
      Date.now() + 300 >= deadline
    ) {
      break;
    }
    await wait(80);
  }
  const pageStack = await currentPageStackSummary().catch(() => null);
  return {
    method,
    url: url ?? null,
    callResult,
    expectedRoute,
    actualRoute,
    matched: actualRoute === expectedRoute,
    pageStack,
  };
}

async function activeRoute() {
  return miniProgram.evaluate(function readAuditActiveRoute() {
    const pages = getCurrentPages();
    return pages[pages.length - 1]?.route ?? null;
  });
}

async function currentPageStackSummary() {
  const payload = await miniProgram.evaluate(function readAuditPageStack() {
    return JSON.stringify(
      getCurrentPages().map((page) => ({
        route: page.route,
        options: page.options ?? {},
      })),
    );
  });
  return JSON.parse(payload);
}

async function pageStateSummary(page) {
  try {
    const data = await withTimeout(
      readPageData(miniProgram, page),
      5_000,
      `${page.path} data read timed out`,
    );
    return {
      status: compactValue(data.status),
      stateStatus: compactValue(data.state?.status),
      pageStateStatus: compactValue(data.pageState?.status),
      workspaceStatus: compactValue(data.workspace?.status),
      phase: compactValue(data.phase),
      errorTitle: compactValue(data.errorTitle),
      errorMessage: compactValue(data.errorMessage),
      configurationError: compactValue(data.configurationError),
      requestId: compactValue(data.requestId),
      showLoginAction: compactValue(data.showLoginAction),
      keys: Object.keys(data).sort(),
    };
  } catch (error) {
    return { readError: errorMessage(error) };
  }
}

async function visibleGeometrySummary(page) {
  try {
    const payload = await withTimeout(
      miniProgram.evaluate(
        function readAuditVisibleGeometry(expectedRoute) {
          return new Promise((resolve) => {
            const pages = getCurrentPages();
            const current = pages[pages.length - 1];
            if (!current || current.route !== expectedRoute) {
              resolve(
                JSON.stringify({
                  error: "route_mismatch",
                  actualRoute: current?.route ?? null,
                }),
              );
              return;
            }
            current
              .createSelectorQuery()
              .selectAll("view,scroll-view,button,image,text,canvas")
              .fields(
                {
                  rect: true,
                  size: true,
                  computedStyle: ["display", "visibility", "opacity"],
                },
                (items) => {
                  const nodes = (items ?? []).map((item) => ({
                    left: Number(item.left ?? 0),
                    top: Number(item.top ?? 0),
                    width: Number(item.width ?? 0),
                    height: Number(item.height ?? 0),
                    display: item.display ?? "",
                    visibility: item.visibility ?? "",
                    opacity: item.opacity ?? "",
                  }));
                  resolve(JSON.stringify({ nodes }));
                },
              )
              .exec();
          });
        },
        page.path,
      ),
      5_000,
      `${page.path} geometry query timed out`,
    );
    const parsed = JSON.parse(payload);
    if (parsed.error) return parsed;
    const visible = parsed.nodes.filter(
      (node) =>
        node.width > 0 &&
        node.height > 0 &&
        node.display !== "none" &&
        node.visibility !== "hidden" &&
        node.opacity !== "0",
    );
    return {
      totalCount: parsed.nodes.length,
      visibleCount: visible.length,
      maxArea: visible.reduce(
        (maximum, node) => Math.max(maximum, node.width * node.height),
        0,
      ),
    };
  } catch (error) {
    return { queryError: errorMessage(error), visibleCount: null };
  }
}

async function screenshotMetrics(filename) {
  const png = PNG.sync.read(await readFile(filename));
  const buckets = new Map();
  let luminanceSum = 0;
  let luminanceSquareSum = 0;
  const pixelCount = png.width * png.height;
  for (let offset = 0; offset < png.data.length; offset += 4) {
    const alpha = png.data[offset + 3] / 255;
    const red = Math.round(png.data[offset] * alpha + 255 * (1 - alpha));
    const green = Math.round(png.data[offset + 1] * alpha + 255 * (1 - alpha));
    const blue = Math.round(png.data[offset + 2] * alpha + 255 * (1 - alpha));
    const key = `${red >> 4}-${green >> 4}-${blue >> 4}`;
    buckets.set(key, (buckets.get(key) ?? 0) + 1);
    const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    luminanceSum += luminance;
    luminanceSquareSum += luminance * luminance;
  }
  const dominantCount = Math.max(...buckets.values());
  const mean = luminanceSum / pixelCount;
  const variance = Math.max(0, luminanceSquareSum / pixelCount - mean * mean);
  const dominantRatio = dominantCount / pixelCount;
  return {
    width: png.width,
    height: png.height,
    quantizedColorCount: buckets.size,
    dominantRatio: Number(dominantRatio.toFixed(6)),
    luminanceMean: Number(mean.toFixed(3)),
    luminanceStdDev: Number(Math.sqrt(variance).toFixed(3)),
    blankSuspect:
      dominantRatio > 0.992 &&
      buckets.size < 8 &&
      Math.sqrt(variance) < 4,
  };
}

async function setAuthentication(authenticated) {
  await miniProgram.evaluate(function applyAuditSession(hasSession) {
    const storageKey = "gongkao.auth.session.v1";
    const app = getApp();
    if (hasSession) {
      const session = {
        accessToken: "audit-route-access-token",
        userId: "audit-route-user",
        displayName: "审计用户",
      };
      wx.setStorageSync(storageKey, session);
      app.globalData.accessToken = session.accessToken;
      app.globalData.userId = session.userId;
      app.globalData.displayName = session.displayName;
      return;
    }
    wx.removeStorageSync(storageKey);
    delete app.globalData.accessToken;
    delete app.globalData.userId;
    delete app.globalData.displayName;
  }, authenticated);
}

function finalizeResult(result, eventStart, startedAt) {
  result.runtimeEvents = runtimeEvents.slice(eventStart);
  result.elapsedMs = Date.now() - startedAt;
  return result;
}

function filenameForRoute(route) {
  return route.replaceAll("/", "__");
}

function compactValue(value) {
  if (value === undefined) return null;
  if (typeof value === "string") return value.slice(0, 500);
  if (typeof value === "number" || typeof value === "boolean" || value === null) {
    return value;
  }
  return safeJson(value).slice(0, 500);
}

function safeJson(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function errorMessage(error) {
  return String(error?.stack ?? error?.message ?? error);
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function withTimeout(promise, timeoutMs, message) {
  let handle;
  const timeout = new Promise((_, reject) => {
    handle = setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(handle);
  }
}

async function writePageSmokeCsv(runState) {
  const missingByRoute = new Map(
    runState.missingParameterSmoke.map((result) => [result.route, result]),
  );
  const backByRoute = new Map(
    runState.backNavigationSmoke.map((result) => [result.route, result]),
  );
  const rows = [
    [
      "route",
      "package_name",
      "valid_url",
      "valid_status",
      "valid_actual_route",
      "valid_reason",
      "screenshot_path",
      "screenshot_width",
      "screenshot_height",
      "blank_suspect",
      "visible_node_count",
      "page_status",
      "page_state_status",
      "missing_params_tested",
      "missing_params_status",
      "missing_params_actual_route",
      "missing_params_page_status",
      "missing_params_error",
      "back_status",
      "back_opened_route",
      "back_returned_route",
      "runtime_exception_count",
    ],
  ];
  for (const result of runState.validSmoke) {
    const missing = missingByRoute.get(result.route);
    const back = backByRoute.get(result.route);
    rows.push([
      result.route,
      result.packageName,
      result.url,
      result.status,
      result.actualRoute,
      result.reason,
      result.screenshot,
      result.screenshotMetrics?.width,
      result.screenshotMetrics?.height,
      result.screenshotMetrics?.blankSuspect,
      result.visibleGeometry?.visibleCount,
      result.pageState?.status,
      result.pageState?.stateStatus,
      Boolean(missing),
      missing?.status,
      missing?.actualRoute,
      missing?.pageState?.status ?? missing?.pageState?.stateStatus,
      missing?.pageState?.errorMessage ??
        missing?.pageState?.configurationError ??
        missing?.reason,
      back?.status,
      back?.openedRoute,
      back?.returnedRoute,
      result.runtimeEvents.filter((event) => event.kind === "exception").length,
    ]);
  }
  await writeFile(
    path.resolve(outputRoot, "page-smoke.csv"),
    `${rows.map((row) => row.map(csvCell).join(",")).join("\n")}\n`,
  );
}

function csvCell(value) {
  const normalized = String(value ?? "");
  return `"${normalized.replaceAll('"', '""')}"`;
}
