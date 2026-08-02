import { describe, expect, it } from "vitest";
import {
  stripOptionContentPrefix,
  stripOptionLabelPrefix,
} from "../miniprogram/task-runtime/option-presentation";

/**
 * 选项展示剥离题库自带 "A、" 前缀的回归测试：
 * 前端已单独渲染 label（A/B/C/D），题库文本再带 "A、" 会视觉重复。
 * 剥离只在共享展示层发生，不修改题库原始数据。
 */

describe("xingce option label prefix strip（题库前缀 vs 前端 label 不重复）", () => {
  it("drops a leading option label prefix", () => {
    expect(stripOptionLabelPrefix("A、智能社会对社会心态治理提出了新的挑战", "A")).toBe(
      "智能社会对社会心态治理提出了新的挑战",
    );
    expect(stripOptionLabelPrefix("B、香榧中紫杉醇含量微乎其微", "B")).toBe(
      "香榧中紫杉醇含量微乎其微",
    );
  });

  it("does not strip a prefix-looking string in the middle of text", () => {
    const text = "该选项考察 B、区域治理 的迁移规律";
    // 只在开头剥离；正文中间的 "B、" 保留。
    expect(stripOptionLabelPrefix(text, "B")).toBe(text);
  });

  it("handles full-width colon and dotted variants", () => {
    expect(stripOptionLabelPrefix("A．智能社会…", "A")).toBe("智能社会…");
    expect(stripOptionLabelPrefix("A. 智能社会…", "A")).toBe("智能社会…");
    expect(stripOptionLabelPrefix("A：智能社会…", "A")).toBe("智能社会…");
    // 无标点的 "A 智能社会" 不剥（空格在字母后不构成选项前缀格式），保留原样。
    expect(stripOptionLabelPrefix("A 智能社会…", "A")).toBe("A 智能社会…");
  });

  it("leaves text without any prefix untouched", () => {
    const plain = "智能社会对社会心态治理提出了新的挑战";
    expect(stripOptionLabelPrefix(plain, "A")).toBe(plain);
  });

  it("does not strip a different label or later paragraph", () => {
    expect(stripOptionLabelPrefix("B、正文", "A")).toBe("B、正文");
    const blocks = [
      { type: "paragraph" as const, blockId: "p1", text: "A、第一段" },
      { type: "paragraph" as const, blockId: "p2", text: "A、第二段正文" },
    ];
    expect(stripOptionContentPrefix(blocks, "A")).toEqual([
      { type: "paragraph", blockId: "p1", text: "第一段" },
      { type: "paragraph", blockId: "p2", text: "A、第二段正文" },
    ]);
  });
});
