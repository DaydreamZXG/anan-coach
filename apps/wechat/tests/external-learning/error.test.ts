import { describe, expect, it } from "vitest";

import { ApiClientError } from "../../miniprogram/api/client";
import { externalLearningError } from "../../miniprogram/features/external-learning/error";

describe("external learning error presentation", () => {
  it("does not misclassify a local file error as a network interruption", () => {
    const view = externalLearningError(new Error("本地文件读取失败。"));

    expect(view).toMatchObject({
      code: "unknown_error",
      title: "操作没有完成",
      message: "本地文件读取失败。",
      retryable: false,
      uncertain: false,
    });
  });

  it("keeps an explicit status-zero transport failure uncertain", () => {
    const view = externalLearningError(new ApiClientError("请求未确认。", 0, "req-network"));

    expect(view).toMatchObject({
      title: "网络连接中断",
      retryable: true,
      uncertain: true,
    });
  });

  it("presents a scanner outage without calling it a revision conflict", () => {
    const view = externalLearningError(
      new ApiClientError("文件安全扫描未完成，不能进入识别。", 503, "", {
        code: "scanner_unavailable",
        message: "文件安全扫描未完成，不能进入识别。",
        requestId: "",
      }),
    );

    expect(view).toMatchObject({
      code: "scanner_unavailable",
      title: "文件安全检查暂不可用",
      uncertain: true,
      retryable: true,
    });
  });

  it("uses the backend public payload code before status fallback", () => {
    const view = externalLearningError(
      new ApiClientError("recognition processor must use HTTPS for learner content", 503, "req-1", {
        code: "processor_not_approved",
        message: "recognition processor must use HTTPS for learner content",
        requestId: "api-req-1",
      }),
    );

    expect(view.code).toBe("processor_not_approved");
    expect(view.title).toBe("外部 AI 处理器未放行");
    expect(view.requestId).toBe("api-req-1");
    expect(view.retryable).toBe(false);
    expect(view.uncertain).toBe(false);
    expect(view.message).toContain("不会把你的题图");
  });
});
