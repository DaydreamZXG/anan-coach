import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const backendRouting = readFileSync("../../backend/app/domains/ai/governance/routing.py", "utf8");

const companionLaunchSources = [
  "miniprogram/essay/essay-review/index.ts",
  "miniprogram/pages/ability/index.ts",
  "miniprogram/pages/capture-import/index.ts",
  "miniprogram/pages/essay-task/index.ts",
  "miniprogram/pages/positions/detail/index.ts",
  "miniprogram/pages/reflection/index.ts",
  "miniprogram/plans/daily-settlement/index.ts",
  "miniprogram/plans/plan-basis/index.ts",
  "miniprogram/plans/weekly-plan/index.ts",
  "miniprogram/reports/error-pattern/index.ts",
];

describe("backend/frontend integration contracts", () => {
  it("accepts every direct Ask Anan launch route in the backend permission catalog", () => {
    const routes = companionLaunchSources.flatMap((path) =>
      [...readFileSync(path, "utf8").matchAll(/routeId:\s*"([^"]+)"/gu)]
        .map((match) => match[1] ?? "")
        .filter(Boolean),
    );

    expect(routes.length).toBeGreaterThan(0);
    expect(new Set(routes).size).toBe(routes.length);
    expect(routes.filter((route) => !backendRouting.includes(`"${route}"`))).toEqual([]);
  });

  it("materializes planTaskId only on task routes understood by the client", () => {
    const planning = readFileSync("../../backend/app/domains/planning/service.py", "utf8");
    const client = readFileSync("miniprogram/app/navigation/routes.ts", "utf8");
    const materializedRoutes = [
      ...planning.matchAll(/parts\.path not in \{([\s\S]*?)\n\s*\}/gu),
    ].flatMap((match) =>
      [...(match[1] ?? "").matchAll(/"([^"]+\/index)"/gu)].map((item) => item[1] ?? ""),
    );

    expect(new Set(materializedRoutes).size).toBe(materializedRoutes.length);
    expect(materializedRoutes.sort()).toEqual([
      "/pages/essay-task/index",
      "/pages/mock-exam/index",
      "/xingce/xingce-task-intro/index",
    ]);
    for (const route of materializedRoutes) {
      expect(client, `${route} is not registered by the Mini Program client`).toContain(route);
    }
  });
});
