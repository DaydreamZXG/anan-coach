import { afterEach, describe, expect, it, vi } from "vitest";

import { mapLearnerSafeMockSession } from "../miniprogram/api/mock-exam";
import type { MockExamAnswerCheckpoint } from "../miniprogram/api/mock-exam";
import { reduceTaskRuntime } from "../miniprogram/task-runtime/state/runtime";
import {
  loadTaskDisplayPreferences,
  storeTaskDisplayPreferences,
} from "../miniprogram/task-runtime/display-preferences";
import {
  buildMockCheckpointCommand,
  buildMockSubmitCommand,
  formatRemainingTime,
  remainingMockSeconds,
  runtimeFromMockSession,
  runtimeWithPendingMockCheckpoint,
  runtimeWithPendingMockSubmit,
} from "../miniprogram/pages/mock-exam/runtime";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("stage 2 F02 mock exam", () => {
  it("rejects a server paper that leaks answer or explanation fields", () => {
    const unsafe = sessionPayload();
    Object.assign(unsafe.questions[0] as object, {
      answer: "B",
      correctAnswer: "B",
      explanation: { preferredAnalysis: "解析泄漏" },
    });

    expect(() => mapLearnerSafeMockSession(unsafe)).toThrow("forbidden field");

    const privateSnapshotLeak = sessionPayload() as ReturnType<typeof sessionPayload> & {
      scoring_snapshot?: object;
    };
    privateSnapshotLeak.scoring_snapshot = { questions: [{ correctOptionIds: ["B"] }] };
    expect(() => mapLearnerSafeMockSession(privateSnapshotLeak)).toThrow("scoring_snapshot");
  });

  it("uses the unified TaskRuntime for answer, elimination, draft, mark and recovery", () => {
    const session = mapLearnerSafeMockSession(sessionPayload());
    let state = runtimeFromMockSession(session, Date.parse("2026-07-15T02:10:00.000Z"));
    state = reduceTaskRuntime(state, { type: "ANSWER_SELECTED", optionIds: ["B"] });
    state = reduceTaskRuntime(state, {
      type: "OPTION_ELIMINATED",
      optionId: "D",
      eliminated: true,
    });
    state = reduceTaskRuntime(state, { type: "DRAFT_CHANGED", text: "先算增长量" });
    state = reduceTaskRuntime(state, { type: "QUESTION_MARKED", marked: true });
    state = reduceTaskRuntime(state, {
      type: "MATERIAL_POSITION_CHANGED",
      position: { anchorId: "material-start", scrollTop: 360, zoom: 1.25 },
    });

    const checkpoint = buildMockCheckpointCommand(state);
    const submit = buildMockSubmitCommand(state);

    expect(checkpoint).toEqual({
      expectedRevision: 3,
      currentQuestionId: "q-1",
      answers: [
        {
          questionId: "q-1",
          selectedAnswer: "B",
          eliminatedOptionIds: ["D"],
          marked: true,
          draftText: "先算增长量",
          materialPosition: { anchorId: "material-start", scrollTop: 360, zoom: 1.25 },
          // 契约增量 v1：模考与真实训练同套动作事件采集；无批注层入口时 annotations 为空数组。
          annotations: [],
          actionEvents: [
            { atMs: 0, sessionAtMs: 600_000, type: "option_selected", optionId: "B" },
            { atMs: 0, sessionAtMs: 600_000, type: "option_eliminated", optionId: "D" },
            { atMs: 0, sessionAtMs: 600_000, type: "draft_edited" },
            { atMs: 0, sessionAtMs: 600_000, type: "marked" },
            { atMs: 0, sessionAtMs: 600_000, type: "material_scrolled", detail: "material-start" },
          ],
        },
      ],
    });
    expect(submit.answers).toEqual([{ questionId: "q-1", selectedAnswer: "B" }]);
    expect(state.mode).toBe("mock_exam");
    expect(state.exitPolicy).toBe("timer_continues");
  });

  it("restores persisted action facts and keeps the per-question ms clock monotonic", () => {
    const payload = sessionPayload();
    payload.checkpoint.answers[0] = {
      questionId: "q-1",
      selectedAnswer: "B",
      eliminatedOptionIds: [],
      marked: false,
      draftText: "",
      actionEvents: [
        { atMs: 0, sessionAtMs: 1_000, type: "question_entered" },
        { atMs: 5_000, sessionAtMs: 6_000, type: "option_selected", optionId: "B" },
      ],
      actionEventsTruncated: true,
    } as never;
    const session = mapLearnerSafeMockSession(payload);
    let state = runtimeFromMockSession(session, Date.parse("2026-07-15T02:10:00.000Z"));

    const restored = state.attempts["q-1"];
    expect(restored?.actionEvents).toHaveLength(2);
    expect(restored?.actionEventsTruncated).toBe(true);
    // 检查点不持久化本题秒表，恢复时以最后一条事件的 atMs 兜底，保证追加事件非降序。
    expect(restored?.elapsedMs).toBe(5_000);

    state = reduceTaskRuntime(state, { type: "ANSWER_SELECTED", optionIds: ["C"] });
    const events = state.attempts["q-1"]?.actionEvents ?? [];
    expect(events.at(-1)).toMatchObject({ type: "option_changed", atMs: 5_000 });
    // 追加事件的 atMs 序列保持非降序（0 -> 5000 -> 5000）。
    expect(events.map((event) => event.atMs)).toEqual([0, 5_000, 5_000]);

    const answer = buildMockCheckpointCommand(state).answers[0];
    expect(answer?.actionEvents).toHaveLength(3);
    expect(answer?.actionEventsTruncated).toBe(true);
    expect(answer?.annotations).toEqual([]);
  });

  it("never rewinds the session ms clock when rebuilding from a checkpoint receipt", () => {
    const session = mapLearnerSafeMockSession(sessionPayload());
    const previous = {
      ...runtimeFromMockSession(session, Date.parse("2026-07-15T02:10:00.900Z")),
      sessionElapsedMs: 600_900,
    };

    // 回执重建时墙钟取整会小于本地累计毫秒；sessionAtMs 时间线不允许倒退。
    const rebuilt = runtimeFromMockSession(
      session,
      Date.parse("2026-07-15T02:10:00.950Z"),
      previous,
    );

    expect(rebuilt.sessionElapsedMs).toBe(600_900);
    expect(rebuilt.totalElapsedSeconds).toBe(600);
  });

  it("keeps pending action facts when replaying a locally stored checkpoint command", () => {
    const session = mapLearnerSafeMockSession(sessionPayload());
    const state = runtimeFromMockSession(session, Date.parse("2026-07-15T02:10:00.000Z"));

    const restored = runtimeWithPendingMockCheckpoint(state, {
      expectedRevision: 3,
      currentQuestionId: "q-1",
      answers: [
        {
          questionId: "q-1",
          selectedAnswer: "B",
          eliminatedOptionIds: [],
          marked: false,
          draftText: "",
          annotations: [],
          actionEvents: [
            { atMs: 0, sessionAtMs: 1_000, type: "question_entered" },
            { atMs: 4_200, sessionAtMs: 5_200, type: "option_selected", optionId: "B" },
          ],
          actionEventsTruncated: true,
        },
      ],
    });

    const attempt = restored.attempts["q-1"];
    expect(attempt?.actionEvents).toHaveLength(2);
    expect(attempt?.actionEventsTruncated).toBe(true);
    expect(attempt?.elapsedMs).toBe(4_200);
  });

  it("sends checkpoint deltas plus the current question instead of the whole paper", () => {
    const session = mapLearnerSafeMockSession(sessionPayload());
    const firstQuestion = session.questions[0];
    if (!firstQuestion) throw new Error("mock fixture question is required");
    session.questions.push({
      ...firstQuestion,
      questionId: "q-2",
      stemContent: firstQuestion.stemContent.map((block) => ({
        ...block,
        blockId: `${block.blockId}-q2`,
      })),
      options: firstQuestion.options.map((option) => ({
        ...option,
        content: option.content.map((block) => ({
          ...block,
          blockId: `${block.blockId}-q2`,
        })),
      })),
    });
    session.questionCount = 2;
    session.checkpoint.answers.push({
      questionId: "q-2",
      selectedAnswer: null,
      eliminatedOptionIds: [],
      marked: false,
      draftText: "",
    });
    let state = runtimeFromMockSession(session, Date.parse("2026-07-15T02:10:00.000Z"));
    const dirty = new Set(["q-1"]);

    expect(buildMockCheckpointCommand(state, dirty).answers.map((item) => item.questionId)).toEqual(
      ["q-1"],
    );

    state = reduceTaskRuntime(state, { type: "QUESTION_CHANGED", index: 1 });
    expect(buildMockCheckpointCommand(state, dirty).answers.map((item) => item.questionId)).toEqual(
      ["q-1", "q-2"],
    );
  });

  it("restores locally pending submit answers before a revision-conflict retry", () => {
    const session = mapLearnerSafeMockSession(sessionPayload());
    const serverRuntime = runtimeFromMockSession(session, Date.parse("2026-07-15T02:10:00.000Z"));

    const restored = runtimeWithPendingMockSubmit(serverRuntime, {
      expectedRevision: 2,
      answers: [{ questionId: "q-1", selectedAnswer: "C" }],
    });

    expect(restored.attempts["q-1"]?.selectedOptionIds).toEqual(["C"]);
  });

  it("restores remaining time from the absolute deadline instead of paused local ticks", () => {
    const deadline = "2026-07-15T04:00:00.000Z";
    expect(remainingMockSeconds(deadline, Date.parse("2026-07-15T02:00:00.000Z"))).toBe(7200);
    expect(remainingMockSeconds(deadline, Date.parse("2026-07-15T02:30:00.000Z"))).toBe(5400);
    expect(formatRemainingTime(5400)).toBe("01:30:00");
    expect(remainingMockSeconds(deadline, Date.parse("2026-07-15T04:00:10.000Z"))).toBe(0);
  });

  it("shares persisted display preferences with other TaskRuntime pages", () => {
    const storage = new Map<string, unknown>();
    vi.stubGlobal("wx", {
      getStorageSync: (key: string) => storage.get(key),
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    });

    storeTaskDisplayPreferences({ fontScale: 1.2, colorMode: "eye_care" });

    expect(loadTaskDisplayPreferences()).toEqual({
      fontScale: 1.2,
      colorMode: "eye_care",
      lineSpacing: 1,
    });
  });

  it("stops page timers in background and recomputes from the server deadline on return", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T02:00:00.000Z"));
    const harness = await createMockPage(sessionPayload());

    expect(harness.page.data.remainingLabel).toBe("02:00:00");
    harness.page.inputDraft.call(harness.page, inputEvent("后台前草稿"));
    harness.page.onHide.call(harness.page);
    await flushMicrotasks();
    expect(vi.getTimerCount()).toBe(0);

    vi.setSystemTime(new Date("2026-07-15T02:30:00.000Z"));
    harness.page.onShow.call(harness.page);

    expect(harness.page.data.remainingLabel).toBe("01:30:00");
    expect(harness.requests.some((request) => request.url.endsWith("/checkpoint"))).toBe(true);
    harness.page.onUnload.call(harness.page);
  });

  it("auto-submits at the absolute deadline without requiring every question to be answered", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T03:59:59.000Z"));
    const harness = await createMockPage(sessionPayload());

    await vi.advanceTimersByTimeAsync(250);
    harness.page.inputDraft.call(harness.page, inputEvent("截止前最后一笔本地草稿"));
    await vi.advanceTimersByTimeAsync(750);
    await flushMicrotasks();

    const submission = harness.requests.find((request) => request.url.endsWith("/submit"));
    expect(submission).toBeDefined();
    expect(submission?.data).toMatchObject({
      expectedRevision: 3,
      answers: [{ questionId: "q-1", selectedAnswer: null }],
    });
    expect(harness.requests.some((request) => request.url.endsWith("/checkpoint"))).toBe(false);
    expect(harness.page.data.status).toBe("submitted");
    harness.page.onUnload.call(harness.page);
  });

  it("waits for an in-flight checkpoint, then submits with its committed revision", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T03:59:59.000Z"));
    const harness = await createMockPage(sessionPayload(), { deferCheckpoint: true });

    harness.page.inputDraft.call(harness.page, inputEvent("截止前保存中的草稿"));
    await vi.advanceTimersByTimeAsync(800);
    await vi.advanceTimersByTimeAsync(200);
    expect(harness.requests.some((request) => request.url.endsWith("/submit"))).toBe(false);

    harness.releaseCheckpoint();
    await flushMicrotasks();

    const submission = harness.requests.find((request) => request.url.endsWith("/submit"));
    expect(submission?.data).toMatchObject({ expectedRevision: 4 });
    harness.page.onUnload.call(harness.page);
  });

  it("recovers a cross-device revision conflict without dropping local answers", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T03:59:59.000Z"));
    const harness = await createMockPage(sessionPayload(), { submitConflictOnce: true });

    await vi.advanceTimersByTimeAsync(250);
    harness.page.selectOption.call(harness.page, optionEvent("B"));
    await vi.advanceTimersByTimeAsync(750);
    await flushMicrotasks();

    const submissions = harness.requests.filter((request) => request.url.endsWith("/submit"));
    expect(submissions).toHaveLength(2);
    expect(submissions.map((request) => request.data)).toMatchObject([
      { expectedRevision: 3, answers: [{ questionId: "q-1", selectedAnswer: "B" }] },
      { expectedRevision: 4, answers: [{ questionId: "q-1", selectedAnswer: "B" }] },
    ]);
    expect(harness.page.data.status).toBe("submitted");
    harness.page.onUnload.call(harness.page);
  });

  it("uploads collected action events with a ticking ms clock in mock checkpoints", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T02:00:00.000Z"));
    const harness = await createMockPage(sessionPayload());

    // 3 次心跳推进毫秒时钟，随后的作答事实应携带 atMs/sessionAtMs=3000。
    await vi.advanceTimersByTimeAsync(3_000);
    harness.page.selectOption.call(harness.page, optionEvent("B"));
    await vi.advanceTimersByTimeAsync(800);
    await flushMicrotasks();

    const checkpoint = harness.requests.find((request) => request.url.endsWith("/checkpoint"));
    expect(checkpoint).toBeDefined();
    const answers = (checkpoint?.data as { answers: MockExamAnswerCheckpoint[] }).answers;
    expect(answers[0]?.annotations).toEqual([]);
    expect(answers[0]?.actionEvents).toEqual([
      { atMs: 3_000, sessionAtMs: 3_000, type: "option_selected", optionId: "B" },
    ]);
    expect(answers[0]?.actionEventsTruncated).toBeUndefined();
    harness.page.onUnload.call(harness.page);
  });
});

function sessionPayload() {
  return {
    sessionId: "mock-session-1",
    status: "assigned",
    title: "国考行测整卷模考",
    durationMinutes: 120,
    questionCount: 1,
    blueprint: [{ moduleName: "资料分析", questionCount: 1 }],
    bundleVersionId: "b".repeat(64),
    paperVersionId: "00000000-0000-4000-8000-000000000602",
    scoringPolicyVersionId: "mock-balanced-performance-index-v1",
    scoreMetricName: "balanced_performance_index",
    scoreMetricUnit: "index_points",
    scoreMetricDefinition: "五个行测模块各占 20 点，不是官方原始分。",
    nextTrajectoryEventSeq: 1,
    materials: [
      {
        materialId: "material-1",
        title: "增长资料",
        text: "2025 年为 120，2024 年为 100。",
        html: null,
        assets: [],
        content: [
          {
            type: "paragraph",
            blockId: "material-paragraph-1",
            text: "2025 年为 120，2024 年为 100。",
            html: null,
          },
        ],
        anchors: [
          {
            anchorId: "material-start",
            blockId: "material-paragraph-1",
            label: "材料开头",
          },
        ],
      },
    ],
    questions: [
      {
        questionId: "q-1",
        contentVersionId: "a".repeat(64),
        moduleName: "资料分析",
        materialId: "material-1",
        stem: "某指标同比增长率为多少？",
        stemHtml: null,
        stemContent: [
          {
            type: "paragraph",
            blockId: "question-stem-1",
            text: "某指标同比增长率为多少？",
            html: null,
          },
        ],
        options: [option("A", "10%"), option("B", "20%"), option("C", "25%"), option("D", "30%")],
      },
    ],
    revision: 3,
    startedAt: "2026-07-15T02:00:00.000Z",
    deadlineAt: "2026-07-15T04:00:00.000Z",
    checkpoint: {
      currentQuestionId: "q-1",
      answers: [
        {
          questionId: "q-1",
          selectedAnswer: null,
          eliminatedOptionIds: [],
          marked: false,
          draftText: "",
        },
      ],
    },
  };
}

function option(optionId: string, text: string) {
  return {
    optionId,
    label: optionId,
    text,
    html: null,
    content: [
      {
        type: "paragraph",
        blockId: `option-${optionId}`,
        text,
        html: null,
      },
    ],
  };
}

async function createMockPage(
  initialSession: ReturnType<typeof sessionPayload>,
  behavior: { deferCheckpoint?: boolean; submitConflictOnce?: boolean } = {},
) {
  let captured: CapturedMockPage | undefined;
  let session = clone(initialSession);
  const requests: WechatMiniprogram.RequestOption[] = [];
  const storage = new Map<string, unknown>();
  let deferredCheckpointSuccess: (() => void) | undefined;
  let submitConflictRemaining = behavior.submitConflictOnce ? 1 : 0;
  vi.stubGlobal("Page", (definition: CapturedMockPage) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "real-token", userId: "user-a" },
  }));
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
    request: (options: WechatMiniprogram.RequestOption) => {
      requests.push(options);
      if (options.url.endsWith("/api/v2/task-editing/leases")) {
        options.success?.({
          statusCode: 200,
          data: apiEnvelope(taskLease(session.sessionId, session.revision, "owned")),
        } as never);
        return;
      }
      if (options.url.endsWith("/conflict")) {
        options.success?.({
          statusCode: 200,
          data: apiEnvelope(
            taskLease(session.sessionId, session.revision, "conflict_choice_required"),
          ),
        } as never);
        return;
      }
      if (options.url.endsWith("/resolve")) {
        options.success?.({
          statusCode: 200,
          data: apiEnvelope(taskLease(session.sessionId, session.revision, "owned")),
        } as never);
        return;
      }
      if (options.url.endsWith("/checkpoint")) {
        const command = options.data as {
          expectedRevision: number;
          currentQuestionId: string;
          answers: ReturnType<typeof sessionPayload>["checkpoint"]["answers"];
        };
        session = {
          ...session,
          revision: command.expectedRevision + 1,
          checkpoint: {
            currentQuestionId: command.currentQuestionId,
            answers: command.answers,
          },
        };
        const succeed = () =>
          options.success?.({
            statusCode: 200,
            data: {
              sessionId: session.sessionId,
              status: "assigned",
              revision: session.revision,
              checkpoint: session.checkpoint,
              checkpointUpdatedAt: new Date().toISOString(),
            },
          } as never);
        if (behavior.deferCheckpoint) deferredCheckpointSuccess = succeed;
        else succeed();
        return;
      }
      if (options.url.endsWith("/submit")) {
        if (submitConflictRemaining > 0) {
          submitConflictRemaining -= 1;
          session = { ...session, revision: session.revision + 1 };
          options.success?.({
            statusCode: 409,
            data: {
              code: "mock_submission_conflict",
              message: "revision changed",
              requestId: "request-conflict",
            },
          } as never);
          return;
        }
        options.success?.({
          statusCode: 200,
          data: {
            sessionId: session.sessionId,
            status: "submitted",
            review: {
              estimatedScore: 0,
              estimateSource: "平衡表现指数",
              performanceIndex: 0,
              performanceRange: { lower: 0, recommended: 0, upper: 79.3 },
              metricName: "balanced_performance_index",
              metricUnit: "index_points",
              metricDefinition: "五个行测模块各占 20 点，不是官方原始分。",
              intervalDefinition: "Wilson 95% 样本区间。",
              paperVersionId: session.paperVersionId,
              scoringPolicyVersionId: session.scoringPolicyVersionId,
              submittedAt: new Date().toISOString(),
              modules: [{ moduleName: "资料分析", correct: 0, total: 1, accuracy: 0 }],
              nextWeekReason: "基于本次整卷重排。",
            },
          },
        } as never);
        return;
      }
      options.success?.({ statusCode: 200, data: session } as never);
    },
    showModal: () => Promise.resolve({ confirm: true, cancel: false }),
    navigateBack: () => Promise.resolve(),
    navigateTo: () => Promise.resolve(),
    switchTab: () => Promise.resolve(),
  });
  await import("../miniprogram/pages/mock-exam/index");
  if (!captured) throw new Error("Mock exam page was not registered");
  const page: TestMockPage = {
    ...captured,
    data: clone(captured.data),
    setData(patch: Record<string, unknown>) {
      Object.assign(this.data, patch);
    },
  };
  page.onLoad.call(page, {});
  page.onShow.call(page);
  await flushMicrotasks();
  expect(page.data.status).toBe("ready");
  return {
    page,
    requests,
    releaseCheckpoint() {
      if (!deferredCheckpointSuccess) throw new Error("No checkpoint response is pending");
      deferredCheckpointSuccess();
      deferredCheckpointSuccess = undefined;
    },
  };
}

interface CapturedMockPage {
  data: Record<string, unknown>;
  onLoad(this: TestMockPage, options: Record<string, string | undefined>): void;
  onShow(this: TestMockPage): void;
  onHide(this: TestMockPage): void;
  onUnload(this: TestMockPage): void;
  inputDraft(this: TestMockPage, event: WechatMiniprogram.Input): void;
  selectOption(this: TestMockPage, event: WechatMiniprogram.BaseEvent): void;
}

interface TestMockPage extends CapturedMockPage {
  data: Record<string, unknown>;
  setData(patch: Record<string, unknown>): void;
}

function inputEvent(value: string): WechatMiniprogram.Input {
  return { detail: { value } } as WechatMiniprogram.Input;
}

function optionEvent(optionId: string): WechatMiniprogram.BaseEvent {
  return {
    currentTarget: { dataset: { optionid: optionId } },
  } as unknown as WechatMiniprogram.BaseEvent;
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 20; index += 1) await Promise.resolve();
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function apiEnvelope<T>(data: T) {
  return { data, meta: { requestId: "request-task-editing" } };
}

function taskLease(
  resourceId: string,
  serverRevision: number,
  state: "owned" | "conflict_choice_required",
) {
  return {
    state,
    resourceType: "mock_exam_session",
    resourceId,
    leaseId: "00000000-0000-4000-8000-000000000902",
    generation: 1,
    deviceId: "mp-device-test-000000000000000002",
    deviceLabel: "当前微信设备",
    serverRevision,
    localBaseRevision: state === "conflict_choice_required" ? serverRevision - 1 : null,
    acquiredAt: "2026-07-19T00:00:00Z",
    lastSeenAt: "2026-07-19T00:00:00Z",
    takenOverAt: null,
  };
}
