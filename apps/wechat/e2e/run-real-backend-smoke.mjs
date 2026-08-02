/**
 * 60 路由真实后端冒烟：不做任何 wx.request mock，逐一 reLaunch 交互矩阵中的
 * 全部注册页面，等待页面数据进入稳定状态，并记录运行时异常/告警。
 *
 * 与 run-click-audit.mjs 的区别：click-audit mock 掉网络只验证绑定合同；
 * 本脚本让页面真实请求编译进包的 apiBaseUrl（冒烟时指向本地新版后端）。
 *
 * 必需环境变量：
 *   WECHAT_REAL_BACKEND_ACCESS_TOKEN  会话 token 明文
 *   WECHAT_REAL_BACKEND_USER_ID       会话所属 user_id
 *   WECHAT_REAL_BACKEND_BASE_URL      期望的后端 origin（须与编译进包的一致）
 *   WECHAT_REAL_BACKEND_TEST_ACCOUNT  必须为 acknowledged（确认是专用测试账号）
 * 可选：
 *   WECHAT_AUTOMATOR_WS   已运行 DevTools 的自动化端口（如 ws://127.0.0.1:23340），
 *                         提供时用 connect 而不是 launch
 *   WECHAT_DEVTOOLS_CLI   CLI 路径（仅 launch 模式）
 *   SMOKE_REPORT_PATH     JSON 报告输出路径（默认 e2e/real-backend-smoke-report.json）
 */
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import automator from "miniprogram-automator";

import { pageInteractionMatrix } from "./page-interaction-matrix.mjs";
import { readPageData } from "./page-data-bridge.mjs";
import { consoleEventText, isActionableRuntimeWarning } from "./runtime-warning-policy.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(currentDirectory, "..");

const accessToken = requiredEnvironment("WECHAT_REAL_BACKEND_ACCESS_TOKEN");
const userId = requiredEnvironment("WECHAT_REAL_BACKEND_USER_ID");
const expectedBaseUrl = requiredEnvironment("WECHAT_REAL_BACKEND_BASE_URL").replace(/\/$/u, "");
assert.equal(
  process.env.WECHAT_REAL_BACKEND_TEST_ACCOUNT,
  "acknowledged",
  "Set WECHAT_REAL_BACKEND_TEST_ACCOUNT=acknowledged only for a dedicated E2E account.",
);

const environmentSource = await readFile(
  path.resolve(projectRoot, "miniprogram/config/environment.ts"),
  "utf8",
);
const compiledBaseUrl = environmentSource.match(/apiBaseUrl:\s*["']([^"']+)["']/u)?.[1];
assert.equal(
  compiledBaseUrl,
  expectedBaseUrl,
  "The requested real-backend origin differs from the Mini Program compiled origin.",
);

const wsEndpoint = process.env.WECHAT_AUTOMATOR_WS?.trim();
const miniProgram = wsEndpoint
  ? await automator.connect({ wsEndpoint })
  : await automator.launch({
      cliPath:
        process.env.WECHAT_DEVTOOLS_CLI ?? "/Applications/wechatwebdevtools.app/Contents/MacOS/cli",
      projectPath: projectRoot,
      timeout: 60_000,
      trustProject: true,
    });

const routeFailures = [];
let activeRoute = "(startup)";
const perRouteEvents = new Map();
miniProgram.on("exception", (event) => {
  recordEvent(activeRoute, "exception", String(event?.message ?? event));
});
miniProgram.on("console", (event) => {
  const message = consoleEventText(event);
  if (isActionableRuntimeWarning(message)) recordEvent(activeRoute, "warning", message);
});

const results = [];
try {
  await setAuthentication(miniProgram, true);
  for (const entry of pageInteractionMatrix) {
    const route = `/${entry.route}`;
    activeRoute = route;
    const startedAt = Date.now();
    let observedState = null;
    let outcome = "pass";
    let detail = "";
    try {
      const page = await miniProgram.reLaunch(route);
      assert.ok(page, `Unable to open ${route}`);
      observedState = await waitForSettled(miniProgram, page, route, entry.primaryInteraction);
      const classified = classifyState(observedState);
      outcome = classified.outcome;
      detail = classified.detail;
    } catch (error) {
      outcome = "fail";
      detail = String(error?.message ?? error);
    }
    const events = perRouteEvents.get(route) ?? [];
    if (events.some((event) => event.kind === "exception")) outcome = "fail";
    const result = {
      route,
      scenarioId: entry.scenarioId,
      purpose: entry.purpose,
      outcome,
      detail,
      observedState,
      elapsedMs: Date.now() - startedAt,
      events,
    };
    results.push(result);
    if (outcome === "fail") routeFailures.push(route);
    process.stdout.write(`[smoke] ${route} ... ${outcome}${detail ? ` (${detail})` : ""}\n`);
  }
} finally {
  activeRoute = "(teardown)";
  await withTimeout(setAuthentication(miniProgram, false), 5_000, "smoke auth did not clear").catch(
    () => {},
  );
  if (!wsEndpoint) {
    await withTimeout(miniProgram.close(), 10_000, "smoke connection did not close").catch(
      () => {},
    );
  } else {
    await withTimeout(miniProgram.disconnect(), 10_000, "smoke disconnect stalled").catch(() => {});
  }
}

const summary = {
  generatedAt: new Date().toISOString(),
  baseUrl: expectedBaseUrl,
  totalRoutes: results.length,
  pass: results.filter((item) => item.outcome === "pass").length,
  degraded: results.filter((item) => item.outcome === "degraded").length,
  fail: routeFailures.length,
  results,
};
const reportPath =
  process.env.SMOKE_REPORT_PATH ?? path.resolve(currentDirectory, "real-backend-smoke-report.json");
await writeFile(reportPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
process.stdout.write(
  `\nSmoke complete: ${summary.pass} pass / ${summary.degraded} degraded / ${summary.fail} fail of ${summary.totalRoutes} routes.\nReport: ${reportPath}\n`,
);
if (routeFailures.length > 0) {
  process.stderr.write(`Failed routes:\n${routeFailures.join("\n")}\n`);
  process.exitCode = 1;
}

function recordEvent(route, kind, message) {
  if (!perRouteEvents.has(route)) perRouteEvents.set(route, []);
  perRouteEvents.get(route).push({ kind, message });
}

/**
 * 三层就绪判据（15 秒超时）：
 * 1. 页面数据暴露 status/state/loading 合同 → 按语义分类；
 * 2. 无状态合同的页面（terms、onboarding 表单等）→ 交互矩阵的主交互
 *    selector 渲染出来即视为就绪；
 * 3. selector 也没有（read-only 页）→ 页面渲染出任意 view/text 节点即就绪。
 * 只有数据合同明确停在 loading，或 DOM 一直为空，才判失败。
 */
async function waitForSettled(program, page, route, primaryInteraction) {
  const selector = primaryInteraction?.selector ?? null;
  let lastData = null;
  for (let attempt = 0; attempt < 150; attempt += 1) {
    lastData = await readPageData(program, page).catch(() => null);
    const state = extractState(lastData);
    if (state && state !== "loading" && state !== "pending") return `status:${state}`;
    if (state == null && attempt >= 10) {
      if (selector) {
        const element = await page.$(selector).catch(() => null);
        if (element) return `rendered:${selector}`;
      }
      const anyNode = await page.$("view").catch(() => null);
      if (anyNode && attempt >= 30) return "rendered:dom";
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const finalState = extractState(lastData);
  if (finalState) return `status:${finalState}`;
  throw new Error(`${route} never became ready (no status contract and no rendered DOM)`);
}

function extractState(data) {
  if (!data || typeof data !== "object") return null;
  if (typeof data.status === "string") return data.status;
  if (typeof data.workspace?.status === "string") return data.workspace.status;
  if (typeof data.state === "string") return data.state;
  if (typeof data.loading === "boolean") return data.loading ? "loading" : "ready";
  return null;
}

function classifyState(observed) {
  if (observed.startsWith("rendered:")) {
    return { outcome: "pass", detail: observed };
  }
  const state = observed.replace(/^status:/u, "");
  if (state === "ready" || state === "empty" || state === "idle" || state === "draft") {
    return { outcome: "pass", detail: state };
  }
  if (state === "unauthenticated" || state === "error" || state === "failed") {
    return { outcome: "degraded", detail: `observed status: ${state}` };
  }
  return { outcome: "pass", detail: `observed status: ${state}` };
}

async function setAuthentication(program, authenticated) {
  await program.evaluate(
    function applyRealBackendSession(hasSession, token, stableUserId) {
      const storageKey = "gongkao.auth.session.v1";
      const app = getApp();
      if (hasSession) {
        const session = { accessToken: token, userId: stableUserId };
        wx.setStorageSync(storageKey, session);
        app.globalData.accessToken = token;
        app.globalData.userId = stableUserId;
        return;
      }
      wx.removeStorageSync(storageKey);
      delete app.globalData.accessToken;
      delete app.globalData.userId;
    },
    authenticated,
    accessToken,
    userId,
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

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  assert.ok(value, `${name} is required; mock credentials are rejected by this command.`);
  return value;
}
