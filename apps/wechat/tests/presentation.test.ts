import { describe, expect, it } from "vitest";
import { evidenceLabel, orderedTasks, scoreRangeLabel } from "../miniprogram/domain/presentation";
import type { TodayTask } from "../miniprogram/domain/contracts";

describe("presentation contracts", () => {
  it("renders ranges without fake precision", () => {
    expect(scoreRangeLabel(121.4, 125.2)).toBe("121–125 分");
  });

  it("uses evidence language instead of probability claims", () => {
    expect(evidenceLabel("low")).toBe("证据较少");
    expect(evidenceLabel("high")).toBe("高可信");
  });

  it("orders daily actions by planner priority without mutating input", () => {
    const task = (id: string, priority: number): TodayTask => ({
      id,
      kind: "practice",
      track: "xingce",
      stage: "objective_practice",
      title: id,
      reason: "reason",
      estimatedMinutes: 10,
      completionRule: "done",
      status: "ready",
      priority,
      priorityScore: 1,
      priorityBreakdown: {
        goalGain: 1,
        abilityGain: 1,
        verificationUrgency: 1,
        timeFit: 1,
        loadPenalty: 0,
        trackBalanceBonus: 0,
      },
      mandatory: false,
      completion: {
        objective: "done",
        successCriteria: ["done"],
        terminalConditions: ["done"],
        completionEvidenceTypes: ["task_execution"],
        requiresReflection: false,
      },
      evidenceRefs: [
        {
          owner: "ability-estimation-agent",
          entityType: "ability_estimate",
          entityId: `evidence-${id}`,
          versionId: `version-${id}`,
          role: "ability",
        },
      ],
      startUrl: "/pages/reflection/index",
    });
    const tasks: TodayTask[] = [task("later", 2), task("first", 1)];

    expect(orderedTasks(tasks).map((task) => task.id)).toEqual(["first", "later"]);
    expect(tasks[0]?.id).toBe("later");
  });
});
