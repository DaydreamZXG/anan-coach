"use strict";

const cloud = require("wx-server-sdk");
const { HunyuanTransportError, createHandler, normalizeInvocationEvent } = require("./runtime");

cloud.init({
  env: cloud.DYNAMIC_CURRENT_ENV,
  timeout: 120000,
});

const handle = createHandler({
  createModel: () => cloud.ai().createModel(process.env.HUNYUAN_PROVIDER || "hunyuan-exp"),
  model: process.env.HUNYUAN_MODEL,
  maxInputBytes: process.env.HUNYUAN_MAX_INPUT_BYTES,
  maxOutputBytes: process.env.HUNYUAN_MAX_OUTPUT_BYTES,
});

exports.main = async (event) => {
  try {
    return await handle(normalizeInvocationEvent(event));
  } catch (error) {
    if (error instanceof HunyuanTransportError) {
      return {
        error: {
          code: error.code,
          message: error.message,
        },
      };
    }
    throw error;
  }
};
