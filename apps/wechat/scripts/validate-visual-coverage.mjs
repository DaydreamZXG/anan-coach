import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { visualScenarios } from "../e2e/visual-scenarios.mjs";
import { assertBusinessCoverage, routeInventory } from "./app-pages.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const miniprogramRoot = path.resolve(projectRoot, "miniprogram");
const app = JSON.parse(await readFile(path.join(miniprogramRoot, "app.json"), "utf8"));
const routeManifest = JSON.parse(
  await readFile(path.join(miniprogramRoot, "app/navigation/routes.manifest.json"), "utf8"),
);
const inventory = routeInventory(app, routeManifest);
const scenarioIds = new Set();
const coveredPages = new Set();

for (const scenario of visualScenarios) {
  assert.match(scenario.id, /^[a-z0-9][a-z0-9-]+$/u, `Invalid scenario id: ${scenario.id}`);
  assert.ok(!scenarioIds.has(scenario.id), `Duplicate visual scenario id: ${scenario.id}`);
  scenarioIds.add(scenario.id);

  const route = scenario.route.split("?", 1)[0].replace(/^\//u, "");
  assert.ok(
    inventory.allRoutes.includes(route),
    `Visual scenario uses an unregistered page: ${route}`,
  );
  coveredPages.add(route);
}

assertBusinessCoverage(inventory, coveredPages, "Visual coverage");

process.stdout.write(
  `Validated ${String(visualScenarios.length)} scenarios covering ${String(coveredPages.size)} business pages; ` +
    `${String(inventory.assetEntryRoutes.length)} asset entries are N/A.\n`,
);
