import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  XingceCheckpointCommand,
  XingceTaskMode,
  XingceTaskSession,
} from "../miniprogram/xingce/shared/api/xingce-learning";
import {
  activeXingceSession,
  apiSuccess,
  createMemoryWechatStorage,
  submittedXingceSession,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";
import {
  loadLocalXingceCheckpointDraft,
  loadPendingXingceCheckpoint,
} from "../miniprogram/pages/practice/shared/features/xingce/real_attempt/pending-store";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import questionToolbarTemplate from "../miniprogram/pages/practice/shared/task-runtime/question-toolbar/index.wxml?raw";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("xingce practice page lifecycle", () => {
  it("remeasures the question content after an orientation change", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();
    const measurementsBeforeResize = runtime.setData.mock.calls.filter(([patch]) =>
      Object.hasOwn(patch, "questionContentHeight"),
    ).length;

    runtime.page.onResize.call(runtime.page);
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(120);

    const measurementsAfterResize = runtime.setData.mock.calls.filter(([patch]) =>
      Object.hasOwn(patch, "questionContentHeight"),
    ).length;
    expect(measurementsAfterResize).toBe(measurementsBeforeResize + 2);
    runtime.page.onUnload.call(runtime.page);
  });

  it("keeps the timer stopped and avoids a duplicate refresh after a hidden-page save", async () => {
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
    ).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
    runtime.page.onUnload.call(runtime.page);
  });

  it("awaits the current checkpoint before asking 岸岸 without creating a second save", async () => {
    let confirmCheckpoint: (() => void) | undefined;
    const runtime = await createPracticePage((options, session) => {
      if (options.method === "PUT" && options.url.endsWith("/checkpoint")) {
        const command = options.data as XingceCheckpointCommand;
        confirmCheckpoint = () =>
          options.success?.(
            apiSuccess({
              ...session,
              revision: command.expectedRevision + 1,
              answers: command.answers,
              currentQuestionId: command.currentQuestionId,
              totalElapsedSeconds: command.totalElapsedSeconds,
            }),
          );
        return true;
      }
      if (options.url.endsWith("/api/v2/companion/ask")) {
        options.success?.(
          apiSuccess({
            answer: "先确认你刚保存的选择，再看题干限定。",
            companionIntent: "thinking",
          }),
        );
        return true;
      }
      return false;
    });
    runtime.page.data.learnerStateVersion = "profile-version-1";

    runtime.page.selectOption.call(runtime.page, {
      currentTarget: { dataset: { optionid: "B" } },
    } as unknown as WechatMiniprogram.BaseEvent);
    await flushMicrotasks();
    expect(confirmCheckpoint).toBeTypeOf("function");

    const ask = runtime.page.askAnan.call(runtime.page, "我刚才为什么会选 B？");
    await flushMicrotasks();
    expect(runtime.requests.filter((item) => item.url.endsWith("/checkpoint"))).toHaveLength(1);
    expect(runtime.requests.some((item) => item.url.endsWith("/api/v2/companion/ask"))).toBe(false);

    confirmCheckpoint?.();
    await ask;
    const checkpointIndex = runtime.requests.findIndex((item) => item.url.endsWith("/checkpoint"));
    const companionIndex = runtime.requests.findIndex((item) =>
      item.url.endsWith("/api/v2/companion/ask"),
    );
    expect(checkpointIndex).toBeGreaterThanOrEqual(0);
    expect(companionIndex).toBeGreaterThan(checkpointIndex);
    expect(runtime.page.data.ananReply).toBe("先确认你刚保存的选择，再看题干限定。");
    runtime.page.onUnload.call(runtime.page);
  });

  it("blocks 岸岸 when the current checkpoint is not confirmed and keeps the retry fact", async () => {
    const runtime = await createPracticePage((options) => {
      if (options.method !== "PUT" || !options.url.endsWith("/checkpoint")) return false;
      options.fail?.(requestFailure());
      return true;
    });
    runtime.page.data.learnerStateVersion = "profile-version-1";

    runtime.page.selectOption.call(runtime.page, {
      currentTarget: { dataset: { optionid: "B" } },
    } as unknown as WechatMiniprogram.BaseEvent);
    await runtime.page.askAnan.call(runtime.page, "我刚才为什么会选 B？");
    await flushMicrotasks();

    expect(runtime.requests.some((item) => item.url.endsWith("/api/v2/companion/ask"))).toBe(false);
    expect(runtime.page.data.ananError).toContain("进度还没保存好");
    expect(loadPendingXingceCheckpoint("user-a", xingceIds.session)).not.toBeNull();
    runtime.page.onUnload.call(runtime.page);
  });

  it("retries a failed draft and both scratch surfaces with the same checkpoint command", async () => {
    vi.useFakeTimers();
    let checkpointAttempts = 0;
    const runtime = await createPracticePage(
      (options) => {
        if (options.method === "PUT" && options.url.endsWith("/checkpoint")) {
          checkpointAttempts += 1;
          if (checkpointAttempts === 1) {
            options.fail?.(requestFailure());
            return true;
          }
        }
        if (options.url.endsWith("/submit")) {
          options.success?.(apiSuccess(submittedXingceSession("incorrect")));
          return true;
        }
        return false;
      },
      "targeted",
      { sharedMaterial: true },
    );
    const questionStroke = annotationStroke("question-stroke-retry", "question");
    const materialStroke = annotationStroke("material-stroke-retry", "material");

    runtime.page.inputDraft.call(runtime.page, inputEvent("先圈分母，再核对增长率。"));
    runtime.page.changeAnnotations.call(runtime.page, annotationEvent(questionStroke));
    runtime.page.changeMaterialAnnotations.call(runtime.page, annotationEvent(materialStroke));
    runtime.page.onHide.call(runtime.page);
    await flushMicrotasks();

    const failed = runtime.requests.find(
      (request) => request.method === "PUT" && request.url.endsWith("/checkpoint"),
    );
    const failedCommand = failed?.data as XingceCheckpointCommand | undefined;
    const failedAnswer = failedCommand?.answers.find(({ questionId }) => questionId === "q-1");
    expect(runtime.page.data.hasPendingCheckpoint).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(loadPendingXingceCheckpoint("user-a", xingceIds.session)?.command).toEqual(
      failedCommand,
    );
    expect(loadLocalXingceCheckpointDraft("user-a", xingceIds.session)?.command).toEqual(
      failedCommand,
    );
    expect(failedAnswer).toMatchObject({
      draftText: "先圈分母，再核对增长率。",
    });
    expect(failedAnswer?.annotations).toEqual([questionStroke, materialStroke]);
    const failedActionTypes = failedAnswer?.actionEvents?.map(({ type }) => type) ?? [];
    expect(failedActionTypes).toContain("draft_edited");
    expect(failedActionTypes).toContain("annotation_added");

    await runtime.page.retryPendingCheckpoint.call(runtime.page);
    await flushMicrotasks();
    const checkpoints = runtime.requests.filter(
      (request) => request.method === "PUT" && request.url.endsWith("/checkpoint"),
    );
    expect(checkpoints).toHaveLength(2);
    expect(checkpoints[1]?.header?.["Idempotency-Key"]).toBe(
      checkpoints[0]?.header?.["Idempotency-Key"],
    );
    expect(checkpoints[1]?.data).toEqual(checkpoints[0]?.data);
    expect(loadPendingXingceCheckpoint("user-a", xingceIds.session)).toBeNull();
    expect(loadLocalXingceCheckpointDraft("user-a", xingceIds.session)).toBeNull();

    runtime.page.onShow.call(runtime.page);
    runtime.page.selectOption.call(runtime.page, {
      currentTarget: { dataset: { optionid: "B" } },
    } as unknown as WechatMiniprogram.BaseEvent);
    await flushMicrotasks();
    await runtime.page.submitAttempt.call(runtime.page);
    await flushMicrotasks();
    const finalCheckpoint = [...runtime.requests]
      .reverse()
      .find((request) => request.method === "PUT" && request.url.endsWith("/checkpoint"));
    const finalCommand = finalCheckpoint?.data as XingceCheckpointCommand | undefined;
    if (!finalCommand) throw new Error("final checkpoint command was not captured");
    const finalAnswer = finalCommand.answers.find(({ questionId }) => questionId === "q-1");
    expect(finalAnswer).toMatchObject({
      questionId: "q-1",
      draftText: "先圈分母，再核对增长率。",
      annotations: [questionStroke, materialStroke],
    });
    const submission = runtime.requests.find((request) => request.url.endsWith("/submit"));
    const submissionCommand = submission?.data as { expectedRevision?: number } | undefined;
    expect(submissionCommand?.expectedRevision).toBe(finalCommand.expectedRevision + 1);
    runtime.page.onUnload.call(runtime.page);
  });

  it("rebuilds a page from the failed command with a new resolved idempotency key", async () => {
    vi.useFakeTimers();
    let checkpointAttempts = 0;
    const storage = createMemoryWechatStorage();
    const intercept: RequestInterceptor = (options) => {
      if (options.method !== "PUT" || !options.url.endsWith("/checkpoint")) return false;
      checkpointAttempts += 1;
      if (checkpointAttempts === 1) {
        options.fail?.(requestFailure());
        return true;
      }
      return false;
    };
    const first = await createPracticePage(intercept, "targeted", {
      sharedMaterial: true,
      storage,
    });
    const questionStroke = annotationStroke("question-stroke-rebuild", "question");
    const materialStroke = annotationStroke("material-stroke-rebuild", "material");
    first.page.inputDraft.call(first.page, inputEvent("重建后也要保留的草稿"));
    first.page.changeAnnotations.call(first.page, annotationEvent(questionStroke));
    first.page.changeMaterialAnnotations.call(first.page, annotationEvent(materialStroke));
    first.page.onHide.call(first.page);
    await flushMicrotasks();
    const failed = first.requests.find(
      (request) => request.method === "PUT" && request.url.endsWith("/checkpoint"),
    );
    first.page.onUnload.call(first.page);

    vi.resetModules();
    const rebuilt = await createPracticePage(intercept, "targeted", {
      sharedMaterial: true,
      storage,
    });
    const recovered = rebuilt.requests.find(
      (request) => request.method === "PUT" && request.url.endsWith("/checkpoint"),
    );
    expect(recovered?.data).toEqual(failed?.data);
    expect(recovered?.header?.["Idempotency-Key"]).not.toBe(failed?.header?.["Idempotency-Key"]);
    expect(recovered?.header?.["Idempotency-Key"]).toContain("xingce-save-resolved");
    const view = rebuilt.page.data.view as {
      question: { draftText: string; annotations: Array<{ strokeId: string }> };
    };
    expect(view.question.draftText).toBe("重建后也要保留的草稿");
    expect(view.question.annotations.map(({ strokeId }) => strokeId)).toEqual([
      "question-stroke-rebuild",
      "material-stroke-rebuild",
    ]);
    rebuilt.page.onUnload.call(rebuilt.page);
  });

  it("updates only timer leaves each second instead of rerendering the complete question", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();
    const callsBeforeTick = runtime.setData.mock.calls.length;

    await vi.advanceTimersByTimeAsync(1_000);

    const tickWrites = runtime.setData.mock.calls.slice(callsBeforeTick).map(([patch]) => patch);
    expect(tickWrites).toContainEqual(
      expect.objectContaining({
        "view.elapsedLabel": "00:13",
        "view.question.elapsedLabel": "00:01",
      }),
    );
    expect(tickWrites.some((patch) => Object.hasOwn(patch, "view"))).toBe(false);
    runtime.page.onUnload.call(runtime.page);
  });

  it("keeps the complete question mounted across the periodic autosave", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();
    runtime.page.inputDraft.call(runtime.page, inputEvent("等待自动保存的草稿"));
    const callsBeforeAutosave = runtime.setData.mock.calls.length;

    // 初始恢复和定时器启动可能跨过一个秒边界，多推进 1 秒覆盖第一个
    // 确定触发的 15 秒自动保存窗口。
    await vi.advanceTimersByTimeAsync(16_000);
    await flushMicrotasks();

    expect(runtime.requests.filter((item) => item.method === "PUT")).toHaveLength(1);
    const autosaveWrites: Array<Record<string, unknown>> = runtime.setData.mock.calls
      .slice(callsBeforeAutosave)
      .map(([patch]) => patch);
    expect(autosaveWrites.some((patch) => Object.hasOwn(patch, "view"))).toBe(false);
    const statusWrite = autosaveWrites.find((patch) => Object.hasOwn(patch, "view.syncLabel"));
    expect(typeof statusWrite?.["view.syncLabel"]).toBe("string");
    expect(typeof statusWrite?.["view.question.elapsedSeconds"]).toBe("number");
    runtime.page.onUnload.call(runtime.page);
  });

  it("ignores a late checkpoint response after the page is unloaded", async () => {
    vi.useFakeTimers();
    let releaseRefresh: (() => void) | undefined;
    const runtime = await createPracticePage((options, session) => {
      if (
        options.method !== "PUT" ||
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

  it("opens the drawable draft directly from the question pen without a mode chooser", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    expect(practiceTemplate).toContain("<question-toolbar");
    expect(practiceTemplate).toContain('binddraft="openAnnotation"');
    expect(questionToolbarTemplate).toContain('class="question-tool question-tool--draft"');
    expect(questionToolbarTemplate).toContain('src="/assets/icons/task/draft.svg"');
    expect(questionToolbarTemplate).toContain('aria-label="打开草稿"');
    expect(questionToolbarTemplate).not.toContain('class="question-tool question-tool--more"');
    expect(questionToolbarTemplate).toContain("'已存疑' : '存疑'");
    expect(questionToolbarTemplate).not.toContain("'已标记' : '标记'");

    Object.assign(runtime.page.data, {
      toolsOpen: true,
      draftOpen: true,
      answerSheetOpen: true,
      ananContextOpen: true,
      selection: { key: "stem-1", text: "题干", toolbarTop: 100 },
    });
    runtime.page.openAnnotation.call(runtime.page);

    expect(runtime.page.data).toMatchObject({
      annotationOpen: true,
      annotationSurface: "question",
      annotationMode: "annotate",
      touchOwner: "annotation",
      swipeBlocked: true,
    });
    expect(runtime.page.data.toolsOpen).toBe(false);
    expect(runtime.page.data.draftOpen).toBe(false);
    expect(runtime.page.data.answerSheetOpen).toBe(false);
    expect(runtime.page.data.ananContextOpen).toBe(false);
    expect(runtime.page.data.selection).toEqual({
      key: "",
      text: "",
      toolbarTop: 0,
      hasCancel: false,
      anchorRect: null,
    });
    runtime.page.onUnload.call(runtime.page);
  });

  it("leaves drawing mode before opening secondary settings and help", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();
    Object.assign(runtime.page.data, {
      annotationOpen: true,
      ananContextOpen: true,
      selection: { key: "stem-1", text: "题干", toolbarTop: 100 },
    });

    runtime.page.openTaskTools.call(runtime.page);

    expect(runtime.page.data.toolsOpen).toBe(true);
    expect(runtime.page.data.annotationOpen).toBe(false);
    expect(runtime.page.data.annotationSurface).toBe("");
    expect(runtime.page.data.annotationMode).toBe("static");
    expect(runtime.page.data.touchOwner).toBe("overlay");
    expect(runtime.page.data.ananContextOpen).toBe(false);
    expect(runtime.page.data.selection).toEqual({
      key: "",
      text: "",
      toolbarTop: 0,
      hasCancel: false,
      anchorRect: null,
    });
    runtime.page.onUnload.call(runtime.page);
  });

  it("releases the physical drawing surface on tools, question and lifecycle transitions", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();

    expect(practiceTemplate).toContain('data-touch-owner="{{touchOwner}}"');
    expect(practiceTemplate).toContain('interactionActive="{{materialCaptureActive}}"');
    expect(practiceTemplate).toContain('wx:if="{{pauseCaptureActive}}"');
    expect(practiceTemplate).not.toContain('wx:if="{{taskPaused}}" class="pause-mask');
    expect(practiceTemplate).toContain(
      "wx:if=\"{{annotationMode === 'annotate' || questionAnnotations.length}}\"",
    );
    expect(practiceTemplate).toContain(
      "mode=\"{{annotationMode === 'annotate' && annotationSurface === 'question' ? 'annotate' : 'static'}}\"",
    );

    runtime.page.openAnnotation.call(runtime.page);
    runtime.page.openTaskTools.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      annotationOpen: false,
      annotationSurface: "",
      annotationMode: "static",
      touchOwner: "overlay",
    });

    runtime.page.closeTaskOverlay.call(runtime.page);
    runtime.page.openAnnotation.call(runtime.page);
    runtime.page.nextQuestion.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      annotationOpen: false,
      annotationSurface: "",
      annotationMode: "static",
    });
    expect(runtime.page.data.touchOwner).not.toBe("annotation");
    await flushMicrotasks();
    expect(runtime.page.data.touchOwner).toBe("question");

    runtime.page.openAnnotation.call(runtime.page);
    runtime.page.onHide.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      annotationOpen: false,
      annotationSurface: "",
      interactionLocked: true,
      touchOwner: "none",
    });
    runtime.page.onShow.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      interactionLocked: false,
      touchOwner: "question",
    });
    runtime.page.onUnload.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      annotationOpen: false,
      interactionLocked: true,
      touchOwner: "none",
    });
  });

  it("projects pause and material capture through the same lifecycle authority", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage(undefined, "targeted", { sharedMaterial: true });

    runtime.page.toggleTaskPause.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      interactionMode: "paused",
      taskPaused: true,
      materialOpen: false,
      pauseCaptureActive: true,
      materialCaptureActive: false,
      captureLayer: "pause",
      touchOwner: "overlay",
      swipeBlocked: true,
    });

    runtime.page.onHide.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      taskPaused: true,
      pauseCaptureActive: false,
      captureLayer: "none",
      touchOwner: "none",
    });
    runtime.page.onShow.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      taskPaused: true,
      pauseCaptureActive: true,
      captureLayer: "pause",
      touchOwner: "overlay",
    });

    runtime.page.toggleTaskPause.call(runtime.page);
    await flushMicrotasks();
    runtime.page.toggleMaterial.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      interactionMode: "material",
      taskPaused: false,
      materialOpen: true,
      materialCaptureActive: true,
      captureLayer: "material",
      touchOwner: "material",
      swipeBlocked: true,
    });

    runtime.page.onHide.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      materialOpen: true,
      materialCaptureActive: false,
      captureLayer: "none",
      touchOwner: "none",
    });
    runtime.page.onShow.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      materialOpen: true,
      materialCaptureActive: true,
      captureLayer: "material",
      touchOwner: "material",
    });
    await flushMicrotasks();

    runtime.page.nextQuestion.call(runtime.page);
    await flushMicrotasks();
    expect(runtime.page.data).toMatchObject({
      interactionMode: "question",
      materialOpen: false,
      materialCaptureActive: false,
      captureLayer: "none",
      touchOwner: "question",
    });
    expect(runtime.page.data.view).toMatchObject({ current: 2 });

    runtime.page.toggleMaterial.call(runtime.page);
    runtime.page.onUnload.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      interactionMode: "question",
      materialOpen: false,
      materialCaptureActive: false,
      pauseCaptureActive: false,
      captureLayer: "none",
      touchOwner: "none",
    });
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
    expect(runtime.page.data.favoriteMessage).toContain("收藏好啦");
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
  it("settles both touchend and native scroll-view touchcancel on the question pane", () => {
    expect(practiceTemplate).toContain('bindtouchend="onQuestionTouchEnd"');
    expect(practiceTemplate).toContain('bindtouchcancel="onQuestionTouchEnd"');
  });

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

  it("switches from the scrollable middle when native scrolling finishes with touchcancel", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();
    const startAt = 10_000;

    runtime.page.onQuestionTouchStart.call(runtime.page, touchEvent(300, 400, startAt));
    runtime.page.onQuestionTouchMove.call(runtime.page, touchEvent(130, 406, startAt + 160));
    // 微信 scroll-view 接管手势后，touchcancel 可能把 changedTouches 补成 (0,0)；
    // WXML 将 cancel 交给同一结算函数，必须使用最后一个真实 move 触点。
    runtime.page.onQuestionTouchEnd.call(runtime.page, {
      type: "touchcancel",
      timeStamp: startAt + 170,
      touches: [],
      changedTouches: [{ clientX: 0, clientY: 0 }],
    } as unknown as WechatMiniprogram.TouchEvent);

    expect(currentOf(runtime.page)).toBe(2);
    expect(runtime.page.data.questionTransition).toBe("next");
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

    const overlays: Array<[string, () => void]> = [
      [
        "answer sheet",
        () => {
          runtime.page.toggleAnswerSheet.call(runtime.page);
        },
      ],
      [
        "draft",
        () => {
          runtime.page.toggleDraft.call(runtime.page);
        },
      ],
      [
        "tools",
        () => {
          runtime.page.openTaskTools.call(runtime.page);
        },
      ],
      [
        "help",
        () => {
          runtime.page.openTaskHelp.call(runtime.page);
        },
      ],
      [
        "dispute",
        () => {
          runtime.page.openDispute.call(runtime.page);
        },
      ],
      [
        "companion",
        () => {
          runtime.page.openAnanContext.call(runtime.page);
        },
      ],
      [
        "annotation",
        () => {
          runtime.page.openAnnotation.call(runtime.page);
        },
      ],
    ];
    for (const [label, open] of overlays) {
      open();
      expect(runtime.page.data.touchOwner, label).not.toBe("question");
      swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
      expect(currentOf(runtime.page)).toBe(1);
      runtime.page.closeTaskOverlay.call(runtime.page);
    }
    runtime.page.toggleMaterial.call(runtime.page);
    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
    expect(currentOf(runtime.page)).toBe(1);
    runtime.page.toggleMaterial.call(runtime.page);

    runtime.page.toggleTaskPause.call(runtime.page);
    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
    expect(currentOf(runtime.page)).toBe(1);
    runtime.page.toggleTaskPause.call(runtime.page);
    await flushMicrotasks();

    runtime.page.onHide.call(runtime.page);
    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
    expect(currentOf(runtime.page)).toBe(1);
    runtime.page.onShow.call(runtime.page);
    await flushMicrotasks();
    // 守卫恢复后同一手势立即生效，证明守卫矩阵是唯一拦截原因。
    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });
    expect(currentOf(runtime.page)).toBe(2);
    runtime.page.onUnload.call(runtime.page);
  });

  it("dismisses an accidental stem selection and still switches questions by swipe", async () => {
    vi.useFakeTimers();
    const runtime = await createPracticePage();
    runtime.page.data.selection = {
      key: "stem-1",
      text: "不小心点中的题干",
      toolbarTop: 120,
    };

    swipe(runtime.page, { fromX: 300, toX: 140, y: 400 });

    expect(currentOf(runtime.page)).toBe(2);
    expect(runtime.page.data.selection).toEqual({
      key: "",
      text: "",
      toolbarTop: 0,
      hasCancel: false,
      anchorRect: null,
    });
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

  it("submits a partially answered set and freezes every remaining item as unanswered", async () => {
    const runtime = await createPracticePage((options) => {
      if (!options.url.endsWith("/submit")) return false;
      options.success?.(apiSuccess(submittedXingceSession("incorrect")));
      return true;
    });

    runtime.page.selectOption.call(runtime.page, {
      currentTarget: { dataset: { optionid: "B" } },
    } as unknown as WechatMiniprogram.BaseEvent);
    await flushMicrotasks();
    await runtime.page.submitAttempt.call(runtime.page);
    await flushMicrotasks();

    const finalCheckpoint = [...runtime.requests]
      .reverse()
      .find((request) => request.method === "PUT" && request.url.endsWith("/checkpoint"));
    const answers = (finalCheckpoint?.data as XingceCheckpointCommand | undefined)?.answers;
    expect(answers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ questionId: "q-1", selectedOptionIds: ["B"], skipped: false }),
        expect.objectContaining({ questionId: "q-2", selectedOptionIds: [], skipped: true }),
        expect.objectContaining({ questionId: "q-3", selectedOptionIds: [], skipped: true }),
      ]),
    );
    expect(runtime.requests.some((request) => request.url.endsWith("/submit"))).toBe(true);
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
  options: {
    sharedMaterial?: boolean;
    storage?: ReturnType<typeof createMemoryWechatStorage>;
  } = {},
) {
  let captured: CapturedPracticePage | undefined;
  let session = activeXingceSession({
    mode,
    ...(options.sharedMaterial === undefined ? {} : { sharedMaterial: options.sharedMaterial }),
  });
  const requests: WechatMiniprogram.RequestOption[] = [];
  const storage = options.storage ?? createMemoryWechatStorage();
  const { wxStorage } = storage;
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
      session = {
        ...session,
        revision: command.expectedRevision + 1,
        answers: command.answers,
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
    createSelectorQuery: selectorQuery,
    selectComponent: vi.fn(() => undefined),
  };
  page.onLoad.call(page, { mode, planTaskId: xingceIds.planTask });
  page.onShow.call(page);
  await flushMicrotasks();
  expect(page.data.status).toBe("ready");
  return { page, requests, setData, globalData, storage };
}

interface CapturedPracticePage {
  data: Record<string, unknown>;
  onLoad(this: TestPracticePage, options: Record<string, string | undefined>): void;
  onShow(this: TestPracticePage): void;
  onHide(this: TestPracticePage): void;
  onUnload(this: TestPracticePage): void;
  onResize(this: TestPracticePage): void;
  loadTask(this: TestPracticePage): Promise<void>;
  inputDraft(this: TestPracticePage, event: WechatMiniprogram.Input): void;
  changeAnnotations(this: TestPracticePage, event: ReturnType<typeof annotationEvent>): void;
  changeMaterialAnnotations(
    this: TestPracticePage,
    event: ReturnType<typeof annotationEvent>,
  ): void;
  retryPendingCheckpoint(this: TestPracticePage): Promise<void>;
  nextQuestion(this: TestPracticePage): void;
  selectQuestion(this: TestPracticePage, event: WechatMiniprogram.BaseEvent): void;
  onQuestionTouchStart(this: TestPracticePage, event: WechatMiniprogram.TouchEvent): void;
  onQuestionTouchMove(this: TestPracticePage, event: WechatMiniprogram.TouchEvent): void;
  onQuestionTouchEnd(this: TestPracticePage, event: WechatMiniprogram.TouchEvent): void;
  clearQuestionTransition(this: TestPracticePage): void;
  toggleFavorite(this: TestPracticePage): Promise<void>;
  selectOption(this: TestPracticePage, event: WechatMiniprogram.BaseEvent): void;
  submitAttempt(this: TestPracticePage): Promise<void>;
  openAnnotation(this: TestPracticePage): void;
  closeAnnotation(this: TestPracticePage): void;
  openTaskTools(this: TestPracticePage): void;
  closeTaskOverlay(this: TestPracticePage): void;
  openAnanContext(this: TestPracticePage): void;
  askAnan(this: TestPracticePage, question: string): Promise<void>;
  openTaskHelp(this: TestPracticePage): void;
  openDispute(this: TestPracticePage): void;
  toggleAnswerSheet(this: TestPracticePage): void;
  toggleDraft(this: TestPracticePage): void;
  toggleMaterial(this: TestPracticePage): void;
  toggleTaskPause(this: TestPracticePage): void;
}

interface TestPracticePage extends CapturedPracticePage {
  data: Record<string, unknown>;
  setData(patch: Record<string, unknown>): void;
  createSelectorQuery(): ReturnType<typeof selectorQuery>;
  selectComponent(selector: string): unknown;
}

function selectorQuery() {
  const query = {
    select: vi.fn(() => query),
    fields: vi.fn(() => query),
    boundingClientRect: vi.fn(() => query),
    exec: vi.fn((callback: (results: Array<Record<string, number>>) => void) => {
      callback([{ top: 0, bottom: 640, width: 390, height: 640, scrollHeight: 1200 }]);
    }),
  };
  return query;
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

function annotationStroke(strokeId: string, surface: "question" | "material") {
  return {
    strokeId,
    surface,
    tool: "pen" as const,
    width: 0.008,
    points: [
      { x: 0.1, y: 0.2, t: 0 },
      { x: 0.3, y: 0.4, t: 80 },
    ],
    atMs: 1_200,
  };
}

function annotationEvent(stroke: ReturnType<typeof annotationStroke>) {
  return {
    detail: { strokes: [stroke], action: "added" as const },
  } as WechatMiniprogram.CustomEvent<{
    strokes: Array<ReturnType<typeof annotationStroke>>;
    action: "added";
  }>;
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
