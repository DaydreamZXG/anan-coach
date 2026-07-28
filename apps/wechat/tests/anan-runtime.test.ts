import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError } from "../miniprogram/api/client";
import type { ApiEnvelope } from "../miniprogram/domain/contracts";
import type {
  CompanionAskRequest,
  CompanionAskResponse,
} from "../miniprogram/api/generated/types.gen";
import {
  ANAN_INTENT_LABELS,
  resolveAnanVisual,
  type AnanIntent,
} from "../miniprogram/design-system/companion/anan-companion/runtime";
import { storeCompanionLaunchContext } from "../miniprogram/features/companion/context-store";
import { companionFailureView } from "../miniprogram/features/companion/presentation";
import { stableFingerprint } from "../miniprogram/platform/command-id";
import { operationAttemptStorageKey } from "../miniprogram/platform/operation-attempt-store";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("岸岸小程序运行时", () => {
  it("按语义请求选择正式资产、动效和无障碍名称", () => {
    for (const intent of Object.keys(ANAN_INTENT_LABELS) as AnanIntent[]) {
      const resolved = resolveAnanVisual({
        intent,
        size: "animated-120",
        label: "",
        animate: true,
        motion: "allowed",
        context: "post-submit",
        visualStage: "",
      });
      expect(resolved).not.toBeNull();
      expect(resolved?.label).toBe(ANAN_INTENT_LABELS[intent]);
      expect(resolved?.motionAllowed).toBe(true);
      expect(resolved?.renderIntentPackage).toBe(intent !== "companion");
    }
  });

  it("将减少动效、题目阅读、大尺寸和专用头像落实为确定性静态规则", () => {
    const reduced = resolveAnanVisual({
      intent: "thinking",
      size: "animated-120",
      label: "",
      animate: true,
      motion: "reduced",
      context: "plan",
      visualStage: "",
    });
    const reading = resolveAnanVisual({
      intent: "reminder",
      size: "medium",
      label: "",
      animate: true,
      motion: "allowed",
      context: "question-reading",
      visualStage: "",
    });
    const hero = resolveAnanVisual({
      intent: "companion",
      size: "hero-240",
      label: "阶段形象",
      animate: true,
      motion: "allowed",
      context: "report",
      visualStage: "stage-3",
    });
    const avatar = resolveAnanVisual({
      intent: "thinking",
      size: "avatar-32",
      label: "",
      animate: true,
      motion: "allowed",
      context: "question-reading",
      visualStage: "",
    });

    expect(reduced?.motionAllowed).toBe(false);
    expect(reading?.motionAllowed).toBe(false);
    expect(hero).toMatchObject({ renderGrowth: true, motionAllowed: false });
    expect(avatar?.staticSrc).toContain("avatar-32@3x.webp");
    expect(avatar?.renderIntentPackage).toBe(false);
  });

  it("拒绝未登记语义，不拿其他形象兜底", () => {
    expect(
      resolveAnanVisual({
        intent: "happy",
        size: "medium",
        label: "",
        animate: true,
        motion: "allowed",
        context: "home",
        visualStage: "",
      }),
    ).toBeNull();
    expect(
      resolveAnanVisual({
        intent: "companion",
        size: "medium",
        label: "",
        animate: true,
        motion: "allowed",
        context: "unknown",
        visualStage: "",
      }),
    ).toBeNull();
  });

  it("外部回答服务未获安全接入时说明未发送资料并保留问题编号", () => {
    const view = companionFailureView(
      new ApiClientError("刚才这一步没有成功，请稍后再试一次。", 503, "mp-anan-503"),
    );

    expect(view).toEqual({
      semanticState: "provider_pending",
      message:
        "岸岸的回答服务尚未完成安全接入，本次没有把你的学习资料发给外部模型。请保留问题编号，服务恢复后再试。",
      requestId: "mp-anan-503",
    });
  });

  it("保留 transport unknown attempt，并让同一问题重试复用 key 后完成", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "资料分析增长率怎么补？");

    const first = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    const firstKey = idempotencyKey(harness.requests[0]);
    respondTransportFailure(harness.requests[0]);
    await first;

    expect(attempt(harness, "user-a")?.state).toBe("unknown");
    expect(harness.page.data.askedQuestion).toBe("资料分析增长率怎么补？");
    expect(harness.page.data.errorMessage).toBe("本次问题结果待确认，再试会继续确认。");

    harness.page.restoreAskedQuestion();
    const second = harness.page.ask();
    await waitForRequestCount(harness.requests, 2);
    expect(idempotencyKey(harness.requests[1])).toBe(firstKey);
    respondCompanionSuccess(harness.requests[1]);
    await second;

    expect(attempt(harness, "user-a")?.state).toBe("succeeded");
  });

  it("重启后只恢复原问题与上下文，并用原 payload 和 key 由用户重试", async () => {
    const firstRuntime = await captureAnanPage();
    prepareAsk(firstRuntime.page, "原岗位上下文里的问题");
    firstRuntime.page.setData({
      learnerStateVersion: "learner-state-original",
      askRouteId: "pages/positions/detail",
      askWorkspace: "position",
      askObjectRefs: [{ type: "position", id: "position-1", version: "position-v1" }],
      askTaskMode: "retest",
      askTaskStage: "reviewing",
    });

    const firstRequest = firstRuntime.page.ask();
    await waitForRequestCount(firstRuntime.requests, 1);
    const originalPayload = companionRequestPayload(firstRuntime.requests[0]);
    const originalKey = idempotencyKey(firstRuntime.requests[0]);
    expect(originalPayload).toEqual({
      questionText: "原岗位上下文里的问题",
      context: {
        routeId: "pages/positions/detail",
        workspace: "position",
        objectRefs: [{ type: "position", id: "position-1", version: "position-v1" }],
        taskMode: "retest",
        taskStage: "reviewing",
        learnerStateVersion: "learner-state-original",
      },
    });
    expect(originalKey).toMatch(/^companion-/u);
    respondTransportFailure(firstRuntime.requests[0]);
    await firstRequest;

    vi.resetModules();
    const restarted = await captureAnanPage({
      appState: firstRuntime.appState,
      entropy: firstRuntime.entropy,
      storage: firstRuntime.storage,
    });
    expect(restarted.page).not.toBe(firstRuntime.page);
    restarted.page.onShow();

    expect(restarted.requests).toHaveLength(0);
    expect(restarted.page.data).toMatchObject({
      contextStatus: "ready",
      question: "原岗位上下文里的问题",
      errorMessage: "本次问题结果待确认，再试会继续确认。",
      semanticState: "provider_pending",
      stateLabel: "回答结果待安全确认",
      askRouteId: "pages/positions/detail",
      askWorkspace: "position",
      askObjectRefs: [{ type: "position", id: "position-1", version: "position-v1" }],
      askTaskMode: "retest",
      askTaskStage: "reviewing",
      learnerStateVersion: "learner-state-original",
    });

    restarted.page.setData({ learnerStateVersion: "learner-state-refreshed" });
    const retry = restarted.page.ask();
    await waitForRequestCount(restarted.requests, 1);
    expect(idempotencyKey(restarted.requests[0])).toBe(originalKey);
    expect(companionRequestPayload(restarted.requests[0])).toEqual(originalPayload);
    expect(JSON.stringify(companionRequestPayload(restarted.requests[0]))).not.toContain("token-a");
    expect(JSON.stringify(attemptValue(restarted, "user-a"))).not.toContain("token-a");
    respondCompanionSuccess(restarted.requests[0]);
    await retry;

    expect(attempt(restarted, "user-a")?.state).toBe("succeeded");
  });

  it("拒绝恢复带额外敏感字段的 persisted canonical input", async () => {
    const harness = await captureAnanPage();
    const canonicalInput = {
      questionText: "不应恢复的问题",
      context: {
        routeId: "pages/anan/index",
        workspace: "training",
        objectRefs: [],
        learnerStateVersion: "learner-state-v1",
      },
      accessToken: "token-a",
    };
    const now = Date.now();
    harness.storage.set(operationAttemptStorageKey("user-a", "companion-ask"), {
      schemaVersion: 2,
      ownerUserId: "user-a",
      scope: "companion-ask",
      canonicalInput,
      canonicalFingerprint: stableFingerprint(canonicalInput),
      idempotencyKey: "companion-poisoned-attempt",
      state: "unknown",
      createdAtMs: now,
      updatedAtMs: now,
      expiresAtMs: now + 60_000,
    });

    harness.page.onShow();

    expect(harness.requests).toHaveLength(0);
    expect(harness.page.data.question).toBe("");
    expect(harness.page.data.errorMessage).toBe("");
  });

  it("unfinished attempt 恢复时保留新 launch context，终结后下次 onShow 再消费", async () => {
    const firstRuntime = await captureAnanPage();
    prepareAsk(firstRuntime.page, "仍在等待结果的旧问题");

    const pendingRequest = firstRuntime.page.ask();
    await waitForRequestCount(firstRuntime.requests, 1);
    storeCompanionLaunchContext("user-a", {
      sourceLabel: "岗位详情",
      title: "新岗位上下文",
      summary: "应在旧 attempt 终结后再显示",
      suggestedQuestion: "新岗位是否适合我？",
      routeId: "pages/positions/detail",
      workspace: "position",
      objectRefs: [{ type: "position", id: "position-new", version: "position-v2" }],
    });

    vi.resetModules();
    const resumed = await captureAnanPage({
      appState: firstRuntime.appState,
      entropy: firstRuntime.entropy,
      storage: firstRuntime.storage,
    });
    resumed.page.onShow();

    expect(resumed.page.data).toMatchObject({
      question: "仍在等待结果的旧问题",
      semanticState: "provider_pending",
      askRouteId: "pages/anan/index",
      askWorkspace: "training",
    });
    expect(resumed.requests).toHaveLength(0);
    expect(launchContextStorageKeys(resumed)).toHaveLength(1);

    respondCompanionSuccess(firstRuntime.requests[0]);
    await pendingRequest;
    resumed.page.onShow();

    expect(resumed.page.data).toMatchObject({
      question: "新岗位是否适合我？",
      contextBannerVisible: true,
      contextTitle: "新岗位上下文",
      askRouteId: "pages/positions/detail",
      askWorkspace: "position",
      askObjectRefs: [{ type: "position", id: "position-new", version: "position-v2" }],
    });
    expect(launchContextStorageKeys(resumed)).toHaveLength(0);
  });

  it("将明确 HTTP 失败标记为 terminal", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "这个岗位能报吗？");

    const request = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    respondHttpFailure(harness.requests[0]);
    await request;

    expect(attempt(harness, "user-a")?.state).toBe("terminal");
  });

  it.each([408, 500, 503])("将 HTTP %s 保留为 unknown", async (statusCode) => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, `HTTP ${String(statusCode)} 待确认`);

    const request = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    respondHttpFailure(harness.requests[0], statusCode);
    await request;

    expect(attempt(harness, "user-a")?.state).toBe("unknown");
    expect(harness.page.data.errorMessage).toBe("本次问题结果待确认，再试会继续确认。");
  });

  it("仅在编辑后的 canonical input 改变时取消旧 attempt 并创建新 key", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "先问这个问题");

    const first = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    const firstKey = idempotencyKey(harness.requests[0]);
    respondTransportFailure(harness.requests[0]);
    await first;

    harness.page.restoreAskedQuestion();
    harness.page.setData({ question: "编辑后的新问题" });
    const second = harness.page.ask();
    await waitForRequestCount(harness.requests, 2);
    const secondKey = idempotencyKey(harness.requests[1]);
    respondTransportFailure(harness.requests[1]);
    await second;

    expect(secondKey).not.toBe(firstKey);
    expect(harness.writes.some((value) => attemptState(value) === "cancelled")).toBe(true);
  });

  it("问题不变但用户明确改变上下文时取消旧 attempt 并发送新 command", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "同一个问题");
    harness.page.setData({
      askRouteId: "pages/positions/detail",
      askWorkspace: "position",
      askObjectRefs: [{ type: "position", id: "position-1", version: "position-v1" }],
    });

    const first = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    const firstKey = idempotencyKey(harness.requests[0]);
    respondTransportFailure(harness.requests[0]);
    await first;

    harness.page.restoreAskedQuestion();
    harness.page.clearLaunchContext();
    const second = harness.page.ask();
    await waitForRequestCount(harness.requests, 2);
    const secondKey = idempotencyKey(harness.requests[1]);
    expect(secondKey).not.toBe(firstKey);
    expect(companionRequestPayload(harness.requests[1])).toMatchObject({
      questionText: "同一个问题",
      context: {
        routeId: "pages/anan/index",
        workspace: "training",
        objectRefs: [],
      },
    });
    respondTransportFailure(harness.requests[1]);
    await second;

    expect(harness.writes.some((value) => attemptState(value) === "cancelled")).toBe(true);
  });

  it("按当前账号隔离 companion attempt", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "同一个问题");

    const first = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    respondTransportFailure(harness.requests[0]);
    await first;
    const firstKey = attempt(harness, "user-a")?.idempotencyKey;

    harness.page.restoreAskedQuestion();
    harness.appState.globalData = {
      accessToken: "token-b",
      userId: "user-b",
      generation: "generation-b",
    };
    const second = harness.page.ask();
    await waitForRequestCount(harness.requests, 2);
    respondTransportFailure(harness.requests[1]);
    await second;

    expect(attempt(harness, "user-a")?.idempotencyKey).toBe(firstKey);
    expect(attempt(harness, "user-b")?.idempotencyKey).not.toBe(firstKey);
  });

  it("切换完整会话后丢弃旧请求成功 UI，但仍完成旧 owner attempt", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "A 用户的成功请求");

    const request = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    const bUi = switchToAccountB(harness);
    respondCompanionSuccess(harness.requests[0]);
    await request;

    expect(attempt(harness, "user-a")?.state).toBe("succeeded");
    expectResponseUi(harness.page, bUi);
  });

  it("同一用户的 token 或 generation 轮换后也丢弃旧请求 UI", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "会话轮换前的请求");

    const request = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    const rotatedUi = switchToRotatedSessionForUserA(harness);
    respondCompanionSuccess(harness.requests[0]);
    await request;

    expect(attempt(harness, "user-a")?.state).toBe("succeeded");
    expectResponseUi(harness.page, rotatedUi);
  });

  it("切换完整会话后丢弃旧请求 transport unknown UI，但仍保留旧 owner attempt", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "A 用户的超时请求");

    const request = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    const bUi = switchToAccountB(harness);
    respondTransportFailure(harness.requests[0]);
    await request;

    expect(attempt(harness, "user-a")?.state).toBe("unknown");
    expectResponseUi(harness.page, bUi);
  });

  it("切换完整会话后丢弃旧请求 HTTP 错误 UI，但仍终结旧 owner attempt", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "A 用户的 HTTP 失败请求");

    const request = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    const bUi = switchToAccountB(harness);
    respondHttpFailure(harness.requests[0]);
    await request;

    expect(attempt(harness, "user-a")?.state).toBe("terminal");
    expectResponseUi(harness.page, bUi);
  });

  it("locks repeated taps before attempt creation and sends one request", async () => {
    const harness = await captureAnanPage();
    prepareAsk(harness.page, "重复点击测试");

    const first = harness.page.ask();
    const second = harness.page.ask();
    await waitForRequestCount(harness.requests, 1);
    respondTransportFailure(harness.requests[0]);
    await Promise.all([first, second]);

    expect(harness.requests).toHaveLength(1);
  });
});

interface CapturedAnanPage {
  data: Record<string, unknown>;
  ask(this: AnanTestPage): Promise<void>;
  clearLaunchContext(this: AnanTestPage): void;
  onShow(this: AnanTestPage): void;
  restoreAskedQuestion(this: AnanTestPage): void;
}

interface AnanTestPage extends CapturedAnanPage {
  setData(patch: Record<string, unknown>): void;
}

interface AnanHarness {
  page: AnanTestPage;
  requests: Array<WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>>>;
  storage: Map<string, unknown>;
  writes: unknown[];
  entropy: { value: number };
  appState: {
    globalData: {
      accessToken: string;
      userId: string;
      generation: string;
    };
  };
}

async function captureAnanPage(
  existing: {
    appState?: AnanHarness["appState"];
    entropy?: AnanHarness["entropy"];
    storage?: AnanHarness["storage"];
  } = {},
): Promise<AnanHarness> {
  let definition: CapturedAnanPage | undefined;
  const requests: Array<WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>>> = [];
  const storage = existing.storage ?? new Map<string, unknown>();
  const writes: unknown[] = [];
  const appState =
    existing.appState ??
    ({
      globalData: {
        accessToken: "token-a",
        userId: "user-a",
        generation: "generation-a",
      },
    } satisfies AnanHarness["appState"]);
  const entropy = existing.entropy ?? { value: 0 };
  vi.stubGlobal("Page", (value: CapturedAnanPage) => {
    definition = value;
  });
  vi.stubGlobal("getApp", () => appState);
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => {
      storage.set(key, clone(value));
      writes.push(clone(value));
    },
    removeStorageSync: (key: string) => storage.delete(key),
    getRandomValues: () => {
      entropy.value += 1;
      return Promise.resolve({ randomValues: new Uint8Array(16).fill(entropy.value).buffer });
    },
    request: (options: WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>>) => {
      requests.push(options);
    },
    pageScrollTo: vi.fn(() => Promise.resolve()),
    reLaunch: vi.fn(() => Promise.resolve()),
  });

  await import("../miniprogram/pages/anan/index");
  if (!definition) throw new Error("anan page was not registered");
  const page: AnanTestPage = {
    ...definition,
    data: clone(definition.data),
    setData(patch: Record<string, unknown>) {
      Object.assign(this.data, patch);
    },
  };
  return { page, requests, storage, writes, entropy, appState };
}

function prepareAsk(page: AnanTestPage, question: string): void {
  page.setData({
    contextStatus: "ready",
    learnerStateVersion: "learner-state-v1",
    question,
    askedQuestion: "",
    submitting: false,
    askRouteId: "pages/anan/index",
    askWorkspace: "training",
    askObjectRefs: [],
    askTaskMode: undefined,
    askTaskStage: undefined,
  });
}

function switchToAccountB(harness: AnanHarness): Record<string, unknown> {
  harness.appState.globalData = {
    accessToken: "token-b",
    userId: "user-b",
    generation: "generation-b",
  };
  const bUi = {
    submitting: false,
    semanticState: "ready",
    stateLabel: "B 用户页面",
    companionIntent: "companion",
    answer: "B 用户回答",
    errorMessage: "B 用户错误",
    requestId: "b-request-id",
  };
  harness.page.setData(bUi);
  return bUi;
}

function switchToRotatedSessionForUserA(harness: AnanHarness): Record<string, unknown> {
  harness.appState.globalData = {
    accessToken: "token-a-rotated",
    userId: "user-a",
    generation: "generation-a-rotated",
  };
  const rotatedUi = {
    submitting: false,
    semanticState: "ready",
    stateLabel: "A 用户的新会话页面",
    companionIntent: "companion",
    answer: "新会话回答",
    errorMessage: "新会话错误",
    requestId: "rotated-request-id",
  };
  harness.page.setData(rotatedUi);
  return rotatedUi;
}

function expectResponseUi(page: AnanTestPage, expected: Record<string, unknown>): void {
  expect(page.data).toMatchObject(expected);
}

function attempt(
  harness: AnanHarness,
  ownerUserId: string,
): { idempotencyKey: string; state: string } | null {
  const value = harness.storage.get(operationAttemptStorageKey(ownerUserId, "companion-ask"));
  if (!value || typeof value !== "object") return null;
  const candidate = value as { idempotencyKey?: unknown; state?: unknown };
  if (typeof candidate.idempotencyKey !== "string" || typeof candidate.state !== "string")
    return null;
  return { idempotencyKey: candidate.idempotencyKey, state: candidate.state };
}

function attemptValue(harness: AnanHarness, ownerUserId: string): unknown {
  return harness.storage.get(operationAttemptStorageKey(ownerUserId, "companion-ask"));
}

function launchContextStorageKeys(harness: AnanHarness): string[] {
  return [...harness.storage.keys()].filter((key) =>
    key.startsWith("gongkao.companion.launch-context.v1."),
  );
}

function attemptState(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const state = (value as { state?: unknown }).state;
  return typeof state === "string" ? state : undefined;
}

function idempotencyKey(
  options: WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>> | undefined,
): string | undefined {
  const header: unknown = options?.header;
  if (!header || typeof header !== "object") return undefined;
  const key = (header as Record<string, unknown>)["Idempotency-Key"];
  return typeof key === "string" ? key : undefined;
}

function companionRequestPayload(
  options: WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>> | undefined,
): CompanionAskRequest | undefined {
  return options?.data as CompanionAskRequest | undefined;
}

function respondTransportFailure(
  options: WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>> | undefined,
): void {
  options?.fail?.({
    errMsg: "request:fail timeout",
    errno: -1,
    exception: { reasons: [], retryCount: 0 },
    useHttpDNS: false,
  });
}

function respondHttpFailure(
  options: WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>> | undefined,
  statusCode = 422,
): void {
  options?.success?.({
    data: {
      code: "companion_rejected",
      message: "本次提问已被拒绝。",
      requestId: "companion-http-failure",
    } as unknown as ApiEnvelope<CompanionAskResponse>,
    statusCode,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function respondCompanionSuccess(
  options: WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>> | undefined,
): void {
  options?.success?.({
    data: {
      data: companionResponse(),
      requestId: "companion-success",
      generatedAt: "2026-07-28T10:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function companionResponse(): CompanionAskResponse {
  return {
    allowedActions: [],
    answer: "建议先完成一道资料分析题。",
    answerId: "answer-1",
    answerType: "learning_explanation",
    citations: [],
    companionIntent: "companion",
    confidence: "high",
    knowledgeFreshness: "current",
    limitations: [],
    policyDecision: "allowed",
    quota: {
      accessMode: "entitlement_quota",
      date: "2026-07-28",
      feature: "companion",
      limit: 3,
      used: 1,
    },
    respondingAgent: "tutor",
    scenarioKey: "training",
    semanticState: "ready",
  };
}

async function waitForRequestCount(
  requests: Array<WechatMiniprogram.RequestOption<ApiEnvelope<CompanionAskResponse>>>,
  expected: number,
): Promise<void> {
  for (let turn = 0; turn < 30; turn += 1) {
    if (requests.length >= expected) return;
    await Promise.resolve();
  }
  throw new Error(`expected ${String(expected)} requests, received ${String(requests.length)}`);
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
