import { afterEach, describe, expect, it, vi } from "vitest";
import { chooseQuestionPdf } from "../../../subpackage-shared-src/features/external-learning/platform";
// @ts-expect-error Vite exposes Mini Program templates through its raw asset loader.
import essayTemplateRaw from "../../../miniprogram/pages/essay-task/index.wxml?raw";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("internal beta runtime blockers 029 failure path", () => {
  it("rejects a non-PDF locally and renders the essay submission message only once", async () => {
    vi.stubGlobal("wx", {
      chooseMessageFile: () =>
        Promise.resolve({
          tempFiles: [
            { path: "wxfile://notes.docx", name: "notes.docx", size: 128, type: "file", time: 1 },
          ],
          errMsg: "chooseMessageFile:ok",
        }),
    });

    await expect(chooseQuestionPdf()).rejects.toThrow("请选择 PDF 文件。");
    const template = String(essayTemplateRaw);
    expect(template.match(/class="submission-message /gu)).toHaveLength(1);
    expect(template).not.toContain("footer-message");
  });
});
