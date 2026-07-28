import { afterEach, describe, expect, it, vi } from "vitest";

import type { QuestionFavoriteListItem } from "../miniprogram/api/generated/types.gen";
import { presentQuestionFavoriteItem } from "../miniprogram/features/xingce/favorites/presentation";
import {
  apiSuccess,
  createMemoryWechatStorage,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("question favorite presentation", () => {
  it("distinguishes current, updated and unavailable records without exposing tombstoned content", () => {
    const current = presentQuestionFavoriteItem(favoriteItem("q-current"));
    const updated = presentQuestionFavoriteItem(
      favoriteItem("q-updated", { contentChanged: true }),
    );
    const unavailable = presentQuestionFavoriteItem(
      favoriteItem("q-unavailable", {
        availability: "unavailable",
        contentChanged: false,
        preview: { moduleName: "不应展示", stem: "已下架题目的旧题干不应展示" },
      }),
    );

    expect(current.availabilityLabel).toBe("当前版本");
    expect(updated.availabilityLabel).toBe("题目已更新");
    expect(updated.availabilityDescription).toContain("当前版本");
    expect(unavailable.availabilityLabel).toBe("内容已下架");
    expect(unavailable.stem).toBe("");
    expect(unavailable.moduleLabel).toBe("行测收藏");
    expect(unavailable.availabilityDescription).toContain("不展示题干");
  });
});

describe("question favorites page lifecycle", () => {
  it("renders all availability states and removes an item only after confirmed cancellation", async () => {
    const runtime = await createFavoritesPage();

    expect(runtime.page.data.status).toBe("ready");
    expect(runtime.page.data.items.map((item) => item.availabilityLabel)).toEqual([
      "当前版本",
      "题目已更新",
      "内容已下架",
    ]);

    await runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));
    await flushMicrotasks();

    expect(runtime.page.data.items.map((item) => item.questionId)).toEqual([
      "q-updated",
      "q-unavailable",
    ]);
    const write = runtime.requests.find(
      (request) => request.method === "PUT" && favoriteQuestionId(request) === "q-current",
    );
    expect(write?.data).toEqual({ desiredState: "not_favorited" });
    expect(write?.header?.["Idempotency-Key"]).toMatch(/^xingce-favorite-/u);
    runtime.page.onUnload.call(runtime.page);
  });

  it("coalesces repeated cancellation taps while the first write is unresolved", async () => {
    let releaseWrite: (() => void) | undefined;
    let cancelled = false;
    const runtime = await createFavoritesPage((options) => {
      if (isListRequest(options) && cancelled) {
        options.success?.(apiSuccess(favoritePage([])));
        return true;
      }
      if (options.method !== "PUT") return false;
      const questionId = favoriteQuestionId(options);
      releaseWrite = () => {
        cancelled = true;
        options.success?.(
          apiSuccess({
            questionId,
            favoriteState: "not_favorited",
            favoritedAt: null,
            savedContentVersionId: null,
            revision: 2,
            updatedAt: "2026-07-28T12:30:00Z",
          }),
        );
      };
      return true;
    });

    const first = runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));
    const second = runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));
    await flushMicrotasks();
    expect(runtime.requests.filter((request) => request.method === "PUT")).toHaveLength(1);

    releaseWrite?.();
    await Promise.all([first, second]);
    await flushMicrotasks();
    expect(runtime.page.data.items.some((item) => item.questionId === "q-current")).toBe(false);
    runtime.page.onUnload.call(runtime.page);
  });

  it("ignores an older list response that arrives after cancellation succeeds", async () => {
    let deliveredInitial = false;
    let releaseStaleList: (() => void) | undefined;
    let releaseWrite: (() => void) | undefined;
    let releaseAuthority: (() => void) | undefined;
    const runtime = await createFavoritesPage((options) => {
      if (isListRequest(options)) {
        if (!deliveredInitial) {
          deliveredInitial = true;
          options.success?.(apiSuccess(favoritePage([favoriteItem("q-current")], "cursor-stale")));
          return true;
        }
        if (options.url.includes("cursor=")) {
          releaseStaleList = () =>
            options.success?.(apiSuccess(favoritePage([favoriteItem("q-current")])));
          return true;
        }
        releaseAuthority = () => options.success?.(apiSuccess(favoritePage([])));
        return true;
      }
      if (options.method === "PUT") {
        releaseWrite = () =>
          options.success?.(apiSuccess(cancelReceipt(favoriteQuestionId(options))));
        return true;
      }
      return false;
    });

    runtime.page.loadMore.call(runtime.page);
    await flushMicrotasks();
    expect(releaseStaleList).toBeTypeOf("function");

    const cancellation = runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));
    await flushMicrotasks();
    const refresh = runtime.page.onPullDownRefresh.call(runtime.page);
    await flushMicrotasks();

    releaseWrite?.();
    await cancellation;
    await flushMicrotasks();
    expect(releaseAuthority).toBeTypeOf("function");
    expect(runtime.showToast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "已取消收藏" }),
    );

    releaseStaleList?.();
    await flushMicrotasks();
    expect(runtime.page.data.items.some((item) => item.questionId === "q-current")).toBe(false);

    releaseAuthority?.();
    await refresh;
    await flushMicrotasks();
    expect(runtime.page.data.status).toBe("ready");
    expect(runtime.page.data.items).toEqual([]);
    expect(hasSavingItem(runtime.page)).toBe(false);
    runtime.page.onUnload.call(runtime.page);
  });

  it("keeps an older list response inert when it arrives before cancellation succeeds", async () => {
    let deliveredInitial = false;
    let releaseStaleList: (() => void) | undefined;
    let releaseWrite: (() => void) | undefined;
    let releaseAuthority: (() => void) | undefined;
    const runtime = await createFavoritesPage((options) => {
      if (isListRequest(options)) {
        if (!deliveredInitial) {
          deliveredInitial = true;
          options.success?.(apiSuccess(favoritePage([favoriteItem("q-current")], "cursor-stale")));
          return true;
        }
        if (options.url.includes("cursor=")) {
          releaseStaleList = () =>
            options.success?.(apiSuccess(favoritePage([favoriteItem("q-current")])));
          return true;
        }
        releaseAuthority = () => options.success?.(apiSuccess(favoritePage([])));
        return true;
      }
      if (options.method === "PUT") {
        releaseWrite = () =>
          options.success?.(apiSuccess(cancelReceipt(favoriteQuestionId(options))));
        return true;
      }
      return false;
    });

    runtime.page.loadMore.call(runtime.page);
    await flushMicrotasks();
    const cancellation = runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));
    await flushMicrotasks();
    const refresh = runtime.page.onPullDownRefresh.call(runtime.page);
    await flushMicrotasks();

    releaseStaleList?.();
    await flushMicrotasks();
    expect(
      runtime.page.data.items.find((item) => item.questionId === "q-current")?.mutationStatus,
    ).toBe("saving");

    releaseWrite?.();
    await cancellation;
    await flushMicrotasks();
    expect(releaseAuthority).toBeTypeOf("function");
    expect(runtime.page.data.items.some((item) => item.questionId === "q-current")).toBe(false);

    releaseAuthority?.();
    await refresh;
    await flushMicrotasks();
    expect(runtime.page.data.items).toEqual([]);
    expect(hasSavingItem(runtime.page)).toBe(false);
    runtime.page.onUnload.call(runtime.page);
  });

  it("reuses the unknown cancellation key after an overlapping stale list settles", async () => {
    let deliveredInitial = false;
    let releaseStaleList: (() => void) | undefined;
    let writeCount = 0;
    let listCount = 0;
    const idempotencyKeys: string[] = [];
    const runtime = await createFavoritesPage((options) => {
      if (isListRequest(options)) {
        listCount += 1;
        if (!deliveredInitial) {
          deliveredInitial = true;
          options.success?.(apiSuccess(favoritePage([favoriteItem("q-current")], "cursor-stale")));
          return true;
        }
        if (options.url.includes("cursor=")) {
          releaseStaleList = () =>
            options.success?.(apiSuccess(favoritePage([favoriteItem("q-current")])));
          return true;
        }
        options.success?.(
          apiSuccess(favoritePage(writeCount === 1 ? [favoriteItem("q-current")] : [])),
        );
        return true;
      }
      if (isStateRequest(options)) {
        options.success?.(
          apiSuccess(favoriteStateResponse(favoriteQuestionId(options), "favorited")),
        );
        return true;
      }
      if (options.method === "PUT") {
        writeCount += 1;
        idempotencyKeys.push(String(options.header?.["Idempotency-Key"] ?? ""));
        if (writeCount === 1) options.fail?.(requestFailure());
        else options.success?.(apiSuccess(cancelReceipt(favoriteQuestionId(options))));
        return true;
      }
      return false;
    });

    runtime.page.loadMore.call(runtime.page);
    await flushMicrotasks();
    await runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));
    await flushMicrotasks();
    expect(listCount).toBe(3);
    expect(
      runtime.page.data.items.find((item) => item.questionId === "q-current")?.mutationStatus,
    ).toBe("idle");

    releaseStaleList?.();
    await flushMicrotasks();
    await runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));
    await flushMicrotasks();

    expect(idempotencyKeys).toHaveLength(2);
    expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
    expect(listCount).toBe(4);
    expect(runtime.page.data.items).toEqual([]);
    expect(hasSavingItem(runtime.page)).toBe(false);
    runtime.page.onUnload.call(runtime.page);
  });

  it("atomically clears old-account cards and leaves cancelling state after a 401 write", async () => {
    const runtime = await createFavoritesPage((options) => {
      if (options.method !== "PUT") return false;
      options.success?.(apiFailure(401, "登录状态已过期", "favorite-cancel-401"));
      return true;
    });

    await runtime.page.cancelFavorite.call(runtime.page, cancelEvent("q-current"));

    expect(runtime.page.data.status).toBe("unavailable");
    expect(runtime.page.data.items).toEqual([]);
    expect(runtime.page.data.showLoginAction).toBe(true);
    expect(runtime.globalData.accessToken).toBeUndefined();
  });

  it("clears old-account data instead of leaving a loading half-state after generation changes", async () => {
    let releaseList: (() => void) | undefined;
    const runtime = await createFavoritesPage((options) => {
      if (!isListRequest(options)) return false;
      releaseList = () =>
        options.success?.(
          apiSuccess({
            items: [favoriteItem("q-current")],
            nextCursor: null,
            generatedAt: "2026-07-28T12:00:00Z",
          }),
        );
      return true;
    });

    expect(runtime.page.data.status).toBe("loading");
    runtime.globalData.generation = "generation-b";
    releaseList?.();
    await flushMicrotasks();

    expect(runtime.page.data.status).toBe("unavailable");
    expect(runtime.page.data.items).toEqual([]);
    expect(runtime.page.data.showLoginAction).toBe(true);
  });

  it("reloads current account data after hiding and returning", async () => {
    let listItems: QuestionFavoriteListItem[] = [favoriteItem("q-current")];
    const runtime = await createFavoritesPage((options) => {
      if (!isListRequest(options)) return false;
      options.success?.(
        apiSuccess({
          items: listItems,
          nextCursor: null,
          generatedAt: "2026-07-28T12:00:00Z",
        }),
      );
      return true;
    });

    expect(runtime.page.data.items).toHaveLength(1);
    runtime.page.onHide.call(runtime.page);
    listItems = [];
    runtime.page.onShow.call(runtime.page);
    await flushMicrotasks();

    expect(runtime.page.data.status).toBe("ready");
    expect(runtime.page.data.items).toEqual([]);
    expect(runtime.requests.filter(isListRequest)).toHaveLength(2);
    runtime.page.onUnload.call(runtime.page);
  });

  it("appends a live cursor page without duplicating an item already shown", async () => {
    let pageNumber = 0;
    const runtime = await createFavoritesPage((options) => {
      if (!isListRequest(options)) return false;
      pageNumber += 1;
      options.success?.(
        apiSuccess({
          items:
            pageNumber === 1
              ? [favoriteItem("q-current")]
              : [favoriteItem("q-current"), favoriteItem("q-next")],
          nextCursor: pageNumber === 1 ? "cursor-2" : null,
          generatedAt: "2026-07-28T12:00:00Z",
        }),
      );
      return true;
    });

    expect(runtime.page.data.hasMore).toBe(true);
    runtime.page.loadMore.call(runtime.page);
    await flushMicrotasks();

    expect(runtime.page.data.items.map((item) => item.questionId)).toEqual(["q-current", "q-next"]);
    expect(runtime.page.data.hasMore).toBe(false);
    runtime.page.onUnload.call(runtime.page);
  });
});

type FavoritesRequestInterceptor = (options: WechatMiniprogram.RequestOption) => boolean;

async function createFavoritesPage(intercept?: FavoritesRequestInterceptor) {
  let captured: CapturedFavoritesPage | undefined;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const { wxStorage } = createMemoryWechatStorage();
  const cancelledQuestionIds = new Set<string>();
  const globalData = {
    accessToken: "real-token",
    userId: "user-a",
    generation: "generation-a",
  };
  const showToast = vi.fn(() => Promise.resolve());
  const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
    requests.push(options);
    if (intercept?.(options)) return;
    if (isListRequest(options)) {
      options.success?.(
        apiSuccess({
          items: [
            favoriteItem("q-current"),
            favoriteItem("q-updated", { contentChanged: true }),
            favoriteItem("q-unavailable", {
              availability: "unavailable",
              contentChanged: false,
              preview: null,
            }),
          ].filter((item) => !cancelledQuestionIds.has(item.questionId)),
          nextCursor: null,
          generatedAt: "2026-07-28T12:00:00Z",
        }),
      );
      return;
    }
    if (options.method === "PUT") {
      const questionId = favoriteQuestionId(options);
      cancelledQuestionIds.add(questionId);
      options.success?.(
        apiSuccess({
          questionId,
          favoriteState: "not_favorited",
          favoritedAt: null,
          savedContentVersionId: null,
          revision: 2,
          updatedAt: "2026-07-28T12:30:00Z",
        }),
      );
      return;
    }
    throw new Error(`Unexpected favorites request: ${String(options.method)} ${options.url}`);
  });
  vi.stubGlobal("Page", (definition: CapturedFavoritesPage) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("wx", {
    ...wxStorage,
    request,
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
    showToast,
    redirectTo: vi.fn(() => Promise.resolve()),
    stopPullDownRefresh: vi.fn(() => Promise.resolve()),
  });
  const { installUnauthorizedReset } = await import("../miniprogram/api/auth");
  installUnauthorizedReset();
  await import("../miniprogram/pages/xingce-mastery/favorites/index");
  if (!captured) throw new Error("question favorites page was not registered");
  const setData = vi.fn(function (this: TestFavoritesPage, patch: Record<string, unknown>) {
    Object.assign(this.data, patch);
  });
  const page: TestFavoritesPage = {
    ...captured,
    data: JSON.parse(JSON.stringify(captured.data)) as FavoritesPageData,
    setData,
  };
  page.onLoad.call(page);
  page.onShow.call(page);
  await flushMicrotasks();
  return { page, requests, globalData, showToast };
}

interface FavoriteItemView {
  questionId: string;
  availabilityLabel: "当前版本" | "题目已更新" | "内容已下架";
  mutationStatus: "idle" | "saving" | "unknown" | "error";
}

interface FavoritesPageData {
  status: "loading" | "ready" | "error" | "unavailable";
  items: FavoriteItemView[];
  hasMore: boolean;
  showLoginAction: boolean;
}

interface CapturedFavoritesPage {
  data: FavoritesPageData;
  onLoad(this: TestFavoritesPage): void;
  onShow(this: TestFavoritesPage): void;
  onHide(this: TestFavoritesPage): void;
  onUnload(this: TestFavoritesPage): void;
  onPullDownRefresh(this: TestFavoritesPage): Promise<void>;
  loadMore(this: TestFavoritesPage): void;
  cancelFavorite(this: TestFavoritesPage, event: WechatMiniprogram.BaseEvent): Promise<void>;
}

interface TestFavoritesPage extends CapturedFavoritesPage {
  data: FavoritesPageData;
  setData(patch: Record<string, unknown>): void;
}

function isListRequest(options: WechatMiniprogram.RequestOption): boolean {
  return (
    options.method === "GET" &&
    options.url.includes("/api/v2/xingce/question-favorites?") &&
    !options.url.includes("/api/v2/xingce/question-favorites/")
  );
}

function isStateRequest(options: WechatMiniprogram.RequestOption): boolean {
  return (
    options.method === "GET" &&
    options.url.includes("/api/v2/xingce/question-favorites/") &&
    !options.url.includes("?")
  );
}

function favoriteQuestionId(options: WechatMiniprogram.RequestOption): string {
  return decodeURIComponent(options.url.split("?")[0]?.split("/").at(-1) ?? "");
}

function cancelEvent(questionId: string): WechatMiniprogram.BaseEvent {
  return {
    currentTarget: { dataset: { questionid: questionId } },
  } as unknown as WechatMiniprogram.BaseEvent;
}

function favoritePage(items: QuestionFavoriteListItem[], nextCursor: string | null = null) {
  return {
    items,
    nextCursor,
    generatedAt: "2026-07-28T12:00:00Z",
  };
}

function cancelReceipt(questionId: string) {
  return {
    questionId,
    favoriteState: "not_favorited" as const,
    favoritedAt: null,
    savedContentVersionId: null,
    revision: 2,
    updatedAt: "2026-07-28T12:30:00Z",
  };
}

function favoriteStateResponse(questionId: string, favoriteState: "favorited" | "not_favorited") {
  return {
    ...favoriteItem(questionId),
    favoriteState,
  };
}

function favoriteItem(
  questionId: string,
  patch: Partial<QuestionFavoriteListItem> = {},
): QuestionFavoriteListItem {
  return {
    questionId,
    favoriteState: "favorited",
    favoritedAt: "2026-07-28T12:00:00Z",
    savedContentVersionId: "a".repeat(64),
    currentContentVersionId: "a".repeat(64),
    availability: "available",
    contentChanged: false,
    preview: {
      moduleName: "资料分析",
      stem: `题干 ${questionId}`,
    },
    revision: 1,
    updatedAt: "2026-07-28T12:00:00Z",
    ...patch,
  };
}

function requestFailure(): WechatMiniprogram.RequestFailCallbackErr {
  return {
    errMsg: "request:fail timeout",
    errno: -1,
    exception: { reasons: [], retryCount: 0 },
    useHttpDNS: false,
  };
}

function hasSavingItem(page: TestFavoritesPage): boolean {
  return page.data.items.some((item) => item.mutationStatus === "saving");
}

function apiFailure(
  statusCode: number,
  message: string,
  requestId: string,
): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: { code: "UNAUTHORIZED", message, requestId },
    statusCode,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 16; index += 1) await Promise.resolve();
}
