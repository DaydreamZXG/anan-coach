import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const template = readFileSync("miniprogram/pages/reflection/index.wxml", "utf8");
const runtime = readFileSync("miniprogram/pages/reflection/index.ts", "utf8");
const api = readFileSync("miniprogram/pages/reflection/shared/api/xingce-learning.ts", "utf8");

describe("grounded 费曼讲回", () => {
  it("复用原救场卡完成讲解、复现、迁移与复测交接", () => {
    expect(template).toContain("讲给岸岸，再验证");
    expect(template).toContain("一无所知的学生");
    expect(template).toContain("迁移验证");
    expect(runtime).toContain("submitTeachBack");
    expect(runtime).toContain("submitTeachBackTransfer");
    expect(api).toContain("/teach-back");
    expect(api).toContain("/transfer");
  });

  it("迁移题渲染前不再暴露正确答案和解析", () => {
    expect(template).not.toContain("similarQuestion.correctOptionIds");
    expect(template).not.toContain("similarQuestion.explanation");
    expect(template).not.toContain("✓ 正确");
    const teachBackBlock = template.slice(template.indexOf("teach-back"));
    expect(teachBackBlock).not.toContain("correctOptionIds");
  });
});
