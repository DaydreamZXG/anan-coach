import { afterEach, describe, expect, it, vi } from "vitest";
import appConfig from "../miniprogram/app.json";
import { captureSourcePhoto } from "../miniprogram/platform/source-image";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import captureTemplateRaw from "../miniprogram/pages/capture-import/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program source through its raw asset loader.
import captureLogicRaw from "../miniprogram/pages/capture-import/index.ts?raw";
// @ts-expect-error Vite exposes mini-program source through its raw asset loader.
import externalLearningPlatformRaw from "../miniprogram/features/external-learning/platform.ts?raw";

const captureTemplate = String(captureTemplateRaw);
const captureLogic = String(captureLogicRaw);
const externalLearningPlatform = String(externalLearningPlatformRaw);

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

  it("keeps native cropping optional on the image confirmation screen", () => {
    const cropStart = captureTemplate.indexOf("captureStage === 'crop'");
    const cropEnd = captureTemplate.indexOf(
      "<block wx:elif=\"{{state && state.status === 'idle'}}\">",
      cropStart,
    );
    const cropTemplate = captureTemplate.slice(cropStart, cropEnd);

    expect(captureLogic).not.toMatch(/await (?:this|page)\.editPendingImage\(\);/u);
    expect(captureLogic).toContain("裁剪没打开，直接用原图也行");
    expect(externalLearningPlatform).toContain('typeof wx.cropImage === "function"');
    expect(externalLearningPlatform).toContain("wx.cropImage({");
    expect(externalLearningPlatform).toContain('cropScale: "9:16"');
    expect(externalLearningPlatform.indexOf("wx.cropImage({")).toBeLessThan(
      externalLearningPlatform.indexOf("wx.editImage({"),
    );
    expect(cropTemplate).toContain('bindtap="editPendingImage"');
    expect(cropTemplate).toContain("点一下图片就能重新裁剪");
    expect(cropTemplate).not.toContain("crop-frame");
    expect(cropTemplate).not.toContain("crop-corner");
    expect(cropTemplate).not.toContain("touchmove");
  });

  it("renders a confirmed match from the catalog instead of OCR fragments", () => {
    expect(captureTemplate).toContain("state.activeQuestionUnit.matchedContent.promptText");
    expect(captureTemplate).toContain("state.activeQuestionUnit.matchedContent.materialTexts");
    expect(captureTemplate).toContain(
      "state.activeQuestionUnit.matchedContent.solutionExplanation",
    );
    expect(captureTemplate).toContain("catalogOptions");
    expect(captureTemplate).not.toContain("详细精讲会保存在这道题的个人学习包中");
  });
});
