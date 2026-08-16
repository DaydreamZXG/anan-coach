import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("roadmap after private question book 022A happy", () => {
  it("records the verified 022 evidence without turning it into a deployment claim", () => {
    const roadmap = readFileSync("../../docs/product/anan-development-roadmap.md", "utf8");
    expect(roadmap).toContain("已验证到 022");
    expect(roadmap).toContain("8c0ab677a74d63aa.49537be715a962956720.json");
    expect(roadmap).toContain("仍不证明真实账号环境、生产迁移或公测就绪");
  });
});
