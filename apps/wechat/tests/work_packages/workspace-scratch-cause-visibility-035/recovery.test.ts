import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionTemplateRaw from "../../../miniprogram/pages/reflection/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionSourceRaw from "../../../miniprogram/pages/reflection/index.ts?raw";

describe("workspace scratch and visible cause 035 recovery", () => {
  it("keeps polling, limitation and correction recovery visible without a dead end", () => {
    const template = String(reflectionTemplateRaw);
    expect(template).toContain('bindtap="refreshCauseCandidate"');
    expect(template).toContain("作答证据已保存");
    expect(template).toContain("正在形成候选错因");
    expect(template).toContain("给出下一题验证动作");
    expect(template).toContain("causeCandidate.missingEvidence.length");
    expect(template).toContain('bindtap="rejectCause"');
    expect(template).toContain('bindtap="replaceCause"');
    expect(String(reflectionSourceRaw)).toContain("scheduleCausePoll(page, runtime)");
  });
});
