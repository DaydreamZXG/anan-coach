import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QualificationApi } from "../miniprogram/api/qualification";
import type {
  PositionDiscoveryItem,
  PositionDiscoveryDetail,
  PositionDiscoveryResponse,
  PositionEligibilitySnapshot,
  PositionFitAssessment,
  PositionWorkspaceItem,
  PositionWorkspaceResponse,
  PrimaryGoalConfirmResponse,
  PrimaryGoalPreview,
} from "../miniprogram/api/generated/types.gen";
import { ApiClientError } from "../miniprogram/api/client";
import type { ApiEnvelope } from "../miniprogram/domain/contracts";
import {
  planInvalidationViews,
  targetValidityView,
} from "../miniprogram/features/positions/contracts";
import { PositionDiscoveryController } from "../miniprogram/pages/positions/features/discovery-controller";
import { PositionCompareController } from "../miniprogram/pages/positions/features/compare-controller";
import { PositionDetailController } from "../miniprogram/pages/positions/features/detail-controller";
import {
  positionOperationError,
  shouldRetainPositionCommand,
} from "../miniprogram/pages/positions/features/error-state";
import { PositionPageRuntimeRegistry } from "../miniprogram/pages/positions/features/runtime";
import { PositionGoalController } from "../miniprogram/pages/positions/features/goal-controller";
import { PositionWorkspaceController } from "../miniprogram/pages/positions/features/workspace-controller";
import { positionFitView } from "../miniprogram/pages/positions/features/presentation";
import {
  clearPositionStateForUser,
  PositionStateStore,
  positionStoragePrefix,
} from "../miniprogram/features/positions/state-store";

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

let storage: Map<string, unknown>;
let entropyCallCount: number;

beforeEach(() => {
  storage = new Map<string, unknown>();
  entropyCallCount = 0;
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({
      keys: [...storage.keys()],
      currentSize: storage.size,
      limitSize: 10_240,
    }),
    getRandomValues: () => {
      entropyCallCount += 1;
      const values = new Uint8Array(16);
      values[0] = entropyCallCount;
      return Promise.resolve({ randomValues: values.buffer });
    },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("position discovery controller", () => {
  it("turns missing score evidence into an actionable gap instead of an opaque trust warning", () => {
    const view = positionFitView(evidenceUnavailableFit());

    expect(view.statusLabel).toBe("待补成绩");
    expect(view.statusDescription).toContain("录入行测和申论成绩");
    expect(view.evidenceStatusLabel).toBe("待录入双科成绩");
    expect(view.scoreGapRangeLabel).toBe("录入双科成绩后计算");
    expect(view.evidenceReasonLabel).toContain("还没有录入行测和申论成绩");
  });

  it("ignores an older fast-search response that arrives after the newest query", async () => {
    const requests: Array<Deferred<ApiEnvelope<PositionDiscoveryResponse>>> = [];
    const api = {
      discoverPositions: vi.fn(() => {
        const request = deferred<ApiEnvelope<PositionDiscoveryResponse>>();
        requests.push(request);
        return request.promise;
      }),
    } satisfies Pick<QualificationApi, "discoverPositions">;
    const controller = new PositionDiscoveryController({
      api,
      store: new PositionStateStore("user-a"),
      onChange: vi.fn(),
    });

    controller.setFilters({ query: "税务" });
    const first = controller.search();
    controller.setFilters({ query: "海关" });
    const second = controller.search();
    requests[1]?.resolve(discoveryEnvelope([position({ positionId: "new", name: "海关岗" })]));
    await second;
    requests[0]?.resolve(discoveryEnvelope([position({ positionId: "old", name: "税务岗" })]));
    await first;

    expect(controller.snapshot().items.map((item) => item.positionId)).toEqual(["new"]);
    expect(controller.snapshot().appliedFilters.query).toBe("海关");
  });

  it("keeps loaded items and marks a partial page when pagination fails", async () => {
    const discoverPositions = vi
      .fn()
      .mockResolvedValueOnce(
        discoveryEnvelope([position()], { hasMore: true, nextCursor: "cursor-next" }),
      )
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-page-2"));
    const api = { discoverPositions } as Pick<QualificationApi, "discoverPositions">;
    const controller = new PositionDiscoveryController({
      api,
      store: new PositionStateStore("user-a"),
      onChange: vi.fn(),
    });

    await controller.search();
    await controller.loadMore();

    const state = controller.snapshot();
    expect(state.status).toBe("ready");
    expect(state.items).toHaveLength(1);
    expect(state.paginationStatus).toBe("error");
    expect(state.paginationError).toMatchObject({ status: "network", requestId: "request-page-2" });
    expect(state.delivery).toMatchObject({ partial: true });
  });

  it("deduplicates position IDs within and across pagination responses", async () => {
    const discoverPositions = vi
      .fn()
      .mockResolvedValueOnce(
        discoveryEnvelope(
          [
            position({ positionId: "position-1" }),
            position({ positionId: "position-1" }),
            position({ positionId: "position-2" }),
          ],
          { hasMore: true, nextCursor: "cursor-next" },
        ),
      )
      .mockResolvedValueOnce(
        discoveryEnvelope([
          position({ positionId: "position-2" }),
          position({ positionId: "position-3" }),
          position({ positionId: "position-3" }),
        ]),
      );
    const controller = new PositionDiscoveryController({
      api: { discoverPositions },
      store: new PositionStateStore("user-a"),
      onChange: vi.fn(),
    });

    await controller.search();
    await controller.loadMore();

    expect(controller.snapshot().items.map((item) => item.positionId)).toEqual([
      "position-1",
      "position-2",
      "position-3",
    ]);
  });

  it("keeps the last workspace version visibly stale when a refresh fails", async () => {
    const discoverPositions = vi
      .fn()
      .mockResolvedValueOnce(
        discoveryEnvelope([position()], { hasMore: true, nextCursor: "cursor-next" }),
      )
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-refresh"));
    const api = { discoverPositions } as Pick<QualificationApi, "discoverPositions">;
    const controller = new PositionDiscoveryController({
      api,
      store: new PositionStateStore("user-a"),
      preserveItemsOnRefreshError: true,
      onChange: vi.fn(),
    });

    await controller.search();
    await controller.refresh();

    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      items: [{ positionId: "position-1" }],
      nextCursor: "cursor-next",
      hasMore: true,
      refreshError: { status: "network", requestId: "request-refresh" },
      delivery: { stale: true, processing: false },
    });
  });

  it("keeps rendered items, cursor and a processing marker while a return-trip refresh runs", async () => {
    const requests: Array<Deferred<ApiEnvelope<PositionDiscoveryResponse>>> = [];
    const api = {
      discoverPositions: vi.fn(() => {
        const request = deferred<ApiEnvelope<PositionDiscoveryResponse>>();
        requests.push(request);
        return request.promise;
      }),
    } satisfies Pick<QualificationApi, "discoverPositions">;
    const controller = new PositionDiscoveryController({
      api,
      store: new PositionStateStore("user-a"),
      preserveItemsOnRefreshError: true,
      onChange: vi.fn(),
    });

    const initial = controller.search();
    requests[0]?.resolve(
      discoveryEnvelope([position()], { hasMore: true, nextCursor: "cursor-next" }),
    );
    await initial;

    const refresh = controller.refresh();
    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      items: [{ positionId: "position-1" }],
      nextCursor: "cursor-next",
      hasMore: true,
      delivery: { processing: true, processingLabel: "正在更新岗位结果" },
    });

    requests[1]?.resolve(
      discoveryEnvelope([position({ name: "更新后的岗位" })], {
        hasMore: true,
        nextCursor: "cursor-fresh",
      }),
    );
    await refresh;

    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      items: [{ positionId: "position-1", name: "更新后的岗位" }],
      nextCursor: "cursor-fresh",
      delivery: { processing: false, processingLabel: "" },
    });
  });

  it("rejects an invalid exam year instead of silently dropping the filter", async () => {
    const api = { discoverPositions: vi.fn() } as Pick<QualificationApi, "discoverPositions">;
    const controller = new PositionDiscoveryController({
      api,
      store: new PositionStateStore("user-a"),
      onChange: vi.fn(),
    });
    controller.setFilters({ examYear: "19" });

    await controller.search();

    expect(api.discoverPositions).not.toHaveBeenCalled();
    expect(controller.snapshot().validationError).toContain("2000–2100");
  });
});

describe("position workspace controller", () => {
  it("recommends qualified positions from target-score evidence before the ability baseline", async () => {
    const getPositionWorkspace = vi.fn().mockResolvedValue(
      workspaceEnvelope([
        workspaceItem({
          positionId: "recommended",
          fit: readyFit(),
        }),
        workspaceItem({
          positionId: "eligible-evidence-pending",
          fit: evidenceUnavailableFit(),
        }),
        workspaceItem({
          positionId: "needs-confirmation",
          eligibilityStatus: "needs_confirmation",
          pendingCount: 1,
          pendingRequirementIds: ["major"],
          fit: evidenceUnavailableFit({
            requirementsBurden: {
              additionalExamRequired: false,
              hardConditionNotMetCount: 0,
              hardConditionNotMetRequirementIds: [],
              pendingRequirementCount: 1,
              pendingRequirementIds: ["major"],
            },
          }),
        }),
        workspaceItem({
          positionId: "ineligible",
          eligibilityStatus: "ineligible",
          notMetCount: 1,
          notMetRequirementIds: ["education"],
          fit: readyFit({
            requirementsBurden: {
              additionalExamRequired: false,
              hardConditionNotMetCount: 1,
              hardConditionNotMetRequirementIds: ["education"],
              pendingRequirementCount: 0,
              pendingRequirementIds: [],
            },
          }),
        }),
      ]),
    );
    const controller = new PositionWorkspaceController({
      qualificationApi: {
        getPositionWorkspace,
        getQualificationGaps: vi.fn(),
      },
      store: new PositionStateStore("user-a"),
      onChange: vi.fn(),
    });

    await controller.refreshPositions();

    const state = controller.snapshot();
    expect(state.candidateItems.map((item) => item.positionId)).toEqual([
      "recommended",
      "eligible-evidence-pending",
    ]);
    expect(state.candidateItems[0]).toMatchObject({
      eligibilityStatus: "eligible",
      recommendationEvidenceAvailable: true,
    });
    expect(state.candidateItems[1]).toMatchObject({
      eligibilityStatus: "eligible",
      recommendationBucket: "recommended",
      recommendationEvidenceAvailable: true,
      recommendationEvidenceLabel: "资格与历年目标分依据完整，完成基线后补充个人分差",
    });
    expect(state.evidencePendingItems).toEqual([]);
    expect(state.needsConfirmationItems.map((item) => item.positionId)).toEqual([
      "needs-confirmation",
    ]);
    expect(state.candidateItems.map((item) => item.positionId)).not.toContain("ineligible");
  });

  it("keeps a primary goal separate without letting it populate recommendation buckets", async () => {
    const getPositionWorkspace = vi.fn().mockResolvedValue(
      workspaceEnvelope([
        workspaceItem({
          positionId: "primary-pending",
          isPrimaryGoal: true,
          eligibilityStatus: "needs_confirmation",
          pendingCount: 1,
          pendingRequirementIds: ["other_conditions"],
          fit: evidenceUnavailableFit({
            requirementsBurden: {
              additionalExamRequired: false,
              hardConditionNotMetCount: 0,
              hardConditionNotMetRequirementIds: [],
              pendingRequirementCount: 1,
              pendingRequirementIds: ["other_conditions"],
            },
          }),
        }),
      ]),
    );
    const controller = new PositionWorkspaceController({
      qualificationApi: {
        getPositionWorkspace,
        getQualificationGaps: vi.fn(),
      },
      store: new PositionStateStore("user-a"),
      onChange: vi.fn(),
    });

    await controller.refreshPositions();

    expect(controller.snapshot()).toMatchObject({
      primaryGoal: {
        positionId: "primary-pending",
        recommendationBucket: "needs_confirmation",
      },
      candidateItems: [],
      evidencePendingItems: [],
      needsConfirmationItems: [],
    });
  });
});

describe("position comparison controller", () => {
  it.each([
    [["position-1", "position-1"], "duplicate"],
    [["position-1", "position-2", "position-3", "position-4"], "overflow"],
    [["position-1", ""], "blank"],
  ])("rejects an invalid %s deep link without rewriting it (%s)", async (positionIds) => {
    const comparePositions = vi.fn();
    const getPositionFit = vi.fn();
    const controller = new PositionCompareController({
      api: { comparePositions, getPositionFit },
      store: new PositionStateStore("user-a"),
      positionIds,
      onChange: vi.fn(),
    });

    await controller.load();

    expect(controller.snapshot()).toMatchObject({
      status: "error",
      error: { retryable: false },
    });
    expect(comparePositions).not.toHaveBeenCalled();
    expect(getPositionFit).not.toHaveBeenCalled();
  });
});

describe("position goal controller", () => {
  it("offers beginner briefing only after the first primary goal is selected", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    store.saveGoalPreview(
      goalPreview({
        planImpact: {
          ...goalPreview().planImpact,
          changeType: "goal_selected",
          previousTargetScore: null,
          targetScoreDelta: null,
        },
      }),
    );
    const controller = goalController({
      store,
      confirmPrimaryGoal: vi.fn().mockResolvedValue(
        goalConfirmationEnvelope({
          changeType: "goal_selected",
          previousTargetScore: null,
          targetScoreDelta: null,
        }),
      ),
    });

    await controller.load();
    await controller.confirm();

    expect(controller.snapshot()).toMatchObject({
      status: "confirmed",
      offerExamBriefing: true,
    });
  });

  it("ignores a slower load after a newer load has established the page state", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const details: Array<Deferred<ApiEnvelope<PositionDiscoveryDetail>>> = [];
    const getPositionDetail = vi.fn(() => {
      const request = deferred<ApiEnvelope<PositionDiscoveryDetail>>();
      details.push(request);
      return request.promise;
    });
    const evaluatePosition = vi.fn().mockResolvedValue(eligibilityEnvelope());
    const previewPrimaryGoal = vi.fn().mockResolvedValue({
      data: goalPreview(),
      requestId: "request-preview",
      generatedAt: "2026-07-13T00:00:00Z",
    } satisfies ApiEnvelope<PrimaryGoalPreview>);
    const controller = new PositionGoalController({
      api: {
        getPositionDetail,
        evaluatePosition,
        previewPrimaryGoal,
        confirmPrimaryGoal: vi.fn(),
      },
      store: new PositionStateStore("user-a"),
      positionId: "position-1",
      onChange: vi.fn(),
    });

    const older = controller.load();
    const newer = controller.load();
    details[1]?.resolve(detailEnvelope({ name: "新版岗位" }));
    await newer;
    details[0]?.resolve(detailEnvelope({ name: "旧版岗位" }));
    await older;

    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      summary: { name: "新版岗位" },
    });
    expect(evaluatePosition).toHaveBeenCalledOnce();
    expect(previewPrimaryGoal).toHaveBeenCalledOnce();
  });

  it("reuses the same confirmation command after an unknown network outcome", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    store.saveGoalPreview(goalPreview());
    const confirmPrimaryGoal = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-offline"))
      .mockResolvedValueOnce(goalConfirmationEnvelope());
    const controller = goalController({ store, confirmPrimaryGoal });

    await controller.load();
    await controller.confirm();
    expect(controller.snapshot()).toMatchObject({
      status: "network",
      preview: { previewId: "preview-1" },
    });
    await controller.confirm();

    expect(confirmPrimaryGoal).toHaveBeenCalledTimes(2);
    expect(confirmPrimaryGoal.mock.calls[0]?.[1]).toBe(confirmPrimaryGoal.mock.calls[1]?.[1]);
    expect(entropyCallCount).toBe(1);
    expect(controller.snapshot()).toMatchObject({
      status: "confirmed",
      offerExamBriefing: false,
    });
    expect(store.pendingCommands("goal-confirm")).toEqual([]);
  });

  it("recovers an unknown confirmation with the same key after reload and preview expiry", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    store.saveGoalPreview(goalPreview());
    const confirmPrimaryGoal = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-offline"))
      .mockResolvedValueOnce(goalConfirmationEnvelope());
    const first = goalController({ store, confirmPrimaryGoal });
    await first.load();
    await first.confirm();
    first.dispose();

    vi.setSystemTime(new Date("2026-07-13T00:16:00Z"));
    const evaluatePosition = vi.fn();
    const previewPrimaryGoal = vi.fn();
    const second = new PositionGoalController({
      api: {
        getPositionDetail: () => Promise.resolve(detailEnvelope()),
        evaluatePosition,
        previewPrimaryGoal,
        confirmPrimaryGoal,
      },
      store,
      positionId: "position-1",
      onChange: vi.fn(),
    });

    await second.load();

    expect(second.snapshot()).toMatchObject({ status: "confirmed" });
    expect(confirmPrimaryGoal).toHaveBeenCalledTimes(2);
    expect(confirmPrimaryGoal.mock.calls[0]?.[1]).toBe(confirmPrimaryGoal.mock.calls[1]?.[1]);
    expect(evaluatePosition).not.toHaveBeenCalled();
    expect(previewPrimaryGoal).not.toHaveBeenCalled();
    expect(entropyCallCount).toBe(1);
    expect(store.pendingCommands("goal-confirm")).toEqual([]);
  });

  it("recovers an interrupted preview with its original eligibility snapshot and key", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    const previewPrimaryGoal = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-offline"))
      .mockResolvedValueOnce({
        data: goalPreview(),
        requestId: "request-preview",
        generatedAt: "2026-07-13T00:00:00Z",
      } satisfies ApiEnvelope<PrimaryGoalPreview>);
    const firstEvaluate = vi.fn().mockResolvedValue(eligibilityEnvelope());
    const first = new PositionGoalController({
      api: {
        getPositionDetail: () => Promise.resolve(detailEnvelope()),
        evaluatePosition: firstEvaluate,
        previewPrimaryGoal,
        confirmPrimaryGoal: vi.fn(),
      },
      store,
      positionId: "position-1",
      onChange: vi.fn(),
    });
    await first.load();
    first.dispose();

    const secondEvaluate = vi.fn();
    const second = new PositionGoalController({
      api: {
        getPositionDetail: () => Promise.resolve(detailEnvelope()),
        evaluatePosition: secondEvaluate,
        previewPrimaryGoal,
        confirmPrimaryGoal: vi.fn(),
      },
      store,
      positionId: "position-1",
      onChange: vi.fn(),
    });
    await second.load();

    expect(second.snapshot()).toMatchObject({
      status: "ready",
      preview: { previewId: "preview-1" },
    });
    expect(firstEvaluate).toHaveBeenCalledOnce();
    expect(secondEvaluate).not.toHaveBeenCalled();
    expect(previewPrimaryGoal).toHaveBeenCalledTimes(2);
    expect(previewPrimaryGoal.mock.calls[0]?.[1]).toBe(previewPrimaryGoal.mock.calls[1]?.[1]);
    expect(entropyCallCount).toBe(1);
    expect(store.pendingCommands("goal-preview")).toEqual([]);
  });

  it("does not submit a new confirmation after its preview expires locally", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    store.saveGoalPreview(goalPreview());
    const confirmPrimaryGoal = vi.fn();
    const controller = goalController({ store, confirmPrimaryGoal });
    await controller.load();

    vi.setSystemTime(new Date("2026-07-13T00:16:00Z"));
    await controller.confirm();

    expect(confirmPrimaryGoal).not.toHaveBeenCalled();
    expect(controller.snapshot()).toMatchObject({
      status: "versionConflict",
      preview: null,
      error: { retryable: true },
    });
    expect(store.loadGoalPreview("position-1")).toBeNull();
  });

  it("clears a consumed preview and terminal command after a 409 conflict", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    store.saveGoalPreview(goalPreview());
    const confirmPrimaryGoal = vi.fn().mockRejectedValue(
      new ApiClientError("预览已消费", 409, "request-conflict", {
        code: "primary_goal_preview_consumed",
        message: "预览已消费",
        requestId: "request-conflict",
      }),
    );
    const controller = goalController({ store, confirmPrimaryGoal });

    await controller.load();
    await controller.confirm();

    expect(controller.snapshot()).toMatchObject({
      status: "versionConflict",
      preview: null,
      error: { requestId: "request-conflict" },
    });
    expect(store.pendingCommands("goal-confirm")).toEqual([]);
    expect(store.loadGoalPreview("position-1")).toBeNull();
  });
});

describe("position detail controller", () => {
  it("hydrates a direct deep link from the formal detail endpoint and eligibility snapshot", async () => {
    const confirmRequirement: QualificationApi["confirmRequirement"] = () =>
      Promise.reject(new Error("not used"));
    const api = {
      getPositionDetail: () => Promise.resolve(detailEnvelope()),
      evaluatePosition: () => Promise.resolve(eligibilityEnvelope()),
      confirmRequirement,
    } satisfies Pick<
      QualificationApi,
      "getPositionDetail" | "evaluatePosition" | "confirmRequirement"
    >;
    const store = new PositionStateStore("user-a");
    const controller = new PositionDetailController({
      api,
      store,
      positionId: "position-1",
      onChange: vi.fn(),
    });

    await controller.load();

    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      summary: { name: "一级行政执法员", positionId: "position-1" },
      snapshotId: "snapshot-1",
      detailTrace: { requestId: "request-detail" },
      delivery: { requestId: "request-eligibility" },
    });
    expect(store.loadSummary("position-1")?.departmentFull).toBe("国家税务总局测试局");
  });

  it("keeps a rendered detail in place during a silent reload and replaces it on success", async () => {
    const detailRequests: Array<Deferred<ApiEnvelope<PositionDiscoveryDetail>>> = [];
    const eligibilityRequests: Array<Deferred<ApiEnvelope<PositionEligibilitySnapshot>>> = [];
    const confirmRequirement: QualificationApi["confirmRequirement"] = () =>
      Promise.reject(new Error("not used"));
    const api = {
      getPositionDetail: vi.fn(() => {
        const request = deferred<ApiEnvelope<PositionDiscoveryDetail>>();
        detailRequests.push(request);
        return request.promise;
      }),
      evaluatePosition: vi.fn(() => {
        const request = deferred<ApiEnvelope<PositionEligibilitySnapshot>>();
        eligibilityRequests.push(request);
        return request.promise;
      }),
      confirmRequirement,
    } satisfies Pick<
      QualificationApi,
      "getPositionDetail" | "evaluatePosition" | "confirmRequirement"
    >;
    const controller = new PositionDetailController({
      api,
      store: new PositionStateStore("user-a"),
      positionId: "position-1",
      onChange: vi.fn(),
    });

    const initial = controller.load();
    detailRequests[0]?.resolve(detailEnvelope());
    eligibilityRequests[0]?.resolve(eligibilityEnvelope());
    await initial;

    const silent = controller.load({ silent: true });
    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      snapshotId: "snapshot-1",
      summaryView: { name: "一级行政执法员" },
    });

    detailRequests[1]?.resolve(detailEnvelope({ name: "更新后的岗位" }));
    eligibilityRequests[1]?.resolve(eligibilityEnvelope());
    await silent;

    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      summary: { name: "更新后的岗位" },
      summaryView: { name: "更新后的岗位" },
    });
  });

  it("switches a rendered detail to a state page only when the silent reload fails", async () => {
    const confirmRequirement: QualificationApi["confirmRequirement"] = () =>
      Promise.reject(new Error("not used"));
    const getPositionDetail = vi
      .fn()
      .mockResolvedValueOnce(detailEnvelope())
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-silent"));
    const api = {
      getPositionDetail,
      evaluatePosition: vi.fn(() => Promise.resolve(eligibilityEnvelope())),
      confirmRequirement,
    } as unknown as Pick<
      QualificationApi,
      "getPositionDetail" | "evaluatePosition" | "confirmRequirement"
    >;
    const controller = new PositionDetailController({
      api,
      store: new PositionStateStore("user-a"),
      positionId: "position-1",
      onChange: vi.fn(),
    });

    await controller.load();
    expect(controller.snapshot().status).toBe("ready");

    await controller.load({ silent: true });

    expect(controller.snapshot()).toMatchObject({
      status: "network",
      error: { status: "network", requestId: "request-silent", retryable: true },
    });
  });
});

describe("position semantic contracts", () => {
  it.each([
    ["onboarding_qualification_incomplete", "needsProfile"],
    ["position_release_unavailable", "noActiveRelease"],
    ["position_release_upcoming", "noActiveRelease"],
    ["position_release_between_windows", "noActiveRelease"],
    ["position_release_closed", "noActiveRelease"],
    ["position_release_conflict", "versionConflict"],
    ["qualification_profile_version_conflict", "versionConflict"],
    ["position_not_found", "notFound"],
  ])("maps stable server code %s to %s", (code, status) => {
    const error = new ApiClientError("公开错误", code === "position_not_found" ? 404 : 409, "r-1", {
      code,
      message: "公开错误",
      requestId: "r-1",
    });
    expect(positionOperationError(error).status).toBe(status);
  });

  it.each([
    ["position_release_unavailable", "本年度岗位数据尚未发布"],
    ["position_release_upcoming", "报名周期尚未开始"],
    ["position_release_between_windows", "当前处于补录等待期"],
    ["position_release_closed", "本年度报名已经结束"],
  ])("does not collapse lifecycle code %s into a false release phase", (code, title) => {
    const error = new ApiClientError("服务端生命周期说明", 409, "r-lifecycle", {
      code,
      message: "服务端生命周期说明",
      requestId: "r-lifecycle",
    });
    expect(positionOperationError(error)).toMatchObject({
      status: "noActiveRelease",
      title,
      message: "服务端生命周期说明",
      retryable: false,
    });
  });

  it("renders only service-declared invalidations", () => {
    expect(planInvalidationViews(["weekly_plan", "score_gap"])).toEqual([
      { code: "weekly_plan", label: "本周计划会重新安排" },
      { code: "score_gap", label: "岗位分差会重新安排" },
    ]);
  });

  it("presents the canonical target-score validity instead of inventing a date", () => {
    expect(
      targetValidityView(
        {
          basisLevel: "direct",
          generatedAt: "2026-07-01T00:00:00Z",
          validUntil: "2026-08-01T00:00:00Z",
          lowerBound: 126,
          recommended: 129,
          upperBound: 132,
          evidenceLevel: "direct",
          sampleCount: 6,
        },
        new Date("2026-07-15T00:00:00Z").getTime(),
      ),
    ).toMatchObject({ available: true, expired: false });
  });

  it("clears terminal 4xx commands but retains network, 5xx and unknown outcomes", () => {
    expect(shouldRetainPositionCommand(new ApiClientError("bad", 400, "r-400"))).toBe(false);
    expect(shouldRetainPositionCommand(new ApiClientError("offline", 0, "r-0"))).toBe(true);
    expect(shouldRetainPositionCommand(new ApiClientError("server", 503, "r-503"))).toBe(true);
    expect(shouldRetainPositionCommand(new Error("unknown"))).toBe(true);
  });
});

describe("position durable state", () => {
  it("reuses an immutable command key and rotates it only after the explicit TTL", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    const first = await store.commandKey("goal-preview", "position-1", { snapshotId: "s-1" });
    const retry = await store.commandKey("goal-preview", "position-1", { snapshotId: "s-1" });
    expect(retry).toBe(first);
    expect(entropyCallCount).toBe(1);
    expect(store.pendingCommands("goal-preview")).toEqual([
      expect.objectContaining({ scope: "position-1", payload: { snapshotId: "s-1" } }),
    ]);
    await expect(
      store.commandKey("goal-preview", "position-1", { snapshotId: "changed" }),
    ).rejects.toThrow("绑定了不同输入");

    vi.advanceTimersByTime(8 * 24 * 60 * 60 * 1000);
    const afterTtl = await store.commandKey("goal-preview", "position-1", {
      snapshotId: "changed",
    });
    expect(afterTtl).not.toBe(first);
    expect(entropyCallCount).toBe(2);
  });

  it("rejects a preview stored under another position or beyond expiresAt", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    const store = new PositionStateStore("user-a");
    const preview = goalPreview({ positionId: "position-1" });
    storage.set(`${positionStoragePrefix()}.user-a.goal-preview.position-2`, preview);
    expect(store.loadGoalPreview("position-2")).toBeNull();
    store.saveGoalPreview(goalPreview({ expiresAt: "2026-07-12T23:59:59Z" }));
    expect(store.loadGoalPreview("position-1")).toBeNull();
  });

  it("clears only the logged-out user's position state", () => {
    storage.set(`${positionStoragePrefix()}.user-a.search`, { query: "税务" });
    storage.set(`${positionStoragePrefix()}.user-b.search`, { query: "海关" });
    clearPositionStateForUser("user-a");
    expect(storage.has(`${positionStoragePrefix()}.user-a.search`)).toBe(false);
    expect(storage.has(`${positionStoragePrefix()}.user-b.search`)).toBe(true);
  });
});

describe("position page lifecycle registry", () => {
  it("disposes only the unloaded page instance", () => {
    const registry = new PositionPageRuntimeRegistry<object, { dispose(): void }>();
    const pageA = {};
    const pageB = {};
    const disposeA = vi.fn();
    const disposeB = vi.fn();
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);
    runtimeA.controller = { dispose: disposeA };
    runtimeB.controller = { dispose: disposeB };

    registry.detach(pageA);

    expect(disposeA).toHaveBeenCalledOnce();
    expect(disposeB).not.toHaveBeenCalled();
    expect(registry.owns(pageA, runtimeA)).toBe(false);
    expect(registry.owns(pageB, runtimeB)).toBe(true);
  });
});

function evidenceUnavailableFit(
  changes: Partial<PositionFitAssessment> = {},
): PositionFitAssessment {
  return {
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
    ...changes,
  };
}

function readyFit(changes: Partial<PositionFitAssessment> = {}): PositionFitAssessment {
  return {
    status: "overlap",
    band: "overlap",
    evidenceStatus: "ready",
    evidenceReasonCodes: [],
    scoreGap: {
      lower: -2,
      recommended: 1,
      upper: 4,
      unit: "points",
      direction: "positive_means_target_above_current",
    },
    requirementsBurden: {
      additionalExamRequired: false,
      hardConditionNotMetCount: 0,
      hardConditionNotMetRequirementIds: [],
      pendingRequirementCount: 0,
      pendingRequirementIds: [],
    },
    ...changes,
  };
}

function workspaceItem(changes: Partial<PositionWorkspaceItem> = {}): PositionWorkspaceItem {
  return {
    ...position(),
    fit: readyFit(),
    ...changes,
  };
}

function workspaceEnvelope(items: PositionWorkspaceItem[]): ApiEnvelope<PositionWorkspaceResponse> {
  return {
    data: {
      abilityEstimate: {
        readiness: "unavailable",
        estimate: null,
        missingComponents: ["xingce", "shenlun"],
      },
      examYear: 2026,
      hasMore: false,
      items,
      learnerProfileVersion: 2,
      nextCursor: null,
      positionDataVersion: "positions-v1",
      ruleSetVersion: "rules-v1",
    },
    requestId: "request-workspace",
    generatedAt: "2026-07-13T00:00:00Z",
  };
}

function position(changes: Partial<PositionDiscoveryItem> = {}): PositionDiscoveryItem {
  return {
    positionId: "position-1",
    positionCode: "3001",
    name: "一级行政执法员",
    departmentFull: "国家税务总局测试局",
    systemCode: "tax",
    region: "江西",
    city: "赣州市",
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
      basisLevel: "direct",
      generatedAt: "2026-07-01T00:00:00Z",
      validUntil: "2026-08-01T00:00:00Z",
      lowerBound: 126.2,
      recommended: 129,
      upperBound: 131.8,
      evidenceLevel: "direct",
      sampleCount: 6,
    },
    isPrimaryGoal: false,
    ...changes,
  };
}

function discoveryEnvelope(
  items: PositionDiscoveryItem[],
  changes: Partial<PositionDiscoveryResponse> = {},
): ApiEnvelope<PositionDiscoveryResponse> {
  return {
    data: {
      examYear: 2026,
      learnerProfileVersion: 2,
      ruleSetVersion: "rules-v1",
      positionDataVersion: "positions-v1",
      items,
      nextCursor: null,
      hasMore: false,
      ...changes,
    },
    requestId: "request-discovery",
    generatedAt: "2026-07-13T00:00:00Z",
  };
}

function detailEnvelope(
  changes: Partial<PositionDiscoveryDetail> = {},
): ApiEnvelope<PositionDiscoveryDetail> {
  return {
    data: {
      ...position(),
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
      ...changes,
    },
    requestId: "request-detail",
    generatedAt: "2026-07-13T00:00:00Z",
  };
}

function goalConfirmationEnvelope(
  impactChanges: Partial<PrimaryGoalConfirmResponse["planImpact"]> = {},
): ApiEnvelope<PrimaryGoalConfirmResponse> {
  return {
    data: {
      goal: {
        goalVersionId: "goal-version-1",
        positionId: "position-1",
        eligibilitySnapshotId: "snapshot-1",
        targetScore: goalPreview().targetScore,
        createdAt: "2026-07-13T00:00:00Z",
        learnerProfileVersion: 2,
        positionDataVersion: "positions-v1",
        role: "primary",
        status: "confirmed",
      },
      planImpact: { ...goalPreview().planImpact, ...impactChanges },
    },
    requestId: "request-confirm",
    generatedAt: "2026-07-13T00:00:00Z",
  };
}

function goalController(options: {
  store: PositionStateStore;
  confirmPrimaryGoal: ReturnType<typeof vi.fn>;
}): PositionGoalController {
  return new PositionGoalController({
    api: {
      getPositionDetail: () => Promise.resolve(detailEnvelope()),
      evaluatePosition: () => Promise.resolve(eligibilityEnvelope()),
      previewPrimaryGoal: () =>
        Promise.resolve({
          data: goalPreview(),
          requestId: "request-preview",
          generatedAt: "2026-07-13T00:00:00Z",
        }),
      confirmPrimaryGoal: options.confirmPrimaryGoal,
    } as unknown as Pick<
      QualificationApi,
      "getPositionDetail" | "evaluatePosition" | "previewPrimaryGoal" | "confirmPrimaryGoal"
    >,
    store: options.store,
    positionId: "position-1",
    onChange: vi.fn(),
  });
}

function eligibilityEnvelope(): ApiEnvelope<PositionEligibilitySnapshot> {
  return {
    data: {
      snapshotId: "snapshot-1",
      positionId: "position-1",
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
    },
    requestId: "request-eligibility",
    generatedAt: "2026-07-13T00:00:00Z",
  };
}

function goalPreview(changes: Partial<PrimaryGoalPreview> = {}): PrimaryGoalPreview {
  return {
    previewId: "preview-1",
    positionId: "position-1",
    eligibilitySnapshotId: "snapshot-1",
    currentGoalVersionId: null,
    learnerProfileVersion: 2,
    positionDataVersion: "positions-v1",
    targetScore: {
      basisLevel: "direct",
      generatedAt: "2026-07-01T00:00:00Z",
      validUntil: "2026-08-01T00:00:00Z",
      lowerBound: 126,
      recommended: 129,
      upperBound: 132,
      evidenceLevel: "direct",
      sampleCount: 6,
    },
    planImpact: {
      requiresPlanRebuild: true,
      changeType: "goal_changed",
      positionChanged: true,
      previousTargetScore: 126,
      nextTargetScore: 129,
      targetScoreDelta: 3,
      invalidates: ["daily_plan"],
    },
    expiresAt: "2026-07-13T00:15:00Z",
    createdAt: "2026-07-13T00:00:00Z",
    ...changes,
  };
}

function deferred<T>(): Deferred<T> {
  let resolvePromise!: (value: T) => void;
  let rejectPromise!: (reason: unknown) => void;
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}
