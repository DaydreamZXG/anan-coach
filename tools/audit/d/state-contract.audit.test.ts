import { afterEach, describe, expect, it, vi } from "vitest";

interface MiniRequest {
  url: string;
  method?: string;
  data?: unknown;
  header?: Record<string, string>;
  success?(response: unknown): void;
  fail?(error?: unknown): void;
}

interface CapturedPageDefinition {
  data: Record<string, unknown>;
  onShow?(this: TestPage): void;
  loadCenter?(this: TestPage): Promise<void>;
  loadContext?(this: TestPage): Promise<void>;
  ask?(this: TestPage): Promise<void>;
  restoreAskedQuestion?(this: TestPage): void;
  confirmAdjustment?(this: TestPage): Promise<void>;
  save?(this: TestPage): Promise<void>;
}

interface TestPage extends CapturedPageDefinition {
  setData(patch: Record<string, unknown>): void;
}

interface AuditHarness {
  definition: CapturedPageDefinition;
  requests: MiniRequest[];
  switchTab: ReturnType<typeof vi.fn>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("audit reproduction: frontend request/state contracts", () => {
  it("maps develop, trial, and release builds to the same documented production origin", async () => {
    const { configureForWechatEnvVersion } =
      await import("../../../apps/wechat/miniprogram/config/environment");

    const origins = (["develop", "trial", "release"] as const).map(
      (envVersion) => configureForWechatEnvVersion(envVersion).apiBaseUrl,
    );

    expect(origins).toEqual([
      "https://kg.xingguoxian.cn",
      "https://kg.xingguoxian.cn",
      "https://kg.xingguoxian.cn",
    ]);
  });

  it("lets a delayed 401 from an old token erase a newly stored session", async () => {
    const requests: MiniRequest[] = [];
    const appState = {
      globalData: {
        accessToken: "old-token",
        userId: "audit-user",
        displayName: "旧会话",
      },
    };
    const persisted = new Map<string, unknown>();
    vi.stubGlobal("getApp", () => appState);
    vi.stubGlobal("wx", {
      getStorageSync: (key: string) => persisted.get(key),
      setStorageSync: (key: string, value: unknown) =>
        persisted.set(key, value),
      removeStorageSync: (key: string) => persisted.delete(key),
      request: (options: MiniRequest) => requests.push(options),
    });

    const [{ ApiClient }, auth, session] = await Promise.all([
      import("../../../apps/wechat/miniprogram/api/client"),
      import("../../../apps/wechat/miniprogram/api/auth"),
      import("../../../apps/wechat/miniprogram/auth/session"),
    ]);
    auth.installUnauthorizedReset();

    const oldRequest = new ApiClient("https://audit.invalid", "old-token").get(
      "/delayed",
    );
    session.storeAuthSession("new-token", "audit-user", "新会话");
    expect(session.currentAuthSession()?.accessToken).toBe("new-token");

    requests[0]?.success?.({
      statusCode: 401,
      data: {
        code: "authentication_required",
        message: "登录状态已失效。",
        requestId: "old-request-401",
      },
    });
    await expect(oldRequest).rejects.toMatchObject({ statusCode: 401 });

    expect(session.currentAuthSession()).toBeUndefined();
    expect(appState.globalData.accessToken).toBeUndefined();
  });

  it("lets an older report-center response overwrite the newer onShow response", async () => {
    const harness = await capturePage(
      "../../../apps/wechat/miniprogram/pages/report-center/index",
      "deferred",
    );
    const page = instantiatePage(harness.definition);

    page.onShow?.call(page);
    page.onShow?.call(page);
    expect(harness.requests).toHaveLength(2);

    harness.requests[1]?.success?.(
      successEnvelope({
        generatedAt: "2026-07-28T10:00:00+08:00",
        latestExternalReport: null,
        reportCards: [],
        standardPapers: [],
        auditMarker: "newer-response",
      }),
    );
    await flushMicrotasks();
    expect((page.data.center as { auditMarker: string }).auditMarker).toBe(
      "newer-response",
    );

    harness.requests[0]?.success?.(
      successEnvelope({
        generatedAt: "2026-07-28T09:00:00+08:00",
        latestExternalReport: null,
        reportCards: [],
        standardPapers: [],
        auditMarker: "older-response",
      }),
    );
    await flushMicrotasks();

    expect((page.data.center as { auditMarker: string }).auditMarker).toBe(
      "older-response",
    );
    expect(page.data.status).toBe("ready");
  });

  it("rotates the companion idempotency key after a transport failure for the same question", async () => {
    const harness = await capturePage(
      "../../../apps/wechat/miniprogram/pages/anan/index",
      "network-fail",
    );
    const page = instantiatePage(harness.definition);
    Object.assign(page.data, {
      contextStatus: "ready",
      learnerStateVersion: "state-v1",
      question: "资料分析增长率怎么补？",
      askedQuestion: "",
      submitting: false,
    });

    await page.ask?.call(page);
    page.restoreAskedQuestion?.call(page);
    await page.ask?.call(page);

    expect(harness.requests).toHaveLength(2);
    expect(harness.requests.map((request) => request.url)).toEqual([
      "https://kg.xingguoxian.cn/api/v2/companion/ask",
      "https://kg.xingguoxian.cn/api/v2/companion/ask",
    ]);
    expect(harness.requests[0]?.data).toEqual(harness.requests[1]?.data);
    expect(harness.requests[0]?.header?.["Idempotency-Key"]).not.toBe(
      harness.requests[1]?.header?.["Idempotency-Key"],
    );
    expect(page.data.errorMessage).toContain("问题没有发送成功");
  });

  it("presents a planning request failure as if the companion had confirmed no goal or tasks", async () => {
    const harness = await capturePage(
      "../../../apps/wechat/miniprogram/pages/anan/index",
      "anan-partial",
    );
    const page = instantiatePage(harness.definition);

    await page.loadContext?.call(page);

    expect(harness.requests.map((request) => request.url)).toEqual([
      "https://kg.xingguoxian.cn/api/v2/learning-brain/profile",
      "https://kg.xingguoxian.cn/api/v2/plans/today",
    ]);
    expect(page.data.contextStatus).toBe("ready");
    expect(page.data.errorMessage).toBe("");
    expect(page.data.contextGoalLabel).toBe("主目标待确认");
    expect(page.data.contextTaskLabel).toBe("今日 0 项任务");
    expect(page.data.insightTitle).toBe("还没有确认主目标岗位");
  });

  it("rotates the capacity idempotency key while retrying the same versioned command", async () => {
    const harness = await capturePage(
      "../../../apps/wechat/miniprogram/pages/day-adjustment/index",
      "capacity-unknown-outcome",
    );
    const page = instantiatePage(harness.definition);
    Object.assign(page.data, {
      status: "ready",
      dashboard: {
        capacity: {
          capacityVersionId: "11111111-1111-4111-8111-111111111111",
        },
      },
      step: "preview",
      minutes: 120,
      energy: 3,
      stress: 3,
      confidenceLevel: 3,
      preview: { proposedPlan: {} },
      previewing: false,
      submitting: false,
    });

    await page.confirmAdjustment?.call(page);
    await page.confirmAdjustment?.call(page);

    expect(harness.requests).toHaveLength(2);
    expect(harness.requests.map((request) => request.method)).toEqual([
      "PUT",
      "PUT",
    ]);
    expect(harness.requests[0]?.data).toEqual(harness.requests[1]?.data);
    expect(harness.requests[0]?.header?.["Idempotency-Key"]).not.toBe(
      harness.requests[1]?.header?.["Idempotency-Key"],
    );
    expect(page.data.errorMessage).toBe("数据已经更新，请刷新后重试。");
    expect(harness.switchTab).not.toHaveBeenCalled();
  });

  it("rotates the notification-preference key while retrying the same versioned command", async () => {
    const harness = await capturePage(
      "../../../apps/wechat/miniprogram/pages/notification-settings/index",
      "coach-unknown-outcome",
    );
    const page = instantiatePage(harness.definition);
    Object.assign(page.data, {
      status: "ready",
      saving: false,
      draft: {
        preferenceVersionId: "22222222-2222-4222-8222-222222222222",
        enabled: true,
        taskRemindersEnabled: true,
        reflectionRemindersEnabled: false,
        retestRemindersEnabled: false,
        mockRemindersEnabled: false,
        weeklySummaryEnabled: false,
        receiveStart: "08:00",
        receiveEnd: "21:00",
        quietEnabled: false,
        quietStart: "",
        quietEnd: "",
        dailyLimit: "3",
      },
    });

    await page.save?.call(page);
    await page.save?.call(page);

    expect(harness.requests).toHaveLength(2);
    expect(harness.requests.map((request) => request.method)).toEqual([
      "PUT",
      "PUT",
    ]);
    expect(harness.requests[0]?.data).toEqual(harness.requests[1]?.data);
    expect(harness.requests[0]?.header?.["Idempotency-Key"]).not.toBe(
      harness.requests[1]?.header?.["Idempotency-Key"],
    );
    expect(page.data.message).toBe("督学偏好已经变化，请刷新后保存。");
  });
});

async function capturePage(
  modulePath: string,
  requestMode:
    | "deferred"
    | "network-fail"
    | "anan-partial"
    | "coach-unknown-outcome"
    | "capacity-unknown-outcome",
): Promise<AuditHarness> {
  let captured: CapturedPageDefinition | undefined;
  const requests: MiniRequest[] = [];
  let randomSeed = 0;
  const switchTab = vi.fn(() => Promise.resolve());

  vi.stubGlobal("Page", (definition: CapturedPageDefinition) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({
    globalData: {
      accessToken: "audit-token",
      userId: "audit-user",
      displayName: "审计账号",
    },
  }));
  vi.stubGlobal("wx", {
    getStorageSync: () => undefined,
    setStorageSync: vi.fn(),
    removeStorageSync: vi.fn(),
    getRandomValues: () => {
      randomSeed += 1;
      return Promise.resolve({
        randomValues: new Uint8Array(16).fill(randomSeed).buffer,
      });
    },
    request: (options: MiniRequest) => {
      requests.push(options);
      if (requestMode === "network-fail") {
        options.fail?.({ errMsg: "request:fail timeout" });
        return;
      }
      if (requestMode === "anan-partial") {
        if (options.url.endsWith("/api/v2/learning-brain/profile")) {
          options.success?.(
            successEnvelope({
              profileVersionId: "profile-v1",
              evidenceSummary: {
                scoreEvidenceCount: 1,
                xingceAttemptCount: 2,
                xingceMasteryCount: 0,
                essayMasteryCount: 0,
                taskExecutionCount: 1,
                externalAnswerCount: 0,
              },
              decisionSummary: { reviewDueCount: 0 },
            }),
          );
          return;
        }
        options.success?.({
          statusCode: 503,
          data: {
            code: "service_unavailable",
            message: "计划服务暂时不可用。",
            requestId: "planning-failed",
          },
        });
        return;
      }
      if (requestMode === "capacity-unknown-outcome") {
        if (requests.length === 1) {
          options.fail?.({ errMsg: "request:fail timeout after send" });
          return;
        }
        options.success?.({
          statusCode: 409,
          data: {
            code: "conflict",
            message: "数据已经更新，请刷新后重试。",
            requestId: "capacity-conflict-2",
          },
        });
      }
      if (requestMode === "coach-unknown-outcome") {
        if (requests.length === 1) {
          options.fail?.({ errMsg: "request:fail timeout after send" });
          return;
        }
        options.success?.({
          statusCode: 409,
          data: {
            code: "conflict",
            message: "督学偏好已经变化，请刷新后保存。",
            requestId: "coach-conflict-2",
          },
        });
      }
    },
    reLaunch: vi.fn(() => Promise.resolve()),
    navigateTo: vi.fn(() => Promise.resolve()),
    redirectTo: vi.fn(() => Promise.resolve()),
    navigateBack: vi.fn(() => Promise.resolve()),
    switchTab,
    showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    showToast: vi.fn(() => Promise.resolve()),
    pageScrollTo: vi.fn(() => Promise.resolve()),
  });

  await import(modulePath);
  if (!captured) throw new Error(`Page was not registered: ${modulePath}`);
  return { definition: captured, requests, switchTab };
}

function instantiatePage(definition: CapturedPageDefinition): TestPage {
  return {
    ...definition,
    data: clone(definition.data),
    setData(patch: Record<string, unknown>) {
      Object.assign(this.data, patch);
    },
  };
}

function successEnvelope(data: unknown): {
  statusCode: number;
  data: { data: unknown; requestId: string; generatedAt: string };
} {
  return {
    statusCode: 200,
    data: {
      data,
      requestId: "audit-request",
      generatedAt: "2026-07-28T10:00:00+08:00",
    },
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

async function flushMicrotasks(turns = 20): Promise<void> {
  for (let index = 0; index < turns; index += 1) await Promise.resolve();
}
