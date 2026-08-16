import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("roadmap after private question book 022A single focus", () => {
  it("keeps exactly one current unique development focus", () => {
    const roadmap = readFileSync("../../docs/product/anan-development-roadmap.md", "utf8");
    expect(roadmap.match(/\*\*当前唯一开发焦点：/gu)).toHaveLength(1);
  });
});
