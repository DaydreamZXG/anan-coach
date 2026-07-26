import { describe, expect, it } from "vitest";

import { taskShellSyncTone } from "../miniprogram/task-runtime/task-shell/state";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import taskShellTemplate from "../miniprogram/task-runtime/task-shell/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import taskShellStyles from "../miniprogram/task-runtime/task-shell/index.wxss?raw";

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
    // Figma 2:27：顶栏止于「当前工具」；岸岸与收藏移到材料标题行（practice 页）。
    expect(taskShellStyles).toContain(".task-shell--xingce .task-header-button--tool");
    expect(taskShellStyles).not.toContain(".task-shell--xingce .task-header-button--anan");
    // sticky 基础规则的 top 偏移必须在 xingce 变体里归零，否则导航整行下坠。
    expect(taskShellStyles).toMatch(/\.task-shell--xingce \.task-header \{[^}]*top: 0/);
  });
});
