import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../miniprogram/api/client";
import type { ApiEnvelope } from "../miniprogram/domain/contracts";
import type { RecognitionRevision } from "../miniprogram/api/external-learning-contracts";
import type { ReportCenterProjection } from "../miniprogram/api/generated/types.gen";
import { ReportingApi } from "../miniprogram/api/reporting";
import { routes } from "../miniprogram/app/navigation/routes";
import { registerReportPage } from "../miniprogram/features/reporting/report-page";
import {
  REPORT_CHART_TEXT_CHANNELS,
  REPORT_CHART_TYPES,
} from "../miniprogram/design-system/reporting/report-chart/contract";
import {
  editExternalScoreSpan,
  reviewingExternalScoreState,
  toggleExternalScoreSpan,
  verifiedExternalScoreSpans,
} from "../miniprogram/features/reporting/external-score-workflow";
import {
  appendPendingMockTrajectory,
  loadPendingMockTrajectory,
  removePendingMockTrajectory,
} from "../miniprogram/pages/mock-exam/pending-store";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("stage 6 mock reporting", () => {
  it("registers F01-F04 and F13 as real mini-program routes", () => {
    expect(routes).toMatchObject({
      reportCenter: "/pages/report-center/index",
      mockExam: "/pages/mock-exam/index",
      mockReport: "/pages/mock-report/index",
      externalReport: "/pages/external-report/index",
      globalReport: "/pages/global-report/index",
    });
    expect(Object.values(routes)).toEqual(
      expect.arrayContaining(["/pages/report-overview/index", "/pages/global-report/index"]),
    );
  });

  it("requires every printed OCR span to be checked again after an edit", () => {
    let state = reviewingExternalScoreState(recognitionRevision(), "/tmp/score.png");
    state = toggleExternalScoreSpan(state, "span-1");
    expect(verifiedExternalScoreSpans(state)).toHaveLength(1);
    state = editExternalScoreSpan(state, "span-1", "行测 72.5");
    expect(() => verifiedExternalScoreSpans(state)).toThrow("勾选全部");
    state = toggleExternalScoreSpan(state, "span-1");
    expect(verifiedExternalScoreSpans(state)[0]?.text).toBe("行测 72.5");
  });

  it("keeps structured draft and final confirmation as two API commands", async () => {
    const calls: Array<{ path: string; body?: unknown }> = [];
    const client = {
      post: (path: string, body: unknown) => {
        calls.push({ path, body });
        return Promise.resolve({
          data: externalProjection(path.endsWith("/confirm") ? "confirmed" : "candidate"),
        });
      },
      get: (path: string) => {
        calls.push({ path });
        return Promise.resolve({ data: externalProjection("confirmed") });
      },
    } as unknown as ApiClient;
    const api = new ReportingApi(client);
    const draft = await api.createExternalMockDraft(
      {
        artifactVersionId: "00000000-0000-4000-8000-000000000001",
        recognitionRevisionId: "00000000-0000-4000-8000-000000000002",
        platformName: "粉笔",
        examName: "第 18 季模考",
        occurredAt: "2026-07-16T04:00:00.000Z",
        timedStatus: "timed",
        durationMinutes: 120,
        totalScore: 132,
        xingceScore: 72,
        shenlunScore: 60,
      },
      "stage6-draft-command",
    );
    expect(draft.data.state).toBe("candidate");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.path).toBe("/api/v2/reporting/external-mocks");
    await api.confirmExternalMock(draft.data.reportVersionId, "stage6-confirm-command");
    expect(calls[1]?.path).toBe(
      `/api/v2/reporting/external-mocks/${draft.data.reportVersionId}/confirm`,
    );
    expect(calls.some((call) => call.path.includes("ability"))).toBe(false);
  });

  it("persists trajectory commands in event order until receipts succeed", () => {
    const storage = new Map<string, unknown>();
    vi.stubGlobal("wx", {
      getStorageSync: (key: string) => storage.get(key),
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    const second = pendingTrajectory(2, "visited");
    const first = pendingTrajectory(1, "resumed");
    appendPendingMockTrajectory(second);
    appendPendingMockTrajectory(first);
    expect(
      loadPendingMockTrajectory("user-1", "session-1").map((item) => item.command.eventSeq),
    ).toEqual([1, 2]);
    removePendingMockTrajectory("user-1", "session-1", first.idempotencyKey);
    expect(loadPendingMockTrajectory("user-1", "session-1")).toEqual([second]);
  });

  it("renders all six chart contracts with numeric text beyond color", () => {
    expect(REPORT_CHART_TYPES).toEqual([
      "confidence_trend",
      "module_bullet",
      "speed_accuracy_quadrant",
      "error_heatmap",
      "score_gap_waterfall",
      "weekly_completion",
    ]);
    expect(REPORT_CHART_TEXT_CHANNELS).toEqual(["label", "primary", "secondary", "summary"]);
  });

  it("turns every report conclusion into an executable return to today's plan", () => {
    let definition: { openNextAction?: () => void } | undefined;
    const switchTab = vi.fn();
    vi.stubGlobal("Page", (value: { openNextAction?: () => void }) => {
      definition = value;
    });
    vi.stubGlobal("wx", { switchTab });

    registerReportPage("weekly", "REPORT_OVERVIEW");
    definition?.openNextAction?.();

    expect(switchTab).toHaveBeenCalledWith({ url: routes.today });
  });

  it("keeps a newer report-center projection when an older successful request arrives later", async () => {
    const harness = await captureReportCenterPage();

    harness.page.onShow();
    harness.page.onShow();
    respondReportSuccess(harness.requests[1], reportCenter("newer"));
    await flushMicrotasks();
    respondReportSuccess(harness.requests[0], reportCenter("older"));
    await flushMicrotasks();

    expect(harness.page.data.status).toBe("ready");
    expect(reportMarker(harness.page.data.center)).toBe("newer");
  });

  it("keeps a newer report-center projection when an older request later fails", async () => {
    const harness = await captureReportCenterPage();

    harness.page.onShow();
    harness.page.onShow();
    respondReportSuccess(harness.requests[1], reportCenter("newer"));
    await flushMicrotasks();
    respondReportFailure(harness.requests[0]);
    await flushMicrotasks();

    expect(harness.page.data.status).toBe("ready");
    expect(harness.page.data.errorMessage).toBe("");
    expect(reportMarker(harness.page.data.center)).toBe("newer");
  });

  it("does not set report-center data after hide or unload", async () => {
    const harness = await captureReportCenterPage();

    harness.page.onShow();
    const callsBeforeHide = harness.page.setDataCalls;
    harness.page.onHide();
    respondReportSuccess(harness.requests[0], reportCenter("hidden"));
    await flushMicrotasks();
    expect(harness.page.setDataCalls).toBe(callsBeforeHide);

    harness.page.onShow();
    const callsBeforeUnload = harness.page.setDataCalls;
    harness.page.onUnload();
    respondReportFailure(harness.requests[1]);
    await flushMicrotasks();
    expect(harness.page.setDataCalls).toBe(callsBeforeUnload);
  });

  it("rejects old report-center responses after an account or same-token generation change", async () => {
    const harness = await captureReportCenterPage();

    harness.page.onShow();
    harness.appState.globalData = {
      accessToken: "token-b",
      userId: "user-b",
      generation: "generation-b",
    };
    respondReportSuccess(harness.requests[0], reportCenter("account-a"));
    await flushMicrotasks();
    expect(harness.page.data.center).toBeNull();

    harness.page.onShow();
    harness.appState.globalData = {
      accessToken: "token-b",
      userId: "user-b",
      generation: "generation-c",
    };
    respondReportSuccess(harness.requests[1], reportCenter("generation-b"));
    await flushMicrotasks();
    expect(harness.page.data.center).toBeNull();

    harness.page.onShow();
    respondReportSuccess(harness.requests[2], reportCenter("current"));
    await flushMicrotasks();
    expect(harness.page.data.status).toBe("ready");
    expect(reportMarker(harness.page.data.center)).toBe("current");
  });

  it("renders the latest report-center request failure", async () => {
    const harness = await captureReportCenterPage();

    harness.page.onShow();
    respondReportFailure(harness.requests[0]);
    await flushMicrotasks();

    expect(harness.page.data.status).toBe("error");
    expect(harness.page.data.errorMessage).toBe("报告中心暂时不可用，请重试。");
    expect(harness.page.data.requestId).toBe("report-center-failed");
  });
});

interface CapturedReportCenterDefinition {
  data: Record<string, unknown>;
  onShow(this: ReportCenterTestPage): void;
  onHide(this: ReportCenterTestPage): void;
  onUnload(this: ReportCenterTestPage): void;
}

interface ReportCenterTestPage extends CapturedReportCenterDefinition {
  data: {
    status: string;
    errorMessage: string;
    requestId: string;
    center: unknown;
  };
  setDataCalls: number;
  setData(patch: Record<string, unknown>): void;
}

interface ReportCenterHarness {
  page: ReportCenterTestPage;
  requests: Array<WechatMiniprogram.RequestOption<ApiEnvelope<ReportCenterProjection>>>;
  appState: {
    globalData: {
      accessToken: string;
      userId: string;
      generation: string;
    };
  };
}

async function captureReportCenterPage(): Promise<ReportCenterHarness> {
  let definition: CapturedReportCenterDefinition | undefined;
  const requests: Array<WechatMiniprogram.RequestOption<ApiEnvelope<ReportCenterProjection>>> = [];
  const storage = new Map<string, unknown>();
  const appState = {
    globalData: {
      accessToken: "token-a",
      userId: "user-a",
      generation: "generation-a",
    },
  };
  vi.stubGlobal("Page", (value: CapturedReportCenterDefinition) => {
    definition = value;
  });
  vi.stubGlobal("getApp", () => appState);
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    request: (options: WechatMiniprogram.RequestOption<ApiEnvelope<ReportCenterProjection>>) => {
      requests.push(options);
    },
    reLaunch: vi.fn(() => Promise.resolve()),
  });

  await import("../miniprogram/pages/report-center/index");
  if (!definition) throw new Error("report-center page was not registered");
  const page: ReportCenterTestPage = {
    ...definition,
    data: {
      status: String(definition.data.status),
      errorMessage: String(definition.data.errorMessage),
      requestId: String(definition.data.requestId),
      center: definition.data.center,
    },
    setDataCalls: 0,
    setData(patch: Record<string, unknown>) {
      this.setDataCalls += 1;
      Object.assign(this.data, patch);
    },
  };
  return { page, requests, appState };
}

function respondReportSuccess(
  options: WechatMiniprogram.RequestOption<ApiEnvelope<ReportCenterProjection>> | undefined,
  center: ReportCenterProjection,
): void {
  options?.success?.({
    data: {
      data: center,
      requestId: "report-center-success",
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

function respondReportFailure(
  options: WechatMiniprogram.RequestOption<ApiEnvelope<ReportCenterProjection>> | undefined,
): void {
  options?.success?.({
    data: {
      code: "service_unavailable",
      message: "报告中心暂时不可用，请重试。",
      requestId: "report-center-failed",
    } as unknown as ApiEnvelope<ReportCenterProjection>,
    statusCode: 503,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function reportCenter(marker: string): ReportCenterProjection {
  return {
    generatedAt: "2026-07-28T10:00:00Z",
    latestExternalReport: null,
    reportCards: [],
    standardPapers: [],
    auditMarker: marker,
  } as unknown as ReportCenterProjection;
}

function reportMarker(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const marker = (value as { auditMarker?: unknown }).auditMarker;
  return typeof marker === "string" ? marker : undefined;
}

async function flushMicrotasks(turns = 12): Promise<void> {
  for (let turn = 0; turn < turns; turn += 1) await Promise.resolve();
}

function recognitionRevision(): RecognitionRevision {
  return {
    contractVersion: "recognition-revision@v1",
    recognitionRevisionId: "00000000-0000-4000-8000-000000000002",
    artifactVersionId: "00000000-0000-4000-8000-000000000001",
    ownerUserId: "user-1",
    revisionNo: 1,
    recognitionKind: "printed_ocr",
    state: "candidate",
    processorPolicyVersion: "external-score-report-ocr-v1",
    processorRoute: "external-score-report-ocr-v1",
    inputSha256: "a".repeat(64),
    outputSha256: "b".repeat(64),
    candidatePayload: {
      textSpans: [
        {
          spanId: "span-1",
          text: "行测 72",
          confidence: 0.91,
          box: { pageNo: 1, x: 0.1, y: 0.2, width: 0.3, height: 0.1 },
        },
      ],
    },
    confidence: 0.91,
    expiresAt: "2026-07-17T00:00:00Z",
    confirmedAt: null,
    createdAt: "2026-07-16T00:00:00Z",
  };
}

function externalProjection(state: "candidate" | "confirmed") {
  return {
    reportVersionId: "00000000-0000-4000-8000-000000000003",
    reportId: "00000000-0000-4000-8000-000000000004",
    versionNo: state === "candidate" ? 1 : 2,
    state,
    artifactVersionId: "00000000-0000-4000-8000-000000000001",
    recognitionRevisionId: "00000000-0000-4000-8000-000000000002",
    platformName: "粉笔",
    examName: "第 18 季模考",
    occurredAt: "2026-07-16T04:00:00Z",
    timedStatus: "timed" as const,
    durationMinutes: 120,
    totalScore: 132,
    xingceScore: 72,
    shenlunScore: 60,
    moduleScores: {},
    evidenceGrade: state === "candidate" ? "candidate_unconfirmed" : "ability_eligible",
    scoreEvidenceId: state === "candidate" ? null : "00000000-0000-4000-8000-000000000005",
    estimateVersionId: state === "candidate" ? null : "00000000-0000-4000-8000-000000000006",
    abilityImpact: state === "candidate" ? "blocked_until_confirmed" : "entered_estimate",
    confirmationMessage: "已核对",
    confirmedAt: state === "candidate" ? null : "2026-07-16T05:00:00Z",
    createdAt: "2026-07-16T04:00:00Z",
  } as const;
}

function pendingTrajectory(eventSeq: number, eventType: "visited" | "resumed") {
  return {
    ownerUserId: "user-1",
    sessionId: "session-1",
    idempotencyKey: `stage6-trajectory-${String(eventSeq)}`,
    command: {
      eventSeq,
      eventType,
      questionId: eventType === "visited" ? "q-1" : null,
      clientOccurredAt: "2026-07-16T00:00:00Z",
      sessionRevision: 1,
      payload: {},
    },
    storedAt: "2026-07-16T00:00:00Z",
  } as const;
}
