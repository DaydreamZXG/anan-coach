#!/usr/bin/env node

// Refresh a single Figma page of the delivery package from the Figma Desktop
// Dev Mode MCP server, without touching the other pages.
//
//   FIGMA_FILE_KEY=0fJdWImI2lGJQqTYjlZPN8 \
//   node apps/wechat/design/figma-delivery/scripts/refresh-page-from-local-mcp.mjs 03-practice-review
//
// The full-file exporter (export-from-local-mcp.mjs) wipes and rebuilds all 8
// pages, which takes minutes and invalidates untouched baselines. This script
// re-reads exactly one page so a page-scoped design change can be picked up.

import { createHash } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const deliveryRoot = resolve(scriptDirectory, "..");

const fileKey = process.env.FIGMA_FILE_KEY ?? "0fJdWImI2lGJQqTYjlZPN8";
const fileName = process.env.FIGMA_FILE_NAME ?? "anan-mini-program-delivery-2026-07-24";
const mcpUrl = process.env.FIGMA_MCP_URL ?? "http://127.0.0.1:3845/mcp";

const pages = [
  { id: "51:2", slug: "00-core", name: "00 · 核心四栏" },
  { id: "51:3", slug: "01-login-profile", name: "01 · 登录与建档" },
  { id: "51:4", slug: "02-today-plan", name: "02 · 今日与计划" },
  { id: "51:5", slug: "03-practice-review", name: "03 · 行测练习与复盘" },
  { id: "51:6", slug: "04-position", name: "04 · 岗位决策" },
  { id: "51:7", slug: "05-photo-search", name: "05 · 拍照搜题" },
  { id: "51:8", slug: "06-essay", name: "06 · 申论工作台" },
  { id: "51:9", slug: "07-library", name: "07 · 私人资料库" },
];

const targetSlug = process.argv[2];
const page = pages.find((item) => item.slug === targetSlug);
if (!page) {
  process.stderr.write(
    `Unknown page slug: ${targetSlug ?? "(missing)"}\nKnown slugs: ${pages.map((item) => item.slug).join(", ")}\n`,
  );
  process.exit(1);
}

let requestId = 1;
let sessionId = "";

function sleep(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function parseSsePayload(text) {
  const messages = text
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice(6)));
  if (messages.length === 0) {
    throw new Error(`Figma MCP returned no SSE payload: ${text.slice(0, 300)}`);
  }
  const payload = messages.at(-1);
  if (payload.error) {
    throw new Error(`Figma MCP error: ${JSON.stringify(payload.error)}`);
  }
  return payload;
}

async function initialize() {
  const response = await fetch(mcpUrl, {
    method: "POST",
    headers: {
      Accept: "application/json, text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: requestId++,
      method: "initialize",
      params: {
        protocolVersion: "2025-06-18",
        capabilities: {},
        clientInfo: { name: "anan-figma-page-refresher", version: "1.0.0" },
      },
    }),
  });
  const responseText = await response.text();
  if (!response.ok) {
    throw new Error(`Figma MCP initialize failed (${response.status}): ${responseText}`);
  }
  parseSsePayload(responseText);
  sessionId = response.headers.get("mcp-session-id") ?? "";
  if (!sessionId) {
    throw new Error("Figma MCP did not return Mcp-Session-Id");
  }
  await fetch(mcpUrl, {
    method: "POST",
    headers: {
      Accept: "application/json, text/event-stream",
      "Content-Type": "application/json",
      "Mcp-Session-Id": sessionId,
    },
    body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
  });
}

async function toolCall(name, args, attempt = 1) {
  try {
    const response = await fetch(mcpUrl, {
      method: "POST",
      headers: {
        Accept: "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: requestId++,
        method: "tools/call",
        params: { name, arguments: args },
      }),
    });
    const responseText = await response.text();
    if (!response.ok) {
      throw new Error(`${name} failed (${response.status}): ${responseText}`);
    }
    const payload = parseSsePayload(responseText);
    if (payload.result?.isError) {
      throw new Error(
        `${name} failed: ${payload.result.content?.map((item) => item.text).join("\n")}`,
      );
    }
    return payload.result?.content ?? [];
  } catch (error) {
    if (attempt >= 3) throw error;
    await sleep(attempt * 1_000);
    return toolCall(name, args, attempt + 1);
  }
}

function textContent(content, index = 0) {
  const textItems = content.filter((item) => item.type === "text");
  const item = textItems[index];
  if (!item?.text) throw new Error("Expected text content from Figma MCP");
  return item.text;
}

function topLevelFrames(xml) {
  return xml
    .split(/\r?\n/)
    .filter((line) => /^ {2}<frame\b/.test(line))
    .map((line) => {
      const value = (attribute) => {
        const match = line.match(new RegExp(`${attribute}="([^"]+)"`));
        return match?.[1] ?? "";
      };
      return {
        nodeId: value("id"),
        name: value("name"),
        width: Number(value("width")),
        height: Number(value("height")),
      };
    });
}

async function loadPageMetadata() {
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    const content = await toolCall("get_metadata", {
      nodeId: page.id,
      clientLanguages: "typescript,wxml,wxss,json",
      clientFrameworks: "wechat-miniprogram",
    });
    const xml = textContent(content);
    if (topLevelFrames(xml).length > 0) return xml;
    if (attempt === 8) {
      throw new Error(`Figma page ${page.id} (${page.name}) did not hydrate`);
    }
    await sleep(1_000);
  }
  throw new Error(`Unreachable metadata state for ${page.id}`);
}

function assetUrls(context) {
  return [
    ...new Set(
      context.match(
        /http:\/\/localhost:3845\/assets\/[a-f0-9]+\.(?:svg|png|jpe?g|webp|gif)/g,
      ) ?? [],
    ),
  ];
}

async function downloadAsset(url) {
  const fileNameFromUrl = new URL(url).pathname.split("/").at(-1);
  if (!fileNameFromUrl) throw new Error(`Invalid Figma asset URL: ${url}`);
  const target = resolve(deliveryRoot, "assets", "by-hash", fileNameFromUrl);
  let bytes;
  try {
    bytes = await readFile(target);
  } catch {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Asset download failed (${response.status}): ${url}`);
    }
    bytes = Buffer.from(await response.arrayBuffer());
    await writeFile(target, bytes);
  }
  return {
    file: `assets/by-hash/${fileNameFromUrl}`,
    sha256: sha256(bytes),
    bytes: bytes.length,
  };
}

async function exportScreen(screen, index) {
  const fileStem = `${String(index + 1).padStart(2, "0")}__node-${screen.nodeId.replace(":", "-")}`;

  const contextContent = await toolCall("get_design_context", {
    nodeId: screen.nodeId,
    clientLanguages: "typescript,wxml,wxss,json",
    clientFrameworks: "wechat-miniprogram",
    artifactType: "WEB_PAGE_OR_APP_SCREEN",
    taskType: "CREATE_ARTIFACT",
  });
  const context = textContent(contextContent);

  // Figma explicitly requires a screenshot after every design-context read.
  const screenshotContent = await toolCall("get_screenshot", {
    nodeId: screen.nodeId,
    contentsOnly: false,
  });
  const screenshotItem = screenshotContent.find((item) => item.type === "image");
  if (!screenshotItem?.data || screenshotItem.mimeType !== "image/png") {
    throw new Error(`Expected PNG screenshot for ${screen.nodeId}`);
  }
  const screenshotBytes = Buffer.from(screenshotItem.data, "base64");
  if (screenshotBytes.length < 1_024) {
    throw new Error(`Screenshot for ${screen.nodeId} is unexpectedly small`);
  }

  const contextDirectory = resolve(deliveryRoot, "contexts", page.slug);
  const screenDirectory = resolve(deliveryRoot, "screens", page.slug);
  await mkdir(contextDirectory, { recursive: true });
  await mkdir(screenDirectory, { recursive: true });

  await writeFile(resolve(contextDirectory, `${fileStem}.tsx`), context);
  await writeFile(resolve(screenDirectory, `${fileStem}.png`), screenshotBytes);

  const assets = [];
  for (const url of assetUrls(context)) {
    assets.push(await downloadAsset(url));
  }

  return {
    ...screen,
    order: index + 1,
    context: `contexts/${page.slug}/${fileStem}.tsx`,
    contextSha256: sha256(Buffer.from(context)),
    screenshot: `screens/${page.slug}/${fileStem}.png`,
    screenshotSha256: sha256(screenshotBytes),
    screenshotBytes: screenshotBytes.length,
    assets,
  };
}

async function main() {
  await initialize();

  process.stdout.write(`Refreshing ${page.name} (${page.id}) from ${fileKey} ...\n`);
  const xml = await loadPageMetadata();
  const frames = topLevelFrames(xml);
  await mkdir(resolve(deliveryRoot, "metadata", "pages"), { recursive: true });
  await writeFile(resolve(deliveryRoot, "metadata", "pages", `${page.slug}.xml`), `${xml}\n`);

  const variableContent = await toolCall("get_variable_defs", {
    nodeId: page.id,
    clientLanguages: "typescript,wxml,wxss,json",
    clientFrameworks: "wechat-miniprogram",
  });
  await writeFile(
    resolve(deliveryRoot, "variables", `${page.slug}.json`),
    `${JSON.stringify(JSON.parse(textContent(variableContent)), null, 2)}\n`,
  );

  // Stale screens from a previous export of this page must not survive.
  for (const directory of ["contexts", "screens"]) {
    await rm(resolve(deliveryRoot, directory, page.slug), { recursive: true, force: true });
  }

  const screens = [];
  for (const [index, frame] of frames.entries()) {
    process.stdout.write(
      `  ${String(index + 1).padStart(2, "0")}/${String(frames.length).padStart(2, "0")} ${frame.name}\n`,
    );
    screens.push(await exportScreen(frame, index));
  }

  const inventoryPath = resolve(deliveryRoot, "screen-inventory.json");
  const inventory = JSON.parse(await readFile(inventoryPath, "utf8"));
  const pageIndex = inventory.pages.findIndex((item) => item.slug === page.slug);
  const refreshed = {
    ...page,
    metadata: `metadata/pages/${page.slug}.xml`,
    variables: `variables/${page.slug}.json`,
    refreshedAt: new Date().toISOString(),
    refreshedFromFileKey: fileKey,
    screens,
  };
  if (pageIndex === -1) inventory.pages.push(refreshed);
  else inventory.pages[pageIndex] = refreshed;

  const uniqueAssets = new Map();
  for (const inventoryPage of inventory.pages) {
    for (const screen of inventoryPage.screens) {
      for (const asset of screen.assets) uniqueAssets.set(asset.file, asset);
    }
  }
  inventory.totals = {
    pageCount: inventory.pages.length,
    screenCount: inventory.pages.reduce((sum, item) => sum + item.screens.length, 0),
    uniqueAssetCount: uniqueAssets.size,
  };
  inventory.generatedAt = new Date().toISOString();

  await writeFile(inventoryPath, `${JSON.stringify(inventory, null, 2)}\n`);
  await writeFile(
    resolve(deliveryRoot, "assets", "inventory.json"),
    `${JSON.stringify([...uniqueAssets.values()], null, 2)}\n`,
  );

  process.stdout.write(
    `Completed: ${page.slug} now has ${screens.length} screens; package totals ${inventory.totals.screenCount} screens.\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
});
