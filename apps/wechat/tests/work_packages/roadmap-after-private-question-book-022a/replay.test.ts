import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("roadmap after private question book 022A immutable identity", () => {
  it("cites the frozen release identity exactly once", () => {
    const roadmap = readFileSync("../../docs/product/anan-development-roadmap.md", "utf8");
    const hash = "sha256:c401517884a532ba5082433c52aa3ffb37e4abb511d33c4a7061fa517322f945";
    expect(roadmap.split(hash)).toHaveLength(2);
  });
});
