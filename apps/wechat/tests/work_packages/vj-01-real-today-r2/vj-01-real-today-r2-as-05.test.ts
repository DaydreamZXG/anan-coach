import { afterEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error Vite exposes TypeScript source through its raw asset loader.
import todayPageSource from "../../../miniprogram/pages/today/index.ts?raw";
// @ts-expect-error Vite exposes WXML source through its raw asset loader.
import todayPageTemplate from "../../../miniprogram/pages/today/index.wxml?raw";
import {
  apiFailure,
  capacityCommand,
  createTodayHarness,
  latestState,
  serverDashboard,
} from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("VJ-01 real Today R2 AS-05", () => {
  it("locks a stale capacity revision until the server returns a newer version", async () => {
    const harness = createTodayHarness();
    await harness.controller.load();
    harness.spies.setCapacity.mockRejectedValueOnce(
      apiFailure(409, "planning_revision_conflict", "capacity-conflict"),
    );

    await harness.controller.submitCapacity(capacityCommand("capacity-version-1"));

    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: { planVersionId: "server-plan-version-1" },
      capacitySubmission: "conflict",
      capacityConflictLocked: true,
    });
    expect(harness.spies.setCapacity).toHaveBeenCalledOnce();

    harness.controller.acceptLatestDashboard(
      serverDashboard("same-conflicted-plan", "capacity-version-1"),
    );
    expect(latestState(harness.states)).toMatchObject({
      capacitySubmission: "conflict",
      capacityConflictLocked: true,
    });

    await harness.controller.submitCapacity(capacityCommand("capacity-version-1"));
    expect(harness.spies.setCapacity).toHaveBeenCalledOnce();

    harness.spies.getToday.mockResolvedValueOnce(
      serverDashboard("server-plan-version-2", "capacity-version-2"),
    );
    await harness.controller.load();

    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: {
        planVersionId: "server-plan-version-2",
        capacity: { capacityVersionId: "capacity-version-2" },
      },
      capacitySubmission: "idle",
      capacityConflictLocked: false,
    });

    await harness.controller.submitCapacity(capacityCommand("capacity-version-2"));
    expect(harness.spies.setCapacity).toHaveBeenCalledTimes(2);
    expect(latestState(harness.states).capacitySubmission).toBe("success");
  });

  it("renders controller capacity failures without reviving the retired inline editor", () => {
    expect(todayPageTemplate).toContain(
      "capacityMessage && (capacitySubmission === 'conflict' || capacitySubmission === 'error')",
    );
    expect(todayPageTemplate).toContain('bindtap="retry"');
    expect(todayPageSource).not.toContain("toggleCapacityEditor");
    expect(todayPageSource).not.toContain("reloadAfterCapacityConflict");
  });
});
