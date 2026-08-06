import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveCompanionAction } from "../miniprogram/features/learning-brain/companion-action";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("岸岸 Agent 计划调整闭环", () => {
  it("只允许白名单的日容量预览参数", () => {
    expect(
      resolveCompanionAction({
        kind: "adjust_plan_preview",
        label: "预览调整为 90 分钟",
        targetRef: "/plans/day-adjustment/index?source=anan&availableMinutes=90",
        requiresConfirmation: true,
      }),
    ).toMatchObject({
      kind: "navigate",
      target: {
        mode: "navigateTo",
        url: "/plans/day-adjustment/index?source=anan&availableMinutes=90",
      },
      requiresConfirmation: true,
    });
    expect(
      resolveCompanionAction({
        kind: "adjust_plan_preview",
        label: "伪造参数",
        targetRef: "/plans/day-adjustment/index?source=anan&availableMinutes=90&goalId=other",
        requiresConfirmation: true,
      }),
    ).toEqual({ kind: "unavailable" });

    expect(
      resolveCompanionAction({
        kind: "adjust_plan_preview",
        label: "预览今天只练资料分析",
        targetRef:
          "/plans/day-adjustment/index?source=anan&preferredXingceModule=%E8%B5%84%E6%96%99%E5%88%86%E6%9E%90&xingceOnly=1",
        requiresConfirmation: true,
      }),
    ).toMatchObject({ kind: "navigate", requiresConfirmation: true });
  });

  it("只接收来自岸岸的 0..1440 整数分钟", async () => {
    vi.stubGlobal("Page", vi.fn());
    const { parseAgentPrefillMinutes } = await import("../miniprogram/plans/day-adjustment/index");

    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "90" })).toBe(90);
    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "0" })).toBe(0);
    expect(parseAgentPrefillMinutes({ source: "other", availableMinutes: "90" })).toBeNull();
    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "90.5" })).toBeNull();
    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "1441" })).toBeNull();
  });

  it("只接收来自岸岸的规范行测模块偏好", async () => {
    vi.stubGlobal("Page", vi.fn());
    const { parseAgentPrefillFocus } = await import("../miniprogram/plans/day-adjustment/index");

    expect(
      parseAgentPrefillFocus({
        source: "anan",
        preferredXingceModule: "资料分析",
        xingceOnly: "1",
      }),
    ).toEqual({ module: "资料分析", xingceOnly: true });
    expect(
      parseAgentPrefillFocus({
        source: "anan",
        preferredXingceModule: "随便练",
        xingceOnly: "1",
      }),
    ).toBeNull();
  });
});
