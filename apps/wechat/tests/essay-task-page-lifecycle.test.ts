import { afterEach, describe, expect, it, vi } from "vitest";

interface CapturedPageDefinition {
  data: Record<string, unknown>;
  onLoad(this: TestPage, options: Record<string, string>): void;
  onShow(this: TestPage): void;
  onUnload(this: TestPage): void;
  retry(this: TestPage): void;
  goLogin(this: TestPage): void;
  submitArtifact(this: TestPage): Promise<void>;
}

interface TestPage extends CapturedPageDefinition {
  setData(patch: Record<string, unknown>): void;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("essay task page lifecycle", () => {
  it("ignores an unloaded instance response while a second instance finishes normally", async () => {
    vi.useFakeTimers();
    let captured: unknown;
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("Page", (definition: unknown) => {
      captured = definition;
    });
    const app = {
      globalData: {
        accessToken: "test-token",
        userId: "test-user",
        generation: "session-a",
      },
    };
    vi.stubGlobal("getApp", () => app);
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

  it("keeps a pending review command across 401 login and resumes it with the new bearer", async () => {
    vi.useFakeTimers();
    const { app, definition, reLaunch, requests, storage } = await capturePageDefinition();
    const pending = reviewPendingRecovery();
    storage.set("gongkao.essay.pending.v4.test-user.drafting.plan-a", {
      schemaVersion: 4,
      ownerUserId: "test-user",
      createdAtMs: Date.now(),
      expiresAtMs: Date.now() + 60_000,
      payload: pending,
    });
    const page = instantiatePage(definition);
    const options = draftingOptions("task-a", "plan-a");
    page.onLoad.call(page, options);
    page.onShow.call(page);

    expect(requestHeader(requiredAt(requests, 0), "Authorization")).toBe("Bearer test-token");
    resolveWorkspace(requiredAt(requests, 0), workspace("task-a", reviewIntent("pending")));
    await flushPromises(6);
    const expiredEnsure = requiredAt(requests, 1);
    expect(requestHeader(expiredEnsure, "Idempotency-Key")).toBe(pending.ensure.idempotencyKey);
    rejectUnauthorized(expiredEnsure);
    await flushPromises();

    expect(app.globalData.accessToken).toBeUndefined();
    expect(page.data).toMatchObject({
      status: "unavailable",
      showLoginAction: true,
      draftLocked: true,
    });
    page.retry.call(page);
    expect(requests).toHaveLength(2);
    page.goLogin.call(page);
    expect(reLaunch).toHaveBeenCalledWith({
      url:
        "/pages/login/index?returnTo=" +
        encodeURIComponent(
          "/pages/essay-task/index?stage=essay_drafting&taskVersionId=task-a&planTaskId=plan-a",
        ),
    });

    app.globalData = {
      accessToken: "new-token",
      userId: "test-user",
      generation: "session-b",
    };
    page.onShow.call(page);
    const resumedWorkspace = requiredAt(requests, 2);
    expect(requestHeader(resumedWorkspace, "Authorization")).toBe("Bearer new-token");
    resolveWorkspace(resumedWorkspace, workspace("task-a", reviewIntent("pending")));
    await flushPromises(6);
    const resumedEnsure = requiredAt(requests, 3);
    expect(requestHeader(resumedEnsure, "Authorization")).toBe("Bearer new-token");
    expect(requestHeader(resumedEnsure, "Idempotency-Key")).toBe(pending.ensure.idempotencyKey);
    expect(
      requests
        .slice(2)
        .some((request) => requestHeader(request, "Authorization") === "Bearer test-token"),
    ).toBe(false);

    resolveApiRequest(resumedEnsure, {
      intent: reviewIntent("queued", { jobId: "job-b", jobStatus: "queued" }),
      job: { jobId: "job-b", status: "queued" },
    });
    await flushPromises();
    expect(page.data).toMatchObject({ status: "ready" });
    page.onUnload.call(page);
  });

  it("invalidates a late workspace response when the session is replaced", async () => {
    vi.useFakeTimers();
    const { app, definition, requests } = await capturePageDefinition();
    const page = instantiatePage(definition);
    page.onLoad.call(page, taskOptions("task-a", "plan-a"));
    page.onShow.call(page);
    const oldWorkspace = requiredAt(requests, 0);

    app.globalData = {
      accessToken: "new-token",
      userId: "test-user",
      generation: "session-b",
    };
    page.onShow.call(page);
    const currentWorkspace = requiredAt(requests, 1);
    expect(requestHeader(currentWorkspace, "Authorization")).toBe("Bearer new-token");
    resolveWorkspace(currentWorkspace, workspace("task-a"));
    await flushPromises();
    expect(page.data.workspace).toMatchObject({ task: { taskVersionId: "task-a" } });

    resolveWorkspace(oldWorkspace, workspace("stale-task"));
    await flushPromises();
    expect(page.data.workspace).toMatchObject({ task: { taskVersionId: "task-a" } });
    page.onUnload.call(page);
  });

  it("does not restore another account's pending review after reauthentication", async () => {
    vi.useFakeTimers();
    const { app, definition, requests, storage } = await capturePageDefinition();
    const pending = reviewPendingRecovery();
    storage.set("gongkao.essay.pending.v4.test-user.drafting.plan-a", {
      schemaVersion: 4,
      ownerUserId: "test-user",
      createdAtMs: Date.now(),
      expiresAtMs: Date.now() + 60_000,
      payload: pending,
    });
    const page = instantiatePage(definition);
    page.onLoad.call(page, draftingOptions("task-a", "plan-a"));
    page.onShow.call(page);
    rejectUnauthorized(requiredAt(requests, 0));
    await flushPromises();

    app.globalData = {
      accessToken: "user-b-token",
      userId: "user-b",
      generation: "user-b-session",
    };
    page.onShow.call(page);
    resolveWorkspace(requiredAt(requests, 1), workspace("task-a", reviewIntent("pending")));
    await flushPromises(6);
    const userBEnsure = requiredAt(requests, 2);
    expect(requestHeader(userBEnsure, "Authorization")).toBe("Bearer user-b-token");
    expect(requestHeader(userBEnsure, "Idempotency-Key")).not.toBe(pending.ensure.idempotencyKey);
    expect(storage.has("gongkao.essay.pending.v4.test-user.drafting.plan-a")).toBe(true);
    expect(storage.has("gongkao.essay.pending.v4.user-b.drafting.plan-a")).toBe(false);
    page.onUnload.call(page);
  });

  it("keeps material input editable when a non-expiry conflict is returned", async () => {
    vi.useFakeTimers();
    const { definition, requests } = await capturePageDefinition();
    const page = instantiatePage(definition);
    page.onLoad.call(page, taskOptions("task-a", "plan-a"));
    page.onShow.call(page);
    resolveWorkspace(requiredAt(requests, 0), workspace("task-a"));
    await flushPromises();
    fillMaterialDraft(page, "task-a");

    const submission = page.submitArtifact.call(page);
    await flushPromises();
    rejectConflict(
      requiredAt(requests, 1),
      "essay_artifact_conflict",
      "这份学习记录没有保存成功，当前填写内容仍然保留，请检查后重试。",
    );
    await submission;

    expect(page.data).toMatchObject({
      canSubmit: true,
      draftLocked: false,
      overallTheme: "材料主题",
    });
    expect(String(page.data.submissionMessage)).toContain("当前填写内容仍然保留");
    expect(String(page.data.blockingReason)).not.toContain("任务已经失效");
    page.onUnload.call(page);
  });

  it("locks the task only when the server explicitly reports plan_task_expired", async () => {
    vi.useFakeTimers();
    const { definition, requests } = await capturePageDefinition();
    const page = instantiatePage(definition);
    page.onLoad.call(page, taskOptions("task-a", "plan-a"));
    page.onShow.call(page);
    resolveWorkspace(requiredAt(requests, 0), workspace("task-a"));
    await flushPromises();
    fillMaterialDraft(page, "task-a");

    const submission = page.submitArtifact.call(page);
    await flushPromises();
    rejectConflict(
      requiredAt(requests, 1),
      "plan_task_expired",
      "该任务所属的每日计划已过期，请刷新今日计划后继续。",
    );
    await submission;

    expect(page.data.canSubmit).toBe(false);
    expect(String(page.data.blockingReason)).toContain("确实已经过期");
    page.onUnload.call(page);
  });
});

async function capturePageDefinition(): Promise<{
  app: {
    globalData: {
      accessToken?: string;
      userId?: string;
      generation?: string;
    };
  };
  definition: CapturedPageDefinition;
  reLaunch: ReturnType<typeof vi.fn>;
  requests: WechatMiniprogram.RequestOption[];
  storage: Map<string, unknown>;
}> {
  let captured: unknown;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const storage = new Map<string, unknown>();
  const reLaunch = vi.fn();
  const app = {
    globalData: {
      accessToken: "test-token",
      userId: "test-user",
      generation: "session-a",
    },
  };
  vi.stubGlobal("Page", (definition: unknown) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => app);
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({
      keys: [...storage.keys()],
      currentSize: 0,
      limitSize: 10_240,
    }),
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).buffer }),
    request: (options: WechatMiniprogram.RequestOption) => {
      requests.push(options);
    },
    reLaunch,
  });
  const { installUnauthorizedReset } = await import("../miniprogram/api/auth");
  installUnauthorizedReset();
  await import("../miniprogram/pages/essay-task/index");
  return { app, definition: captured as CapturedPageDefinition, reLaunch, requests, storage };
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

function reviewPendingRecovery() {
  return {
    phase: "review_pending" as const,
    taskVersionId: "task-a",
    planTaskId: "plan-a",
    receipt: {
      attemptId: "attempt-a",
      answerVersionId: "answer-version-a",
      reflectionArtifactVersionId: "reflection-version-a",
    },
    intent: {
      intentId: "intent-a",
      intentVersionId: "intent-version-a",
    },
    ensure: {
      idempotencyKey: "essay-review-ensure-fixed",
    },
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

function rejectUnauthorized(request: WechatMiniprogram.RequestOption): void {
  request.success?.({
    data: {
      code: "AUTH_EXPIRED",
      message: "登录状态已过期",
      requestId: "request-auth-expired",
    },
    statusCode: 401,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function rejectConflict(
  request: WechatMiniprogram.RequestOption,
  code: string,
  message: string,
): void {
  request.success?.({
    data: { code, message, requestId: `request-${code}` },
    statusCode: 409,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function fillMaterialDraft(page: TestPage, taskVersionId: string): void {
  page.setData({
    canSubmit: true,
    submissionStatus: "idle",
    overallTheme: "材料主题",
    materialAnnotations: [
      {
        segmentId: `segment-${taskVersionId}`,
        label: "材料 1 · 第 1 段",
        paragraphSummary: "材料正文",
        subjectsText: "材料主体",
        evidenceQuote: "材料正文。",
      },
    ],
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
