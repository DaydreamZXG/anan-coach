import { describe, expect, it, vi } from "vitest";

import type { TodayDashboard } from "../../../miniprogram/api/generated/types.gen";
import { ApiClientError } from "../../../miniprogram/api/client";
import { loadAbilityGoal } from "../../../miniprogram/pages/ability/features/goal-load";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import ananSource from "../../../miniprogram/pages/anan/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import meSource from "../../../miniprogram/pages/me/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import weeklySource from "../../../miniprogram/plans/weekly-plan/index.ts?raw";

describe("workspace truth consistency recovery", () => {
  it("restores the real plan on retry without changing preference confirmation", async () => {
    const dashboard = { planVersionId: "plan-recovered-026" } as TodayDashboard;
    const load = vi
      .fn<() => Promise<TodayDashboard>>()
      .mockRejectedValueOnce(new ApiClientError("temporary", 503, "req-temporary"))
      .mockResolvedValueOnce(dashboard);

    await expect(loadAbilityGoal(load)).resolves.toMatchObject({ kind: "failed" });
    await expect(loadAbilityGoal(load)).resolves.toEqual({ kind: "ready", dashboard });

    expect(ananSource).toContain("retryTodayContext()");
    expect(meSource).toContain("retryProfileContext()");
    expect(weeklySource).toContain("retry()");
    expect(meSource).toContain('studyModeLabel: "岸岸暂排"');
    expect(meSource).toContain("studyPreferenceReady: false");
  });
});
