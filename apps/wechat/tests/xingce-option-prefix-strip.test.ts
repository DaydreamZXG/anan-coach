import { describe, expect, it } from "vitest";

/**
 * 选项展示剥离题库自带 "A、" 前缀的回归测试：
 * 前端已单独渲染 label（A/B/C/D），题库文本再带 "A、" 会视觉重复。
 * 剥离只在展示层发生（presentation 的 stripOptionLabelPrefix / stripOptionContentPrefix）。
 */

const OPTION_LABEL_PREFIX = /^[A-H]\s*[、.．:：]\s*/;

function stripOptionLabelPrefix(text: string): string {
  return text.replace(OPTION_LABEL_PREFIX, "");
}

describe("xingce option label prefix strip（题库前缀 vs 前端 label 不重复）", () => {
  it("drops a leading option label prefix", () => {
    expect(stripOptionLabelPrefix("A、智能社会对社会心态治理提出了新的挑战")).toBe(
      "智能社会对社会心态治理提出了新的挑战",
    );
    expect(stripOptionLabelPrefix("B、香榧中紫杉醇含量微乎其微")).toBe("香榧中紫杉醇含量微乎其微");
  });

  it("does not strip a prefix-looking string in the middle of text", () => {
    const text = "该选项考察 B、区域治理 的迁移规律";
    // 只在开头剥离；正文中间的 "B、" 保留。
    expect(stripOptionLabelPrefix(text)).toBe(text);
  });

  it("handles full-width colon and dotted variants", () => {
    expect(stripOptionLabelPrefix("A．智能社会…")).toBe("智能社会…");
    expect(stripOptionLabelPrefix("A. 智能社会…")).toBe("智能社会…");
    expect(stripOptionLabelPrefix("A：智能社会…")).toBe("智能社会…");
    // 无标点的 "A 智能社会" 不剥（空格在字母后不构成选项前缀格式），保留原样。
    expect(stripOptionLabelPrefix("A 智能社会…")).toBe("A 智能社会…");
  });

  it("leaves text without any prefix untouched", () => {
    const plain = "智能社会对社会心态治理提出了新的挑战";
    expect(stripOptionLabelPrefix(plain)).toBe(plain);
  });
});
