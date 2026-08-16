import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("roadmap after private question book 022A recovery sequence", () => {
  it("names the real golden journey and its recovery checks", () => {
    const roadmap = readFileSync("../../docs/product/anan-development-roadmap.md", "utf8");
    for (const phrase of [
      "真实 PDF/图片题本",
      "确认一次歧义",
      "真实作答",
      "错因确认",
      "岸岸建议及后续复测",
      "中断恢复",
      "跨账号隔离",
      "删除传播",
    ]) {
      expect(roadmap).toContain(phrase);
    }
  });
});
