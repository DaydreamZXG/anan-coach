import { afterEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error Vite exposes TypeScript source through its raw asset loader.
import todayPageSource from "../../../miniprogram/pages/today/index.ts?raw";
import { apiFailure, capacityCommand, createTodayHarness, latestState } from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("VJ-01 real Today R2 AS-06", () => {
  it("claims the write before awaiting entropy so repeated taps issue one command", async () => {
    let resolveKey!: (value: string) => void;
    const key = new Promise<string>((resolve) => {
      resolveKey = resolve;
    });
    const harness = createTodayHarness({ createIdempotencyKey: () => key });
    await harness.controller.load();
    const command = capacityCommand("capacity-version-1");

    const first = harness.controller.submitCapacity(command);
    const second = harness.controller.submitCapacity(command);
    await Promise.resolve();

    expect(harness.spies.createIdempotencyKey).toHaveBeenCalledOnce();
    expect(harness.spies.setCapacity).not.toHaveBeenCalled();
    resolveKey("single-capacity-key");
    await Promise.all([first, second]);

    expect(harness.spies.setCapacity).toHaveBeenCalledOnce();
    expect(harness.spies.setCapacity).toHaveBeenCalledWith(
      "server-token-a",
      command,
      "single-capacity-key",
    );
  });

  it("never fabricates a plan when the first server read fails", async () => {
    const harness = createTodayHarness();
    harness.spies.getToday.mockRejectedValueOnce(
      apiFailure(503, "service_unavailable", "today-initial-failure"),
    );

    await harness.controller.load();

    expect(latestState(harness.states)).toMatchObject({
      status: "error",
      dashboard: null,
      capacitySubmission: "idle",
      capacityMessage: "",
    });
    expect(harness.states.some((state) => state.capacitySubmission === "success")).toBe(false);
  });

  it("keeps the confirmed plan and idempotency key across a failed write retry", async () => {
    const harness = createTodayHarness();
    await harness.controller.load();
    harness.spies.setCapacity.mockRejectedValueOnce(
      apiFailure(503, "service_unavailable", "capacity-server-failure"),
    );
    const command = capacityCommand("capacity-version-1");

    await harness.controller.submitCapacity(command);

    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: { planVersionId: "server-plan-version-1" },
      capacitySubmission: "error",
      capacityConflictLocked: false,
    });
    expect(latestState(harness.states).capacityMessage).not.toContain("已确认");

    await harness.controller.submitCapacity(command);

    expect(harness.spies.setCapacity).toHaveBeenCalledTimes(2);
    expect(harness.spies.setCapacity.mock.calls[0]?.[2]).toBe(
      harness.spies.setCapacity.mock.calls[1]?.[2],
    );
    expect(latestState(harness.states).capacitySubmission).toBe("success");

    const committedPlan = latestState(harness.states).dashboard;
    harness.spies.getToday.mockRejectedValueOnce(
      apiFailure(503, "service_unavailable", "today-reload-failure"),
    );
    await harness.controller.load();

    expect(latestState(harness.states).dashboard).toEqual(committedPlan);
    expect(latestState(harness.states).capacitySubmission).not.toBe("success");
    expect(latestState(harness.states).capacityMessage).toContain("上次确认的计划");
  });

  it("keeps capacity writes out of the Today page after adjustment moved to its own route", () => {
    expect(todayPageSource).toContain("routes.dayAdjustment");
    expect(todayPageSource).not.toContain("submitCapacity()");
    expect(todayPageSource).not.toContain("changeCapacityDraft");
  });
});
