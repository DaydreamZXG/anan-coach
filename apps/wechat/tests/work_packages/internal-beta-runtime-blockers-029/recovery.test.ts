import { describe, expect, it } from "vitest";
// @ts-expect-error Vite exposes Mini Program source through its raw asset loader.
import essayLogicRaw from "../../../miniprogram/pages/essay-task/index.ts?raw";
// @ts-expect-error Vite exposes Mini Program templates through its raw asset loader.
import captureTemplateRaw from "../../../miniprogram/pages/capture-import/index.wxml?raw";

describe("internal beta runtime blockers 029 recovery path", () => {
  it("reloads server truth after completion conflict and keeps PDF recovery discoverable", () => {
    const source = String(essayLogicRaw);
    const conflictStart = source.indexOf("error.statusCode === 409");
    const conflictEnd = source.indexOf(
      "handleDraftingRequestError(this, runtime, error)",
      conflictStart,
    );
    const recovery = source.slice(conflictStart, conflictEnd);

    expect(recovery).toContain("storeEssayDraftingDraft");
    expect(recovery).toContain("clearStoredDraftingSubmission");
    expect(recovery).toContain("await this.loadWorkspace()");
    expect(recovery).toContain("!this.data.workspace?.progression.answerVersionId");
    expect(source).not.toContain("数据已经更新，请刷新后重试");

    const template = String(captureTemplateRaw);
    expect(template).toContain("从微信选 PDF");
    expect(template).toContain("PDF 先发到文件传输助手或聊天");
  });
});
