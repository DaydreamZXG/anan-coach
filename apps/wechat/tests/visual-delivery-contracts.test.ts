import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import routeMapJson from "../design/figma-delivery/route-map.json";
// @ts-expect-error JavaScript visual fixtures intentionally run in Node and do not ship declarations.
import { visualDeliveryContracts } from "../e2e/visual-delivery-contracts.mjs";
// @ts-expect-error JavaScript visual fixtures intentionally run in Node and do not ship declarations.
import { visualScenarios } from "../e2e/visual-scenarios.mjs";

interface RouteMapping {
  figmaNodeId: string;
  route: string;
  routeState?: string;
}

interface DeliveryContract extends RouteMapping {
  scenarioId: string;
  semanticSelector: string;
  requiredText: string[];
  primarySelector?: string;
}

interface VisualScenario {
  id: string;
  route: string;
  data: Record<string, unknown>;
}

const mappings = routeMapJson.mappings as RouteMapping[];
const contracts = visualDeliveryContracts as DeliveryContract[];
const scenarios = visualScenarios as VisualScenario[];

describe("Figma delivery runtime visual contracts", () => {
  it("binds every Figma screen to exactly one runtime scenario", () => {
    expect(contracts).toHaveLength(mappings.length);
    expect(new Set(contracts.map((contract) => contract.figmaNodeId)).size).toBe(mappings.length);
    expect(
      contracts
        .map((contract) => contract.figmaNodeId)
        .sort((left, right) => left.localeCompare(right)),
    ).toEqual(
      mappings
        .map((mapping) => mapping.figmaNodeId)
        .sort((left, right) => left.localeCompare(right)),
    );
  });

  it("keeps route and routeState identity aligned with route-map.json", () => {
    const contractByNode = new Map(
      contracts.map((contract) => [contract.figmaNodeId, contract] as const),
    );
    const mismatches = mappings.flatMap((mapping) => {
      const contract = contractByNode.get(mapping.figmaNodeId);
      if (!contract) return [`${mapping.figmaNodeId}：缺少运行时契约`];
      const issues: string[] = [];
      if (contract.route !== mapping.route) {
        issues.push(`${mapping.figmaNodeId}：路由 ${contract.route} != ${mapping.route}`);
      }
      if ((contract.routeState ?? "") !== (mapping.routeState ?? "")) {
        issues.push(
          `${mapping.figmaNodeId}：相态 ${contract.routeState ?? "无"} != ${mapping.routeState ?? "无"}`,
        );
      }
      return issues;
    });
    expect(mismatches).toEqual([]);
  });

  it("requires each contract to use a real scenario and semantic assertions", () => {
    const scenarioById = new Map(scenarios.map((scenario) => [scenario.id, scenario] as const));
    const problems = contracts.flatMap((contract) => {
      const scenario = scenarioById.get(contract.scenarioId);
      if (!scenario) return [`${contract.figmaNodeId}：场景 ${contract.scenarioId} 不存在`];
      const problemsForContract: string[] = [];
      const scenarioRoute = scenario.route.split("?", 1)[0] ?? "";
      if (scenarioRoute !== contract.route) {
        problemsForContract.push(
          `${contract.figmaNodeId}：场景路由 ${scenarioRoute} != ${contract.route}`,
        );
      }
      if (!contract.semanticSelector.startsWith(".")) {
        problemsForContract.push(`${contract.figmaNodeId}：缺少明确的相态选择器`);
      }
      if (
        contract.requiredText.length < 2 ||
        contract.requiredText.some((text) => text.trim().length === 0)
      ) {
        problemsForContract.push(`${contract.figmaNodeId}：至少需要两条非空可见文本断言`);
      }
      return problemsForContract;
    });
    expect(problems).toEqual([]);
  });

  it("does not reuse one scenario as evidence for different Figma screens", () => {
    const reused = contracts
      .map((contract) => contract.scenarioId)
      .filter((scenarioId, index, all) => all.indexOf(scenarioId) !== index);
    expect(reused).toEqual([]);
  });

  it("keeps the practice annotation scenario aligned with the current writable canvas contract", () => {
    const scenario = scenarios.find(({ id }) => id === "c13-xingce-annotation");
    const data = scenario?.data as
      | { annotationMode?: unknown; annotationTool?: unknown; questionAnnotations?: unknown[] }
      | undefined;

    expect(data?.annotationMode).toBe("annotate");
    expect(data?.annotationTool).toBe("pen");
    expect(data?.questionAnnotations?.length).toBeGreaterThan(0);
  });

  it("keeps daily settlement mock evidence traceable before opening 岸岸", () => {
    const scenario = scenarios.find(({ id }) => id === "b04-daily-settlement-ready");
    const settlement = (scenario?.data as { settlement?: Record<string, unknown> } | undefined)
      ?.settlement;

    expect(settlement?.localDate).toBeTruthy();
    expect(settlement?.planVersionId).toBeTruthy();
    expect(settlement?.planVersionNo).toBeGreaterThan(0);
  });

  it("keeps every required visible phrase in the production WXML or its deterministic scenario", () => {
    const scenarioById = new Map(scenarios.map((scenario) => [scenario.id, scenario] as const));
    const missing = contracts.flatMap((contract) => {
      const scenario = scenarioById.get(contract.scenarioId);
      if (!scenario) return [];
      const wxmlPath = resolve(
        process.cwd(),
        "miniprogram",
        `${contract.route.replace(/^\/+/u, "")}.wxml`,
      );
      const evidence = normalizeEvidence(
        `${readFileSync(wxmlPath, "utf8")}\n${JSON.stringify(scenario.data)}`,
      );
      return contract.requiredText
        .filter((phrase) => !evidence.includes(normalizeEvidence(phrase)))
        .map(
          (phrase) =>
            `${contract.figmaNodeId} / ${contract.scenarioId}：正式 WXML 与场景数据均缺少“${phrase}”`,
        );
    });
    expect(missing).toEqual([]);
  });
});

function normalizeEvidence(value: string): string {
  return value
    .replace(/\s+/gu, "")
    .replace(/\\u([0-9a-f]{4})/giu, (_, code: string) =>
      String.fromCharCode(Number.parseInt(code, 16)),
    );
}
