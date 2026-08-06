import { describe, expect, it } from "vitest";
import {
  qualificationActionView,
  positionCardView,
  positionComparisonItemView,
  positionRecommendationPresentation,
  qualificationGapView,
  primaryGoalPreviewView,
  requirementCheckView,
} from "../miniprogram/pages/positions/features/presentation";
import type {
  PositionComparisonItem,
  PositionDiscoveryItem,
  PositionWorkspaceItem,
  RequirementCheck,
} from "../miniprogram/api/generated/types.gen";

function position(changes: Partial<PositionDiscoveryItem> = {}): PositionDiscoveryItem {
  return {
    positionId: "position-1",
    positionCode: "3001",
    name: "一级行政执法员",
    departmentFull: "国家税务总局测试局",
    systemCode: "tax",
    region: "江西",
    city: "赣州市",
    hasExtraExam: false,
    headcount: 2,
    eligibilityStatus: "eligible",
    metCount: 3,
    pendingCount: 0,
    notMetCount: 0,
    notMetRequirementIds: [],
    pendingRequirementIds: [],
    reasonCodes: [],
    targetScore: {
      basisLevel: "direct",
      generatedAt: "2026-07-01T00:00:00Z",
      validUntil: "2026-08-01T00:00:00Z",
      lowerBound: 126.2,
      recommended: 129,
      upperBound: 131.8,
      evidenceLevel: "direct",
      sampleCount: 6,
    },
    isPrimaryGoal: false,
    ...changes,
  };
}

describe("position presentation", () => {
  it("presents qualification gaps as fixed actions without inventing eligibility", () => {
    const gap = qualificationGapView({
      factKey: "major",
      gapType: "position_confirmation",
      affectedPositionCount: 12,
      samplePositionIds: ["position-1"],
      samplePositionCodes: ["3001"],
      action: "review_position_requirement",
    });

    expect(gap.factLabel).toBe("专业");
    expect(gap.statusLabel).toBe("需按岗位原文确认");
    expect(gap.actionLabel).toBe("在待确认岗位中处理");
  });

  it("does not present catalog-wide position confirmations as an incomplete profile", () => {
    const action = qualificationActionView([
      qualificationGapView({
        factKey: "major",
        gapType: "position_confirmation",
        affectedPositionCount: 12,
        samplePositionIds: ["position-1"],
        samplePositionCodes: ["3001"],
        action: "review_position_requirement",
      }),
      qualificationGapView({
        factKey: "other_conditions",
        gapType: "position_confirmation",
        affectedPositionCount: 8,
        samplePositionIds: ["position-2"],
        samplePositionCodes: ["3002"],
        action: "review_position_requirement",
      }),
    ]);

    expect(action).toBeNull();
  });

  it("keeps true missing profile facts routed to profile completion", () => {
    const action = qualificationActionView([
      qualificationGapView({
        factKey: "hukou",
        gapType: "missing_fact",
        affectedPositionCount: 4,
        samplePositionIds: ["position-1"],
        samplePositionCodes: ["3001"],
        action: "complete_profile",
      }),
    ]);

    expect(action).toEqual({
      title: "还有 1 项报考资料待补充",
      copy: "补齐档案后，岗位判断会自动更新。",
      label: "去补充",
    });
  });

  it("presents a goal change preview without deriving business impact", () => {
    const expiry = new Date("2026-07-12T12:15:00Z");
    const view = primaryGoalPreviewView({
      previewId: "preview-1",
      positionId: "position-1",
      eligibilitySnapshotId: "snapshot-1",
      currentGoalVersionId: "goal-1",
      learnerProfileVersion: 2,
      positionDataVersion: "positions-v1",
      targetScore: {
        basisLevel: "direct",
        generatedAt: "2026-07-01T00:00:00Z",
        validUntil: "2026-08-01T00:00:00Z",
        lowerBound: 126,
        recommended: 129,
        upperBound: 132,
        evidenceLevel: "direct",
        sampleCount: 6,
      },
      planImpact: {
        requiresPlanRebuild: true,
        changeType: "goal_changed",
        positionChanged: true,
        previousTargetScore: 125,
        nextTargetScore: 129,
        targetScoreDelta: 4,
        invalidates: ["daily_plan", "weekly_plan", "score_gap"],
      },
      expiresAt: expiry.toISOString(),
      createdAt: "2026-07-12T12:00:00Z",
    });

    expect(view.previousScoreLabel).toBe("125.0");
    expect(view.nextScoreLabel).toBe("129.0");
    expect(view.deltaLabel).toBe("提高 4.0 分");
    expect(view.expiryLabel).toBe(
      `${String(expiry.getHours()).padStart(2, "0")}:${String(expiry.getMinutes()).padStart(
        2,
        "0",
      )} 前确认有效`,
    );
  });

  it("presents server comparison highlights without inventing a winner", () => {
    const item: PositionComparisonItem = {
      positionId: "position-1",
      positionCode: "3001",
      name: "一级行政执法员",
      departmentFull: "国家税务总局测试局",
      systemCode: "tax",
      region: "江西",
      city: "南昌市",
      hasExtraExam: false,
      headcount: 2,
      eligibilityStatus: "eligible" as const,
      metCount: 3,
      pendingCount: 0,
      notMetCount: 0,
      notMetRequirementIds: [],
      pendingRequirementIds: [],
      reasonCodes: [],
      targetScore: {
        basisLevel: "direct",
        generatedAt: "2026-07-01T00:00:00Z",
        validUntil: "2026-08-01T00:00:00Z",
        lowerBound: 125,
        recommended: 127,
        upperBound: 129,
        evidenceLevel: "direct" as const,
        sampleCount: 8,
      },
      isPrimaryGoal: false,
      paperType: "administrativeLaw",
    };
    const view = positionComparisonItemView(item, {
      confirmedEligiblePositionIds: ["position-1"],
      largestHeadcountPositionIds: ["position-1"],
      lowestTargetScorePositionIds: [],
      primaryGoalPositionId: null,
    });

    expect(view.paperTypeLabel).toBe("行政执法类");
    expect(view.highlightLabels).toEqual(["资格已确认", "招录人数最多"]);
    expect(view.highlightLabels).not.toContain("最适合");
  });

  it("renders backend evidence without inventing a probability", () => {
    const view = positionCardView(position());
    expect(view.eligibilityLabel).toBe("资格已确认");
    expect(view.scoreLabel).toBe("126.2–131.8");
    expect(view.scoreEvidenceLabel).toContain("6 个真实样本");
    expect(JSON.stringify(view)).not.toContain("概率");
  });

  it("keeps pending and unavailable evidence explicit", () => {
    const view = positionCardView(
      position({
        eligibilityStatus: "needs_confirmation",
        metCount: 1,
        pendingCount: 2,
        targetScore: null,
      }),
    );
    expect(view.eligibilityLabel).toBe("待确认");
    expect(view.conditionSummary).toContain("2 项");
    expect(view.scoreLabel).toBe("暂无可靠区间");
  });

  it("requires confirmed eligibility and complete fit evidence before calling a position recommended", () => {
    expect(positionRecommendationPresentation(workspacePosition())).toEqual({
      recommendationBucket: "recommended",
      recommendationEvidenceAvailable: true,
      recommendationEvidenceLabel: "资格、目标分与能力分差证据完整",
    });

    expect(
      positionRecommendationPresentation(
        workspacePosition({
          fit: {
            status: "evidence_unavailable",
            band: null,
            evidenceStatus: "partial",
            evidenceReasonCodes: ["ability_estimate_partial"],
            scoreGap: null,
            requirementsBurden: {
              additionalExamRequired: false,
              hardConditionNotMetCount: 0,
              hardConditionNotMetRequirementIds: [],
              pendingRequirementCount: 0,
              pendingRequirementIds: [],
            },
          },
        }),
      ),
    ).toMatchObject({
      recommendationBucket: "recommended",
      recommendationEvidenceAvailable: true,
      recommendationEvidenceLabel: "资格与历年目标分依据完整，完成基线后补充个人分差",
    });

    expect(
      positionRecommendationPresentation(
        workspacePosition({
          eligibilityStatus: "needs_confirmation",
          pendingCount: 1,
          pendingRequirementIds: ["major"],
        }),
      ),
    ).toMatchObject({
      recommendationBucket: "needs_confirmation",
      recommendationEvidenceAvailable: false,
    });
  });

  it("shows the original condition and evidence lineage", () => {
    const check: RequirementCheck = {
      requirementId: "requirement-1",
      status: "met",
      reasonCode: "condition_met",
      factId: "fact-1",
      evidenceIds: ["evidence-1"],
      field: "education",
      operator: "one_of",
      expected: ["本科", "硕士"],
      sourceText: "本科及以上",
      sourceReference: "position:education_min",
    };
    const view = requirementCheckView(check);
    expect(view.conditionLabel).toBe("本科及以上");
    expect(view.evidenceLabel).toContain("1 条");
  });
});

function workspacePosition(changes: Partial<PositionWorkspaceItem> = {}): PositionWorkspaceItem {
  return {
    ...position(),
    fit: {
      status: "overlap",
      band: "overlap",
      evidenceStatus: "ready",
      evidenceReasonCodes: [],
      scoreGap: {
        direction: "positive_means_target_above_current",
        lower: -2,
        recommended: 1,
        upper: 4,
        unit: "points",
      },
      requirementsBurden: {
        additionalExamRequired: false,
        hardConditionNotMetCount: 0,
        hardConditionNotMetRequirementIds: [],
        pendingRequirementCount: 0,
        pendingRequirementIds: [],
      },
    },
    ...changes,
  };
}
