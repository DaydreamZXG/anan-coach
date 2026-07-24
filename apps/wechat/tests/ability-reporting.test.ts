import { describe, expect, it } from "vitest";
import { reportedScoreRequestFromDraft } from "../miniprogram/features/ability/reporting";

describe("reported score evidence", () => {
  it("builds a same-date structured total-score report", () => {
    const result = reportedScoreRequestFromDraft({
      xingceScore: "65",
      shenlunScore: "55.5",
      scoreContext: "full_mock",
      scoreTimedStatus: "timed",
      scoreDate: "2026-07-13",
      supersedesEvidenceId: "evidence-old",
    });

    expect(result.request).toEqual({
      reportedContext: "full_mock",
      xingceScore: 65,
      shenlunScore: 55.5,
      timedStatus: "timed",
      occurredAt: "2026-07-12T16:00:00.000Z",
      supersedesEvidenceId: "evidence-old",
    });
  });

  it("rejects empty and out-of-range reports before the API call", () => {
    const empty = reportedScoreRequestFromDraft({
      xingceScore: "",
      shenlunScore: "",
      scoreContext: "full_mock",
      scoreTimedStatus: "timed",
      scoreDate: "2026-07-13",
    });
    const invalid = reportedScoreRequestFromDraft({
      xingceScore: "101",
      shenlunScore: "55",
      scoreContext: "full_mock",
      scoreTimedStatus: "timed",
      scoreDate: "2026-07-13",
    });

    expect(empty.error).toContain("至少填写");
    expect(invalid.error).toContain("0–100");
  });
});
