import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../miniprogram/api/client";
import type { RecognitionRevision } from "../miniprogram/api/external-learning-contracts";
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

afterEach(() => vi.unstubAllGlobals());

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
});

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
