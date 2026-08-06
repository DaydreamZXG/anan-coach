import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import { XfyunApi } from "../miniprogram/pages/reflection/features/api/xfyun";
import { maximumReflectionVoiceDurationMilliseconds } from "../miniprogram/features/learning-media/contracts";

describe("讯飞学习媒体补强", () => {
  it("把复盘录音限制在讯飞单连接支持的 60 秒", () => {
    expect(maximumReflectionVoiceDurationMilliseconds).toBe(60_000);
  });

  it("听写与合成均通过已登录 API 客户端调用", async () => {
    const post = vi
      .fn()
      .mockResolvedValueOnce({ data: { transcript: "识别原话", providerRequestId: "iat-1" } })
      .mockResolvedValueOnce({
        data: { audioBase64: "bXAz", mimeType: "audio/mpeg", providerRequestId: "tts-1" },
      });
    const api = new XfyunApi({ post } as never);

    await expect(
      api.speechToText({ audioBase64: "bXAz", durationMilliseconds: 1_000 }),
    ).resolves.toMatchObject({ transcript: "识别原话" });
    await expect(api.textToSpeech("识别原话")).resolves.toMatchObject({
      mimeType: "audio/mpeg",
    });
    expect(post).toHaveBeenNthCalledWith(
      1,
      "/api/v2/xfyun/speech-to-text",
      { audioBase64: "bXAz", durationMilliseconds: 1_000, encoding: "lame" },
      { timeoutMs: 45_000 },
    );
  });

  it("复盘页明确由用户点击转文字，且文字仍可编辑", () => {
    const wxml = readFileSync("miniprogram/pages/reflection/index.wxml", "utf8");
    const page = readFileSync("miniprogram/pages/reflection/index.ts", "utf8");

    expect(wxml).toContain('bindtap="transcribeVoice">转文字</button>');
    expect(wxml).toContain('bindtap="readRawText"');
    expect(page).toContain("runtime.mediaDraft.rawText = rawText");
    expect(page).not.toContain("autoplay");
  });
});
