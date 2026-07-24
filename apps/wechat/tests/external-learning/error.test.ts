import { describe, expect, it } from "vitest";

import { ApiClientError } from "../../miniprogram/api/client";
import { externalLearningError } from "../../miniprogram/features/external-learning/error";

describe("external learning error presentation", () => {
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
