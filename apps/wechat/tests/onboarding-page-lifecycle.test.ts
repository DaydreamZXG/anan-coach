import { afterEach, describe, expect, it, vi } from "vitest";

interface CapturedPageDefinition {
  data: Record<string, unknown>;
  onLoad(this: TestPage): void;
  onShow(this: TestPage): void;
  onUnload(this: TestPage): void;
}

interface TestPage extends CapturedPageDefinition {
  setData(patch: Record<string, unknown>): void;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("qualification onboarding page lifecycle", () => {
  it("does not let a profile response mutate an unloaded page", async () => {
    const harness = await pageHarness();
    const page = instantiatePage(harness.definition);
    page.onLoad.call(page);
    resolveProgress(requiredAt(harness.requests, 0), "qualification", 2);
    await flushPromises();
    expect(harness.requests).toHaveLength(3);

    page.onUnload.call(page);
    const stateAfterUnload = clone(page.data);
    resolveProfile(requiredAt(harness.requests, 2), "法学", 2);
    await flushPromises();

    expect(page.data).toEqual(stateAfterUnload);
  });

  it("keeps a slow old-account profile from covering the new account", async () => {
    const harness = await pageHarness();
    const page = instantiatePage(harness.definition);
    page.onLoad.call(page);
    resolveProgress(requiredAt(harness.requests, 0), "qualification", 2);
    await flushPromises();
    expect(harness.requests).toHaveLength(3);

    harness.globalData.accessToken = "token-b";
    harness.globalData.userId = "user-b";
    page.onShow.call(page);
    expect(harness.requests).toHaveLength(5);
    expect(requiredAt(harness.requests, 3).header).toMatchObject({
      Authorization: "Bearer token-b",
    });
    expect(requiredAt(harness.requests, 4).header).toMatchObject({
      Authorization: "Bearer token-b",
    });

    resolveProgress(requiredAt(harness.requests, 3), "qualification", 8);
    await flushPromises();
    expect(harness.requests).toHaveLength(6);
    resolveProfile(requiredAt(harness.requests, 5), "新闻学", 9);
    await flushPromises();
    expect(page.data).toMatchObject({ major: "新闻学", profileVersion: 9 });

    resolveProfile(requiredAt(harness.requests, 2), "法学", 2);
    await flushPromises();
    expect(page.data).toMatchObject({ major: "新闻学", profileVersion: 9 });
    page.onUnload.call(page);
  });
});

async function pageHarness() {
  let captured: unknown;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const globalData: { accessToken?: string; userId?: string } = {
    accessToken: "token-a",
    userId: "user-a",
  };
  vi.stubGlobal("Page", (definition: unknown) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("wx", {
    getStorageSync: () => "",
    setStorageSync: vi.fn(),
    removeStorageSync: vi.fn(),
    getStorageInfoSync: () => ({ keys: [], currentSize: 0, limitSize: 10_240 }),
    request: (options: WechatMiniprogram.RequestOption) => {
      requests.push(options);
    },
    reLaunch: vi.fn(),
    redirectTo: vi.fn(),
    switchTab: vi.fn(),
    getRandomValues: vi.fn(),
  });

  await import("../miniprogram/pages/onboarding/index");
  return {
    definition: captured as CapturedPageDefinition,
    requests,
    globalData,
  };
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

function resolveProgress(
  request: WechatMiniprogram.RequestOption,
  nextStep: "qualification" | "study_preference" | "result" | "workspace",
  progressVersion: number,
): void {
  resolveRequest(request, {
    progressVersion,
    qualificationReady: nextStep !== "qualification",
    qualificationProfileVersion: nextStep === "qualification" ? null : 2,
    studyPreferenceReady: nextStep === "result" || nextStep === "workspace",
    studyPreferenceVersion: nextStep === "result" || nextStep === "workspace" ? 1 : null,
    resultReady: nextStep === "result" || nextStep === "workspace",
    resultAcknowledged: nextStep === "workspace",
    nextStep,
    completed: nextStep === "workspace",
    updatedAt: "2026-07-14T00:00:00Z",
    completedAt: nextStep === "workspace" ? "2026-07-14T00:00:00Z" : null,
  });
}

function resolveProfile(
  request: WechatMiniprogram.RequestOption,
  major: string,
  profileVersion: number,
): void {
  const facts = [
    { key: "education", value: "本科" },
    { key: "major", value: major },
    { key: "graduation_year", value: 2026 },
    { key: "fresh_graduate", value: true },
    { key: "political_status", value: "群众" },
    { key: "grassroots_years", value: 0 },
  ].map((fact, index) => ({
    ...fact,
    factId: `fact-${String(index)}`,
    verificationStatus: "reported",
    evidence: [],
    effectiveAt: "2026-07-14T00:00:00Z",
    supersedesFactId: null,
  }));
  resolveRequest(request, { profileVersion, facts });
}

function resolveRequest(request: WechatMiniprogram.RequestOption, data: unknown): void {
  request.success?.({
    data: {
      data,
      requestId: "request-test",
      generatedAt: "2026-07-14T00:00:00Z",
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

function requiredAt<T>(values: T[], index: number): T {
  const value = values[index];
  if (value === undefined) throw new Error(`missing value at ${String(index)}`);
  return value;
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
