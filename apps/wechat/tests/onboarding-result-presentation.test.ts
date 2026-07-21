import { describe, expect, it } from "vitest";
import type { OnboardingResultProjection } from "../miniprogram/api/generated/types.gen";
import { onboardingResultView } from "../miniprogram/features/onboarding/result-presentation";

describe("onboarding result presentation", () => {
  it("shows a signed, auditable gap only for complete dual-subject evidence", () => {
    const view = onboardingResultView(result({ status: "evidence_sufficient" }));

    expect(view.title).toContain("11");
    expect(view.gapLabel).toBe("+11 分");
    expect(view.currentTotalLabel).toBe("121–125");
    expect(view.targetTotalLabel).toBe("132–136");
    expect(view.weekdayBudgetLabel).toBe("1 小时");
    expect(view.weekendBudgetLabel).toBe("2 小时");
    expect(view.action).toBe("enter_workspace");
  });

  it("names the missing subject and refuses to synthesize a total gap", () => {
    const partial = result({ status: "baseline_required" });
    const ability = partial.abilityEstimate;
    if (!ability) throw new Error("test fixture requires an ability estimate");
    partial.reasonCode = "ability_partial";
    partial.abilityEstimate = {
      ...ability,
      status: "partial",
      shenlun: null,
      total: null,
      evidenceSummary: {
        ...ability.evidenceSummary,
        missingComponents: ["shenlun"],
      },
    };
    partial.scoreGap = null;
    partial.nextAction = "record_baseline";

    const view = onboardingResultView(partial);
    expect(view.title).toContain("申论");
    expect(view.gapLabel).toBe("暂不可计算");
    expect(view.actionLabel).toContain("双科");
  });

  it.each([
    ["position_selection_required", "先选一个主目标"],
    ["no_eligible_position", "不编造岗位分差"],
  ] as const)("keeps %s distinct", (status, title) => {
    const projection = result({ status });
    projection.primaryGoal = null;
    projection.scoreGap = null;
    projection.nextAction = "choose_position";
    projection.reasonCode =
      status === "position_selection_required"
        ? "primary_goal_missing"
        : "no_current_eligible_position";

    expect(onboardingResultView(projection).title).toContain(title);
  });
});

function result(input: {
  status: OnboardingResultProjection["status"];
}): OnboardingResultProjection {
  return {
    status: input.status,
    reasonCode: "ready",
    progressVersion: 3,
    qualificationProfileVersion: 2,
    preparationExperience: {
      experienceVersionId: "experience-1",
      experienceVersion: 1,
      declarationStatus: "declared",
      firstSystematic: true,
      externalLearning: false,
      priorGuokaoExam: false,
      retake: false,
      priorExamYears: [],
      sourceType: "user_confirmed",
      recommendedEntry: "guided_first_start",
      createdAt: "2026-07-13T00:00:00Z",
      supersedesExperienceVersionId: null,
    },
    abilityEstimate: {
      estimateVersionId: "estimate-1",
      examCode: "guokao",
      examYear: 2027,
      status: "complete",
      xingce: { lower: 62, recommended: 64, upper: 66 },
      shenlun: { lower: 59, recommended: 60, upper: 61 },
      total: { lower: 121, recommended: 124, upper: 125 },
      confidence: "medium",
      sourceEvidenceIds: ["evidence-1", "evidence-2"],
      evidenceSummary: {
        evidenceCount: 2,
        sourceCounts: { self_report: 2 },
        xingceEvidenceCount: 1,
        shenlunEvidenceCount: 1,
        latestEvidenceAt: "2026-07-12T00:00:00Z",
        missingComponents: [],
        conflictComponents: [],
      },
      algorithmVersion: "ability-v1",
      inputFingerprint: "fingerprint",
      generatedAt: "2026-07-13T00:00:00Z",
      validUntil: "2027-07-12T00:00:00Z",
    },
    primaryGoal: {
      goalVersionId: "goal-1",
      positionId: "position-1",
      positionName: "综合管理岗",
      departmentName: "中央机关",
      targetScore: {
        lowerBound: 132,
        recommended: 135,
        upperBound: 136,
        sampleCount: 24,
        basisLevel: "direct",
        evidenceLevel: "direct",
        generatedAt: "2026-07-13T00:00:00Z",
        validUntil: "2027-07-12T00:00:00Z",
      },
    },
    scoreGap: {
      unit: "points",
      direction: "positive_means_target_above_current",
      lower: 7,
      recommended: 11,
      upper: 15,
    },
    weeklyPreference: {
      preferenceVersionId: "preference-1",
      preferenceVersion: 1,
      timezone: "Asia/Shanghai",
      effectiveFrom: "2026-07-13",
      weekdayMinutes: 60,
      weekendMinutes: 120,
      longBlockMinutes: 120,
      fragmentMinutes: 25,
      fixedRestDay: "sunday",
      mockExamDay: "saturday",
      sourceType: "user_confirmed",
      createdAt: "2026-07-13T00:00:00Z",
      temporaryAdjustmentOwner: "daily_capacity",
      temporaryAdjustmentEndpoint: "/api/v2/plans/today/capacity",
    },
    nextAction: "enter_workspace",
    generatedAt: "2026-07-13T00:00:00Z",
    validUntil: "2027-07-12T00:00:00Z",
  };
}
