import { describe, expect, it } from "vitest";
import { prototypeToday } from "./fixtures/prototype-data";
import type { TodayDashboard } from "../miniprogram/api/generated/types.gen";
import {
  evidenceReferenceLabel,
  presentTodayDashboard,
} from "../miniprogram/pages/today/presentation";

describe("today dual-track planning", () => {
  it("allocates the confirmed daily budget across both exam tracks", () => {
    const totalMinutes = prototypeToday.tasks.reduce(
      (total, task) => total + task.estimatedMinutes,
      0,
    );

    expect(totalMinutes).toBe(prototypeToday.capacity.availableMinutes);
    expect(new Set(prototypeToday.tasks.map((task) => task.track))).toEqual(
      new Set(["xingce", "essay"]),
    );

    const view = presentTodayDashboard(prototypeToday);
    expect(view.trackAllocations.map((item) => [item.track, item.minutes])).toEqual([
      ["xingce", 25],
      ["essay", 20],
    ]);
    expect(view.planSectionTitle).toBe("今日双轨任务");
  });

  it("makes every assignment traceable to evidence and an explicit priority score", () => {
    for (const task of prototypeToday.tasks) {
      const breakdown = task.priorityBreakdown;

      expect(task.evidenceRefs.length).toBeGreaterThan(0);
      expect(Object.values(breakdown).every((value) => value >= 0 && value <= 1)).toBe(true);
      expect(task.priorityScore).toBeGreaterThanOrEqual(0);
      expect(task.priorityScore).toBeLessThanOrEqual(1);
      expect(task.reason.length).toBeGreaterThan(0);
      expect(task.completionRule.length).toBeGreaterThan(0);
      expect("startUrl" in task).toBe(true);
    }

    const priorities = prototypeToday.tasks.map((task) => task.priorityScore);
    expect(priorities).toEqual([...priorities].sort((left, right) => right - left));
  });

  it("does not present unknown objective closure counts as zero or null items", () => {
    const view = presentTodayDashboard({
      ...prototypeToday,
      dueReflectionCount: null,
      dueVerificationCount: null,
      dualTrackExplanation: {
        ...prototypeToday.dualTrackExplanation,
        capabilityGaps: {
          objectiveClosure: "行测结构化复盘与间隔验证的 canonical writer 尚未发布。",
        },
      },
    });

    expect(view.dueSummary).toBe("复盘与间隔验证正在整理");
    expect(view.dueSummary).not.toContain("null 项");
    expect(view.dueSummary).not.toContain("0 项");
  });

  it("gives the essay track a concrete learning stage and acceptance rule", () => {
    const essayTask = prototypeToday.tasks.find((task) => task.track === "essay");

    expect(essayTask?.kind).toBe("essay");
    expect(essayTask?.stage).toBe("essay_material_reading");
    expect(essayTask?.reason).toContain("申论估分证据");
    expect(essayTask?.completionRule).toContain("材料出处");

    const essayView = presentTodayDashboard(prototypeToday).tasks.find(
      (task) => task.track === "essay",
    );
    expect(essayView?.trackLabel).toBe("申论");
    expect(essayView?.stageLabel).toBe("材料理解");
    expect(essayView?.priorityMetrics[0]).toEqual({ label: "优先度", value: "83%" });
    expect(essayView?.canOpen).toBe(true);
    expect(essayView?.navigationUrl).toContain("stage=essay_material_reading");
    expect(essayView?.navigationUrl).toContain("planTaskId=task-shenlun-material");
  });

  it("opens the authoritative Xingce briefing URL issued by the server", () => {
    const [source] = prototypeToday.tasks;
    if (!source || source.track !== "xingce") throw new Error("Xingce fixture is required");
    const task = presentTodayDashboard({
      ...prototypeToday,
      tasks: [
        {
          ...source,
          startUrl: "/pages/xingce-task-intro/index?mode=targeted&planTaskId=task-data",
        },
      ],
    }).tasks[0];

    expect(task?.canOpen).toBe(true);
    expect(task?.navigationUrl).toBe(
      "/pages/xingce-task-intro/index?mode=targeted&planTaskId=task-data",
    );
  });

  it.each([
    "/pages/xingce-task-intro/index?mode=targeted",
    "/pages/xingce-task-intro/index?mode=targeted&planTaskId=another-task",
    "/pages/xingce-task-intro/index?mode=targeted&planTaskId=task-data&planTaskId=task-data",
  ])("rejects a Xingce briefing URL that is not authoritative: %s", (startUrl) => {
    const [source] = prototypeToday.tasks;
    if (!source || source.track !== "xingce") throw new Error("Xingce fixture is required");
    const task = presentTodayDashboard({
      ...prototypeToday,
      tasks: [{ ...source, startUrl }],
    }).tasks[0];

    expect(task).toMatchObject({
      canOpen: false,
      navigationMode: null,
      navigationUrl: null,
    });
    expect(task?.disabledReason).toContain("任务入口生成失败");
    expect(task?.disabledReason).not.toContain("所需资料齐全");
  });

  it("does not treat a registered static prototype as an executable task page", () => {
    const prototypeOnly = presentTodayDashboard({
      ...prototypeToday,
      tasks: prototypeToday.tasks
        .slice(0, 1)
        .map((task) => ({ ...task, startUrl: "/pages/reflection/index?source=today" })),
    }).tasks[0];

    expect(prototypeOnly).toMatchObject({
      canOpen: false,
      navigationMode: null,
      navigationUrl: null,
    });
    expect(prototypeOnly?.disabledReason).toContain("任务入口生成失败");
  });

  it("presents execution status and elapsed time without exposing raw status roles", () => {
    const [source] = prototypeToday.tasks;
    if (!source) throw new Error("prototype task fixture is required");
    const task = presentTodayDashboard({
      ...prototypeToday,
      tasks: [
        {
          ...source,
          status: "in_progress",
          execution: {
            executionVersionId: "00000000-0000-4000-8000-000000000011",
            taskId: source.id,
            planVersionId: prototypeToday.planVersionId,
            localDate: prototypeToday.localDate,
            workItemKey: "a".repeat(64),
            versionNo: 1,
            status: "in_progress",
            elapsedMinutes: 8,
            evidenceRefs: [],
            abandonmentReason: null,
            inputFingerprint: "b".repeat(64),
            supersedesExecutionVersionId: null,
            supersedesExecutionVersionNo: null,
            recordedAt: "2026-07-13T08:00:00Z",
          },
          evidenceRefs: [
            ...source.evidenceRefs,
            {
              owner: "planning_execution",
              entityType: "task_execution_version",
              entityId: "00000000-0000-4000-8000-000000000011",
              role: "in_progress",
            },
          ],
        },
      ],
    }).tasks[0];

    expect(task).toMatchObject({
      status: "in_progress",
      statusLabel: "进行中",
      elapsedMinutes: 8,
    });
    expect(task?.evidenceLabels).toContain("进行中");
  });

  it("uses stable evidence labels when the API omits role", () => {
    expect(
      evidenceReferenceLabel({
        owner: "essay_intelligence",
        entityType: "essay_review_version",
        entityId: "review-1",
      }),
    ).toBe("申论学习记录 · 申论评阅");
    expect(
      evidenceReferenceLabel({
        owner: "custom_owner",
        entityType: "custom_evidence",
        entityId: "custom-1",
      }),
    ).toBe("custom_owner · custom_evidence");
  });

  it.each([
    ["mock", "mock_day", "今日标准模考"],
    ["recovery", "recovery_day", "今日恢复任务"],
    ["unavailable", "evidence_missing", "今日暂无可执行任务"],
  ] as const)("presents %s mode without claiming a dual-track day", (dayMode, mode, title) => {
    const view = presentTodayDashboard({
      ...prototypeToday,
      dayMode,
      tasks: dayMode === "unavailable" ? [] : prototypeToday.tasks,
      dualTrackExplanation: {
        ...prototypeToday.dualTrackExplanation,
        mode,
        dualTrackConstraintApplied: false,
      },
    });

    expect(view.planSectionTitle).toBe(title);
    expect(view.planSectionTitle).not.toBe("今日双轨任务");
    expect(view.hasTasks).toBe(dayMode !== "unavailable");
  });

  it.each(["unavailable", "partial", "stale"] as const)(
    "does not fabricate a goal gap when score readiness is %s",
    (readiness) => {
      const dashboard: TodayDashboard = {
        ...prototypeToday,
        goalGap: {
          ...prototypeToday.goalGap,
          readiness,
          currentScore: null,
          baselineGap: null,
        },
      };

      const view = presentTodayDashboard(dashboard);
      expect(view.hasCompleteGoalGap).toBe(false);
      expect(view.currentScore).toBeNull();
      expect(view.targetScore).toBeNull();
      expect(view.scoreGap).toBeNull();
      expect(view.gapStateLabel).not.toBe("");
      expect(view.gapGuidance).not.toBe("");
    },
  );

  it("distinguishes a missing goal, missing score evidence, and stale evidence", () => {
    const missingGoal = presentTodayDashboard({
      ...prototypeToday,
      goalGap: {
        ...prototypeToday.goalGap,
        goalId: null,
        positionName: null,
        readiness: "unavailable",
        currentScore: null,
        baselineGap: null,
      },
    });
    const missingScore = presentTodayDashboard({
      ...prototypeToday,
      goalGap: {
        ...prototypeToday.goalGap,
        readiness: "partial",
        currentScore: null,
        baselineGap: null,
      },
    });
    const stale = presentTodayDashboard({
      ...prototypeToday,
      goalGap: {
        ...prototypeToday.goalGap,
        readiness: "stale",
        currentScore: null,
        baselineGap: null,
      },
    });

    expect(missingGoal.gapStateLabel).toContain("主目标岗位");
    expect(missingScore.gapStateLabel).toContain("两科成绩还不完整");
    expect(stale.gapStateLabel).toContain("已过期");
  });

  it("shows why a weekly mock was omitted when today's capacity is too short", () => {
    const view = presentTodayDashboard({
      ...prototypeToday,
      replanReasonCodes: [...prototypeToday.replanReasonCodes, "weekly_mock_capacity_insufficient"],
      dualTrackExplanation: {
        ...prototypeToday.dualTrackExplanation,
        omittedTrackReasons: {
          ...prototypeToday.dualTrackExplanation.omittedTrackReasons,
          mock: "周度标准卷需要连续 120 分钟，今日容量不足。",
        },
      },
    });

    expect(view.omittedTrackReasons).toContainEqual({
      track: "mock",
      trackLabel: "模考",
      reason: "周度标准卷需要连续 120 分钟，今日容量不足。",
    });
  });
});
