"use strict";

const { createHash } = require("node:crypto");

const CONTRACT_VERSION = "hunyuan-text-candidate@v2";
const PROVIDER_NAME = "cloudbase-hunyuan";
const CONNECTIVITY_PURPOSE = "connectivity_probe";
const ESSAY_SCORE_PURPOSE = "essay_score_candidate";
const DEFAULT_MODEL = "hunyuan-2.0-instruct-20251111";
const DEFAULT_MAX_INPUT_BYTES = 196608;
const DEFAULT_MAX_OUTPUT_BYTES = 262144;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const MESSAGE_ROLES = new Set(["system", "user", "assistant"]);

class HunyuanTransportError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "HunyuanTransportError";
    this.code = code;
  }
}

function positiveInteger(value, fallback, name) {
  if (value === undefined || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new HunyuanTransportError("configuration_invalid", `${name} must be a positive integer`);
  }
  return parsed;
}

function ownKeysAre(value, allowed) {
  const actual = Object.keys(value).sort();
  const expected = [...allowed].sort();
  return (
    actual.length === expected.length && actual.every((item, index) => item === expected[index])
  );
}

function validateRequest(event, maxInputBytes) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    throw new HunyuanTransportError("request_invalid", "request must be an object");
  }
  if (!ownKeysAre(event, ["contractVersion", "requestId", "purpose", "messages"])) {
    throw new HunyuanTransportError(
      "request_invalid",
      "request fields do not match the v1 contract",
    );
  }
  if (event.contractVersion !== CONTRACT_VERSION) {
    throw new HunyuanTransportError("request_invalid", "unsupported contract version");
  }
  if (typeof event.requestId !== "string" || !REQUEST_ID_PATTERN.test(event.requestId)) {
    throw new HunyuanTransportError("request_invalid", "invalid request identity");
  }
  if (![CONNECTIVITY_PURPOSE, ESSAY_SCORE_PURPOSE].includes(event.purpose)) {
    throw new HunyuanTransportError("request_invalid", "purpose is not enabled in this release");
  }
  if (!Array.isArray(event.messages) || event.messages.length < 1 || event.messages.length > 8) {
    throw new HunyuanTransportError(
      "request_invalid",
      "messages must contain between one and eight items",
    );
  }
  for (const message of event.messages) {
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      throw new HunyuanTransportError("request_invalid", "message must be an object");
    }
    if (!ownKeysAre(message, ["role", "content"])) {
      throw new HunyuanTransportError(
        "request_invalid",
        "message fields do not match the v1 contract",
      );
    }
    if (!MESSAGE_ROLES.has(message.role)) {
      throw new HunyuanTransportError("request_invalid", "message role is not permitted");
    }
    if (typeof message.content !== "string" || message.content.length === 0) {
      throw new HunyuanTransportError("request_invalid", "message content must be non-empty text");
    }
  }
  if (
    event.purpose === ESSAY_SCORE_PURPOSE &&
    (event.messages.length !== 2 ||
      event.messages[0].role !== "system" ||
      event.messages[1].role !== "user")
  ) {
    throw new HunyuanTransportError(
      "request_invalid",
      "essay scoring requires one server-owned system message and one input document",
    );
  }
  const inputBytes = Buffer.byteLength(JSON.stringify(event), "utf8");
  if (inputBytes > maxInputBytes) {
    throw new HunyuanTransportError(
      "request_invalid",
      "request exceeds the configured input limit",
    );
  }
}

function normalizeInvocationEvent(event) {
  if (!event || typeof event !== "object" || Array.isArray(event)) return event;
  if (!Object.prototype.hasOwnProperty.call(event, "tcbContext")) return event;
  const context = event.tcbContext;
  if (
    context !== null &&
    typeof context !== "string" &&
    (typeof context !== "object" || Array.isArray(context))
  ) {
    throw new HunyuanTransportError("request_invalid", "CloudBase invocation context is invalid");
  }
  const normalized = { ...event };
  delete normalized.tcbContext;
  return normalized;
}

function normalizeUsage(usage) {
  const source = usage && typeof usage === "object" ? usage : {};
  const read = (snake, camel) => {
    const value = source[snake] ?? source[camel] ?? 0;
    return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  };
  const promptTokens = read("prompt_tokens", "promptTokens");
  const completionTokens = read("completion_tokens", "completionTokens");
  const totalTokens = read("total_tokens", "totalTokens") || promptTokens + completionTokens;
  return { promptTokens, completionTokens, totalTokens };
}

function createHandler({ createModel, model, maxInputBytes, maxOutputBytes }) {
  if (typeof createModel !== "function") {
    throw new HunyuanTransportError("configuration_invalid", "createModel is required");
  }
  const configuredModel = typeof model === "string" && model.trim() ? model.trim() : DEFAULT_MODEL;
  const inputLimit = positiveInteger(maxInputBytes, DEFAULT_MAX_INPUT_BYTES, "maxInputBytes");
  const outputLimit = positiveInteger(maxOutputBytes, DEFAULT_MAX_OUTPUT_BYTES, "maxOutputBytes");

  return async function main(event) {
    validateRequest(event, inputLimit);
    let result;
    try {
      const hunyuan = createModel();
      result = await hunyuan.generateText(
        {
          model: configuredModel,
          messages: event.messages,
          temperature: event.purpose === ESSAY_SCORE_PURPOSE ? 0 : 0.1,
          top_p: 0.8,
        },
        { timeout: 120000 },
      );
    } catch (_error) {
      throw new HunyuanTransportError("provider_failed", "Hunyuan generation failed");
    }
    if (result && result.error) {
      throw new HunyuanTransportError("provider_failed", "Hunyuan returned an error result");
    }
    const text = result && typeof result.text === "string" ? result.text : "";
    if (!text) {
      throw new HunyuanTransportError("response_invalid", "Hunyuan returned no text");
    }
    if (Buffer.byteLength(text, "utf8") > outputLimit) {
      throw new HunyuanTransportError(
        "response_invalid",
        "Hunyuan output exceeds the configured limit",
      );
    }
    return {
      contractVersion: CONTRACT_VERSION,
      requestId: event.requestId,
      provider: PROVIDER_NAME,
      model: configuredModel,
      text,
      usage: normalizeUsage(result.usage),
      outputSha256: createHash("sha256").update(text, "utf8").digest("hex"),
    };
  };
}

module.exports = {
  CONTRACT_VERSION,
  CONNECTIVITY_PURPOSE,
  ESSAY_SCORE_PURPOSE,
  DEFAULT_MODEL,
  HunyuanTransportError,
  createHandler,
  normalizeInvocationEvent,
  normalizeUsage,
  validateRequest,
};
