import { describe, expect, it, vi } from "vitest";
import type { ApiEnvelope } from "../miniprogram/domain/contracts";
import type { PositionDiscoveryDetail } from "../miniprogram/api/generated/types.gen";
import {
  PositionBriefingController,
  positionBriefingView,
} from "../miniprogram/features/positions/briefing-controller";

describe("position beginner briefing", () => {
  it("explains versioned position facts without claiming an admission probability", () => {
    const view = positionBriefingView(positionDetail());

    expect(view).toMatchObject({
      examLabel: "2026 国考",
      paperTypeLabel: "行政执法类试卷",
      headcountLabel: "计划招录 2 人",
      targetScoreLabel: "126.0–132.0 分，建议点 129.0 分",
      targetEvidenceLabel: "6 条样本 · 岗位直接证据",
      abilityStatusLabel: "等待总分证据",
    });
    expect(view.abilityNextStepLabel).toContain("不生成录取概率");
  });

  it("loads the exact deep-linked position and publishes the request trace", async () => {
    const getPositionDetail = vi.fn().mockResolvedValue({
      data: positionDetail(),
      requestId: "request-briefing",
      generatedAt: "2026-07-13T00:00:00Z",
    } satisfies ApiEnvelope<PositionDiscoveryDetail>);
    const controller = new PositionBriefingController({
      api: { getPositionDetail },
      positionId: " position-1 ",
      onChange: vi.fn(),
    });

    await controller.load();

    expect(getPositionDetail).toHaveBeenCalledWith("position-1");
    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      positionId: "position-1",
      trace: { requestId: "request-briefing" },
    });
  });

  it("rejects a missing position id instead of guessing from cached state", async () => {
    const getPositionDetail = vi.fn();
    const controller = new PositionBriefingController({
      api: { getPositionDetail },
      positionId: " ",
      onChange: vi.fn(),
    });

    await controller.load();

    expect(controller.snapshot()).toMatchObject({
      status: "error",
      error: { retryable: false },
    });
    expect(getPositionDetail).not.toHaveBeenCalled();
  });
});

function positionDetail(): PositionDiscoveryDetail {
  return {
    positionId: "position-1",
    positionCode: "300110001001",
    name: "一级行政执法员",
    departmentFull: "国家税务总局测试局",
    systemCode: "tax",
    region: "江西",
    city: "赣州市",
    headcount: 2,
    hasExtraExam: false,
    eligibilityStatus: "eligible",
    metCount: 4,
    pendingCount: 0,
    notMetCount: 0,
    pendingRequirementIds: [],
    notMetRequirementIds: [],
    reasonCodes: [],
    targetScore: {
      lowerBound: 126,
      recommended: 129,
      upperBound: 132,
      sampleCount: 6,
      basisLevel: "direct",
      evidenceLevel: "direct",
      generatedAt: "2026-07-01T00:00:00Z",
      validUntil: "2026-08-01T00:00:00Z",
    },
    isPrimaryGoal: true,
    paperType: "administrativeLaw",
    examYear: 2026,
    learnerProfileVersion: 2,
    ruleSetVersion: "rules-v1",
    positionDataVersion: "positions-v1",
    abilityEstimate: {
      readiness: "unavailable",
      estimate: null,
      missingComponents: ["xingce", "shenlun"],
    },
    fit: {
      status: "evidence_unavailable",
      band: null,
      evidenceStatus: "unavailable",
      evidenceReasonCodes: ["ability_estimate_unavailable"],
      scoreGap: null,
      requirementsBurden: {
        additionalExamRequired: false,
        pendingRequirementCount: 0,
        hardConditionNotMetCount: 0,
        pendingRequirementIds: [],
        hardConditionNotMetRequirementIds: [],
      },
    },
  };
}
