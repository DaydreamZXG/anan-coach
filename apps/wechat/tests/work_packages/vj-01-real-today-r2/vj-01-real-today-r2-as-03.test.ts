import { afterEach, describe, expect, it, vi } from "vitest";

import {
  consumeLatestTodayDashboard,
  publishLatestTodayDashboard,
} from "../../../miniprogram/features/planning/runtime-state";
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

describe("VJ-01 real Today R2 AS-03", () => {
  it("wires an unauthenticated Today page to the real login return route", async () => {
    let captured: unknown;
    const reLaunch = vi.fn(() => Promise.resolve());
    const request = vi.fn();
    vi.stubGlobal("Page", (definition: unknown) => {
      captured = definition;
    });
    vi.stubGlobal("getApp", () => ({ globalData: {} }));
    vi.stubGlobal("wx", {
      getStorageSync: () => "",
      pageScrollTo: vi.fn(() => Promise.resolve()),
      removeStorageSync: vi.fn(),
      reLaunch,
      request,
    });

    await import("../../../miniprogram/pages/today/index");
    const page = instantiatePage(captured as CapturedTodayPage);
    page.onLoad.call(page);
    page.onShow.call(page);
    await Promise.resolve();

    expect(reLaunch).toHaveBeenCalledWith({
      url: "/pages/login/index?returnTo=%2Fpages%2Ftoday%2Findex",
    });
    expect(request).not.toHaveBeenCalled();
    page.onUnload.call(page);
  });

  it("routes an absent session to real authentication without reading user data", async () => {
    const harness = createTodayHarness();
    harness.session.current = undefined;

    await harness.controller.load();

    expect(harness.spies.clearSession).toHaveBeenCalledOnce();
    expect(harness.spies.reauthenticate).toHaveBeenCalledOnce();
    expect(harness.spies.getProgress).not.toHaveBeenCalled();
    expect(harness.spies.getToday).not.toHaveBeenCalled();
    expect(latestState(harness.states).dashboard).toBeNull();
  });

  it("clears an expired Today session and routes to real reauthentication", async () => {
    const harness = createTodayHarness({
      getProgress: () =>
        Promise.reject(apiFailure(401, "authentication_required", "expired-session")),
    });

    await harness.controller.load();

    expect(harness.spies.clearSession).toHaveBeenCalledOnce();
    expect(harness.spies.reauthenticate).toHaveBeenCalledOnce();
    expect(harness.spies.getToday).not.toHaveBeenCalled();
    expect(latestState(harness.states)).toMatchObject({
      status: "redirecting",
      dashboard: null,
      capacitySubmission: "idle",
    });
  });

  it("contains a failed real-login redirect as a recoverable empty state", async () => {
    const harness = createTodayHarness({
      reauthenticate: () => Promise.reject(new Error("reLaunch rejected")),
    });
    harness.session.current = undefined;

    await expect(harness.controller.load()).resolves.toBeUndefined();

    expect(harness.spies.clearSession).toHaveBeenCalledOnce();
    expect(harness.spies.reauthenticate).toHaveBeenCalledOnce();
    expect(harness.spies.getProgress).not.toHaveBeenCalled();
    expect(harness.spies.getToday).not.toHaveBeenCalled();
    const failedRedirect = latestState(harness.states);
    expect(failedRedirect.status).toBe("error");
    expect(failedRedirect.dashboard).toBeNull();
    expect(failedRedirect.errorMessage).toContain("重新登录");
    expect(failedRedirect.capacitySubmission).toBe("idle");
  });

  it("does not let an old login redirect rejection overwrite a new account load", async () => {
    let rejectReauthentication!: (reason?: unknown) => void;
    const reauthentication = new Promise<void>((_resolve, reject) => {
      rejectReauthentication = reject;
    });
    const harness = createTodayHarness({ reauthenticate: () => reauthentication });
    harness.session.current = undefined;

    const anonymousLoad = harness.controller.load();
    await vi.waitFor(() => {
      expect(harness.spies.reauthenticate).toHaveBeenCalledOnce();
    });

    harness.session.current = { accessToken: "server-token-b", userId: "user-b" };
    harness.spies.getToday.mockResolvedValueOnce(
      serverDashboard("user-b-after-redirect", "user-b-capacity"),
    );
    await harness.controller.load();

    rejectReauthentication(new Error("old reLaunch rejected"));
    await expect(anonymousLoad).resolves.toBeUndefined();

    const currentAccountState = latestState(harness.states);
    expect(currentAccountState.status).toBe("ready");
    expect(currentAccountState.dashboard?.planVersionId).toBe("user-b-after-redirect");
    expect(currentAccountState.errorMessage).toBe("");
    expect(
      harness.states.every(
        (state) => state.status !== "error" || !state.errorMessage.includes("重新登录"),
      ),
    ).toBe(true);
  });

  it("keeps authentication redirect authoritative when an older Today reload finishes late", async () => {
    const harness = createTodayHarness();
    await harness.controller.load();

    let resolveStaleReload!: (dashboard: ReturnType<typeof serverDashboard>) => void;
    const staleDashboard = new Promise<ReturnType<typeof serverDashboard>>((resolve) => {
      resolveStaleReload = resolve;
    });
    harness.spies.getToday.mockReturnValueOnce(staleDashboard);
    const staleReload = harness.controller.load();
    await vi.waitFor(() => {
      expect(harness.spies.getToday).toHaveBeenCalledTimes(2);
    });

    harness.spies.setCapacity.mockRejectedValueOnce(
      apiFailure(401, "authentication_required", "expired-capacity-session"),
    );
    await harness.controller.submitCapacity(capacityCommand());

    resolveStaleReload(serverDashboard("stale-plan", "stale-capacity"));
    await staleReload;

    expect(harness.spies.clearSession).toHaveBeenCalledOnce();
    expect(harness.spies.reauthenticate).toHaveBeenCalledOnce();
    expect(latestState(harness.states)).toMatchObject({
      status: "redirecting",
      dashboard: null,
      capacitySubmission: "idle",
    });
    expect(harness.states.some((state) => state.dashboard?.planVersionId === "stale-plan")).toBe(
      false,
    );
  });

  it("starts a new account load and discards the previous account's late response", async () => {
    const harness = createTodayHarness();
    let resolveUserA!: (dashboard: ReturnType<typeof serverDashboard>) => void;
    const userADashboard = new Promise<ReturnType<typeof serverDashboard>>((resolve) => {
      resolveUserA = resolve;
    });
    harness.spies.getToday
      .mockReturnValueOnce(userADashboard)
      .mockResolvedValueOnce(serverDashboard("user-b-plan", "user-b-capacity"));

    const userALoad = harness.controller.load();
    await vi.waitFor(() => {
      expect(harness.spies.getToday).toHaveBeenCalledWith("server-token-a");
    });

    harness.session.current = { accessToken: "server-token-b", userId: "user-b" };
    const userBLoad = harness.controller.load();
    await userBLoad;

    resolveUserA(serverDashboard("user-a-late-plan", "user-a-late-capacity"));
    await userALoad;

    expect(harness.spies.getProgress).toHaveBeenCalledWith("server-token-b");
    expect(harness.spies.getToday).toHaveBeenCalledWith("server-token-b");
    expect(latestState(harness.states).dashboard?.planVersionId).toBe("user-b-plan");
    expect(
      harness.states.some((state) => state.dashboard?.planVersionId === "user-a-late-plan"),
    ).toBe(false);
  });

  it("never exposes a cached plan to another account or to an unauthenticated process", () => {
    const globalData: { accessToken?: string; userId?: string } = {
      accessToken: "token-a",
      userId: "user-a",
    };
    vi.stubGlobal("getApp", () => ({ globalData }));
    vi.stubGlobal("wx", {
      getStorageSync: () => "",
      removeStorageSync: vi.fn(),
    });

    publishLatestTodayDashboard(serverDashboard("plan-a"));
    globalData.accessToken = "token-b";
    globalData.userId = "user-b";
    expect(consumeLatestTodayDashboard()).toBeNull();

    globalData.accessToken = "token-a";
    globalData.userId = "user-a";
    publishLatestTodayDashboard(serverDashboard("plan-a-again"));
    delete globalData.accessToken;
    delete globalData.userId;
    expect(consumeLatestTodayDashboard()).toBeNull();
  });
});

interface CapturedTodayPage {
  data: Record<string, unknown>;
  onLoad(this: TestTodayPage): void;
  onShow(this: TestTodayPage): void;
  onUnload(this: TestTodayPage): void;
}

interface TestTodayPage extends CapturedTodayPage {
  setData(patch: Record<string, unknown>): void;
}

function instantiatePage(definition: CapturedTodayPage): TestTodayPage {
  return {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)) as Record<string, unknown>,
    setData(patch) {
      Object.assign(this.data, patch);
    },
  };
}
