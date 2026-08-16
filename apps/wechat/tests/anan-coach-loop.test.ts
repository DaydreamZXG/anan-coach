import { afterEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import todayStyles from "../miniprogram/pages/today/index.wxss?raw";
import type {
  LearnerCoachContext,
  LearningBrainProfileVersion,
  TodayDashboard,
} from "../miniprogram/api/generated/types.gen";
import { presentTodayDashboard } from "../miniprogram/pages/today/presentation";
import {
  coachLocalDate,
  readCoachTaskBeforeSnapshot,
  storeCoachTaskBeforeSnapshot,
  type CoachTaskBeforeSnapshot,
} from "../miniprogram/features/coach-loop/feedback-store";
import { prototypeToday } from "./fixtures/prototype-data";

type TodayTask = TodayDashboard["tasks"][number];

afterEach(() => {
  vi.unstubAllGlobals();
});

function clone<T extends object>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function dashboardWithTask(task: TodayTask): TodayDashboard {
  return {
    ...clone(prototypeToday),
    tasks: [task],
  };
}

function taskForKind(kind: TodayTask["kind"], status: TodayTask["status"] = "ready"): TodayTask {
  const source = prototypeToday.tasks[0];
  if (!source) throw new Error("Prototype task is missing");
  const base = clone(source);
  return {
    ...base,
    kind,
    status,
    stage:
      kind === "reflection"
        ? "objective_reflection"
        : kind === "verification"
          ? "objective_verification"
          : kind === "essay"
            ? "essay_drafting"
            : kind === "mock_exam"
              ? "weekly_mock"
              : "objective_practice",
  };
}

function coachProfile(): LearningBrainProfileVersion {
  return {
    contractVersion: "learning-brain-profile@v1" as const,
    profileVersionId: "00000000-0000-4000-8000-000000000901",
    versionNo: 2,
    readiness: "partial" as const,
    ability: {
      readiness: "unavailable" as const,
      estimate: null,
      limitations: [],
    },
    evidenceSummary: {
      scoreEvidenceCount: 0,
      xingceAttemptCount: 3,
      xingceMasteryCount: 1,
      essayMasteryCount: 0,
      taskExecutionCount: 1,
      externalAnswerCount: 0,
      latestEvidenceAt: "2026-08-12T10:00:00Z",
    },
    candidateSummary: {
      awaitingConfirmation: 0,
      accepted: 0,
      rejected: 0,
      expired: 0,
    },
    modules: [],
    errorPatterns: [],
    knowledgePointWeaknesses: [],
    decisionSummary: {
      activeStrategyCount: 0,
      reviewDueCount: 0,
      currentPlanVersionId: prototypeToday.planVersionId,
      currentWeeklyPlanVersionId: null,
    },
    conflicts: [],
    sourceRefs: [],
    inputVersions: {},
    algorithmVersion: "learning-brain-profile@v1" as const,
    inputFingerprint: "a".repeat(64),
    generatedAt: "2026-08-12T10:00:00Z",
    validUntil: "2026-08-13T10:00:00Z",
  } as unknown as LearningBrainProfileVersion;
}

function coachContext(profile: LearningBrainProfileVersion): LearnerCoachContext {
  return {
    contractVersion: "learner-coach-context@v1",
    profileVersionId: profile.profileVersionId,
    profile,
    recognition: {
      level: "verifying",
      label: "正在验证",
      observation: "岸岸正在用练习和复测校准当前判断。",
      evidenceCount: 5,
      stableMasteryCount: 0,
    },
    freshness: "current",
    strategyDirective: { active: null, pending: null },
    generatedAt: "2026-08-12T10:00:00Z",
    validUntil: "2026-08-13T10:00:00Z",
  };
}

function beforeSnapshot(
  task: TodayTask,
  errorPatterns?: CoachTaskBeforeSnapshot["errorPatterns"],
): CoachTaskBeforeSnapshot {
  return {
    version: 1,
    ownerUserId: "user-1",
    localDate: "2026-08-12",
    capturedAt: "2026-08-12T09:00:00Z",
    taskId: task.id,
    taskTitle: task.title,
    taskKind: task.kind,
    taskReason: task.reason,
    completionRule: task.completionRule,
    planVersionId: "before-plan-version",
    profileVersionId: "00000000-0000-4000-8000-000000000900",
    evidence: {
      scoreEvidenceCount: 0,
      xingceAttemptCount: 3,
      xingceMasteryCount: 1,
      essayMasteryCount: 0,
      taskExecutionCount: 1,
      externalAnswerCount: 0,
    },
    ...(errorPatterns ? { errorPatterns } : {}),
  };
}

describe("岸岸今日反馈循环", () => {
  // 循环只解释已有任务事实，执行结果仍由原任务命令写回并触发下一次投影。
  it.each([
    ["practice", "先解决最高收益缺口", "正确率、用时和过程记录"],
    ["reflection", "先复盘，再加练", "更新错因判断"],
    ["verification", "到了复测时间", "是否已经稳定修复"],
    ["essay", "推进申论主线", "作答与评阅结果"],
    ["mock_exam", "用整卷校准判断", "整卷结果"],
  ] as const)("把 %s 任务投影为完整行动反馈", (kind, statusLabel, feedbackCopy) => {
    const task = taskForKind(kind);
    const view = presentTodayDashboard(dashboardWithTask(task));

    expect(view.loop).toMatchObject({
      statusLabel,
      judgment: prototypeToday.headline,
      why: task.reason,
      actionTitle: task.title,
      completionRule: task.completionRule,
    });
    expect(view.loop?.feedbackPromise).toContain(feedbackCopy);
  });

  it("进行中的任务明确继续上次进度，仍承诺同类完成反馈", () => {
    const view = presentTodayDashboard(
      dashboardWithTask(taskForKind("verification", "in_progress")),
    );

    expect(view.loop).toMatchObject({
      phase: "resume",
      statusLabel: "继续上次进度",
    });
    expect(view.loop?.feedbackPromise).toContain("是否已经稳定修复");
  });

  it("投影可重放且不修改权威仪表盘输入", () => {
    const dashboard = dashboardWithTask(taskForKind("reflection"));
    const before = JSON.stringify(dashboard);
    const first = presentTodayDashboard(dashboard).loop;
    const replay = presentTodayDashboard(dashboard).loop;

    expect(replay).toEqual(first);
    expect(JSON.stringify(dashboard)).toBe(before);
  });

  it("缺失解释或完成标准时明确说明缺失，不编造数字事实", () => {
    const task = taskForKind("practice");
    task.reason = "";
    task.completionRule = "";
    const dashboard = dashboardWithTask(task);
    dashboard.rationale = "";

    const loop = presentTodayDashboard(dashboard).loop;

    expect(loop?.why).toBe("当前任务暂未附带更多判断依据。");
    expect(loop?.completionRule).toBe("任务页暂未给出完成标准。");
    expect(loop?.why).not.toContain("0");
    expect(loop?.completionRule).not.toContain("0");
  });

  it("同一错误模式形成完整反证后，岸岸明确收回旧判断", () => {
    const task = taskForKind("verification", "complete");
    const dashboard = dashboardWithTask(task);
    const profile = coachProfile();
    profile.errorPatterns = [
      {
        patternId: "b".repeat(64),
        track: "xingce",
        moduleKey: "言语理解",
        causeCode: "stem_misread",
        displayName: "题干条件或问法审读错误",
        status: "counterevidence",
        occurrenceCount: 1,
        counterevidenceCount: 1,
        confidence: "medium",
        latestObservedAt: "2026-08-12T10:00:00Z",
        evidenceRefs: [],
      },
    ];
    const view = presentTodayDashboard(dashboard, {
      profileStatus: "ready",
      coachContext: coachContext(profile),
      candidate: null,
      beforeSnapshot: beforeSnapshot(task, [
        {
          patternId: "b".repeat(64),
          displayName: "题干条件或问法审读错误",
          status: "confirmed",
          counterevidenceCount: 0,
        },
      ]),
      entryIntent: null,
    });

    expect(view.loop?.feedback?.changes).toContain(
      "独立复测已形成反证，岸岸收回“题干条件或问法审读错误”这个当前弱项判断。",
    );
  });

  it("只有部分反证时保留当前判断并把理由说清楚", () => {
    const task = taskForKind("verification", "complete");
    const profile = coachProfile();
    profile.errorPatterns = [
      {
        patternId: "c".repeat(64),
        track: "xingce",
        moduleKey: "资料分析",
        causeCode: "calculation_error",
        displayName: "方法成立但计算、单位或抄写错误",
        status: "confirmed",
        occurrenceCount: 2,
        counterevidenceCount: 1,
        confidence: "medium",
        latestObservedAt: "2026-08-12T10:00:00Z",
        evidenceRefs: [],
      },
    ];
    const view = presentTodayDashboard(dashboardWithTask(task), {
      profileStatus: "ready",
      coachContext: coachContext(profile),
      candidate: null,
      beforeSnapshot: beforeSnapshot(task, [
        {
          patternId: "c".repeat(64),
          displayName: "方法成立但计算、单位或抄写错误",
          status: "confirmed",
          counterevidenceCount: 0,
        },
      ]),
      entryIntent: null,
    });

    expect(view.loop?.feedback?.changes).toContain(
      "“方法成立但计算、单位或抄写错误”出现了新的反向证据，但还有未修复记录，岸岸暂不收回。",
    );
  });

  it("发现新错误模式时命名待验证判断，旧快照仍保留通用反馈", () => {
    const task = taskForKind("reflection", "complete");
    const profile = coachProfile();
    profile.errorPatterns = [
      {
        patternId: "d".repeat(64),
        track: "xingce",
        moduleKey: "判断推理",
        causeCode: "option_elimination_error",
        displayName: "选项排除路径错误",
        status: "confirmed",
        occurrenceCount: 1,
        counterevidenceCount: 0,
        confidence: "medium",
        latestObservedAt: "2026-08-12T10:00:00Z",
        evidenceRefs: [],
      },
    ];
    const context = {
      profileStatus: "ready" as const,
      coachContext: coachContext(profile),
      candidate: null,
      entryIntent: null,
    };
    const dashboard = dashboardWithTask(task);
    const before = JSON.stringify({ dashboard, profile });
    const specific = presentTodayDashboard(dashboard, {
      ...context,
      beforeSnapshot: beforeSnapshot(task, []),
    });
    const legacy = presentTodayDashboard(dashboard, {
      ...context,
      beforeSnapshot: beforeSnapshot(task),
    });

    expect(specific.loop?.feedback?.changes).toContain(
      "岸岸新增了一条待验证判断：“选项排除路径错误”。",
    );
    expect(legacy.loop?.feedback?.changes).not.toEqual(
      expect.arrayContaining([expect.stringContaining("选项排除路径错误")]),
    );
    expect(JSON.stringify({ dashboard, profile })).toBe(before);
  });

  it("损坏和跨账号的错误模式快照不会成为岸岸反馈", () => {
    const storage = new Map<string, unknown>();
    vi.stubGlobal("wx", {
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      getStorageSync: (key: string) => storage.get(key),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    const now = new Date(2026, 7, 12, 18, 0, 0);
    const task = taskForKind("verification");
    storeCoachTaskBeforeSnapshot({
      ...beforeSnapshot(task, []),
      ownerUserId: "user-a",
      localDate: coachLocalDate(now),
      capturedAt: now.toISOString(),
    });

    expect(readCoachTaskBeforeSnapshot("user-b", now)).toBeNull();
    storage.set("gongkao.coach-loop.before-task.v1.user-a", {
      ...beforeSnapshot(task, []),
      ownerUserId: "user-a",
      localDate: coachLocalDate(now),
      capturedAt: now.toISOString(),
      errorPatterns: [{ patternId: "broken" }],
    });
    expect(readCoachTaskBeforeSnapshot("user-a", now)).toBeNull();
  });

  it("保留紫色渐变图标、极光背景和黑色主按钮", () => {
    expect(todayTemplate).toContain('class="task-aurora"');
    expect(todayTemplate).toContain('class="anan-icon"');
    expect(todayStyles).toContain(".task-aurora");
    expect(todayStyles).toContain("var(--color-ai-violet)");
    expect(todayStyles).toContain(".primary-button");
    expect(todayStyles).toContain("background: var(--ink)");
  });
});
