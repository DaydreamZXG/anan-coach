import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../miniprogram/api/client";
import {
  XingceLearningApi,
  type XingceMasteryQueueAction,
  type XingceMasteryQueueBucket,
  type XingceMasteryQueueItem,
  type XingceMasteryQueuePage,
} from "../miniprogram/api/xingce-learning";
import {
  assertMasteryQueuePage,
  masteryQueueActionTarget,
} from "../miniprogram/features/xingce/mastery/contracts";
import { apiSuccess } from "./work_packages/vj-02-real-xingce-wechat/harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("Xingce mastery queue API and contract", () => {
  it("requests the frozen bucket, page size and opaque cursor without rewriting it", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("wx", {
      request: vi.fn((options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
        options.success?.(apiSuccess(queuePage("due_verification")));
      }),
    });
    const api = new XingceLearningApi(new ApiClient("https://api.example.test", "token"));

    await api.getMasteryQueue({
      bucket: "due_verification",
      cursor: "opaque+/= cursor",
      pageSize: 30,
    });

    expect(requests[0]?.url).toBe(
      "https://api.example.test/api/v2/xingce/mastery-queue?bucket=due_verification&pageSize=30&cursor=opaque%2B%2F%3D%20cursor",
    );
    expect(() => api.getMasteryQueue({ bucket: "stable_mastery", pageSize: 0 })).toThrow(
      "between 1 and 100",
    );
  });

  it("accepts only version-bound service navigation and rejects client-invented actions", () => {
    const reflection = queueItem("pending_reflection", {
      action: {
        state: "ready",
        startUrl: "/pages/reflection/index?sessionId=session-a&reviewId=review-a",
        planTaskId: null,
      },
    });
    expect(masteryQueueActionTarget(reflection)).toEqual({
      mode: "navigateTo",
      url: "/pages/reflection/index?sessionId=session-a&reviewId=review-a",
    });

    const due = queueItem("due_verification", {
      action: {
        state: "ready",
        startUrl: "/pages/xingce-task-intro/index?mode=verification&planTaskId=plan-a",
        planTaskId: "plan-a",
      },
    });
    expect(masteryQueueActionTarget(due)).toEqual({
      mode: "navigateTo",
      url: "/pages/xingce-task-intro/index?mode=verification&planTaskId=plan-a",
    });
    expect(
      masteryQueueActionTarget({
        ...due,
        action: {
          ...due.action,
          startUrl: "/pages/xingce-task-intro/index?mode=verification&planTaskId=other",
        },
      }),
    ).toBeNull();

    const stable = queuePage("stable_mastery");
    const firstStable = stable.items[0];
    if (!firstStable) throw new Error("stable fixture item is required");
    stable.items[0] = {
      ...firstStable,
      action: {
        state: "ready",
        startUrl: "/pages/xingce-task-intro/index?mode=verification&planTaskId=plan-a",
        planTaskId: "plan-a",
      },
    };
    expect(() => assertMasteryQueuePage(stable, "stable_mastery")).toThrow("不应提供启动动作");
  });

  it("normalizes an omitted OpenAPI default cursor without inventing another page", () => {
    const page = queuePage("pending_reflection");
    delete page.nextCursor;

    expect(assertMasteryQueuePage(page, "pending_reflection").nextCursor).toBeNull();
  });
});

describe("C08 mastery queue page", () => {
  it("loads pending reflection by default and reloads the first page for each selected bucket", async () => {
    const runtime = await createPage((options) => {
      const bucket = requestBucket(options.url);
      options.success?.(apiSuccess(queuePage(bucket)));
    });

    expect(runtime.page.data.status).toBe("ready");
    expect(runtime.page.data.activeBucket).toBe("pending_reflection");
    expect(runtime.page.data.tabs).toEqual([
      expect.objectContaining({ bucket: "pending_reflection", count: 2, active: true }),
      expect.objectContaining({ bucket: "due_verification", count: 3, active: false }),
      expect.objectContaining({ bucket: "stable_mastery", count: 4, active: false }),
      expect.objectContaining({ bucket: "invalidated", count: 1, active: false }),
    ]);
    expect(runtime.page.data.items[0]).toMatchObject({
      bucket: "pending_reflection",
      actionState: "ready",
      actionLabel: "继续复盘",
    });

    runtime.page.selectBucket.call(runtime.page, bucketEvent("due_verification"));
    await flushMicrotasks();
    expect(runtime.page.data.activeBucket).toBe("due_verification");
    expect(runtime.page.data.items[0]).toMatchObject({
      bucket: "due_verification",
      actionState: "awaiting_plan",
      actionLabel: "返回今日等待计划",
    });
    expect(runtime.requests[1]?.url).toContain("bucket=due_verification&pageSize=30");
    expect(runtime.requests[1]?.url).not.toContain("cursor=");
  });

  it("reuses the exact next cursor, preserves partial data and deduplicates item IDs", async () => {
    let loadMoreAttempts = 0;
    const runtime = await createPage((options) => {
      const cursor = requestCursor(options.url);
      if (!cursor) {
        options.success?.(
          apiSuccess(
            queuePage("pending_reflection", {
              items: [queueItem("pending_reflection", { itemId: "item-a" })],
              nextCursor: "cursor+/=one",
            }),
          ),
        );
        return;
      }
      loadMoreAttempts += 1;
      if (loadMoreAttempts === 1) {
        options.success?.({
          statusCode: 503,
          data: { code: "queue-temporary", message: "暂时不可用", requestId: "request-more" },
          header: {},
          cookies: [],
          exception: { reasons: [], retryCount: 0 },
          profile: {} as WechatMiniprogram.RequestProfile,
          useHttpDNS: false,
          errMsg: "request:ok",
        });
        return;
      }
      options.success?.(
        apiSuccess(
          queuePage("pending_reflection", {
            items: [
              queueItem("pending_reflection", { itemId: "item-a" }),
              queueItem("pending_reflection", {
                itemId: "item-b",
                sessionId: "session-b",
                reviewId: "review-b",
                action: {
                  state: "ready",
                  startUrl: "/pages/reflection/index?sessionId=session-b&reviewId=review-b",
                  planTaskId: null,
                },
              }),
            ],
            nextCursor: null,
          }),
        ),
      );
    });

    runtime.page.loadMore.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data.items.map((item) => item.itemId)).toEqual(["item-a"]);
    expect(runtime.page.data.partialErrorMessage).toContain("暂时不可用");
    expect(runtime.page.data.partialRequestId).toBe("request-more");

    runtime.page.retryLoadMore.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.requests.slice(1).map((request) => request.url)).toEqual([
      expect.stringContaining("cursor=cursor%2B%2F%3Done"),
      expect.stringContaining("cursor=cursor%2B%2F%3Done"),
    ]);
    expect(runtime.page.data.items.map((item) => item.itemId)).toEqual(["item-a", "item-b"]);
    expect(runtime.page.data.hasMore).toBe(false);
  });

  it("ignores late bucket and old-account responses while clearing prior account data", async () => {
    const releases: Array<() => void> = [];
    const runtime = await createPage(
      (options) => {
        const bucket = requestBucket(options.url);
        const authorization = requestAuthorization(options.header);
        if (authorization === "Bearer token-b") {
          options.success?.(
            apiSuccess(
              queuePage(bucket, {
                items: [queueItem(bucket, { itemId: `user-b-${bucket}` })],
              }),
            ),
          );
          return;
        }
        releases.push(() =>
          options.success?.(
            apiSuccess(
              queuePage(bucket, {
                items: [queueItem(bucket, { itemId: `late-${bucket}` })],
              }),
            ),
          ),
        );
      },
      { waitForReady: false },
    );

    runtime.page.selectBucket.call(runtime.page, bucketEvent("due_verification"));
    expect(runtime.requests).toHaveLength(2);
    releases[1]?.();
    await flushMicrotasks();
    expect(runtime.page.data.items[0]?.itemId).toBe("late-due_verification");
    releases[0]?.();
    await flushMicrotasks();
    expect(runtime.page.data.items[0]?.itemId).toBe("late-due_verification");

    runtime.globalData.accessToken = "token-b";
    runtime.globalData.userId = "user-b";
    runtime.page.onShow.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data.items[0]?.itemId).toBe("user-b-due_verification");
    expect(runtime.requests.at(-1)?.header).toMatchObject({ Authorization: "Bearer token-b" });

    runtime.page.onUnload.call(runtime.page);
    const writesBeforeLate = runtime.setData.mock.calls.length;
    releases.at(-1)?.();
    await flushMicrotasks();
    expect(runtime.setData).toHaveBeenCalledTimes(writesBeforeLate);
  });

  it("uses ready service URLs, sends awaiting plans to Today and never acts on stable mastery", async () => {
    const pendingReady = queueItem("pending_reflection");
    const runtime = await createPage((options) => {
      const bucket = requestBucket(options.url);
      const item = bucket === "pending_reflection" ? pendingReady : queueItem(bucket);
      options.success?.(apiSuccess(queuePage(bucket, { items: [item] })));
    });

    await runtime.page.openItem.call(runtime.page, itemEvent(pendingReady.itemId));
    expect(runtime.navigateTo).toHaveBeenCalledWith({
      url: "/pages/reflection/index?sessionId=session-a&reviewId=review-a",
    });

    runtime.page.selectBucket.call(runtime.page, bucketEvent("due_verification"));
    await flushMicrotasks();
    await runtime.page.openItem.call(runtime.page, itemEvent("item-due_verification"));
    const modalOptions = runtime.showModal.mock.calls[0]?.[0];
    expect(modalOptions?.title).toBe("复测计划尚未发布");
    expect(modalOptions?.content).toContain("不会临时拼题");
    expect(runtime.switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });

    runtime.page.selectBucket.call(runtime.page, bucketEvent("stable_mastery"));
    await flushMicrotasks();
    expect(runtime.page.data.items[0]?.actionAvailable).toBe(false);
    await runtime.page.openItem.call(runtime.page, itemEvent("item-stable_mastery"));
    expect(runtime.navigateTo).toHaveBeenCalledTimes(1);

    runtime.page.selectBucket.call(runtime.page, bucketEvent("invalidated"));
    await flushMicrotasks();
    expect(runtime.page.data.items[0]).toMatchObject({
      invalidated: true,
      stateLabel: "内容已撤回",
      stageLabel: "不再进入掌握判断",
      actionAvailable: false,
    });
  });

  it("recovers an initial failure and exposes an expired login state without stale items", async () => {
    let requestNo = 0;
    const runtime = await createPage(
      (options) => {
        requestNo += 1;
        if (requestNo === 1) {
          options.success?.({
            statusCode: 503,
            data: { code: "queue-down", message: "队列暂不可用", requestId: "request-first" },
            header: {},
            cookies: [],
            exception: { reasons: [], retryCount: 0 },
            profile: {} as WechatMiniprogram.RequestProfile,
            useHttpDNS: false,
            errMsg: "request:ok",
          });
          return;
        }
        if (requestNo === 3) {
          options.success?.({
            statusCode: 401,
            data: { code: "auth-expired", message: "登录已过期", requestId: "request-auth" },
            header: {},
            cookies: [],
            exception: { reasons: [], retryCount: 0 },
            profile: {} as WechatMiniprogram.RequestProfile,
            useHttpDNS: false,
            errMsg: "request:ok",
          });
          return;
        }
        options.success?.(apiSuccess(queuePage("pending_reflection")));
      },
      { waitForReady: false },
    );
    await flushMicrotasks();
    expect(runtime.page.data).toMatchObject({
      status: "error",
      errorMessage: "队列暂不可用",
      requestId: "request-first",
    });

    runtime.page.retry.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data.status).toBe("ready");
    expect(runtime.page.data.items).toHaveLength(1);

    runtime.page.retry.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data).toMatchObject({
      status: "unavailable",
      showLoginAction: true,
    });
    expect(runtime.page.data.items).toEqual([]);
  });
});

interface CapturedMasteryPage {
  data: Record<string, unknown>;
  onLoad(this: TestMasteryPage): void;
  onShow(this: TestMasteryPage): void;
  onUnload(this: TestMasteryPage): void;
  retry(this: TestMasteryPage): void;
  retryLoadMore(this: TestMasteryPage): void;
  selectBucket(this: TestMasteryPage, event: WechatMiniprogram.BaseEvent): void;
  loadMore(this: TestMasteryPage): void;
  openItem(this: TestMasteryPage, event: WechatMiniprogram.BaseEvent): Promise<void>;
}

interface TestMasteryPage extends CapturedMasteryPage {
  data: MasteryPageData;
  setData(patch: Record<string, unknown>): void;
}

interface MasteryPageData extends Record<string, unknown> {
  status: string;
  activeBucket: XingceMasteryQueueBucket;
  tabs: Array<{ bucket: XingceMasteryQueueBucket; count: number; active: boolean }>;
  items: Array<{
    itemId: string;
    bucket: XingceMasteryQueueBucket;
    actionState: string;
    actionLabel: string;
    actionAvailable: boolean;
    invalidated: boolean;
    stateLabel: string;
    stageLabel: string;
  }>;
  hasMore: boolean;
  partialErrorMessage: string;
  partialRequestId: string;
  errorMessage: string;
  requestId: string;
  showLoginAction: boolean;
}

type RequestHandler = (options: WechatMiniprogram.RequestOption) => void;

async function createPage(handleRequest: RequestHandler, options: { waitForReady?: boolean } = {}) {
  let captured: CapturedMasteryPage | undefined;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const globalData: { accessToken?: string; userId?: string } = {
    accessToken: "token-a",
    userId: "user-a",
  };
  const navigateTo = vi.fn(() => Promise.resolve());
  const switchTab = vi.fn(() => Promise.resolve());
  const showModal = vi.fn((options: WechatMiniprogram.ShowModalOption) => {
    void options;
    return Promise.resolve({ confirm: true, cancel: false });
  });
  const request = vi.fn((requestOptions: WechatMiniprogram.RequestOption) => {
    requests.push(requestOptions);
    handleRequest(requestOptions);
  });
  vi.stubGlobal("Page", (definition: CapturedMasteryPage) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("wx", {
    request,
    navigateTo,
    switchTab,
    showModal,
    redirectTo: vi.fn(() => Promise.resolve()),
    stopPullDownRefresh: vi.fn(() => Promise.resolve()),
    getStorageSync: vi.fn(() => ""),
    removeStorageSync: vi.fn(),
  });
  await import("../miniprogram/pages/xingce-mastery/index");
  if (!captured) throw new Error("mastery queue page was not registered");
  const setData = vi.fn(function (this: TestMasteryPage, patch: Record<string, unknown>) {
    Object.assign(this.data, patch);
  });
  const page: TestMasteryPage = {
    ...captured,
    data: JSON.parse(JSON.stringify(captured.data)) as MasteryPageData,
    setData,
  };
  page.onLoad.call(page);
  if (options.waitForReady !== false) {
    await vi.waitFor(() => {
      expect(page.data.status).toBe("ready");
    });
  }
  return { page, requests, setData, globalData, navigateTo, switchTab, showModal };
}

function queuePage(
  bucket: XingceMasteryQueueBucket,
  overrides: Partial<XingceMasteryQueuePage> = {},
): XingceMasteryQueuePage {
  return {
    contractVersion: "xingce-mastery-queue@v1",
    bucket,
    summary: {
      pendingReflectionCount: 2,
      dueVerificationCount: 3,
      stableMasteryCount: 4,
      invalidatedCount: 1,
    },
    items: [queueItem(bucket)],
    nextCursor: null,
    generatedAt: "2026-07-16T09:30:00+08:00",
    ...overrides,
  };
}

function queueItem(
  bucket: XingceMasteryQueueBucket,
  overrides: Partial<XingceMasteryQueueItem> = {},
): XingceMasteryQueueItem {
  const mastery = bucket !== "pending_reflection";
  const action: XingceMasteryQueueAction =
    bucket === "pending_reflection"
      ? {
          state: "ready",
          startUrl: "/pages/reflection/index?sessionId=session-a&reviewId=review-a",
          planTaskId: null,
        }
      : bucket === "due_verification"
        ? { state: "awaiting_plan", startUrl: null, planTaskId: null }
        : { state: "not_applicable", startUrl: null, planTaskId: null };
  return {
    itemId: `item-${bucket}`,
    bucket,
    state:
      bucket === "pending_reflection"
        ? "reflection_required"
        : bucket === "stable_mastery"
          ? "stable_mastered"
          : "provisionally_mastered",
    verificationStage: mastery ? "first_review" : null,
    moduleName: "资料分析",
    dueAt: bucket === "due_verification" ? "2026-07-16T08:00:00+08:00" : null,
    createdAt: "2026-07-15T09:00:00+08:00",
    questionCount: 3,
    evidenceRefs: ["attempt-version:a", "review-version:b"],
    action,
    sessionId: mastery ? null : "session-a",
    reviewId: mastery ? null : "review-a",
    masteryId: mastery ? "mastery-a" : null,
    masteryVersionId: mastery ? "mastery-version-a" : null,
    invalidationReason: bucket === "invalidated" ? "content_withdrawn" : null,
    ...overrides,
  };
}

function requestBucket(url: string): XingceMasteryQueueBucket {
  const value = decodeURIComponent(url.match(/[?&]bucket=([^&]+)/u)?.[1] ?? "");
  if (
    value !== "pending_reflection" &&
    value !== "due_verification" &&
    value !== "stable_mastery" &&
    value !== "invalidated"
  ) {
    throw new Error(`unexpected mastery bucket: ${value}`);
  }
  return value;
}

function requestCursor(url: string): string | null {
  const value = url.match(/[?&]cursor=([^&]+)/u)?.[1];
  return value ? decodeURIComponent(value) : null;
}

function requestAuthorization(header: unknown): string {
  if (typeof header !== "object" || header === null) return "";
  const value = (header as Record<string, unknown>).Authorization;
  return typeof value === "string" ? value : "";
}

function bucketEvent(bucket: XingceMasteryQueueBucket): WechatMiniprogram.BaseEvent {
  return { currentTarget: { dataset: { bucket } } } as unknown as WechatMiniprogram.BaseEvent;
}

function itemEvent(itemId: string): WechatMiniprogram.BaseEvent {
  return {
    currentTarget: { dataset: { itemid: itemId } },
  } as unknown as WechatMiniprogram.BaseEvent;
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 16; index += 1) await Promise.resolve();
}
