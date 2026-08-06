import { describe, expect, it } from "vitest";

import type { CompanionAskResponse } from "../miniprogram/api/generated/types.gen";
import { companionAnswerView } from "../miniprogram/features/companion/presentation";

function responseWithCitation(summary: string, kind = "learning_brain"): CompanionAskResponse {
  return {
    answerId: "answer-1",
    semanticState: "ready",
    scenarioKey: "tutoring_explanation",
    respondingAgent: "tutor",
    answerType: "learning_explanation",
    policyDecision: "answer",
    companionIntent: "thinking",
    answer: "先完成今天的训练，再根据结果调整。",
    citations: [
      {
        kind,
        title: kind === "daily_plan" ? "今日完整计划" : "当前学习档案",
        sourceId: "profile-1",
        version: "1",
        anchor: "profile-evidence",
        summary,
      },
    ],
    confidence: "medium",
    limitations: [],
    knowledgeFreshness: "current",
    allowedActions: [],
    highImpactDecision: null,
    quota: {
      accessMode: "unlimited",
      date: "2026-07-28",
      feature: "companion_ask",
      limit: null,
      used: 1,
    },
  };
}

describe("岸岸用户态证据展示", () => {
  it("保留后端提供的人话摘要", () => {
    const view = companionAnswerView(
      responseWithCitation("当前综合能力参考值约 112 分，本次判断使用了 2 条学习证据。"),
      "已根据当前证据回答",
    );

    expect(view.citations[0]?.summary).toBe(
      "当前综合能力参考值约 112 分，本次判断使用了 2 条学习证据。",
    );
    expect(view.quotaSummaryLabel).toBe("");
  });

  it("不让旧响应中的原始 JSON 泄漏到界面", () => {
    const view = companionAnswerView(
      responseWithCitation('{"ability":{"estimate":{"algorithmVersion":"ability-estimate-v1"}}}'),
      "已根据当前证据回答",
    );

    expect(view.citations[0]?.summary).toBe("已读取当前学习档案中的能力、错因与计划记录。");
    expect(view.citations[0]?.summary).not.toContain("algorithmVersion");
  });

  it("把重复的今日训练压成可扫读的计划摘要", () => {
    const view = companionAnswerView(
      responseWithCitation(
        "今日共 4 项任务：申论「先读懂申论材料」，15 分钟；行测「言语理解与表达」，30 分钟；行测「言语理解与表达（第 2 组）」，30 分钟；行测「言语理解与表达（第 3 组）」，30 分钟。",
        "daily_plan",
      ),
      "已根据当前证据回答",
    );

    expect(view.citations[0]).toMatchObject({
      title: "今日计划概览",
      sourceLabel: "今日计划",
      summary: "今日 4 项 · 预计 105 分钟",
      summaryItems: ["申论 · 先读懂申论材料 · 15 分钟", "行测 · 言语理解与表达 · 3 组 · 90 分钟"],
    });
  });
});
