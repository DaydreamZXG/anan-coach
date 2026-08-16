import { describe, expect, it, vi } from "vitest";

import type { TodayDashboard } from "../../../miniprogram/api/generated/types.gen";
import { loadAbilityGoal } from "../../../miniprogram/pages/ability/features/goal-load";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import ananSource from "../../../miniprogram/pages/anan/index.ts?raw";

describe("workspace truth consistency replay", () => {
  it("replaying an incomplete progress response cannot reinstate a plan gate", async () => {
    const dashboard = { planVersionId: "plan-replay-026" } as TodayDashboard;
    const load = vi.fn(() => Promise.resolve(dashboard));

    await loadAbilityGoal(load);
    await loadAbilityGoal(load);

    expect(load).toHaveBeenCalledTimes(2);
    expect(ananSource).not.toContain("loadTodayDashboardIfReady");
    expect(ananSource).not.toContain("先把每天的学习时间定下来");
  });
});
