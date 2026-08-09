import { describe, expect, it } from "vitest";

import { MePageRequestOwner } from "../miniprogram/pages/me/features/request-owner";

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
});
