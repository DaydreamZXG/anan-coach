#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readdir, readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const deliveryRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function fileExists(path) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

function pngDimensions(bytes) {
  const signature = "89504e470d0a1a0a";
  if (bytes.subarray(0, 8).toString("hex") !== signature) {
    throw new Error("Invalid PNG signature");
  }
  return {
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
}

async function main() {
  const manifest = JSON.parse(
    await readFile(resolve(deliveryRoot, "manifest.json"), "utf8"),
  );
  const inventory = JSON.parse(
    await readFile(resolve(deliveryRoot, "screen-inventory.json"), "utf8"),
  );
  const routeMap = JSON.parse(
    await readFile(resolve(deliveryRoot, "route-map.json"), "utf8"),
  );
  const assetInventory = JSON.parse(
    await readFile(resolve(deliveryRoot, "assets", "inventory.json"), "utf8"),
  );
  JSON.parse(
    await readFile(resolve(deliveryRoot, "tokens", "design-file.tokens.json"), "utf8"),
  );
  JSON.parse(
    await readFile(resolve(deliveryRoot, "tokens", "observed-values.json"), "utf8"),
  );

  const archive = await readFile(resolve(deliveryRoot, manifest.source.localArchive));
  if (archive.length !== manifest.source.archiveBytes) {
    throw new Error("Figma archive size does not match manifest");
  }
  if (sha256(archive) !== manifest.source.archiveSha256) {
    throw new Error("Figma archive checksum does not match manifest");
  }

  const pageIds = new Set();
  const screenIds = new Set();
  let screenCount = 0;
  let contextBytes = 0;
  let screenshotBytes = 0;

  for (const page of inventory.pages) {
    if (pageIds.has(page.id)) throw new Error(`Duplicate Figma page id: ${page.id}`);
    pageIds.add(page.id);
    for (const requiredPath of [page.metadata, page.variables]) {
      if (!(await fileExists(resolve(deliveryRoot, requiredPath)))) {
        throw new Error(`Missing page export: ${requiredPath}`);
      }
    }
    JSON.parse(await readFile(resolve(deliveryRoot, page.variables), "utf8"));

    for (const screen of page.screens) {
      if (screenIds.has(screen.nodeId)) {
        throw new Error(`Duplicate Figma screen id: ${screen.nodeId}`);
      }
      screenIds.add(screen.nodeId);
      screenCount += 1;

      const context = await readFile(resolve(deliveryRoot, screen.context));
      const screenshot = await readFile(resolve(deliveryRoot, screen.screenshot));
      contextBytes += context.length;
      screenshotBytes += screenshot.length;

      if (sha256(context) !== screen.contextSha256) {
        throw new Error(`Context checksum mismatch: ${screen.context}`);
      }
      if (sha256(screenshot) !== screen.screenshotSha256) {
        throw new Error(`Screenshot checksum mismatch: ${screen.screenshot}`);
      }
      if (!context.toString("utf8").includes(`data-node-id="${screen.nodeId}"`)) {
        throw new Error(`Context is not traceable to node ${screen.nodeId}`);
      }
      const dimensions = pngDimensions(screenshot);
      if (dimensions.width <= 1 || dimensions.height <= 1) {
        throw new Error(`Empty screenshot: ${screen.screenshot}`);
      }
    }
  }

  if (inventory.totals.pageCount !== pageIds.size) {
    throw new Error("Page total does not match inventory");
  }
  if (inventory.totals.screenCount !== screenCount) {
    throw new Error("Screen total does not match inventory");
  }
  if (manifest.coverage.status !== "complete") {
    throw new Error("Manifest does not mark delivery coverage complete");
  }
  if (
    manifest.totals.pageCount !== pageIds.size ||
    manifest.totals.screenCount !== screenCount
  ) {
    throw new Error("Manifest totals do not match inventory");
  }

  const mappedScreenIds = routeMap.mappings.map((mapping) => mapping.figmaNodeId);
  if (mappedScreenIds.length !== new Set(mappedScreenIds).size) {
    throw new Error("Route map contains duplicate Figma node ids");
  }
  if (
    mappedScreenIds.length !== screenIds.size ||
    mappedScreenIds.some((nodeId) => !screenIds.has(nodeId))
  ) {
    throw new Error("Route map does not cover the complete Figma screen set");
  }

  const assetFiles = await readdir(resolve(deliveryRoot, "assets", "by-hash"));
  if (inventory.totals.uniqueAssetCount !== assetFiles.length) {
    throw new Error("Asset total does not match inventory");
  }
  if (assetInventory.length !== assetFiles.length) {
    throw new Error("Asset inventory length does not match files");
  }
  for (const asset of assetInventory) {
    const bytes = await readFile(resolve(deliveryRoot, asset.file));
    if (bytes.length !== asset.bytes) throw new Error(`Asset size mismatch: ${asset.file}`);
    if (sha256(bytes) !== asset.sha256) {
      throw new Error(`Asset checksum mismatch: ${asset.file}`);
    }
  }

  process.stdout.write(
    JSON.stringify(
      {
        valid: true,
        pages: pageIds.size,
        screens: screenCount,
        mappedScreens: mappedScreenIds.length,
        assets: assetFiles.length,
        contextBytes,
        screenshotBytes,
      },
      null,
      2,
    ) + "\n",
  );
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
});
