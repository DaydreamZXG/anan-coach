#!/usr/bin/env node
/**
 * OCR MCP server — 答题卡 / 图片文字识别
 *
 * 提供一个 `ocr_image` 工具:传入图片路径,自动压缩后调用
 * https://tp.ergou.pro/v1/ocr,返回识别出的文本。
 *
 * 配置方式(项目根 .mcp.json):
 *   "ocr": { "command": "node", "args": ["tools/ocr-mcp/server.mjs"] }
 */
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import sharp from "sharp";
import { createRequire } from "node:module";
import { stat } from "node:fs/promises";
import path from "node:path";

const OCR_URL = process.env.OCR_URL ?? "https://tp.ergou.pro/v1/ocr";
const OCR_API_KEY = process.env.OCR_API_KEY?.trim() ?? "";
const TIMEOUT_MS = Number(process.env.OCR_TIMEOUT_MS ?? 120_000);

const require = createRequire(import.meta.url);
const pkg = require("./package.json");
const server = new Server(
  { name: "ocr-mcp", version: pkg.version ?? "0.1.0" },
  { capabilities: { tools: {} } },
);

const TOOL_SPEC = {
  name: "ocr_image",
  description:
    "识别图片中的文字(答题卡、试卷、截图等)。传入图片的本地路径,自动压缩到适合 OCR 的尺寸,返回识别文本和置信度。",
  inputSchema: {
    type: "object",
    properties: {
      imagePath: {
        type: "string",
        description: "图片文件的绝对路径,支持 jpg / png / webp / heic 等",
      },
      maxWidth: {
        type: "number",
        description: "可选,压缩到的最长边像素(默认 2000),用于大图提速",
      },
    },
    required: ["imagePath"],
  },
};

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [TOOL_SPEC],
}));

/** 压缩图片到 OCR 友好的尺寸,返回 JPEG Buffer */
async function prepareImage(imagePath, maxWidth = 2000) {
  const st = await stat(imagePath);
  if (!st.isFile()) throw new Error(`不是文件:${imagePath}`);
  const img = sharp(imagePath);
  const meta = await img.metadata();
  let pipeline = img;
  const longest = Math.max(meta.width ?? 0, meta.height ?? 0);
  if (longest > maxWidth) {
    pipeline = pipeline.resize({ width: maxWidth, height: maxWidth, fit: "inside" });
  }
  return pipeline.jpeg({ quality: 88 }).toBuffer();
}

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  if (name !== "ocr_image") {
    return { content: [{ type: "text", text: `未知工具:${name}` }], isError: true };
  }
  const imagePath = args?.imagePath;
  if (typeof imagePath !== "string" || !imagePath) {
    return {
      content: [{ type: "text", text: "缺少 imagePath 参数" }],
      isError: true,
    };
  }
  try {
    if (!OCR_API_KEY) {
      throw new Error("未配置 OCR_API_KEY 环境变量");
    }
    const jpeg = await prepareImage(path.resolve(imagePath), args?.maxWidth);
    const form = new FormData();
    form.append("image", new Blob([jpeg], { type: "image/jpeg" }), "image.jpg");

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    let resp;
    try {
      resp = await fetch(OCR_URL, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${OCR_API_KEY}`,
          Accept: "application/json",
        },
        body: form,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    const raw = await resp.text();
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new Error(`OCR 服务返回非 JSON(HTTP ${resp.status}):${raw.slice(0, 200)}`);
    }
    if (!resp.ok) {
      const detail = parsed?.detail ? String(parsed.detail) : `HTTP ${resp.status}`;
      throw new Error(`OCR 服务错误:${detail}`);
    }
    const text = parsed?.text ?? "";
    const parts = [`识别结果:\n${text}`];
    if (typeof parsed?.confidence === "number") {
      parts.push(`\n置信度:${(parsed.confidence * 100).toFixed(1)}%`);
    }
    return { content: [{ type: "text", text: parts.join("") }] };
  } catch (err) {
    return {
      content: [{ type: "text", text: `OCR 失败:${err?.message ?? err}` }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
