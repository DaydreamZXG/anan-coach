import { describe, expect, it, vi } from "vitest";

import type { TodayDashboard } from "../../../miniprogram/api/generated/types.gen";
import { loadAbilityGoal } from "../../../miniprogram/pages/ability/features/goal-load";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import abilitySource from "../../../miniprogram/pages/ability/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import ananSource from "../../../miniprogram/pages/anan/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import meSource from "../../../miniprogram/pages/me/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import weeklySource from "../../../miniprogram/plans/weekly-plan/index.ts?raw";

describe("workspace truth consistency happy path", () => {
  it("reads the authoritative plan in every workspace even when preference is unconfirmed", async () => {
    const dashboard = { planVersionId: "plan-026" } as TodayDashboard;
    const load = vi.fn(() => Promise.resolve(dashboard));

    await expect(loadAbilityGoal(load)).resolves.toEqual({ kind: "ready", dashboard });
    expect(load).toHaveBeenCalledTimes(1);

    expect(ananSource).toContain("loadTodayDashboard(client)");
    expect(abilitySource).toContain("new PlanningApi(client).getToday()");
    expect(meSource).toContain("new PlanningApi(client).getToday()");
    expect(weeklySource).toContain("new PlanningApi(client).getWeek()");
    for (const source of [ananSource, abilitySource, weeklySource]) {
      expect(source).not.toContain("studyPreferenceReady");
      expect(source).not.toContain("OnboardingApi");
    }
  });
});
