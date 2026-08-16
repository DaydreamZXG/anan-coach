import { describe, expect, it } from "vitest";

import type { CompanionAskResponse } from "../miniprogram/api/generated/types.gen";
import { companionAnswerView } from "../miniprogram/features/companion/presentation";
import {
  essayStageCapability,
  type EssayTaskStage,
} from "../miniprogram/features/essay-task/capabilities";

function response(respondingAgent: CompanionAskResponse["respondingAgent"]): CompanionAskResponse {
  return {
    answerId: "answer-1",
    semanticState: "ready",
    scenarioKey: "tutoring_explanation",
    respondingAgent,
    answerType: "learning_explanation",
    policyDecision: "answer",
    companionIntent: "thinking",
    answer: "先完成今天的训练，再根据结果调整。",
    citations: [],
    confidence: "medium",
    limitations: [],
    knowledgeFreshness: "current",
    allowedActions: [],
    highImpactDecision: null,
    quota: {
      accessMode: "unlimited",
      date: "2026-08-11",
      feature: "companion_ask",
      limit: null,
      used: 1,
    },
  };
}

describe("岸岸是唯一用户可见教练", () => {
  it.each(["position", "tutor", "planner"] as const)(
    "隐藏内部 %s 路由身份但保留路由事实",
    (respondingAgent) => {
      const view = companionAnswerView(response(respondingAgent), "已根据当前证据回答");

      expect(view.agentLabel).toBe("岸岸");
      expect(view.respondingAgent).toBe(respondingAgent);
    },
  );

  it("所有申论能力入口只显示岸岸，不暴露 Agent 产品角色", () => {
    const stages: EssayTaskStage[] = [
      "essay_material_reading",
      "essay_point_extraction",
      "essay_outlining",
      "essay_drafting",
      "essay_review",
      "essay_rewrite",
      "essay_transfer",
    ];

    for (const stage of stages) {
      const label = essayStageCapability(stage).modeLabel;
      expect(label).toMatch(/^岸岸 · /u);
      expect(label).not.toContain("Agent");
    }
  });
});
