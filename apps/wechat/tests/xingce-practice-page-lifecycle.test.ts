import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  XingceCheckpointCommand,
  XingceTaskMode,
  XingceTaskSession,
} from "../miniprogram/api/xingce-learning";
import {
  activeXingceSession,
  apiSuccess,
  createMemoryWechatStorage,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("xingce practice page lifecycle", () => {
  it("keeps the timer stopped after a hidden-page checkpoint refresh", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    expect(vi.getTimerCount()).toBe(1);
    runtime.page.inputDraft.call(runtime.page, inputEvent("我需要重新检查排除理由。"));
    runtime.page.onHide.call(runtime.page);
    await flushMicrotasks();

    expect(runtime.requests.filter((item) => item.method === "PUT")).toHaveLength(1);
    expect(
      runtime.requests.filter(
        (item) => item.method === "GET" && item.url.includes("/api/v2/xingce/task-sessions/"),
      ),
    ).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
    runtime.page.onUnload.call(runtime.page);
  });

  it("ignores a late checkpoint refresh after the page is unloaded", async () => {
    vi.useFakeTimers();
    let releaseRefresh: (() => void) | undefined;
    const runtime = await createPracticePage((options, session) => {
      if (
        options.method !== "GET" ||
        !options.url.includes(`/api/v2/xingce/task-sessions/${session.sessionId}`)
      ) {
        return false;
      }
      releaseRefresh = () => options.success?.(apiSuccess(session));
      return true;
    });

    runtime.page.inputDraft.call(runtime.page, inputEvent("离页前保存这段草稿。"));
    runtime.page.onHide.call(runtime.page);
    await flushMicrotasks();
    expect(releaseRefresh).toBeTypeOf("function");

    runtime.page.onUnload.call(runtime.page);
    const writesBeforeLateResponse = runtime.setData.mock.calls.length;
    releaseRefresh?.();
    await flushMicrotasks();

    expect(runtime.setData).toHaveBeenCalledTimes(writesBeforeLateResponse);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("starts a server-owned verification task through the same real attempt page", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage(undefined, "verification");

    expect(runtime.page.data.status).toBe("ready");
    expect((runtime.page.data.view as { modeLabel: string }).modeLabel).toBe("独立复测");
    expect(runtime.requests[0]?.method).toBe("POST");
    expect(runtime.requests[0]?.data).toEqual({ planTaskId: xingceIds.planTask });
    runtime.page.onUnload.call(runtime.page);
  });

  it("resets the question pane scroll to the top after switching questions", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    expect(runtime.page.data.questionScrollTop).toBe(0);
    runtime.page.nextQuestion.call(runtime.page);
    // scroll-top 同值不触发：切题后必须写入一个新值来回顶。
    expect(runtime.page.data.questionScrollTop).toBe(0.1);
    await flushMicrotasks();

    runtime.page.selectQuestion.call(runtime.page, sheetTapEvent(0));
    expect(runtime.page.data.questionScrollTop).toBe(0);
    await flushMicrotasks();

    // 答题卡点当前题＝没有切题，不应打断已有的阅读位置。
    runtime.page.selectQuestion.call(runtime.page, sheetTapEvent(0));
    expect(runtime.page.data.questionScrollTop).toBe(0);
    runtime.page.onUnload.call(runtime.page);
  });
});

describe("xingce practice favorite ownership and idempotency", () => {
  it("ignores a late favorite read after switching to another question", async () => {
    vi.useFakeTimers();
    let releaseQuestionOne: (() => void) | undefined;
    const runtime = await createPracticePage((options) => {
      if (!isFavoriteRequest(options, "GET")) return false;
      const questionId = favoriteQuestionId(options);
      if (questionId === "q-1") {
        releaseQuestionOne = () =>
          options.success?.(apiSuccess(favoriteStateResponse("q-1", "not_favorited")));
        return true;
      }
      options.success?.(apiSuccess(favoriteStateResponse(questionId, "favorited")));
      return true;
    });

    expect(runtime.page.data.favoriteQuestionId).toBe("q-1");
    expect(runtime.page.data.favoriteStatus).toBe("loading");

    runtime.page.nextQuestion.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data.favoriteQuestionId).toBe("q-2");
    expect(runtime.page.data.favoriteState).toBe("favorited");

    releaseQuestionOne?.();
    await flushMicrotasks();
    expect(runtime.page.data.favoriteQuestionId).toBe("q-2");
    expect(runtime.page.data.favoriteState).toBe("favorited");
    runtime.page.onUnload.call(runtime.page);
  });

  it("invalidates the whole current page when the same account gets a new session generation", async () => {
    vi.useFakeTimers();
    let releaseFavorite: (() => void) | undefined;
    const runtime = await createPracticePage((options) => {
      if (!isFavoriteRequest(options, "GET")) return false;
      releaseFavorite = () =>
        options.success?.(apiSuccess(favoriteStateResponse("q-1", "favorited")));
      return true;
    });

    runtime.globalData.generation = "generation-b";
    releaseFavorite?.();
    await flushMicrotasks();

    expect(runtime.page.data.status).toBe("unavailable");
    expect(runtime.page.data.view).toBeNull();
    expect(runtime.page.data.favoriteStatus).toBe("idle");
    expect(runtime.page.data.interactionLocked).toBe(true);
  });

  it("invalidates the whole current page when a late response belongs to another account", async () => {
    vi.useFakeTimers();
    let releaseFavorite: (() => void) | undefined;
    const runtime = await createPracticePage((options) => {
      if (!isFavoriteRequest(options, "GET")) return false;
      releaseFavorite = () =>
        options.success?.(apiSuccess(favoriteStateResponse("q-1", "favorited")));
      return true;
    });

    runtime.globalData.userId = "user-b";
    runtime.globalData.generation = "generation-b";
    releaseFavorite?.();
    await flushMicrotasks();

    expect(runtime.page.data.status).toBe("unavailable");
    expect(runtime.page.data.view).toBeNull();
    expect(runtime.page.data.favoriteQuestionId).toBe("");
    expect(runtime.page.data.interactionLocked).toBe(true);
  });

  it("accepts an unknown write only after the authoritative read reaches the desired state", async () => {
    vi.useFakeTimers();
    let writeStarted = false;
    const runtime = await createPracticePage((options) => {
      if (isFavoriteRequest(options, "PUT")) {
        writeStarted = true;
        options.fail?.(requestFailure());
        return true;
      }
      if (isFavoriteRequest(options, "GET")) {
        options.success?.(
          apiSuccess(favoriteStateResponse("q-1", writeStarted ? "favorited" : "not_favorited")),
        );
        return true;
      }
      return false;
    });

    await runtime.page.toggleFavorite.call(runtime.page);

    expect(runtime.page.data.favoriteStatus).toBe("ready");
    expect(runtime.page.data.favoriteState).toBe("favorited");
    expect(runtime.page.data.favoriteMessage).toContain("已收藏");
    runtime.page.onUnload.call(runtime.page);
  });

  it("keeps an unknown write retryable with the same key when the authoritative state is not reached", async () => {
    vi.useFakeTimers();
    let writeCount = 0;
    const idempotencyKeys: string[] = [];
    const runtime = await createPracticePage((options) => {
      if (isFavoriteRequest(options, "PUT")) {
        writeCount += 1;
        idempotencyKeys.push(String(options.header?.["Idempotency-Key"] ?? ""));
        if (writeCount === 1) {
          options.fail?.(requestFailure());
        } else {
          options.success?.(
            apiSuccess({
              questionId: "q-1",
              favoriteState: "favorited",
              favoritedAt: "2026-07-28T12:00:00Z",
              savedContentVersionId: "b".repeat(64),
              revision: 1,
              updatedAt: "2026-07-28T12:00:00Z",
            }),
          );
        }
        return true;
      }
      if (isFavoriteRequest(options, "GET")) {
        options.success?.(apiSuccess(favoriteStateResponse("q-1", "not_favorited")));
        return true;
      }
      return false;
    });

    await runtime.page.toggleFavorite.call(runtime.page);
    expect(runtime.page.data.favoriteStatus).toBe("unknown");
    expect(runtime.page.data.favoriteDesiredState).toBe("favorited");

    await runtime.page.toggleFavorite.call(runtime.page);
    expect(idempotencyKeys).toHaveLength(2);
    expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
    expect(runtime.page.data.favoriteStatus).toBe("ready");
    expect(runtime.page.data.favoriteState).toBe("favorited");
    runtime.page.onUnload.call(runtime.page);
  });

  it("coalesces repeated taps while a favorite write is still in flight", async () => {
    vi.useFakeTimers();
    let releaseWrite: (() => void) | undefined;
    const runtime = await createPracticePage((options) => {
      if (!isFavoriteRequest(options, "PUT")) return false;
      releaseWrite = () =>
        options.success?.(
          apiSuccess({
            questionId: "q-1",
            favoriteState: "favorited",
            favoritedAt: "2026-07-28T12:00:00Z",
            savedContentVersionId: "b".repeat(64),
            revision: 1,
            updatedAt: "2026-07-28T12:00:00Z",
          }),
        );
      return true;
    });

    const first = runtime.page.toggleFavorite.call(runtime.page);
    const second = runtime.page.toggleFavorite.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.requests.filter((item) => isFavoriteRequest(item, "PUT"))).toHaveLength(1);

    releaseWrite?.();
    await Promise.all([first, second]);
    expect(runtime.page.data.favoriteState).toBe("favorited");
    runtime.page.onUnload.call(runtime.page);
  });

  it("atomically leaves saving state and clears old-account content after a 401 write", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage((options) => {
      if (!isFavoriteRequest(options, "PUT")) return false;
      options.success?.(apiFailure(401, "登录状态已过期", "favorite-write-401"));
      return true;
    });

    await runtime.page.toggleFavorite.call(runtime.page);

    expect(runtime.page.data.status).toBe("unavailable");
    expect(runtime.page.data.view).toBeNull();
    expect(runtime.page.data.favoriteStatus).toBe("idle");
    expect(runtime.page.data.favoriteButtonLabel).toBe("收藏");
    expect(runtime.page.data.interactionLocked).toBe(true);
    expect(runtime.globalData.accessToken).toBeUndefined();
  });

  it("force-refreshes the authoritative state after hiding and returning", async () => {
    vi.useFakeTimers();
    let state: "favorited" | "not_favorited" = "not_favorited";
    const runtime = await createPracticePage((options) => {
      if (!isFavoriteRequest(options, "GET")) return false;
      options.success?.(apiSuccess(favoriteStateResponse("q-1", state)));
      return true;
    });

    expect(runtime.page.data.favoriteState).toBe("not_favorited");
    runtime.page.onHide.call(runtime.page);
    await flushMicrotasks();
    state = "favorited";
    runtime.page.onShow.call(runtime.page);
    await flushMicrotasks();

    expect(runtime.page.data.favoriteStatus).toBe("ready");
    expect(runtime.page.data.favoriteState).toBe("favorited");
    expect(runtime.requests.filter((item) => isFavoriteRequest(item, "GET"))).toHaveLength(2);
    runtime.page.onUnload.call(runtime.page);
  });
});

describe("xingce practice swipe question navigation", () => {
  it("switches to the next question on a fast leftward swipe with a one-shot transition", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
    expect(currentOf(runtime.page)).toBe(2);
    expect(runtime.page.data.questionTransition).toBe("next");
    // 与既有回顶逻辑共存：切题必须写入新的 scroll-top 值。
    expect(runtime.page.data.questionScrollTop).toBe(0.1);
    await flushMicrotasks();

    // 转场类是一次性的：动画结束事件负责清空。
    runtime.page.clearQuestionTransition.call(runtime.page);
    expect(runtime.page.data.questionTransition).toBe("");

    swipe(runtime.page, { fromX: 120, toX: 290, y: 400 });
    expect(currentOf(runtime.page)).toBe(1);
    expect(runtime.page.data.questionTransition).toBe("prev");
    await flushMicrotasks();
    runtime.page.onUnload.call(runtime.page);
  });

  it("prefers vertical scrolling: short, slanted or slow horizontal moves never switch", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    // 位移不足 80px。
    swipe(runtime.page, { fromX: 300, toX: 230, y: 400 });
    // 斜滑：纵向位移过大，横向不占优。
    swipe(runtime.page, { fromX: 300, toX: 160, y: 400, toY: 520 });
    // 慢拖：时间窗内速度不足。
    swipe(runtime.page, { fromX: 300, toX: 180, y: 400, durationMs: 650 });
    expect(currentOf(runtime.page)).toBe(1);
    expect(runtime.page.data.questionTransition).toBe("");
    runtime.page.onUnload.call(runtime.page);
  });

  it("stays put while any overlay, annotation mode or interaction lock is active", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    const guards = [
      { answerSheetOpen: true },
      { draftOpen: true },
      { toolsOpen: true },
      { helpOpen: true },
      { disputeOpen: true },
      { ananContextOpen: true },
      { annotationOpen: true },
      { materialOpen: true },
      { selection: { key: "stem-1", text: "选中句", toolbarTop: 120 } },
      { interactionLocked: true },
      { taskPaused: true },
    ];
    for (const guard of guards) {
      const restore = applyGuard(runtime.page, guard);
      swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
      expect(currentOf(runtime.page)).toBe(1);
      restore();
    }
    // 守卫恢复后同一手势立即生效，证明守卫矩阵是唯一拦截原因。
    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
    expect(currentOf(runtime.page)).toBe(2);
    runtime.page.onUnload.call(runtime.page);
  });

  it("never submits from a swipe on the last question and ignores prev on the first", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    // 首题右滑：无前题，轻提示后原地不动。
    swipe(runtime.page, { fromX: 120, toX: 290, y: 400 });
    expect(currentOf(runtime.page)).toBe(1);

    runtime.page.nextQuestion.call(runtime.page);
    await flushMicrotasks();
    runtime.page.nextQuestion.call(runtime.page);
    await flushMicrotasks();
    expect(currentOf(runtime.page)).toBe(3);

    // 末题左滑：底部按钮语义是交卷，手势绝不能代点交卷。
    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
    await flushMicrotasks();
    expect(currentOf(runtime.page)).toBe(3);
    expect(runtime.page.data.questionTransition).toBe("");
    expect(runtime.requests.some((request) => request.url.endsWith("/submit"))).toBe(false);
    runtime.page.onUnload.call(runtime.page);
  });
});

type RequestInterceptor = (
  options: WechatMiniprogram.RequestOption,
  session: XingceTaskSession,
) => boolean;

async function createPracticePage(
  intercept?: RequestInterceptor,
  mode: XingceTaskMode = "targeted",
) {
  let captured: CapturedPracticePage | undefined;
  let session = activeXingceSession({ mode });
  const requests: WechatMiniprogram.RequestOption[] = [];
  const { wxStorage } = createMemoryWechatStorage();
  const globalData = {
    accessToken: "real-token",
    userId: "user-a",
    generation: "generation-a",
  };
  const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
    requests.push(options);
    if (options.url.endsWith("/api/v2/task-editing/leases")) {
      options.success?.(
        apiSuccess({
          state: "owned",
          resourceType: "xingce_task_session",
          resourceId: session.sessionId,
          leaseId: "00000000-0000-4000-8000-000000000901",
          generation: 1,
          deviceId: "mp-device-test-000000000000000001",
          deviceLabel: "当前微信设备",
          serverRevision: session.revision,
          localBaseRevision: null,
          acquiredAt: "2026-07-19T00:00:00Z",
          lastSeenAt: "2026-07-19T00:00:00Z",
          takenOverAt: null,
        }),
      );
      return;
    }
    if (intercept?.(options, session)) return;
    if (options.url.includes("/api/v2/xingce/question-favorites/")) {
      const questionId = decodeURIComponent(options.url.split("/").at(-1) ?? "");
      if (options.method === "GET") {
        options.success?.(
          apiSuccess({
            questionId,
            favoriteState: "not_favorited",
            favoritedAt: null,
            savedContentVersionId: null,
            currentContentVersionId: "b".repeat(64),
            availability: "available",
            contentChanged: false,
            revision: 0,
            updatedAt: null,
          }),
        );
        return;
      }
      const desiredState = (options.data as { desiredState: "favorited" | "not_favorited" })
        .desiredState;
      options.success?.(
        apiSuccess({
          questionId,
          favoriteState: desiredState,
          favoritedAt: desiredState === "favorited" ? "2026-07-28T12:00:00Z" : null,
          savedContentVersionId: desiredState === "favorited" ? "b".repeat(64) : null,
          revision: 1,
          updatedAt: "2026-07-28T12:00:00Z",
        }),
      );
      return;
    }
    if (options.method === "POST") {
      options.success?.(apiSuccess(session));
      return;
    }
    if (options.method === "PUT") {
      const command = options.data as XingceCheckpointCommand;
      session = activeXingceSession({
        mode,
        revision: command.expectedRevision + 1,
        answers: command.answers,
      });
      session = {
        ...session,
        currentQuestionId: command.currentQuestionId,
        totalElapsedSeconds: command.totalElapsedSeconds,
      };
      options.success?.(apiSuccess(session));
      return;
    }
    options.success?.(apiSuccess(session));
  });
  vi.stubGlobal("Page", (definition: CapturedPracticePage) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("wx", {
    ...wxStorage,
    request,
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(9).buffer }),
    showToast: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    navigateBack: vi.fn(() => Promise.resolve()),
    redirectTo: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
  });
  const { installUnauthorizedReset } = await import("../miniprogram/api/auth");
  installUnauthorizedReset();
  await import("../miniprogram/pages/practice/index");
  if (!captured) throw new Error("practice page was not registered");
  const setData = vi.fn(function (this: TestPracticePage, patch: Record<string, unknown>) {
    Object.assign(this.data, patch);
  });
  const page: TestPracticePage = {
    ...captured,
    data: JSON.parse(JSON.stringify(captured.data)) as Record<string, unknown>,
    setData,
  };
  page.onLoad.call(page, { mode, planTaskId: xingceIds.planTask });
  page.onShow.call(page);
  await flushMicrotasks();
  expect(page.data.status).toBe("ready");
  return { page, requests, setData, globalData };
}

interface CapturedPracticePage {
  data: Record<string, unknown>;
  onLoad(this: TestPracticePage, options: Record<string, string | undefined>): void;
  onShow(this: TestPracticePage): void;
  onHide(this: TestPracticePage): void;
  onUnload(this: TestPracticePage): void;
  loadTask(this: TestPracticePage): Promise<void>;
  inputDraft(this: TestPracticePage, event: WechatMiniprogram.Input): void;
  nextQuestion(this: TestPracticePage): void;
  selectQuestion(this: TestPracticePage, event: WechatMiniprogram.BaseEvent): void;
  onQuestionTouchStart(this: TestPracticePage, event: WechatMiniprogram.TouchEvent): void;
  onQuestionTouchMove(this: TestPracticePage, event: WechatMiniprogram.TouchEvent): void;
  onQuestionTouchEnd(this: TestPracticePage, event: WechatMiniprogram.TouchEvent): void;
  clearQuestionTransition(this: TestPracticePage): void;
  toggleFavorite(this: TestPracticePage): Promise<void>;
}

interface TestPracticePage extends CapturedPracticePage {
  data: Record<string, unknown>;
  setData(patch: Record<string, unknown>): void;
}

function isFavoriteRequest(
  options: WechatMiniprogram.RequestOption,
  method: "GET" | "PUT",
): boolean {
  return options.method === method && options.url.includes("/api/v2/xingce/question-favorites/");
}

function favoriteQuestionId(options: WechatMiniprogram.RequestOption): string {
  return decodeURIComponent(options.url.split("/").at(-1) ?? "");
}

function favoriteStateResponse(questionId: string, favoriteState: "favorited" | "not_favorited") {
  return {
    questionId,
    favoriteState,
    favoritedAt: favoriteState === "favorited" ? "2026-07-28T12:00:00Z" : null,
    savedContentVersionId: favoriteState === "favorited" ? "b".repeat(64) : null,
    currentContentVersionId: "b".repeat(64),
    availability: "available",
    contentChanged: false,
    revision: favoriteState === "favorited" ? 1 : 0,
    updatedAt: favoriteState === "favorited" ? "2026-07-28T12:00:00Z" : null,
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

function inputEvent(value: string): WechatMiniprogram.Input {
  return { detail: { value } } as WechatMiniprogram.Input;
}

function sheetTapEvent(index: number): WechatMiniprogram.BaseEvent {
  return { currentTarget: { dataset: { index } } } as unknown as WechatMiniprogram.BaseEvent;
}

function currentOf(page: TestPracticePage): number {
  return (page.data.view as { current: number }).current;
}

/** 覆写守卫字段并返回恢复函数。 */
function applyGuard(page: TestPracticePage, guard: Record<string, unknown>): () => void {
  const previous: Record<string, unknown> = {};
  for (const key of Object.keys(guard)) previous[key] = page.data[key];
  Object.assign(page.data, guard);
  return () => {
    Object.assign(page.data, previous);
  };
}

function touchEvent(x: number, y: number, timeStamp: number, ended = false) {
  const touch = { clientX: x, clientY: y };
  return {
    timeStamp,
    touches: ended ? [] : [touch],
    changedTouches: [touch],
  } as unknown as WechatMiniprogram.TouchEvent;
}

/** 默认参数构成一次合法的快滑（160px / 180ms）；可分别调低距离、角度或速度。 */
function swipe(
  page: TestPracticePage,
  options: { fromX: number; toX: number; y: number; toY?: number; durationMs?: number },
): void {
  const startAt = 10_000;
  const endAt = startAt + (options.durationMs ?? 180);
  page.onQuestionTouchStart.call(page, touchEvent(options.fromX, options.y, startAt));
  page.onQuestionTouchMove.call(
    page,
    touchEvent((options.fromX + options.toX) / 2, options.y, (startAt + endAt) / 2),
  );
  page.onQuestionTouchEnd.call(
    page,
    touchEvent(options.toX, options.toY ?? options.y, endAt, true),
  );
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}
