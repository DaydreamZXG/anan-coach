import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { TodayDashboard } from "../miniprogram/api/generated/types.gen";
import { presentTodayDashboard } from "../miniprogram/pages/today/presentation";
import { prototypeToday } from "./fixtures/prototype-data";

function dashboardWithVerbalGroups(groupCount = 13): TodayDashboard {
  const source = prototypeToday.tasks[0];
  if (!source) throw new Error("Prototype today requires a source task");
  const groups = Array.from({ length: groupCount }, (_, index) => ({
    ...source,
    id: `task-verbal-${String(index + 1)}`,
    title: `行测提分练习：言语理解与表达（第 ${String(index + 1)} 组）`,
    estimatedMinutes: 30,
    priority: index + 4,
    priorityScore: 0.7 - index * 0.01,
    startUrl: "/xingce/xingce-task-intro/index?mode=targeted",
  }));
  return {
    ...prototypeToday,
    capacity: {
      ...prototypeToday.capacity,
      availableMinutes: 480,
      confirmed: false,
      sourceType: "weekly_preference",
    },
    tasks: [...prototypeToday.tasks, ...groups],
  };
}

describe("today workload defaults", () => {
  it("projects repeated atomic practice groups as one transparent queue", () => {
    const dashboard = dashboardWithVerbalGroups();
    const view = presentTodayDashboard(dashboard);
    const queue = view.secondaryTasks.find((item) => item.queueSize === 13);

    expect(view.activeTasks).toHaveLength(16);
    expect(view.secondaryTasks).toHaveLength(3);
    expect(queue).toMatchObject({
      id: "task-verbal-1",
      title: "行测提分练习：言语理解与表达",
      queueSize: 13,
      estimatedMinutes: 390,
      queueMetaLabel: "行测 · 还剩 13 组 · 共 390 分钟",
      queueActionLabel: "开始",
    });
  });

  it("replays deterministically and advances only from a refreshed dashboard", () => {
    const dashboard = dashboardWithVerbalGroups();
    const first = presentTodayDashboard(dashboard);
    const replay = presentTodayDashboard(dashboard);
    expect(replay.secondaryTasks).toEqual(first.secondaryTasks);

    const refreshed = presentTodayDashboard({
      ...dashboard,
      tasks: dashboard.tasks.map((task) =>
        task.id === "task-verbal-1" ? { ...task, status: "complete" as const } : task,
      ),
    });
    const queue = refreshed.secondaryTasks.find((item) => item.queueSize === 12);
    expect(queue).toMatchObject({
      id: "task-verbal-2",
      queueSize: 12,
      estimatedMinutes: 360,
      queueMetaLabel: "行测 · 还剩 12 组 · 共 360 分钟",
    });
  });

  it("keeps weekly capacity automatic and the queue projection read-only", () => {
    const dashboard = dashboardWithVerbalGroups();
    const before = JSON.stringify(dashboard);
    const view = presentTodayDashboard(dashboard);

    expect(JSON.stringify(dashboard)).toBe(before);
    expect(view.capacityStatusLabel).toBe("按每周计划");
    expect(view.capacitySourceLabel).toBe("按每周学习时间自动安排");
    expect(view.capacitySourceLabel).not.toContain("确认");

    const template = readFileSync("miniprogram/pages/today/index.wxml", "utf8");
    expect(template).toContain('wx:for="{{view.secondaryTasks}}"');
    expect(template).toContain('data-id="{{item.id}}"');
    expect(template).toContain("{{item.queueMetaLabel}}");
    expect(template).toContain('class="compact-task__copy"');
    expect(template).not.toContain('wx:if="{{index > 0}}"');

    const stylesheet = readFileSync("miniprogram/pages/today/index.wxss", "utf8");
    expect(stylesheet).toMatch(/\.compact-task__action\s*\{[^}]*flex:\s*none;/s);
    expect(stylesheet).toMatch(/\.compact-task__action\s*\{[^}]*white-space:\s*nowrap;/s);
  });
});
