import { describe, expect, it } from "vitest";
import { abilityEstimateView } from "../miniprogram/pages/positions/features/ability/presentation";

describe("ability estimate presentation", () => {
  it("never labels a partial single-subject estimate as a usable total", () => {
    const view = abilityEstimateView({
      readiness: "partial",
      missingComponents: ["shenlun"],
      estimate: {
        estimateVersionId: "estimate-1",
        examCode: "guokao",
        examYear: 2026,
        status: "partial",
        xingce: { lower: 58, recommended: 64, upper: 70 },
        shenlun: null,
        total: null,
        confidence: "low",
        sourceEvidenceIds: ["evidence-1"],
        evidenceSummary: {
          evidenceCount: 1,
          xingceEvidenceCount: 1,
          shenlunEvidenceCount: 0,
          sourceCounts: { self_report: 1 },
          latestEvidenceAt: "2026-07-12T04:00:00Z",
          missingComponents: ["shenlun"],
          conflictComponents: [],
        },
        algorithmVersion: "ability-estimate-v1",
        inputFingerprint: "a".repeat(64),
        supersedesEstimateVersionId: null,
        generatedAt: "2026-07-13T00:00:00Z",
        validUntil: "2027-07-12T00:00:00Z",
      },
    });

    expect(view.totalLabel).toBe("待补证据");
    expect(view.missingLabel).toBe("还需补充申论");
    expect(view.canCalculatePositionGap).toBe(false);
  });

  it("blocks position-gap calculation when complete evidence has expired", () => {
    const view = abilityEstimateView({
      readiness: "stale",
      missingComponents: [],
      estimate: {
        estimateVersionId: "estimate-stale",
        examCode: "guokao",
        examYear: 2026,
        status: "complete",
        xingce: { lower: 58, recommended: 64, upper: 70 },
        shenlun: { lower: 51, recommended: 56, upper: 61 },
        total: { lower: 109, recommended: 120, upper: 131 },
        confidence: "medium",
        sourceEvidenceIds: ["evidence-1"],
        evidenceSummary: {
          evidenceCount: 1,
          xingceEvidenceCount: 1,
          shenlunEvidenceCount: 1,
          sourceCounts: { self_report: 1 },
          latestEvidenceAt: "2025-07-12T04:00:00Z",
          missingComponents: [],
          conflictComponents: [],
        },
        algorithmVersion: "ability-estimate-v1",
        inputFingerprint: "a".repeat(64),
        supersedesEstimateVersionId: null,
        generatedAt: "2025-07-13T00:00:00Z",
        validUntil: "2026-07-12T00:00:00Z",
      },
    });

    expect(view.statusLabel).toBe("总分记录已过期");
    expect(view.missingLabel).toBe("需补充新成绩");
    expect(view.gapBlockDetail).toContain("过期记录");
    expect(view.canCalculatePositionGap).toBe(false);
  });
});
