import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("roadmap after private question book 022A boundaries", () => {
  it("keeps production, public-beta and graphic-reasoning work explicitly unfinished", () => {
    const roadmap = readFileSync("../../docs/product/anan-development-roadmap.md", "utf8");
    expect(roadmap).toContain("全产品公测状态 | **未验证**");
    expect(roadmap).toContain("图推结构化资产暂不作为全局阻断条件");
    expect(roadmap).toContain("生产部署已验收");
  });
});
