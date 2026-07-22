import { describe, expect, it } from "vitest";

import { taskShellSyncTone } from "../miniprogram/task-runtime/task-shell/state";

describe("compact task shell", () => {
  it("maps explicit persistence labels to the toolbar status dot", () => {
    expect(taskShellSyncTone("已保存")).toBe("saved");
    expect(taskShellSyncTone("答案已保存 · 评阅中")).toBe("saved");
    expect(taskShellSyncTone("正在保存")).toBe("saving");
    expect(taskShellSyncTone("上次进度待确认")).toBe("pending");
    expect(taskShellSyncTone("保存失败")).toBe("error");
  });
});
