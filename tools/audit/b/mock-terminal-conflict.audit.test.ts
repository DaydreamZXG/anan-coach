import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("audit reproduction: terminal mock submission conflict", () => {
  it("keeps the local page answerable after the server confirms the exam is already submitted", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T03:00:00.000Z"));
    const harness = await createHarness();

    expect(harness.page.data.status).toBe("ready");
    harness.page.submitExam.call(harness.page);
    await harness.page.confirmSubmitExam.call(harness.page);
    await flushMicrotasks();

    expect(harness.requests.filter((request) => request.url.endsWith("/submit"))).toHaveLength(1);
    expect(harness.requests.some((request) => request.url.endsWith("/conflict"))).toBe(true);
    expect(harness.requests.some((request) => request.url.endsWith("/resolve"))).toBe(true);
    expect(harness.page.data).toMatchObject({
      status: "ready",
      interactionLocked: false,
      syncLabel: "交卷结果待确认",
      errorMessage: "云端已经完成交卷，当前设备不再重复提交。",
    });

    harness.page.onUnload.call(harness.page);
  });
});

interface RequestRecord {
  url: string;
  data?: unknown;
}

interface CapturedMockPage {
  data: Record<string, unknown>;
  onLoad(this: TestMockPage, options: Record<string, string | undefined>): void;
  onShow(this: TestMockPage): void;
  onUnload(this: TestMockPage): void;
  submitExam(this: TestMockPage): void;
  confirmSubmitExam(this: TestMockPage): Promise<void>;
}

interface TestMockPage extends CapturedMockPage {
  setData(patch: Record<string, unknown>): void;
}

async function createHarness(): Promise<{
  page: TestMockPage;
  requests: RequestRecord[];
}> {
  let captured: CapturedMockPage | undefined;
  let session = sessionPayload();
  const requests: RequestRecord[] = [];
  const storage = new Map<string, unknown>();

  vi.stubGlobal("Page", (definition: CapturedMockPage) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "audit-token", userId: "audit-user" },
  }));
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getRandomValues: () =>
      Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
    request: (options: {
      url: string;
      data?: unknown;
      success?(response: unknown): void;
    }) => {
      requests.push({ url: options.url, data: options.data });
      if (options.url.endsWith("/api/v2/task-editing/leases")) {
        options.success?.({
          statusCode: 200,
          data: apiEnvelope(taskLease(session.sessionId, session.revision, "owned")),
        });
        return;
      }
      if (options.url.endsWith("/conflict")) {
        options.success?.({
          statusCode: 200,
          data: apiEnvelope(
            taskLease(session.sessionId, session.revision, "conflict_choice_required"),
          ),
        });
        return;
      }
      if (options.url.endsWith("/resolve")) {
        options.success?.({
          statusCode: 200,
          data: apiEnvelope(taskLease(session.sessionId, session.revision, "owned")),
        });
        return;
      }
      if (options.url.endsWith("/submit")) {
        session = { ...session, status: "submitted", revision: session.revision + 1 };
        options.success?.({
          statusCode: 409,
          data: {
            code: "mock_submission_conflict",
            message: "交卷版本冲突。",
            requestId: "request-submit-conflict",
          },
        });
        return;
      }
      if (options.url.includes("/trajectory-events")) {
        options.success?.({ statusCode: 200, data: {} });
        return;
      }
      options.success?.({ statusCode: 200, data: session });
    },
    showModal: () => Promise.resolve({ confirm: true, cancel: false }),
    showToast: vi.fn(() => Promise.resolve()),
    navigateBack: vi.fn(() => Promise.resolve()),
    navigateTo: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
    pageScrollTo: vi.fn(() => Promise.resolve()),
  });

  await import("../../../apps/wechat/miniprogram/pages/mock-exam/index");
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
  return { page, requests };
}

function sessionPayload() {
  return {
    sessionId: "mock-session-audit",
    status: "assigned" as "assigned" | "submitted",
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
        options: [
          option("A", "10%"),
          option("B", "20%"),
          option("C", "25%"),
          option("D", "30%"),
        ],
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
    deviceId: "mp-device-audit-0000000000000002",
    deviceLabel: "当前微信设备",
    serverRevision,
    localBaseRevision: state === "conflict_choice_required" ? serverRevision - 1 : null,
    acquiredAt: "2026-07-19T00:00:00Z",
    lastSeenAt: "2026-07-19T00:00:00Z",
    takenOverAt: null,
  };
}

async function flushMicrotasks(turns = 30): Promise<void> {
  for (let index = 0; index < turns; index += 1) await Promise.resolve();
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
