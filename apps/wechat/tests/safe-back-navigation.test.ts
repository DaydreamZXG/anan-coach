import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routes } from "../miniprogram/app/navigation/routes";
import { navigateBackOr } from "../miniprogram/plans/shared/app/navigation/safe-back";

let navigateBack: ReturnType<typeof vi.fn>;
let redirectTo: ReturnType<typeof vi.fn>;
let switchTab: ReturnType<typeof vi.fn>;

beforeEach(() => {
  navigateBack = vi.fn();
  redirectTo = vi.fn();
  switchTab = vi.fn();
  vi.stubGlobal("wx", { navigateBack, redirectTo, switchTab });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("safe back navigation", () => {
  it("returns through the existing page stack", () => {
    vi.stubGlobal("getCurrentPages", () => [{}, {}]);

    navigateBackOr(routes.today);

    expect(navigateBack).toHaveBeenCalledTimes(1);
    expect(switchTab).not.toHaveBeenCalled();
    expect(redirectTo).not.toHaveBeenCalled();
  });

  it("switches to a tab fallback when the current page is the entry page", () => {
    vi.stubGlobal("getCurrentPages", () => [{}]);

    navigateBackOr(routes.me);

    expect(navigateBack).not.toHaveBeenCalled();
    expect(switchTab).toHaveBeenCalledWith({ url: routes.me });
    expect(redirectTo).not.toHaveBeenCalled();
  });

  it("replaces the entry page with a non-tab fallback", () => {
    vi.stubGlobal("getCurrentPages", () => [{}]);

    navigateBackOr(routes.privateKnowledge);

    expect(navigateBack).not.toHaveBeenCalled();
    expect(redirectTo).toHaveBeenCalledWith({ url: routes.privateKnowledge });
    expect(switchTab).not.toHaveBeenCalled();
  });

  it("uses the same fallback if navigateBack reports a runtime failure", () => {
    vi.stubGlobal("getCurrentPages", () => [{}, {}]);
    navigateBack.mockImplementation(({ fail }: { fail?: () => void }) => fail?.());

    navigateBackOr(routes.today);

    expect(switchTab).toHaveBeenCalledWith({ url: routes.today });
  });
});
