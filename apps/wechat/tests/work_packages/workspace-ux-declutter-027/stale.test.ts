import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../../../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import todayControllerSource from "../../../miniprogram/features/planning/today-controller.ts?raw";

describe("workspace UX declutter stale ownership", () => {
  it("leaves authoritative task navigation and coach evidence ownership intact", () => {
    expect(todayTemplate).toContain('data-id="{{item.id}}"');
    expect(todayTemplate).toContain('bindtap="openTask"');
    expect(todayTemplate).toContain("{{view.loop.observation}}");
    expect(todayTemplate).toContain("{{view.loop.judgment}}");
    expect(todayTemplate).toContain("{{view.loop.verification}}");
    expect(todayTemplate).toContain("{{view.loop.correction}}");
    expect(todayControllerSource).toContain("dashboardOwnerSessionKey");
    expect(todayControllerSource).toContain("isRequestCurrent");
  });
});
