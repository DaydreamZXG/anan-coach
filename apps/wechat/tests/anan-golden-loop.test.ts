import { afterEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import onboardingEntryTemplate from "../miniprogram/pages/onboarding/entry/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import todayStyles from "../miniprogram/pages/today/index.wxss?raw";
import type {
  InferenceCandidateVersion,
  LearnerCoachContext,
  LearningBrainProfileVersion,
} from "../miniprogram/api/generated/types.gen";
import { routes } from "../miniprogram/app/navigation/routes";
import {
  coachLocalDate,
  readCoachTaskBeforeSnapshot,
  storeCoachTaskBeforeSnapshot,
} from "../miniprogram/features/coach-loop/feedback-store";
import { silentEntryNavigationTarget } from "../miniprogram/features/identity/silent-entry";
import {
  quickDiagnosticTarget,
  staticCoachRecognitionTarget,
} from "../miniprogram/pages/onboarding/features/coach-recognition-routing";
import {
  markCoachEntryEvidence,
  readCoachEntryIntent,
  storeCoachEntryIntent,
} from "../miniprogram/features/onboarding/coach-entry";
import { presentTodayDashboard } from "../miniprogram/pages/today/presentation";
import { prototypeToday } from "./fixtures/prototype-data";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("岸岸新用户黄金链路", () => {
  it("新用户静默登录后先选择真实认识路径", () => {
    expect(silentEntryNavigationTarget("preparation_experience", null)).toEqual({
      mode: "navigateTo",
      url: routes.onboardingEntry,
    });
    expect(onboardingEntryTemplate).toContain("你想让岸岸怎么认识你？");
    expect(onboardingEntryTemplate).toContain('data-path="score_report"');
    expect(onboardingEntryTemplate).toContain('data-path="wrong_question"');
    expect(onboardingEntryTemplate).toContain('data-path="quick_diagnostic"');
    expect(onboardingEntryTemplate).toContain('data-path="position_first"');
    expect(onboardingEntryTemplate).toContain("导入最近成绩单");
    expect(onboardingEntryTemplate).toContain("导入几道错题");
    expect(onboardingEntryTemplate).toContain("我还没选岗");
    expect(onboardingEntryTemplate).toContain('bindtap="chooseRecognitionPath"');
    expect(onboardingEntryTemplate).toContain("只决定先从哪里开始");
    expect(onboardingEntryTemplate).toContain("第一次判断只是起点");
    expect(onboardingEntryTemplate).not.toContain("建立备考档案");
    expect(onboardingEntryTemplate).not.toContain("我是新用户，开始建档");
    expect(onboardingEntryTemplate).not.toContain('bindtap="startProfile"');
    expect(todayTemplate).toContain('bindtap="openRecognitionOptions"');
    expect(todayTemplate).toContain("可选 · 让岸岸更快认识你");
  });

  it("认识方式和已获得证据严格按账号隔离", () => {
    installStorage();
    storeCoachEntryIntent("user-a", "wrong_question", new Date("2026-08-11T08:00:00Z"));

    expect(readCoachEntryIntent("user-b")).toBeNull();
    expect(readCoachEntryIntent("user-a")?.path).toBe("wrong_question");
    expect(markCoachEntryEvidence("user-a", "external_answer")?.evidenceSource).toBe(
      "external_answer",
    );

    storeCoachEntryIntent("user-a", "position_first", new Date("2026-08-11T08:05:00Z"));
    expect(markCoachEntryEvidence("user-a", "position_preference")?.evidenceSource).toBe(
      "position_preference",
    );
  });

  it("四条入口只接正式能力，不再用备考经历页冒充诊断或选岗", () => {
    expect(staticCoachRecognitionTarget("score_report")).toEqual({
      mode: "navigateTo",
      url: routes.externalReport,
    });
    expect(staticCoachRecognitionTarget("wrong_question")).toEqual({
      mode: "navigateTo",
      url: routes.captureImport,
    });
    expect(staticCoachRecognitionTarget("position_first")).toEqual({
      mode: "navigateTo",
      url: routes.onboardingPositionPreference,
    });

    const dashboard = clone(prototypeToday);
    const task = dashboard.tasks[0];
    if (!task) throw new Error("Prototype task is missing");
    task.track = "xingce";
    task.stage = "objective_baseline";
    task.status = "ready";
    task.startUrl = `/xingce/xingce-task-intro/index?mode=baseline&planTaskId=${task.id}`;
    expect(quickDiagnosticTarget(dashboard)).toEqual({
      mode: "navigateTo",
      url: task.startUrl,
    });

    task.startUrl = "/xingce/xingce-task-intro/index?mode=baseline";
    expect(quickDiagnosticTarget(dashboard)).toBeNull();

    const validTask = {
      ...task,
      id: "00000000-0000-4000-8000-000000000099",
      startUrl:
        "/xingce/xingce-task-intro/index?mode=baseline&planTaskId=00000000-0000-4000-8000-000000000099",
    };
    dashboard.tasks.push(validTask);
    expect(quickDiagnosticTarget(dashboard)).toEqual({
      mode: "navigateTo",
      url: validTask.startUrl,
    });

    expect(
      String(onboardingEntryTemplate).match(/disabled="\{\{openingPath !== ''\}\}"/gu),
    ).toHaveLength(4);
  });
});

describe("岸岸证据成熟度与可修正判断", () => {
  it.each([
    [profile({}), "just_met", "刚认识你"],
    [profile({ scoreEvidenceCount: 1 }), "initial_hypothesis", "形成初步判断"],
    [profile({ taskExecutionCount: 1 }), "verifying", "正在验证"],
    [profile({}, 1), "understands", "比较了解你"],
  ] as const)("按权威证据投影 %s", (brain, level, label) => {
    const view = presentTodayDashboard(prototypeToday, {
      profileStatus: "ready",
      coachContext: coachContext(brain),
      candidate: null,
      beforeSnapshot: null,
    });

    expect(view.loop?.recognitionLevel).toBe(level);
    expect(view.loop?.recognitionLabel).toBe(label);
  });

  it("画像失败时公开证据边界，不伪装精准", () => {
    const view = presentTodayDashboard(prototypeToday, {
      profileStatus: "unavailable",
      coachContext: null,
      candidate: null,
      beforeSnapshot: null,
    });

    expect(view.loop?.recognitionLabel).toBe("证据待同步");
    expect(view.loop?.observation).toContain("先不升级判断");
  });

  it("今日会接住新用户选择的认识方式，而不是让入口意图失联", () => {
    const view = presentTodayDashboard(prototypeToday, {
      profileStatus: "ready",
      coachContext: coachContext(profile({})),
      candidate: null,
      beforeSnapshot: null,
      entryIntent: {
        version: 1,
        ownerUserId: "user-a",
        path: "quick_diagnostic",
        selectedAt: "2026-08-11T08:00:00.000Z",
      },
    });

    expect(view.loop?.recognitionLabel).toBe("刚认识你");
    expect(view.loop?.observation).toContain("快速诊断");
    expect(view.loop?.observation).toContain("不会包装成完整能力测评");
  });

  it("只把基于当前画像的每日候选嵌入原岸岸建议", () => {
    const currentProfile = profile({ scoreEvidenceCount: 1 });
    const candidate = strategyCandidate(currentProfile.profileVersionId);
    const view = presentTodayDashboard(prototypeToday, {
      profileStatus: "ready",
      coachContext: coachContext(currentProfile),
      candidate,
      beforeSnapshot: null,
    });

    expect(view.loop?.candidate).toMatchObject({
      candidateId: candidate.candidateId,
      title: "先校准行测基线",
    });
    expect(view.loop?.judgment).toContain("先完成一次行测摸底");
    expect(todayTemplate).toContain("确认前不改变计划");
    expect(todayTemplate).not.toContain("大脑建议卡");
  });

  it("画像已更新时收回旧候选，不让旧判断覆盖岸岸当前认识", () => {
    const staleCandidate = strategyCandidate("00000000-0000-4000-8000-00000000b000");
    const dashboard = clone(prototypeToday);
    dashboard.headline = "刚才的真实诊断已进入画像，先复盘第一道错题";

    const view = presentTodayDashboard(dashboard, {
      profileStatus: "ready",
      coachContext: coachContext(
        profile(
          { xingceAttemptCount: 15, taskExecutionCount: 1 },
          0,
          "00000000-0000-4000-8000-00000000b002",
        ),
      ),
      candidate: staleCandidate,
      beforeSnapshot: null,
    });

    expect(view.loop?.candidate).toBeNull();
    expect(view.loop?.judgment).toBe(dashboard.headline);
    expect(view.loop?.judgment).not.toContain("先完成一次行测摸底");
  });
});

describe("练完后的可见反馈", () => {
  it("只展示任务、证据、画像和计划的真实差异", () => {
    const beforeProfile = profile({ xingceAttemptCount: 1, taskExecutionCount: 1 });
    const afterProfile = profile(
      { xingceAttemptCount: 2, xingceMasteryCount: 1, taskExecutionCount: 2 },
      0,
      "00000000-0000-4000-8000-00000000b002",
    );
    const dashboard = clone(prototypeToday);
    const firstTask = dashboard.tasks[0];
    if (!firstTask) throw new Error("Prototype task is missing");
    dashboard.planVersionId = "00000000-0000-4000-8000-00000000c002";
    firstTask.status = "complete";
    const beforeSnapshot = {
      version: 1 as const,
      ownerUserId: "user-a",
      localDate: coachLocalDate(),
      capturedAt: new Date().toISOString(),
      taskId: firstTask.id,
      taskTitle: firstTask.title,
      taskKind: firstTask.kind,
      taskReason: firstTask.reason,
      completionRule: firstTask.completionRule,
      planVersionId: "00000000-0000-4000-8000-00000000c001",
      profileVersionId: beforeProfile.profileVersionId,
      evidence: {
        scoreEvidenceCount: 0,
        xingceAttemptCount: 1,
        xingceMasteryCount: 0,
        essayMasteryCount: 0,
        taskExecutionCount: 1,
        externalAnswerCount: 0,
      },
    };

    const view = presentTodayDashboard(dashboard, {
      profileStatus: "ready",
      coachContext: coachContext(afterProfile),
      candidate: null,
      beforeSnapshot,
    });

    expect(view.loop?.feedback?.changes).toEqual(
      expect.arrayContaining([
        expect.stringContaining("你刚完成了"),
        expect.stringContaining("真实作答让岸岸更了解"),
        expect.stringContaining("掌握或复测证据"),
        expect.stringContaining("岸岸已根据这次证据"),
        expect.stringContaining("接下来的安排已经"),
      ]),
    );
    expect(view.loop?.feedback?.nextAction).toContain("申论");
  });

  it("损坏、跨账号和跨日快照不会成为当前反馈", () => {
    const storage = installStorage();
    const yesterday = new Date(2026, 7, 10, 20, 0, 0);
    storeCoachTaskBeforeSnapshot({
      version: 1,
      ownerUserId: "user-a",
      localDate: coachLocalDate(yesterday),
      capturedAt: yesterday.toISOString(),
      taskId: "task-a",
      taskTitle: "任务 A",
      taskKind: "practice",
      taskReason: "用于测试",
      completionRule: "完成测试",
      planVersionId: "plan-a",
      evidence: zeroEvidence(),
    });

    expect(readCoachTaskBeforeSnapshot("user-b", new Date(2026, 7, 11, 8, 0, 0))).toBeNull();
    expect(readCoachTaskBeforeSnapshot("user-a", new Date(2026, 7, 11, 8, 0, 0))).toBeNull();
    storage.set("gongkao.coach-loop.before-task.v1.user-a", { version: 1, ownerUserId: "user-a" });
    expect(readCoachTaskBeforeSnapshot("user-a", new Date(2026, 7, 11, 8, 0, 0))).toBeNull();
  });

  it("继续保留原卡片的极光、紫色图标和黑色主按钮", () => {
    expect(todayTemplate).toContain('class="task-aurora"');
    expect(todayTemplate).toContain('class="anan-icon"');
    expect(todayStyles).toContain("var(--color-ai-violet)");
    expect(todayStyles).toContain(".primary-button");
    expect(todayStyles).toContain("background: var(--ink)");
  });
});

function profile(
  counters: Partial<ReturnType<typeof zeroEvidence>>,
  stableMasteryCount = 0,
  profileVersionId = "00000000-0000-4000-8000-00000000b001",
): LearningBrainProfileVersion {
  return {
    profileVersionId,
    evidenceSummary: { ...zeroEvidence(), ...counters },
    modules: [
      {
        stableMasteryCount,
      },
    ],
  } as unknown as LearningBrainProfileVersion;
}

function strategyCandidate(basedOnProfileVersionId: string): InferenceCandidateVersion {
  return {
    candidateId: "00000000-0000-4000-8000-00000000d001",
    candidateVersionId: "00000000-0000-4000-8000-00000000d002",
    status: "awaiting_confirmation",
    value: {
      decisionType: "baseline_guidance",
      scopeType: "track",
      scopeKey: "xingce",
      rationale: "当前证据还少，先完成一次行测摸底，再验证当前判断。",
      basedOnProfileVersionId,
    },
  } as unknown as InferenceCandidateVersion;
}

function coachContext(profile: LearningBrainProfileVersion): LearnerCoachContext {
  const evidence = profile.evidenceSummary;
  const evidenceCount =
    evidence.scoreEvidenceCount +
    evidence.xingceAttemptCount +
    evidence.xingceMasteryCount +
    evidence.essayMasteryCount +
    evidence.taskExecutionCount +
    (evidence.externalAnswerCount ?? 0);
  const stableMasteryCount = profile.modules.reduce(
    (sum, module) => sum + module.stableMasteryCount,
    0,
  );
  const level =
    stableMasteryCount > 0
      ? "understands"
      : evidence.taskExecutionCount > 0 ||
          evidence.xingceMasteryCount > 0 ||
          evidence.essayMasteryCount > 0
        ? "verifying"
        : evidenceCount > 0
          ? "initial_hypothesis"
          : "just_met";
  const labels = {
    just_met: "刚认识你",
    initial_hypothesis: "形成初步判断",
    verifying: "正在验证",
    understands: "比较了解你",
  } as const;
  return {
    contractVersion: "learner-coach-context@v1",
    profileVersionId: profile.profileVersionId,
    profile,
    freshness: "current",
    recognition: {
      level,
      label: labels[level],
      observation: `${labels[level]}的服务端观察。`,
      evidenceCount,
      stableMasteryCount,
    },
    strategyDirective: { active: null, pending: null },
    generatedAt: "2026-08-13T10:00:00Z",
    validUntil: "2026-08-14T10:00:00Z",
  };
}

function zeroEvidence() {
  return {
    scoreEvidenceCount: 0,
    xingceAttemptCount: 0,
    xingceMasteryCount: 0,
    essayMasteryCount: 0,
    taskExecutionCount: 0,
    externalAnswerCount: 0,
  };
}

function clone<T extends object>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function installStorage(): Map<string, unknown> {
  const storage = new Map<string, unknown>();
  vi.stubGlobal("wx", {
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    getStorageSync: (key: string) => storage.get(key),
    removeStorageSync: (key: string) => storage.delete(key),
  });
  return storage;
}
