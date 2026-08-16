import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import meTemplateRaw from "../../../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import privacyTemplateRaw from "../../../miniprogram/account/privacy/index.wxml?raw";
import type { XingceMasteryQueueItem } from "../../../miniprogram/xingce/shared/api/xingce-learning";
import { resolveQuickDiagnostic } from "../../../miniprogram/pages/onboarding/features/coach-recognition-routing";
import { presentErrorPatternEvidence } from "../../../miniprogram/reports/error-pattern/features/evidence-presentation";
import { presentMasteryQueueItem } from "../../../miniprogram/xingce/shared/features/xingce/mastery/presentation";
import { prototypeToday } from "../../fixtures/prototype-data";

describe("full product audit repair 033a happy path", () => {
  it("keeps a mixed account on its truthful executable task", () => {
    const dashboard = JSON.parse(JSON.stringify(prototypeToday)) as typeof prototypeToday;
    const task = dashboard.tasks[0];
    if (!task) throw new Error("today fixture must include one task");
    task.track = "xingce";
    task.stage = "objective_verification";
    task.kind = "verification";
    task.status = "ready";
    task.startUrl = `/xingce/xingce-task-intro/index?mode=verification&planTaskId=${task.id}`;

    expect(resolveQuickDiagnostic(dashboard)).toEqual({
      source: "existing_xingce_task",
      target: { mode: "navigateTo", url: task.startUrl },
    });
  });

  it("explains cumulative mastery counts and keeps legal terms discoverable", () => {
    const item = {
      itemId: "mastery-due",
      bucket: "due_verification",
      state: "provisionally_mastered",
      verificationStage: "first_review",
      moduleName: "资料分析",
      dueAt: "2026-08-16T08:00:00+08:00",
      createdAt: "2026-08-15T08:00:00+08:00",
      questionCount: 15,
      evidenceRefs: ["attempt:1"],
      action: {
        state: "ready",
        startUrl: "/xingce/xingce-task-intro/index?mode=verification&planTaskId=plan-a",
        planTaskId: "plan-a",
      },
      sessionId: null,
      reviewId: null,
      masteryId: "mastery-a",
      masteryVersionId: "mastery-version-a",
      invalidationReason: null,
    } as XingceMasteryQueueItem;

    expect(presentMasteryQueueItem(item)).toMatchObject({
      questionLabel: "累计 15 道待复测",
      actionLabel: "开始本次复测",
    });
    expect(String(meTemplateRaw)).toContain('bindtap="openTerms"');
    expect(String(meTemplateRaw)).toContain("服务条款");
    expect(String(privacyTemplateRaw)).toContain('url="/account/terms/index"');
  });

  it("turns a known evidence role into language a learner can understand", () => {
    expect(
      presentErrorPatternEvidence({
        owner: "xingce_learning",
        entityType: "xingce_confirmed_cause_version",
        entityId: "cause-a",
        role: "confirmed_pattern",
      }),
    ).toEqual({ label: "支持当前判断的作答记录", meta: "已纳入当前判断" });
  });
});
