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
        targetRef: "/pages/day-adjustment/index?source=anan&availableMinutes=90",
        requiresConfirmation: true,
      }),
    ).toMatchObject({
      kind: "navigate",
      target: {
        mode: "navigateTo",
        url: "/pages/day-adjustment/index?source=anan&availableMinutes=90",
      },
      requiresConfirmation: true,
    });
    expect(
      resolveCompanionAction({
        kind: "adjust_plan_preview",
        label: "伪造参数",
        targetRef: "/pages/day-adjustment/index?source=anan&availableMinutes=90&goalId=other",
        requiresConfirmation: true,
      }),
    ).toEqual({ kind: "unavailable" });
  });

  it("只接收来自岸岸的 0..1440 整数分钟", async () => {
    vi.stubGlobal("Page", vi.fn());
    const { parseAgentPrefillMinutes } = await import("../miniprogram/pages/day-adjustment/index");

    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "90" })).toBe(90);
    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "0" })).toBe(0);
    expect(parseAgentPrefillMinutes({ source: "other", availableMinutes: "90" })).toBeNull();
    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "90.5" })).toBeNull();
    expect(parseAgentPrefillMinutes({ source: "anan", availableMinutes: "1441" })).toBeNull();
  });
});
