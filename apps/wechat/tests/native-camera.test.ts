import { afterEach, describe, expect, it, vi } from "vitest";
import appConfig from "../miniprogram/app.json";
import { captureSourcePhoto } from "../miniprogram/platform/source-image";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import captureTemplate from "../miniprogram/pages/capture-import/index.wxml?raw";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("capture import native camera", () => {
  it("renders the WeChat camera component and binds its real lifecycle", () => {
    expect(captureTemplate).toContain("<camera");
    expect(captureTemplate).toContain('bindinitdone="handleCameraInit"');
    expect(captureTemplate).toContain('binderror="handleCameraError"');
    expect(captureTemplate).toContain('bindtap="chooseCamera"');
    expect(appConfig.permission["scope.camera"].desc).toContain("拍摄");
  });

  it("takes the displayed camera frame instead of reopening chooseMedia", async () => {
    const takePhoto = vi.fn((option: WechatMiniprogram.TakePhotoOption) =>
      option.success?.({
        tempImagePath: "wxfile://camera/question.png",
        metadata: {},
        errMsg: "takePhoto:ok",
      }),
    );
    const chooseMedia = vi.fn();
    vi.stubGlobal("wx", {
      chooseMedia,
      createCameraContext: () => ({ takePhoto }),
      getFileSystemManager: () => ({
        getFileInfo: (option: { success?: (result: { size: number; errMsg: string }) => void }) => {
          option.success?.({
            size: 2_048,
            errMsg: "getFileInfo:ok",
          });
        },
      }),
    });

    await expect(captureSourcePhoto("拍照题目.jpg")).resolves.toEqual({
      localPath: "wxfile://camera/question.png",
      displayName: "question.png",
      sourceKind: "camera_image",
      mimeType: "image/png",
      byteSize: 2_048,
      pageCount: null,
    });
    expect(takePhoto).toHaveBeenCalledWith(expect.objectContaining({ quality: "high" }));
    expect(chooseMedia).not.toHaveBeenCalled();
  });
});
