import { afterEach, describe, expect, it, vi } from "vitest";
import { editQuestionImage } from "../../../subpackage-shared-src/features/external-learning/platform";
// @ts-expect-error Vite exposes Mini Program styles through its raw asset loader.
import taskShellStylesRaw from "../../../subpackage-shared-src/task-runtime/task-shell/index.wxss?raw";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("internal beta runtime blockers 029 capability race", () => {
  it("uses fixed-ratio crop only as a deterministic old-client fallback", async () => {
    const cropImage = vi.fn((option: WechatMiniprogram.CropImageOption) => {
      option.success?.({ tempFilePath: "wxfile://fallback.jpg", errMsg: "cropImage:ok" });
    });
    vi.stubGlobal("wx", {
      cropImage,
      getFileSystemManager: () => ({
        getFileInfo: (option: WechatMiniprogram.GetFileInfoOption) => {
          option.success?.({ size: 512, digest: "", errMsg: "getFileInfo:ok" });
        },
      }),
    });

    await expect(
      editQuestionImage({
        localPath: "wxfile://question.jpg",
        displayName: "question.jpg",
        sourceKind: "camera_image",
        mimeType: "image/jpeg",
        byteSize: 256,
        pageCount: null,
      }),
    ).resolves.toMatchObject({ localPath: "wxfile://fallback.jpg" });
    expect(cropImage).toHaveBeenCalledWith(expect.objectContaining({ cropScale: "9:16" }));
    const styles = String(taskShellStylesRaw);
    expect(styles).toContain(".task-shell::before");
    expect(styles).toContain("height: var(--task-status-bar-height)");
  });
});
