import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { visualScenarios } from "../e2e/visual-scenarios.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = JSON.parse(await readFile(path.resolve(projectRoot, "miniprogram/app.json"), "utf8"));
const registeredPages = new Set(app.pages ?? []);
const scenarioIds = new Set();
const coveredPages = new Set();

for (const scenario of visualScenarios) {
  assert.match(scenario.id, /^[a-z0-9][a-z0-9-]+$/u, `Invalid scenario id: ${scenario.id}`);
  assert.ok(!scenarioIds.has(scenario.id), `Duplicate visual scenario id: ${scenario.id}`);
  scenarioIds.add(scenario.id);

  const route = scenario.route.split("?", 1)[0].replace(/^\//u, "");
  assert.ok(registeredPages.has(route), `Visual scenario uses an unregistered page: ${route}`);
  coveredPages.add(route);
}

const missing = [...registeredPages].filter((route) => !coveredPages.has(route));
assert.deepEqual(
  missing,
  [],
  `Registered pages without a visual runtime scenario:\n${missing.join("\n")}`,
);

process.stdout.write(
  `Validated ${String(visualScenarios.length)} scenarios covering ${String(coveredPages.size)} registered pages.\n`,
);
