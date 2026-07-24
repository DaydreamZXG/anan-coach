import { describe, expect, it } from "vitest";
import {
  plannedDuration,
  presentCapacityChanges,
  type CapacityComparableTask,
} from "../miniprogram/features/planning/capacity-preview";

describe("capacity adjustment preview", () => {
  it("describes retained, shortened, added and removed tasks from real plan versions", () => {
    const current = [
      task({ title: "资料分析验证", estimatedMinutes: 60 }),
      task({
        title: "晚间复盘",
        kind: "reflection",
        stage: "objective_reflection",
        estimatedMinutes: 45,
      }),
      task({ title: "判断推理", estimatedMinutes: 60 }),
    ];
    const proposed = [
      task({ title: "资料分析验证", estimatedMinutes: 60 }),
      task({
        title: "晚间复盘",
        kind: "reflection",
        stage: "objective_reflection",
        estimatedMinutes: 20,
      }),
      task({
        title: "言语理解复测",
        kind: "verification",
        stage: "objective_verification",
        estimatedMinutes: 30,
      }),
    ];

    const changes = presentCapacityChanges(current, proposed);

    expect(changes.map((change) => change.kind)).toEqual([
      "retained",
      "shortened",
      "added",
      "removed",
    ]);
    expect(changes[1]).toMatchObject({
      title: "晚间复盘",
      duration: "45 分钟 → 20 分钟",
    });
    expect(changes[3]).toMatchObject({
      title: "判断推理",
      label: "今天不排",
    });
    expect(plannedDuration(proposed)).toBe("1.8 小时");
  });

  it("matches duplicate task signatures one by one instead of collapsing them", () => {
    const current = [
      task({ title: "同名训练", estimatedMinutes: 30 }),
      task({ title: "同名训练", estimatedMinutes: 60 }),
    ];
    const proposed = [
      task({ title: "同名训练", estimatedMinutes: 30 }),
      task({ title: "同名训练", estimatedMinutes: 45 }),
    ];

    expect(presentCapacityChanges(current, proposed).map((change) => change.kind)).toEqual([
      "retained",
      "shortened",
    ]);
  });
});

function task(
  overrides: Partial<CapacityComparableTask> & Pick<CapacityComparableTask, "title">,
): CapacityComparableTask {
  return {
    title: overrides.title,
    estimatedMinutes: overrides.estimatedMinutes ?? 30,
    kind: overrides.kind ?? "practice",
    reason: overrides.reason ?? "来自当前真实计划的排序理由。",
    stage: overrides.stage ?? "objective_practice",
    track: overrides.track ?? "xingce",
  };
}
