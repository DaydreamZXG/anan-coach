import { describe, expect, it } from "vitest";

import type { LearnerCoachContext } from "../miniprogram/api/generated/types.gen";
import { companionContextView } from "../miniprogram/features/companion/presentation";
import { prototypeToday } from "./fixtures/prototype-data";

describe("岸岸统一学员上下文", () => {
  it("用服务端 coach context 版本发问，不再从另一份前端画像推导", () => {
    const context = coachContext("verifying", 8);

    const view = companionContextView(
      { status: "ready", value: context },
      { status: "ready", value: prototypeToday },
    );

    expect(view.askAvailable).toBe(true);
    expect(view.learnerStateVersion).toBe(context.profileVersionId);
    expect(view.contextEvidenceLabel).toBe("8 条学习记录");
  });

  it("上下文请求过期时禁止发问，仍保留上次结果供用户看", () => {
    const context = coachContext("initial_hypothesis", 2);

    const view = companionContextView(
      { status: "stale", value: context, message: "画像已过期", requestId: "request-1" },
      { status: "ready", value: prototypeToday },
    );

    expect(view.askAvailable).toBe(false);
    expect(view.learnerStateVersion).toBe(context.profileVersionId);
    expect(view.contextEvidenceLabel).toBe("上次同步 · 2 条学习记录");
  });
});

function coachContext(
  level: LearnerCoachContext["recognition"]["level"],
  evidenceCount: number,
): LearnerCoachContext {
  return {
    contractVersion: "learner-coach-context@v1",
    profileVersionId: "00000000-0000-4000-8000-000000000901",
    profile: {
      profileVersionId: "00000000-0000-4000-8000-000000000901",
      evidenceSummary: {
        scoreEvidenceCount: evidenceCount,
        xingceAttemptCount: 0,
        xingceMasteryCount: 0,
        essayMasteryCount: 0,
        taskExecutionCount: 0,
        externalAnswerCount: 0,
      },
      decisionSummary: { reviewDueCount: 0 },
    } as LearnerCoachContext["profile"],
    recognition: {
      level,
      label: level === "verifying" ? "正在验证" : "形成初步判断",
      observation: "由服务端证据编译的观察。",
      evidenceCount,
      stableMasteryCount: 0,
    },
    freshness: "current",
    strategyDirective: { active: null, pending: null },
    generatedAt: "2026-08-13T10:00:00Z",
    validUntil: "2026-08-14T10:00:00Z",
  };
}
