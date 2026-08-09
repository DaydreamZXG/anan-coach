import { describe, expect, it } from "vitest";

import { MePageRequestOwner } from "../miniprogram/pages/me/features/request-owner";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import pageSource from "../miniprogram/pages/me/index.ts?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import pageStyles from "../miniprogram/pages/me/index.wxss?raw";

const sessionA = {
  userId: "user-a",
  accessToken: "token-a",
  generation: "generation-a",
};

describe("me page request owner", () => {
  it("lets repeated onShow keep only the newest request per page instance", () => {
    const owner = new MePageRequestOwner(sessionA);
    owner.show(sessionA);
    const first = owner.begin("account", sessionA);

    owner.show(sessionA);
    const second = owner.begin("account", sessionA);

    expect(owner.owns(first, sessionA)).toBe(false);
    expect(owner.owns(second, sessionA)).toBe(true);
  });

  it("rejects same-user token/generation changes, account switches, hide and unload", () => {
    const owner = new MePageRequestOwner(sessionA);
    owner.show(sessionA);
    const request = owner.begin("profile-context", sessionA);

    expect(owner.owns(request, { ...sessionA, accessToken: "token-b" })).toBe(false);
    expect(owner.owns(request, { ...sessionA, generation: "generation-b" })).toBe(false);
    expect(owner.owns(request, { ...sessionA, userId: "user-b" })).toBe(false);

    owner.hide();
    expect(owner.owns(request, sessionA)).toBe(false);
    owner.show(sessionA);
    const afterShow = owner.begin("profile-context", sessionA);
    owner.unload();
    expect(owner.owns(afterShow, sessionA)).toBe(false);
    expect(owner.show(sessionA)).toBe(false);
  });

  it("guards page requests and destroys the owner with the page instance", () => {
    expect(pageSource).toContain('beginRequest(this, "account")');
    expect(pageSource).toContain('beginRequest(this, "profile-context")');
    expect(pageSource).toContain("requestOwners.get(this)?.hide()");
    expect(pageSource).toContain("requestOwners.get(this)?.unload()");
    expect(pageSource).toContain("request.runtime.owns(request.lease, currentAuthSession())");
  });

  it("uses width-only compact, medium and wide layout boundaries", () => {
    expect(pageStyles).toContain("@media (min-width: 600px)");
    expect(pageStyles).toContain("@media (min-width: 1024px)");
    expect(pageStyles).not.toMatch(/min-width:\s*(700|768)px/u);
    expect(pageStyles).not.toContain("orientation:");
  });
});
