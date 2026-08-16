import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import abilitySource from "../../../miniprogram/pages/ability/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import ananSource from "../../../miniprogram/pages/anan/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import meSource from "../../../miniprogram/pages/me/index.ts?raw";

describe("workspace truth consistency stale ownership", () => {
  it("rejects hidden-page and previous-session responses before they update workspace truth", () => {
    expect(abilitySource).toContain("requestOwner.owns(request, currentAuthSession())");
    expect(ananSource).toContain("sameSessionOwner(currentAuthSession(), auth)");
    expect(ananSource).toContain("runtime.todayGeneration !== todayGeneration");
    expect(meSource).toContain("requestIsCurrent(this, request)");
    expect(meSource).toContain("request.runtime.owns(request.lease, currentAuthSession())");
  });
});
