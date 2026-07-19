"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const test = require("node:test");

const {
  CONTRACT_VERSION,
  HunyuanTransportError,
  createHandler,
  normalizeInvocationEvent,
} = require("./runtime");

function request(overrides = {}) {
  return {
    contractVersion: CONTRACT_VERSION,
    requestId: "probe-20260718-0001",
    purpose: "connectivity_probe",
    messages: [{ role: "user", content: "Reply with exactly: PONG" }],
    ...overrides,
  };
}

test("returns a fingerprinted response while keeping model ownership in the function", async () => {
  let providerInput;
  const main = createHandler({
    model: "hunyuan-test-model",
    createModel: () => ({
      generateText: async (input) => {
        providerInput = input;
        return {
          text: "PONG",
          usage: { prompt_tokens: 7, completion_tokens: 1, total_tokens: 8 },
        };
      },
    }),
  });

  const result = await main(request());
  assert.equal(providerInput.model, "hunyuan-test-model");
  assert.equal(result.requestId, "probe-20260718-0001");
  assert.equal(result.text, "PONG");
  assert.deepEqual(result.usage, { promptTokens: 7, completionTokens: 1, totalTokens: 8 });
  assert.equal(result.outputSha256, createHash("sha256").update("PONG").digest("hex"));
});

test("rejects unknown fields and unknown purposes", async () => {
  const main = createHandler({
    createModel: () => ({ generateText: async () => ({ text: "PONG" }) }),
  });
  await assert.rejects(() => main({ ...request(), model: "caller-owned" }), HunyuanTransportError);
  await assert.rejects(
    () => main(request({ purpose: "unknown" })),
    HunyuanTransportError,
  );
});

test("accepts formal scoring only with the closed two-message contract", async () => {
  let providerInput;
  const main = createHandler({
    createModel: () => ({
      generateText: async (input) => {
        providerInput = input;
        return { text: "{\"score\":{}}" };
      },
    }),
  });
  const result = await main(
    request({
      purpose: "essay_score_candidate",
      messages: [
        { role: "system", content: "server-owned scoring contract" },
        { role: "user", content: "immutable anonymous grading document" },
      ],
    }),
  );
  assert.equal(providerInput.temperature, 0);
  assert.equal(result.text, "{\"score\":{}}");
  await assert.rejects(
    () =>
      main(
        request({
          purpose: "essay_score_candidate",
          messages: [{ role: "user", content: "caller prompt" }],
        }),
      ),
    HunyuanTransportError,
  );
});

test("removes only the CloudBase gateway context before closed validation", () => {
  assert.deepEqual(
    normalizeInvocationEvent({ ...request(), tcbContext: { requestId: "provider-owned" } }),
    request(),
  );
  assert.deepEqual(normalizeInvocationEvent({ ...request(), tcbContext: "{}" }), request());
  assert.throws(
    () => normalizeInvocationEvent({ ...request(), tcbContext: 1 }),
    HunyuanTransportError,
  );
  assert.deepEqual(normalizeInvocationEvent({ ...request(), unknown: true }), {
    ...request(),
    unknown: true,
  });
});

test("rejects unsupported roles and oversized input", async () => {
  const main = createHandler({
    maxInputBytes: 512,
    createModel: () => ({ generateText: async () => ({ text: "PONG" }) }),
  });
  await assert.rejects(
    () => main(request({ messages: [{ role: "tool", content: "not allowed" }] })),
    HunyuanTransportError,
  );
  await assert.rejects(
    () => main(request({ messages: [{ role: "user", content: "x".repeat(1024) }] })),
    HunyuanTransportError,
  );
});

test("does not return partial or oversized provider output", async () => {
  const empty = createHandler({
    createModel: () => ({ generateText: async () => ({ text: "" }) }),
  });
  await assert.rejects(() => empty(request()), HunyuanTransportError);
  const oversized = createHandler({
    maxOutputBytes: 3,
    createModel: () => ({ generateText: async () => ({ text: "PONG" }) }),
  });
  await assert.rejects(() => oversized(request()), HunyuanTransportError);
});
