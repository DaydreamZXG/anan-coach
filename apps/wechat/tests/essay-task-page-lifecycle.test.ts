import { afterEach, describe, expect, it, vi } from "vitest";
import { environment } from "../miniprogram/config/environment";

interface CapturedPageDefinition {
  data: Record<string, unknown>;
  onLoad(this: TestPage, options: Record<string, string>): void;
  onShow(this: TestPage): void;
  onUnload(this: TestPage): void;
}

interface TestPage extends CapturedPageDefinition {
  setData(patch: Record<string, unknown>): void;
}

afterEach(() => {
  environment.dataMode = "prototype";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("essay task page lifecycle", () => {
  it("ignores an unloaded instance response while a second instance finishes normally", async () => {
    vi.useFakeTimers();
    environment.dataMode = "api";
    let captured: unknown;
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("Page", (definition: unknown) => {
      captured = definition;
    });
    vi.stubGlobal("getApp", () => ({
      globalData: { accessToken: "test-token", userId: "test-user" },
    }));
    vi.stubGlobal("wx", {
      getStorageSync: () => "",
      setStorageSync: vi.fn(),
      removeStorageSync: vi.fn(),
      getStorageInfoSync: () => ({ keys: [], currentSize: 0, limitSize: 10_240 }),
      request: (options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
      },
    });

    await import("../miniprogram/pages/essay-task/index");
    const definition = captured as CapturedPageDefinition;
    const pageA = instantiatePage(definition);
    const pageB = instantiatePage(definition);
    pageA.onLoad.call(pageA, taskOptions("task-a", "plan-a"));
    pageA.onShow.call(pageA);
    pageB.onLoad.call(pageB, taskOptions("task-b", "plan-b"));
    pageB.onShow.call(pageB);

    expect(requests).toHaveLength(2);
    pageA.onUnload.call(pageA);
    const pageAStateAfterUnload = clone(pageA.data);
    resolveWorkspace(requiredAt(requests, 0), workspace("task-a"));
    resolveWorkspace(requiredAt(requests, 1), workspace("task-b"));
    await flushPromises();

    expect(pageA.data).toEqual(pageAStateAfterUnload);
    expect(pageB.data.status).toBe("ready");
    expect(pageB.data.workspace).toMatchObject({
      task: { taskVersionId: "task-b" },
    });
    pageB.onUnload.call(pageB);
  });

  it("restores a pending review intent from workspace on another device and calls ensure", async () => {
    vi.useFakeTimers();
    environment.dataMode = "api";
    const { definition, requests } = await capturePageDefinition();
    const page = instantiatePage(definition);
    page.onLoad.call(page, draftingOptions("task-a", "plan-a"));
    page.onShow.call(page);
    resolveWorkspace(requiredAt(requests, 0), workspace("task-a", reviewIntent("pending")));
    await flushPromises(6);

    expect(requests).toHaveLength(2);
    const ensureRequest = requiredAt(requests, 1);
    expect(ensureRequest.url).toContain("/reviews/current/ensure");
    expect(ensureRequest.data).toEqual({
      taskVersionId: "task-a",
      materialBundleVersionId: "material-task-a",
      rubricVersionId: "rubric-task-a",
      reviewScope: "grounded_full",
    });
    expect(requestHeader(ensureRequest, "Idempotency-Key")).toMatch(/^essay-review-ensure-/u);
    resolveApiRequest(ensureRequest, {
      intent: reviewIntent("queued", { jobId: "job-a", jobStatus: "queued" }),
      job: { jobId: "job-a", status: "queued" },
    });
    await flushPromises();

    expect(page.data.reviewDisplay).toMatchObject({ state: "queued" });
    page.onUnload.call(page);
  });

  it("shows cross-device quota waiting without issuing another ensure before retryAfter", async () => {
    vi.useFakeTimers();
    environment.dataMode = "api";
    const { definition, requests } = await capturePageDefinition();
    const page = instantiatePage(definition);
    page.onLoad.call(page, draftingOptions("task-a", "plan-a"));
    page.onShow.call(page);
    resolveWorkspace(
      requiredAt(requests, 0),
      workspace(
        "task-a",
        reviewIntent("waiting_quota", {
          quota: {
            feature: "essay_review",
            accessMode: "entitlement_quota",
            used: 20,
            limit: 20,
            usageDate: "2026-07-13",
          },
          retryAfter: "2099-07-14T00:00:00+08:00",
        }),
      ),
    );
    await flushPromises(6);

    expect(requests).toHaveLength(1);
    expect(page.data.reviewDisplay).toMatchObject({
      state: "waiting_quota",
      title: "答案已保存，评阅额度待恢复",
    });
    expect(page.data.submissionStatus).toBe("saved");
    page.onUnload.call(page);
  });

  it("polls the durable job id from a queued workspace intent", async () => {
    vi.useFakeTimers();
    environment.dataMode = "api";
    const { definition, requests } = await capturePageDefinition();
    const page = instantiatePage(definition);
    page.onLoad.call(page, draftingOptions("task-a", "plan-a"));
    page.onShow.call(page);
    resolveWorkspace(
      requiredAt(requests, 0),
      workspace("task-a", reviewIntent("queued", { jobId: "job/a", jobStatus: "queued" })),
    );
    await flushPromises();
    vi.advanceTimersByTime(0);
    await flushPromises();

    expect(requests).toHaveLength(2);
    expect(requiredAt(requests, 1).url).toContain("/review-jobs/job%2Fa");
    page.onUnload.call(page);
  });
});

async function capturePageDefinition(): Promise<{
  definition: CapturedPageDefinition;
  requests: WechatMiniprogram.RequestOption[];
}> {
  let captured: unknown;
  const requests: WechatMiniprogram.RequestOption[] = [];
  vi.stubGlobal("Page", (definition: unknown) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "test-token", userId: "test-user" },
  }));
  vi.stubGlobal("wx", {
    getStorageSync: () => "",
    setStorageSync: vi.fn(),
    removeStorageSync: vi.fn(),
    getStorageInfoSync: () => ({ keys: [], currentSize: 0, limitSize: 10_240 }),
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).buffer }),
    request: (options: WechatMiniprogram.RequestOption) => {
      requests.push(options);
    },
  });
  const runtimeEnvironment = await import("../miniprogram/config/environment");
  runtimeEnvironment.environment.dataMode = "api";
  await import("../miniprogram/pages/essay-task/index");
  return { definition: captured as CapturedPageDefinition, requests };
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

function taskOptions(taskVersionId: string, planTaskId: string): Record<string, string> {
  return {
    taskVersionId,
    planTaskId,
    stage: "essay_material_reading",
  };
}

function draftingOptions(taskVersionId: string, planTaskId: string): Record<string, string> {
  return { taskVersionId, planTaskId, stage: "essay_drafting" };
}

function workspace(
  taskVersionId: string,
  intent: Record<string, unknown> | null = null,
): Record<string, unknown> {
  return {
    task: {
      taskVersionId,
      taskKey: `guokao:${taskVersionId}`,
      prompt: "概括给定材料。",
      requirements: "全面、准确。",
      questionType: "summary",
      wordLimit: 300,
      maximumScore: 20,
    },
    material: {
      materialBundleVersionId: `material-${taskVersionId}`,
      sourceKind: "own_bank",
      segments: [
        {
          materialId: `material-item-${taskVersionId}`,
          segmentId: `segment-${taskVersionId}`,
          materialOrder: 1,
          segmentOrder: 1,
          text: "材料正文。",
        },
      ],
    },
    rubricAvailability: {
      status: intent ? "published" : "pending",
      rubric: intent
        ? {
            rubricVersionId: `rubric-${taskVersionId}`,
            sourceKind: "system_compiled",
            verificationStatus: "unverified",
            totalMaximum: 20,
            points: [],
          }
        : null,
    },
    currentArtifacts: {},
    progression: {
      attemptId: null,
      answerVersionId: null,
      reviewVersionId: null,
      reviewJobId: null,
      reviewIntent: intent,
      reviewStatus: null,
      rewriteVersionId: null,
      transferVerificationVersionId: null,
      transferStatus: null,
      currentReview: null,
    },
  };
}

function reviewIntent(
  status: "pending" | "waiting_quota" | "queued",
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    intentId: "intent-a",
    intentVersionId: "intent-version-a",
    versionNo: 1,
    status,
    attemptId: "attempt-a",
    answerVersionId: "answer-version-a",
    taskVersionId: "task-a",
    materialBundleVersionId: "material-task-a",
    rubricVersionId: "rubric-task-a",
    reviewScope: "grounded_full",
    inputFingerprint: "a".repeat(64),
    intentFingerprint: "b".repeat(64),
    stateFingerprint: "c".repeat(64),
    jobId: null,
    jobStatus: null,
    quota: null,
    retryAfter: null,
    supersedesIntentVersionId: null,
    createdAt: "2026-07-13T00:00:00Z",
    ...overrides,
  };
}

function resolveWorkspace(
  request: WechatMiniprogram.RequestOption,
  value: Record<string, unknown>,
): void {
  request.success?.({
    data: { data: value },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function resolveApiRequest(request: WechatMiniprogram.RequestOption, value: unknown): void {
  request.success?.({
    data: { data: value },
    statusCode: 202,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function requiredAt<T>(values: T[], index: number): T {
  const value = values[index];
  if (value === undefined) throw new Error(`missing fixture at index ${String(index)}`);
  return value;
}

function requestHeader(request: WechatMiniprogram.RequestOption, name: string): string | undefined {
  const headers: unknown = request.header;
  if (!headers || typeof headers !== "object") return undefined;
  const value = (headers as Record<string, unknown>)[name];
  return typeof value === "string" ? value : undefined;
}

async function flushPromises(turns = 3): Promise<void> {
  for (let index = 0; index < turns; index += 1) await Promise.resolve();
}
