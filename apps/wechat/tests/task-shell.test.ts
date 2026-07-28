import { describe, expect, it } from "vitest";

import { taskShellSyncTone } from "../miniprogram/task-runtime/task-shell/state";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import taskShellTemplate from "../miniprogram/task-runtime/task-shell/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import taskShellStylesRaw from "../miniprogram/task-runtime/task-shell/index.wxss?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const taskShellStyles = normalizeRpxCalc(taskShellStylesRaw);

describe("compact task shell", () => {
  it("maps explicit persistence labels to the toolbar status dot", () => {
    expect(taskShellSyncTone("已保存")).toBe("saved");
    expect(taskShellSyncTone("答案已保存 · 评阅中")).toBe("saved");
    expect(taskShellSyncTone("正在保存")).toBe("saving");
    expect(taskShellSyncTone("上次进度待确认")).toBe("pending");
    expect(taskShellSyncTone("保存失败")).toBe("error");
  });

  it("uses the capsule-left navigation row for the xingce workbench", () => {
    expect(taskShellTemplate).toContain("task-shell--{{layoutVariant}}");
    expect(taskShellTemplate).toContain("--task-navigation-height");
    expect(taskShellStyles).toContain(".task-shell--xingce .task-header");
    expect(taskShellStyles).toContain("padding: 0 var(--task-capsule-reserve)");
    // practice 可用专属 companion prop 把岸岸放在会话级顶栏；其他消费者仍保留 tools。
    expect(taskShellStyles).toContain(".task-shell--xingce .task-header-button--tool");
    expect(taskShellTemplate).toContain('wx:if="{{companionAvailable}}"');
    expect(taskShellTemplate).toContain('bindtap="openCompanion"');
    expect(taskShellStyles).toContain(".task-shell--xingce .task-header-button--companion");
    // sticky 基础规则的 top 偏移必须在 xingce 变体里归零，否则导航整行下坠。
    expect(taskShellStyles).toMatch(/\.task-shell--xingce \.task-header \{[^}]*top: 0/);
  });
});
