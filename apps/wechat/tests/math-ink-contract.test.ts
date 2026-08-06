import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import {
  MathInkApi,
  type MathInkRecognitionResult,
} from "../miniprogram/pages/practice/shared/api/math-ink";
import type { RecognizeMathInkCommand } from "../miniprogram/api/generated/types.gen";

describe("Math Ink generated contract", () => {
  it("sends session and question timing fields from the generated command", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const result = {
      state: "failed",
      requestId: "math-ink-request-1",
      formula: null,
      failureCode: "recognition_unavailable",
    } satisfies MathInkRecognitionResult;
    vi.stubGlobal("wx", {
      request: (options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
        options.success?.({
          data: {
            data: result,
            requestId: "math-ink-envelope-1",
            generatedAt: "2026-07-28T07:00:00Z",
          },
          statusCode: 200,
          header: {},
          cookies: [],
          exception: { reasons: [], retryCount: 0 },
          profile: {} as WechatMiniprogram.RequestProfile,
          useHttpDNS: false,
          errMsg: "request:ok",
        });
      },
    });
    const command = {
      sessionId: "00000000-0000-0000-0000-000000000001",
      questionId: "question-1",
      sessionAtMs: 35_000,
      questionElapsedMs: 12_000,
      scaleX: 1,
      scaleY: 1,
      strokes: [{ x: [1, 2], y: [3, 4], t: [0, 10] }],
    } satisfies RecognizeMathInkCommand;

    const response = await new MathInkApi(
      new ApiClient("https://api.example.test", "access-token-1"),
    ).recognize(command, "math-ink-command-1");

    expect(response).toEqual(result);
    expect(requests).toHaveLength(1);
    expect(requests[0]?.data).toEqual(command);
    expect(requests[0]?.header?.Authorization).toBe("Bearer access-token-1");
    expect(requests[0]?.header?.["Idempotency-Key"]).toBe("math-ink-command-1");
  });
});
