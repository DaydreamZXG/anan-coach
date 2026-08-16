import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("roadmap after private question book 022A stale focus", () => {
  it("replaces the old 022 next-focus marker with the real-account stage C journey", () => {
    const roadmap = readFileSync("../../docs/product/anan-development-roadmap.md", "utf8");
    expect(roadmap).toContain("022｜用户入口与教练反馈（已完成受控实现）");
    expect(roadmap).toContain("阶段 C，用真实账号把题本黄金旅程跑穿");
    expect(roadmap).not.toContain("022｜用户入口与教练反馈（下一焦点）");
  });
});
