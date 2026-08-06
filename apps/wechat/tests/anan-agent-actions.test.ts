import { describe, expect, it } from "vitest";
import { resolveCompanionAction } from "../miniprogram/features/learning-brain/companion-action";

describe("岸岸 Agent 动作白名单", () => {
  it("可打开已注册的专属资料库", () => {
    expect(
      resolveCompanionAction({
        kind: "supply_information",
        label: "打开专属资料库",
        targetRef: "/pages/private-knowledge/index",
        requiresConfirmation: false,
      }),
    ).toMatchObject({
      kind: "navigate",
      target: {
        mode: "navigateTo",
        url: "/pages/private-knowledge/index",
      },
      requiresConfirmation: false,
    });
  });

  it("不放行同类动作中未注册的路径", () => {
    expect(
      resolveCompanionAction({
        kind: "supply_information",
        label: "伪造路径",
        targetRef: "/pages/private-knowledge/admin",
        requiresConfirmation: false,
      }),
    ).toEqual({ kind: "unavailable" });
  });

  it("放行档案与周计划的已注册路由", () => {
    for (const targetRef of [
      "/pages/profile/archive/index",
      "/pages/onboarding/profile/index",
      "/plans/study-preference/index",
      "/plans/weekly-plan/index",
    ]) {
      expect(
        resolveCompanionAction({
          kind: "supply_information",
          label: "打开",
          targetRef,
          requiresConfirmation: false,
        }),
      ).toMatchObject({ kind: "navigate", target: { url: targetRef } });
    }
  });

  it("只放行白名单周节奏与岗位目标参数", () => {
    const positionId = "00000000-0000-0000-0000-000000009901";
    expect(
      resolveCompanionAction({
        kind: "supply_information",
        label: "预览周节奏",
        targetRef:
          "/plans/study-preference/index?source=anan&mockExamDay=saturday&fixedRestDay=sunday",
        requiresConfirmation: true,
      }),
    ).toMatchObject({ kind: "navigate", requiresConfirmation: true });
    expect(
      resolveCompanionAction({
        kind: "supply_information",
        label: "伪造周节奏",
        targetRef:
          "/plans/study-preference/index?source=anan&mockExamDay=saturday&fixedRestDay=saturday",
        requiresConfirmation: true,
      }),
    ).toEqual({ kind: "unavailable" });
    expect(
      resolveCompanionAction({
        kind: "open_position_workspace",
        label: "预览目标岗位",
        targetRef: `/pages/positions/goal/index?positionId=${positionId}`,
        requiresConfirmation: true,
      }),
    ).toMatchObject({ kind: "navigate", requiresConfirmation: true });
    expect(
      resolveCompanionAction({
        kind: "open_position_workspace",
        label: "伪造岗位",
        targetRef: "/pages/positions/goal/index?positionId=other&admin=1",
        requiresConfirmation: true,
      }),
    ).toEqual({ kind: "unavailable" });
  });
});
