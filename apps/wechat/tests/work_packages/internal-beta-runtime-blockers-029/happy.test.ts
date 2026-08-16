import { afterEach, describe, expect, it, vi } from "vitest";
import {
  chooseQuestionPdf,
  editQuestionImage,
} from "../../../subpackage-shared-src/features/external-learning/platform";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("internal beta runtime blockers 029 happy path", () => {
  it("opens the free editor first and accepts a PDF selected from WeChat files", async () => {
    const editImage = vi.fn((option: WechatMiniprogram.EditImageOption) => {
      option.success?.({ tempFilePath: "wxfile://edited-question.jpg", errMsg: "editImage:ok" });
    });
    const cropImage = vi.fn();
    const chooseMessageFile = vi.fn(() =>
      Promise.resolve({
        tempFiles: [
          {
            path: "wxfile://teacher-book.PDF",
            name: "名师题本.PDF",
            size: 4_096,
            type: "file",
            time: 1,
          },
        ],
        errMsg: "chooseMessageFile:ok",
      }),
    );
    vi.stubGlobal("wx", {
      editImage,
      cropImage,
      chooseMessageFile,
      getFileSystemManager: () => ({
        getFileInfo: (option: WechatMiniprogram.GetFileInfoOption) => {
          option.success?.({ size: 2_048, digest: "", errMsg: "getFileInfo:ok" });
        },
      }),
    });

    await expect(
      editQuestionImage({
        localPath: "wxfile://question.jpg",
        displayName: "question.jpg",
        sourceKind: "camera_image",
        mimeType: "image/jpeg",
        byteSize: 1_024,
        pageCount: null,
      }),
    ).resolves.toMatchObject({ localPath: "wxfile://edited-question.jpg", byteSize: 2_048 });
    await expect(chooseQuestionPdf()).resolves.toMatchObject({
      displayName: "名师题本.PDF",
      mimeType: "application/pdf",
      byteSize: 4_096,
    });
    expect(editImage).toHaveBeenCalledOnce();
    expect(cropImage).not.toHaveBeenCalled();
    expect(chooseMessageFile).toHaveBeenCalledWith({ count: 1, type: "all" });
  });
});
