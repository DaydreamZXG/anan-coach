import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import ananSourceRaw from "../../../miniprogram/pages/anan/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import meSourceRaw from "../../../miniprogram/pages/me/index.ts?raw";

const ananSource = rawAsset(ananSourceRaw);
const meSource = rawAsset(meSourceRaw);

function rawAsset(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source;
}

describe("workspace truth consistency response races", () => {
  it("starts plan reads independently instead of waiting for onboarding readiness", () => {
    const mePlanRead = meSource.indexOf("new PlanningApi(client).getToday()");
    const mePreferenceBranch = meSource.indexOf("if (!progress.value.data.studyPreferenceReady)");

    expect(mePlanRead).toBeGreaterThan(-1);
    expect(mePreferenceBranch).toBeGreaterThan(mePlanRead);
    expect(ananSource).toContain("Promise.allSettled([");
    expect(ananSource).toContain("loadTodayDashboard(client)");
    expect(ananSource).not.toContain("getProgress()");
  });
});
