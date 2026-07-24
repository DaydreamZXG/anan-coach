import { describe, expect, it } from "vitest";
import { scoreEvidenceHistoryItemView } from "../miniprogram/features/ability/history";

describe("score evidence history presentation", () => {
  it("keeps a superseded record visible but unavailable for correction", () => {
    const view = scoreEvidenceHistoryItemView({
      evidenceId: "evidence-old",
      examCode: "guokao",
      examYear: 2026,
      sourceType: "self_report",
      reportedContext: "full_mock",
      timedStatus: "timed",
      occurredAt: "2026-07-12T16:00:00.000Z",
      xingceScore: 64,
      shenlunScore: 55.5,
      totalScore: 119.5,
      supersedesEvidenceId: null,
      supersededByEvidenceId: "evidence-new",
      active: false,
      verificationStatus: "user_confirmed",
      createdAt: "2026-07-13T00:00:00.000Z",
    });

    expect(view.statusLabel).toBe("已被新的判断替代");
    expect(view.active).toBe(false);
    expect(view.scoreLabel).toBe("行测 64 · 申论 55.5 · 总分 119.5");
    expect(view.dateLabel).toBe("2026-07-13");
  });

  it("builds a correction draft that explicitly supersedes the active evidence", () => {
    const view = scoreEvidenceHistoryItemView({
      evidenceId: "evidence-active",
      examCode: "guokao",
      examYear: 2026,
      sourceType: "self_report",
      reportedContext: "historical_estimate",
      timedStatus: "unknown",
      occurredAt: "2026-06-01T03:00:00.000Z",
      xingceScore: 61,
      shenlunScore: null,
      totalScore: null,
      supersedesEvidenceId: null,
      supersededByEvidenceId: null,
      active: true,
      verificationStatus: "user_confirmed",
      createdAt: "2026-06-01T04:00:00.000Z",
    });

    expect(view.statusLabel).toBe("当前有效");
    expect(view.correctionDraft).toEqual({
      xingceScore: "61",
      shenlunScore: "",
      scoreContext: "historical_estimate",
      scoreTimedStatus: "unknown",
      scoreDate: "2026-06-01",
      supersedesEvidenceId: "evidence-active",
    });
  });
});
