import { describe, expect, it, vi } from "vitest";

// The deployed cloud function remains CommonJS so WeChat can load it.
// @ts-expect-error The runtime is intentionally JavaScript and has no declaration bundle.
import runtimeModule from "../cloudfunctions/hunyuan-essay-grader/runtime.js";

type HunyuanRuntime = {
  CONTRACT_VERSION: string;
  createHandler(options: {
    createModel: () => {
      generateText(input: Record<string, unknown>): Promise<{
        text: string;
        usage?: Record<string, number>;
      }>;
    };
    model?: string;
    maxInputBytes?: number;
    maxOutputBytes?: number;
  }): (event: Record<string, unknown>) => Promise<Record<string, unknown>>;
  normalizeInvocationEvent(event: Record<string, unknown>): Record<string, unknown>;
};

const runtime = runtimeModule as HunyuanRuntime;

function request(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contractVersion: runtime.CONTRACT_VERSION,
    requestId: "probe-20260718-vitest",
    purpose: "connectivity_probe",
    messages: [{ role: "user", content: "Reply with exactly: PONG" }],
    ...overrides,
  };
}

describe("CloudBase Hunyuan function contract", () => {
  it("keeps model selection in the cloud function and fingerprints the result", async () => {
    const generateText = vi.fn(() =>
      Promise.resolve({
        text: "PONG",
        usage: { prompt_tokens: 7, completion_tokens: 1, total_tokens: 8 },
      }),
    );
    const handle = runtime.createHandler({
      createModel: () => ({ generateText }),
      model: "server-owned-model",
    });

    const result = await handle(request());

    expect(generateText).toHaveBeenCalledWith(
      expect.objectContaining({ model: "server-owned-model" }),
      { timeout: 120000 },
    );
    expect(result).toMatchObject({
      requestId: "probe-20260718-vitest",
      provider: "cloudbase-hunyuan",
      model: "server-owned-model",
      text: "PONG",
      outputSha256: "95784973cc639977bff93619700168505cb8f7855e44fa643d34622a43706c87",
    });
  });

  it("removes only CloudBase context and still rejects caller-owned model fields", async () => {
    const normalized = runtime.normalizeInvocationEvent({
      ...request(),
      tcbContext: { requestId: "gateway-owned" },
    });
    expect(normalized).toEqual(request());

    const handle = runtime.createHandler({
      createModel: () => ({ generateText: () => Promise.resolve({ text: "PONG" }) }),
    });
    await expect(handle(request({ model: "caller-owned" }))).rejects.toThrow(
      "request fields do not match the v1 contract",
    );
    await expect(handle(request({ purpose: "essay_score_candidate" }))).rejects.toThrow(
      "purpose is not enabled in this release",
    );
  });
});
