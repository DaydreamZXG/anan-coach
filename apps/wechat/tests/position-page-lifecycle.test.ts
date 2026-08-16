import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  PositionDiscoveryDetail,
  PositionDiscoveryItem,
  PositionEligibilitySnapshot,
  PositionWorkspaceResponse,
  QualificationGapSummary,
} from "../miniprogram/api/generated/types.gen";
import { positionStoragePrefix } from "../miniprogram/features/positions/state-store";

interface CapturedPageDefinition {
  data: Record<string, unknown>;
  onLoad(this: TestPage, options: Record<string, string | undefined>): void;
  onShow?(this: TestPage): void;
  onUnload(this: TestPage): void;
  goBack?(this: TestPage): void;
  openPreparation?(this: TestPage): void;
}

interface TestPage extends CapturedPageDefinition {
  writes: Array<Record<string, unknown>>;
  setData(patch: Record<string, unknown>): void;
}

interface LifecycleScenario {
  name: string;
  load: () => Promise<unknown>;
  options: Record<string, string>;
  requestsPerLoad: number;
}

let captured: CapturedPageDefinition | null;
let requests: WechatMiniprogram.RequestOption[];
let storage: Map<string, unknown>;
let reLaunch: ReturnType<typeof vi.fn>;
let navigateBack: ReturnType<typeof vi.fn>;
let redirectTo: ReturnType<typeof vi.fn>;
let switchTab: ReturnType<typeof vi.fn>;

beforeEach(() => {
  captured = null;
  requests = [];
  storage = new Map<string, unknown>();
  reLaunch = vi.fn();
  navigateBack = vi.fn();
  redirectTo = vi.fn();
  switchTab = vi.fn();
  vi.stubGlobal("Page", (definition: CapturedPageDefinition) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "token-a", userId: "user-a" },
  }));
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({
      keys: [...storage.keys()],
      currentSize: storage.size,
      limitSize: 10_240,
    }),
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).buffer }),
    request: (options: WechatMiniprogram.RequestOption) => {
      requests.push(options);
    },
    navigateBack,
    navigateTo: vi.fn(),
    reLaunch,
    redirectTo,
    showToast: vi.fn(),
    switchTab,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

const lifecycleScenarios: LifecycleScenario[] = [
  {
    name: "workspace",
    load: () => import("../miniprogram/pages/positions/index"),
    options: {},
    requestsPerLoad: 2,
  },
  {
    name: "search",
    load: () => import("../miniprogram/pages/positions/search/index"),
    options: { q: "税务" },
    requestsPerLoad: 1,
  },
  {
    name: "detail",
    load: () => import("../miniprogram/pages/positions/detail/index"),
    options: { positionId: "position-1" },
    requestsPerLoad: 2,
  },
  {
    name: "compare",
    load: () => import("../miniprogram/pages/positions/compare/index"),
    options: { ids: "position-1,position-2" },
    requestsPerLoad: 3,
  },
  {
    name: "goal",
    load: () => import("../miniprogram/pages/positions/goal/index"),
    options: { positionId: "position-1" },
    requestsPerLoad: 1,
  },
];

describe("position page lifecycle ownership", () => {
  it.each(lifecycleScenarios)(
    "$name skips the first onShow refresh, refreshes on return, and never writes after unload",
    async ({ load, options, requestsPerLoad }) => {
      const definition = await capturePage(load);
      const page = instantiatePage(definition);

      page.onLoad.call(page, options);
      expect(requests).toHaveLength(requestsPerLoad);
      page.onShow?.call(page);
      expect(requests).toHaveLength(requestsPerLoad);
      page.onShow?.call(page);
      expect(requests).toHaveLength(requestsPerLoad * 2);

      page.onUnload.call(page);
      const stateAfterUnload = clone(page.data);
      const writeCountAfterUnload = page.writes.length;
      requests.forEach((request, index) => {
        resolveEnvelope(request, {}, `request-${String(index)}`);
      });
      await flushPromises();

      expect(page.data).toEqual(stateAfterUnload);
      expect(page.writes).toHaveLength(writeCountAfterUnload);
      expect(requests).toHaveLength(requestsPerLoad * 2);
    },
  );

  it("keeps another active search instance isolated when the first instance unloads", async () => {
    const definition = await capturePage(
      () => import("../miniprogram/pages/positions/search/index"),
    );
    const pageA = instantiatePage(definition);
    const pageB = instantiatePage(definition);
    pageA.onLoad.call(pageA, { q: "税务" });
    pageB.onLoad.call(pageB, { q: "海关" });
    expect(requests).toHaveLength(2);

    pageA.onUnload.call(pageA);
    const stateAfterUnload = clone(pageA.data);
    resolveDiscovery(requiredAt(requests, 0), "position-a", "税务岗");
    resolveDiscovery(requiredAt(requests, 1), "position-b", "海关岗");
    await flushPromises();

    expect(pageA.data).toEqual(stateAfterUnload);
    expect(pageB.data.state).toMatchObject({
      status: "ready",
      items: [{ positionId: "position-b", name: "海关岗" }],
    });
    pageB.onUnload.call(pageB);
  });

  it("does not let a slow response from a replaced search route overwrite the new route", async () => {
    const definition = await capturePage(
      () => import("../miniprogram/pages/positions/search/index"),
    );
    const page = instantiatePage(definition);
    page.onLoad.call(page, { q: "旧筛选" });
    page.onLoad.call(page, { q: "新筛选" });
    expect(requests).toHaveLength(2);

    resolveDiscovery(requiredAt(requests, 1), "position-new", "新岗位");
    await flushPromises();
    resolveDiscovery(requiredAt(requests, 0), "position-old", "旧岗位");
    await flushPromises();

    expect(page.data.state).toMatchObject({
      status: "ready",
      appliedFilters: { query: "新筛选" },
      items: [{ positionId: "position-new", name: "新岗位" }],
    });
    page.onUnload.call(page);
  });

  it("keeps rendered search results and cursor state while the return refresh runs", async () => {
    const definition = await capturePage(
      () => import("../miniprogram/pages/positions/search/index"),
    );
    const page = instantiatePage(definition);
    page.onLoad.call(page, { q: "税务" });
    resolveDiscovery(requiredAt(requests, 0), "position-a", "税务岗");
    await flushPromises();

    page.onShow?.call(page);
    page.onShow?.call(page);
    expect(requests).toHaveLength(2);
    expect(page.data.state).toMatchObject({
      status: "ready",
      items: [{ positionId: "position-a", name: "税务岗" }],
      delivery: { processing: true, processingLabel: "正在更新岗位结果" },
    });

    resolveDiscovery(requiredAt(requests, 1), "position-a", "税务岗（更新）");
    await flushPromises();
    expect(page.data.state).toMatchObject({
      status: "ready",
      items: [{ positionId: "position-a", name: "税务岗（更新）" }],
      delivery: { processing: false },
    });
    page.onUnload.call(page);
  });

  it("keeps a rendered detail on return and replaces it in place after the silent reload", async () => {
    const definition = await capturePage(
      () => import("../miniprogram/pages/positions/detail/index"),
    );
    const page = instantiatePage(definition);
    page.onLoad.call(page, { positionId: "position-1" });
    resolveEnvelope(
      requestEndingWith("/api/v2/positions/position-1"),
      detail("position-1"),
      "request-detail",
    );
    resolveEnvelope(
      requestEndingWith("/api/v2/positions/position-1/eligibility"),
      eligibility("position-1"),
      "request-eligibility",
    );
    await flushPromises();
    expect(page.data.state).toMatchObject({ status: "ready", snapshotId: "snapshot-1" });

    page.onShow?.call(page);
    page.onShow?.call(page);
    expect(requests).toHaveLength(4);
    expect(page.data.state).toMatchObject({ status: "ready", snapshotId: "snapshot-1" });

    resolveEnvelope(
      lastRequestEndingWith("/api/v2/positions/position-1"),
      detail("position-1"),
      "request-detail-2",
    );
    resolveEnvelope(
      lastRequestEndingWith("/api/v2/positions/position-1/eligibility"),
      eligibility("position-1"),
      "request-eligibility-2",
    );
    await flushPromises();
    expect(page.data.state).toMatchObject({
      status: "ready",
      snapshotId: "snapshot-1",
      delivery: { requestId: "request-eligibility-2" },
    });
    page.onUnload.call(page);
  });

  it("synchronizes comparison selections made on another page before refreshing the workspace", async () => {
    const definition = await capturePage(() => import("../miniprogram/pages/positions/index"));
    const page = instantiatePage(definition);
    page.onLoad.call(page, {});
    page.onShow?.call(page);
    expect(requests).toHaveLength(2);

    storage.set(`${positionStoragePrefix()}.user-a.comparison`, ["position-1", "position-2"]);
    page.onShow?.call(page);
    expect(requests).toHaveLength(4);
    const refreshedWorkspace = workspace(["position-1", "position-2"]);
    const pendingItem = refreshedWorkspace.items[1];
    if (!pendingItem) throw new Error("workspace fixture is missing its second item");
    pendingItem.eligibilityStatus = "needs_confirmation";
    resolveEnvelope(requiredAt(requests, 2), refreshedWorkspace, "request-workspace-new");
    resolveEnvelope(requiredAt(requests, 3), qualificationGaps(), "request-gaps-new");
    await flushPromises();

    expect(page.data.workspace).toMatchObject({
      status: "ready",
      eligibleCount: 1,
      selectedIds: ["position-1", "position-2"],
      items: [
        { positionId: "position-1", comparisonSelected: true },
        { positionId: "position-2", comparisonSelected: true },
      ],
    });

    resolveEnvelope(requiredAt(requests, 0), workspace(["position-old"]), "request-workspace-old");
    resolveEnvelope(requiredAt(requests, 1), qualificationGaps(), "request-gaps-old");
    await flushPromises();
    expect(page.data.workspace).toMatchObject({
      selectedIds: ["position-1", "position-2"],
      items: [{ positionId: "position-1" }, { positionId: "position-2" }],
    });
    page.onUnload.call(page);
  });
});

describe("position route lifecycle contracts", () => {
  it.each([
    {
      name: "workspace",
      load: () => import("../miniprogram/pages/positions/index"),
      options: {},
      expected: "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Findex",
    },
    {
      name: "search",
      load: () => import("../miniprogram/pages/positions/search/index"),
      options: { q: "税务" },
      expected:
        "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Fsearch%2Findex%3Fq%3D%25E7%25A8%258E%25E5%258A%25A1",
    },
    {
      name: "detail",
      load: () => import("../miniprogram/pages/positions/detail/index"),
      options: { positionId: "position/1" },
      expected:
        "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Fdetail%2Findex%3FpositionId%3Dposition%252F1",
    },
    {
      name: "compare",
      load: () => import("../miniprogram/pages/positions/compare/index"),
      options: { ids: "position-1,position-2" },
      expected:
        "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Fcompare%2Findex%3Fids%3Dposition-1%252Cposition-2",
    },
    {
      name: "goal",
      load: () => import("../miniprogram/pages/positions/goal/index"),
      options: { positionId: "position-1" },
      expected:
        "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Fgoal%2Findex%3FpositionId%3Dposition-1",
    },
    {
      name: "workspace score sheet",
      load: () => import("../miniprogram/pages/positions/index"),
      options: { score: "1" },
      expected: "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Findex%3Fscore%3D1",
    },
    {
      name: "briefing",
      load: () => import("../miniprogram/pages/positions/briefing/index"),
      options: { positionId: "position-1" },
      expected:
        "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Fbriefing%2Findex%3FpositionId%3Dposition-1",
    },
    {
      name: "briefing without an id",
      load: () => import("../miniprogram/pages/positions/briefing/index"),
      options: {},
      expected: "/pages/login/index",
    },
  ])(
    "preserves the authenticated $name destination across login",
    async ({ load, options, expected }) => {
      vi.stubGlobal("getApp", () => ({ globalData: {} }));
      const definition = await capturePage(load);
      const page = instantiatePage(definition);

      page.onLoad.call(page, options);

      expect(requests).toHaveLength(0);
      expect(reLaunch).toHaveBeenCalledWith({ url: expected });
    },
  );

  it("hydrates a direct detail deep link from encoded formal endpoints", async () => {
    const definition = await capturePage(
      () => import("../miniprogram/pages/positions/detail/index"),
    );
    const page = instantiatePage(definition);
    page.onLoad.call(page, { positionId: "position/1" });
    expect(requests).toHaveLength(2);
    expect(requests.map((request) => request.url)).toEqual(
      expect.arrayContaining([
        expect.stringContaining("/api/v2/positions/position%2F1"),
        expect.stringContaining("/api/v2/positions/position%2F1/eligibility"),
      ]),
    );

    const detailRequest = requestEndingWith("/api/v2/positions/position%2F1");
    const eligibilityRequest = requestEndingWith("/api/v2/positions/position%2F1/eligibility");
    resolveEnvelope(detailRequest, detail("position/1"), "request-detail");
    resolveEnvelope(eligibilityRequest, eligibility("position/1"), "request-eligibility");
    await flushPromises();

    expect(page.data.state).toMatchObject({
      status: "ready",
      positionId: "position/1",
      summary: { positionId: "position/1", name: "深链岗位" },
      snapshotId: "snapshot-1",
    });
    page.onUnload.call(page);
  });

  it.each([
    "position-1,position-1",
    "position-1,position-2,position-3,position-4",
    "position-1,,position-2",
  ])("rejects malformed comparison IDs without issuing requests: %s", async (ids) => {
    const definition = await capturePage(
      () => import("../miniprogram/pages/positions/compare/index"),
    );
    const page = instantiatePage(definition);

    page.onLoad.call(page, { ids });
    page.onShow?.call(page);

    expect(requests).toHaveLength(0);
    expect(page.data.state).toMatchObject({
      status: "error",
      error: { retryable: false },
    });
    page.onUnload.call(page);
  });

  it("decodes the comparison delimiter passed by the WeChat route runtime", async () => {
    const definition = await capturePage(
      () => import("../miniprogram/pages/positions/compare/index"),
    );
    const page = instantiatePage(definition);

    page.onLoad.call(page, { ids: "position-1%2Cposition-2" });

    expect(requests).toHaveLength(3);
    const comparisonRequest = requestEndingWith("/api/v2/positions/compare");
    expect(comparisonRequest.data).toEqual({ positionIds: ["position-1", "position-2"] });
    page.onUnload.call(page);
  });

  it.each([
    ["detail", () => import("../miniprogram/pages/positions/detail/index")],
    ["goal", () => import("../miniprogram/pages/positions/goal/index")],
  ] as const)("puts a missing-ID %s deep link into a terminal route error", async (_name, load) => {
    const definition = await capturePage(load);
    const page = instantiatePage(definition);

    page.onLoad.call(page, {});

    expect(requests).toHaveLength(0);
    expect(page.data.state).toMatchObject({
      status: "error",
      error: { retryable: false },
    });
    page.onUnload.call(page);
  });
});

describe("position page back navigation fallbacks", () => {
  it.each([
    {
      name: "workspace",
      load: () => import("../miniprogram/pages/positions/index"),
      fallbackMethod: "switchTab" as const,
      fallbackUrl: "/pages/today/index",
    },
    {
      name: "search",
      load: () => import("../miniprogram/pages/positions/search/index"),
      fallbackMethod: "redirectTo" as const,
      fallbackUrl: "/pages/positions/index",
    },
    {
      name: "detail",
      load: () => import("../miniprogram/pages/positions/detail/index"),
      fallbackMethod: "redirectTo" as const,
      fallbackUrl: "/pages/positions/index",
    },
    {
      name: "compare",
      load: () => import("../miniprogram/pages/positions/compare/index"),
      fallbackMethod: "redirectTo" as const,
      fallbackUrl: "/pages/positions/index",
    },
    {
      name: "goal",
      load: () => import("../miniprogram/pages/positions/goal/index"),
      fallbackMethod: "redirectTo" as const,
      fallbackUrl: "/pages/positions/index",
    },
  ])(
    "$name pops the stack when history exists and reroutes when it is the entry page",
    async ({ load, fallbackMethod, fallbackUrl }) => {
      const definition = await capturePage(load);
      const page = instantiatePage(definition);
      const fallback = fallbackMethod === "switchTab" ? switchTab : redirectTo;

      vi.stubGlobal("getCurrentPages", () => [{}, {}]);
      page.goBack?.call(page);
      expect(navigateBack).toHaveBeenCalledTimes(1);
      expect(fallback).not.toHaveBeenCalled();

      vi.stubGlobal("getCurrentPages", () => [{}]);
      page.goBack?.call(page);
      expect(navigateBack).toHaveBeenCalledTimes(1);
      expect(fallback).toHaveBeenCalledWith({ url: fallbackUrl });
    },
  );
});

describe("position release lifecycle actions", () => {
  it.each(lifecycleScenarios)(
    "$name keeps a real preparation path when no recruitment window is open",
    async ({ load }) => {
      const definition = await capturePage(load);
      const page = instantiatePage(definition);

      page.openPreparation?.call(page);

      expect(switchTab).toHaveBeenCalledWith({ url: "/pages/ability/index" });
    },
  );
});

async function capturePage(load: () => Promise<unknown>): Promise<CapturedPageDefinition> {
  await load();
  if (!captured) throw new Error("Page definition was not captured");
  return captured;
}

function instantiatePage(definition: CapturedPageDefinition): TestPage {
  return {
    ...definition,
    data: clone(definition.data),
    writes: [],
    setData(patch: Record<string, unknown>) {
      this.writes.push(clone(patch));
      Object.assign(this.data, patch);
    },
  };
}

function resolveDiscovery(
  request: WechatMiniprogram.RequestOption,
  positionId: string,
  name: string,
): void {
  resolveEnvelope(request, discoveryItem(positionId, name), `request-${positionId}`, true);
}

function resolveEnvelope(
  request: WechatMiniprogram.RequestOption,
  data: unknown,
  requestId: string,
  discoveryResponse = false,
): void {
  request.success?.({
    data: {
      data: discoveryResponse
        ? {
            examYear: 2026,
            learnerProfileVersion: 2,
            ruleSetVersion: "rules-v1",
            positionDataVersion: "positions-v1",
            items: [data],
            nextCursor: null,
            hasMore: false,
          }
        : data,
      requestId,
      generatedAt: "2026-07-13T00:00:00Z",
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

function discoveryItem(positionId: string, name: string): PositionDiscoveryItem {
  return {
    positionId,
    positionCode: "3001",
    name,
    departmentFull: "测试单位",
    systemCode: "customs",
    region: "江西",
    city: "南昌",
    hasExtraExam: false,
    headcount: 2,
    eligibilityStatus: "eligible",
    metCount: 3,
    pendingCount: 0,
    notMetCount: 0,
    notMetRequirementIds: [],
    pendingRequirementIds: [],
    reasonCodes: [],
    targetScore: {
      lowerBound: 126,
      recommended: 129,
      upperBound: 132,
      sampleCount: 6,
      basisLevel: "direct",
      evidenceLevel: "direct",
      generatedAt: "2026-07-01T00:00:00Z",
      validUntil: "2027-07-01T00:00:00Z",
    },
    isPrimaryGoal: false,
  };
}

function detail(positionId: string): PositionDiscoveryDetail {
  return {
    ...discoveryItem(positionId, "深链岗位"),
    examYear: 2026,
    learnerProfileVersion: 2,
    ruleSetVersion: "rules-v1",
    positionDataVersion: "positions-v1",
    paperType: "administrativeLaw",
    abilityEstimate: {
      readiness: "unavailable",
      estimate: null,
      missingComponents: ["xingce", "shenlun"],
    },
    fit: {
      status: "evidence_unavailable",
      band: null,
      evidenceStatus: "unavailable",
      evidenceReasonCodes: ["ability_estimate_unavailable"],
      scoreGap: null,
      requirementsBurden: {
        additionalExamRequired: false,
        hardConditionNotMetCount: 0,
        hardConditionNotMetRequirementIds: [],
        pendingRequirementCount: 0,
        pendingRequirementIds: [],
      },
    },
  };
}

function eligibility(positionId: string): PositionEligibilitySnapshot {
  return {
    snapshotId: "snapshot-1",
    positionId,
    learnerProfileVersion: 2,
    facts: [],
    evaluation: {
      status: "eligible",
      checks: [],
      missingFactKeys: [],
      ruleSetVersion: "rules-v1",
      positionDataVersion: "positions-v1",
      evaluatedAt: "2026-07-13T00:00:00Z",
    },
  };
}

function workspace(positionIds: string[]): PositionWorkspaceResponse {
  return {
    abilityEstimate: {
      readiness: "unavailable",
      estimate: null,
      missingComponents: ["xingce", "shenlun"],
    },
    examYear: 2026,
    hasMore: false,
    items: positionIds.map((positionId) => ({
      ...discoveryItem(positionId, `岗位 ${positionId}`),
      fit: {
        status: "evidence_unavailable",
        band: null,
        evidenceStatus: "unavailable",
        evidenceReasonCodes: ["ability_estimate_unavailable"],
        scoreGap: null,
        requirementsBurden: {
          additionalExamRequired: false,
          hardConditionNotMetCount: 0,
          hardConditionNotMetRequirementIds: [],
          pendingRequirementCount: 0,
          pendingRequirementIds: [],
        },
      },
    })),
    learnerProfileVersion: 2,
    nextCursor: null,
    positionDataVersion: "positions-v1",
    ruleSetVersion: "rules-v1",
  };
}

function qualificationGaps(): QualificationGapSummary {
  return {
    complete: true,
    examYear: 2026,
    gaps: [],
    learnerProfileVersion: 2,
    positionDataVersion: "positions-v1",
    ruleSetVersion: "rules-v1",
    totalAffectedPositions: 0,
  };
}

function requestEndingWith(path: string): WechatMiniprogram.RequestOption {
  const request = requests.find((candidate) => candidate.url.endsWith(path));
  if (!request) throw new Error(`missing request ending with ${path}`);
  return request;
}

function lastRequestEndingWith(path: string): WechatMiniprogram.RequestOption {
  const request = [...requests].reverse().find((candidate) => candidate.url.endsWith(path));
  if (!request) throw new Error(`missing request ending with ${path}`);
  return request;
}

function requiredAt<T>(values: T[], index: number): T {
  const value = values[index];
  if (value === undefined) throw new Error(`missing value at ${String(index)}`);
  return value;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}
