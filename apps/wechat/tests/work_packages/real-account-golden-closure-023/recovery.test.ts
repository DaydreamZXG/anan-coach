import { afterEach, describe, expect, it, vi } from "vitest";

import type { TodayDashboard } from "../../../miniprogram/api/generated/types.gen";
import {
  coachLocalDate,
  readCoachTaskBeforeSnapshot,
  storeCoachTaskBeforeSnapshot,
  type CoachTaskBeforeSnapshot,
} from "../../../miniprogram/features/coach-loop/feedback-store";
import {
  coachReturnFeedbackConsumedAuthoritatively,
  presentTodayDashboard,
} from "../../../miniprogram/pages/today/presentation";
import { prototypeToday } from "../../fixtures/prototype-data";

afterEach(() => {
  vi.unstubAllGlobals();
});

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function dashboardAfterTask(): TodayDashboard {
  const dashboard = clone(prototypeToday);
  dashboard.planVersionId = "00000000-0000-4000-8000-000000000923";
  return dashboard;
}

function dashboardOnlySnapshot(ownerUserId: string): CoachTaskBeforeSnapshot {
  const task = prototypeToday.tasks[0];
  if (!task) throw new Error("Prototype task is missing");
  const now = new Date(2026, 7, 14, 21, 0, 0);
  return {
    version: 1,
    ownerUserId,
    localDate: coachLocalDate(now),
    capturedAt: now.toISOString(),
    taskId: task.id,
    taskTitle: task.title,
    taskKind: task.kind,
    taskReason: task.reason,
    completionRule: task.completionRule,
    planVersionId: "00000000-0000-4000-8000-000000000922",
  };
}

describe("WP-REAL-ACCOUNT-GOLDEN-CLOSURE-023-AS-06", () => {
  it("画像不可用时仍用同账号任务快照呈现诚实的计划变化", () => {
    const before = dashboardOnlySnapshot("user-023");
    const dashboard = dashboardAfterTask();

    const view = presentTodayDashboard(dashboard, {
      profileStatus: "unavailable",
      coachContext: null,
      candidate: null,
      beforeSnapshot: before,
      entryIntent: null,
    });

    expect(view.loop?.feedback?.changes).toContain("接下来的安排已经根据这次结果重新排过。");
    expect(view.loop?.feedback?.changes.join(" ")).not.toMatch(/\d+\s+道真实作答/u);
    expect(view.loop?.feedback?.nextAction).toContain("下一步");
    expect(coachReturnFeedbackConsumedAuthoritatively(dashboard, null, before)).toBe(true);
  });

  it("丢回执且计划尚未变化时保留快照等待恢复", () => {
    const before = dashboardOnlySnapshot("user-023");
    const unchanged = dashboardAfterTask();
    unchanged.planVersionId = before.planVersionId;

    expect(coachReturnFeedbackConsumedAuthoritatively(unchanged, null, before)).toBe(false);
  });

  it("同一计划内任务被权威标记完成时才消费快照", () => {
    const before = dashboardOnlySnapshot("user-023");
    const dashboard = dashboardAfterTask();
    dashboard.planVersionId = before.planVersionId;
    const currentTask = dashboard.tasks.find((task) => task.id === before.taskId);
    if (!currentTask) throw new Error("Current task is missing");
    currentTask.status = "complete";

    expect(coachReturnFeedbackConsumedAuthoritatively(dashboard, null, before)).toBe(true);
  });

  it("无画像快照可恢复，但损坏和跨账号数据不进入反馈", () => {
    const storage = new Map<string, unknown>();
    vi.stubGlobal("wx", {
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      getStorageSync: (key: string) => storage.get(key),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    const now = new Date(2026, 7, 14, 21, 0, 0);
    const before = dashboardOnlySnapshot("user-023");

    storeCoachTaskBeforeSnapshot(before);
    expect(readCoachTaskBeforeSnapshot("user-023", now)).toEqual(before);
    expect(readCoachTaskBeforeSnapshot("other-user", now)).toBeNull();

    storage.set("gongkao.coach-loop.before-task.v1.user-023", {
      ...before,
      taskId: "",
    });
    expect(readCoachTaskBeforeSnapshot("user-023", now)).toBeNull();
  });
});
