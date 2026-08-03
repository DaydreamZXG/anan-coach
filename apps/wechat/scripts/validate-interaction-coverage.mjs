import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  interactionEvidenceKinds,
  pageInteractionMatrix,
} from "../e2e/page-interaction-matrix.mjs";
import { visualScenarios } from "../e2e/visual-scenarios.mjs";
import { assertBusinessCoverage, routeInventory } from "./app-pages.mjs";
import {
  assertSelectorBindsHandler,
  interactionEventKinds,
} from "./interaction-contract-identity.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const miniProgramRoot = path.resolve(projectRoot, "miniprogram");
const app = JSON.parse(await readFile(path.resolve(miniProgramRoot, "app.json"), "utf8"));
const routeManifest = JSON.parse(
  await readFile(path.resolve(miniProgramRoot, "app/navigation/routes.manifest.json"), "utf8"),
);
const inventory = routeInventory(app, routeManifest);
const scenarioById = new Map(visualScenarios.map((scenario) => [scenario.id, scenario]));
const matrixByPage = new Map();
const allowedEvidence = new Set(Object.values(interactionEvidenceKinds));
let bindingControllerCount = 0;
let readOnlyCount = 0;
let platformDeviceCount = 0;

async function readTemplateClosure(templatePath, seen = new Set()) {
  const absolutePath = path.resolve(templatePath);
  if (seen.has(absolutePath)) return "";
  seen.add(absolutePath);

  const template = await readFile(absolutePath, "utf8");
  const includes = Array.from(
    template.matchAll(/<include\s+[^>]*src=["']([^"']+)["'][^>]*\/?\s*>/gu),
  );
  const includedTemplates = await Promise.all(
    includes.map((match) =>
      readTemplateClosure(path.resolve(path.dirname(absolutePath), match[1]), seen),
    ),
  );
  return [template, ...includedTemplates].join("\n");
}

for (const contract of pageInteractionMatrix) {
  assert.ok(
    inventory.allRoutes.includes(contract.route),
    `Unknown interaction page: ${contract.route}`,
  );
  assert.ok(!matrixByPage.has(contract.route), `Duplicate interaction page: ${contract.route}`);
  matrixByPage.set(contract.route, contract);

  const scenario = scenarioById.get(contract.scenarioId);
  assert.ok(scenario, `${contract.route} references unknown scenario ${contract.scenarioId}`);
  assert.equal(
    scenario.route.split("?", 1)[0].replace(/^\//u, ""),
    contract.route,
    `${contract.route} uses a scenario from another page`,
  );

  const interaction = contract.primaryInteraction;
  assert.ok(
    allowedEvidence.has(interaction.evidence),
    `${contract.route} has invalid evidence kind`,
  );
  if (interaction.handler) {
    assert.ok(
      Object.values(interactionEventKinds).includes(interaction.eventKind),
      `${contract.route} has unsupported interaction event kind: ${interaction.eventKind}`,
    );
  }
  const wxml = await readTemplateClosure(path.resolve(miniProgramRoot, `${contract.route}.wxml`));
  const controllerPath = path.resolve(miniProgramRoot, `${contract.route}.ts`);
  const controller = await readControllerClosure(controllerPath);

  if (interaction.evidence === interactionEvidenceKinds.RUNTIME_READ_ONLY) {
    assert.equal(
      interaction.handler,
      null,
      `${contract.route} read-only contract declares a handler`,
    );
    readOnlyCount += 1;
    continue;
  }

  assert.ok(interaction.handler, `${contract.route} has no primary handler`);
  assert.ok(interaction.selector, `${contract.route} has no primary selector`);
  assertSelectorBindsHandler({
    route: contract.route,
    wxml,
    selector: interaction.selector,
    handler: interaction.handler,
    eventKind: interaction.eventKind,
  });
  if (interaction.within) {
    assert.match(
      wxml,
      new RegExp(`<${escapeRegExp(interaction.within)}(?:\\s|>)`, "u"),
      `${contract.route} WXML does not expose interaction container ${interaction.within}`,
    );
  }
  assert.match(
    controller,
    new RegExp(`\\b${escapeRegExp(interaction.handler)}\\s*\\(`, "u"),
    `${contract.route} controller does not implement ${interaction.handler}`,
  );
  if (interaction.evidence === interactionEvidenceKinds.PLATFORM_DEVICE) platformDeviceCount += 1;
  else bindingControllerCount += 1;
}

assertBusinessCoverage(inventory, matrixByPage.keys(), "Interaction coverage");

process.stdout.write(
  `Validated ${String(pageInteractionMatrix.length)} page interaction contracts: ` +
    `${String(bindingControllerCount)} binding/controller, ${String(readOnlyCount)} read-only, ` +
    `${String(platformDeviceCount)} platform-device; all registered pages are business routes.\n`,
);

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

async function readControllerClosure(entryPath, visited = new Set()) {
  if (visited.has(entryPath)) return "";
  visited.add(entryPath);
  const source = await readFile(entryPath, "utf8");
  const dependencies = [];
  for (const match of source.matchAll(/from\s+["'](\.{1,2}\/[^"']+)["']/gu)) {
    const candidate = path.resolve(path.dirname(entryPath), `${match[1]}.ts`);
    try {
      dependencies.push(await readControllerClosure(candidate, visited));
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return `${source}\n${dependencies.join("\n")}`;
}
