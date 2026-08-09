import { describe, expect, it } from "vitest";

import { PageEffectOwner } from "../miniprogram/app/runtime/page-effect-owner";

const sessionA = {
  userId: "user-a",
  accessToken: "token-a",
  generation: "generation-a",
};

describe("neutral page effect owner", () => {
  it("requires the exact visible user, token and auth generation", () => {
    const owner = new PageEffectOwner(sessionA);
    expect(owner.show(sessionA)).toBe(true);
    const lease = owner.begin("load", sessionA);

    expect(owner.owns(lease, sessionA)).toBe(true);
    expect(owner.owns(lease, { ...sessionA, userId: "user-b" })).toBe(false);
    expect(owner.owns(lease, { ...sessionA, accessToken: "token-b" })).toBe(false);
    expect(owner.owns(lease, { ...sessionA, generation: "generation-b" })).toBe(false);

    owner.hide();
    expect(owner.owns(lease, sessionA)).toBe(false);
  });

  it("keeps only the newest request per lane and invalidates every show/unload boundary", () => {
    const owner = new PageEffectOwner(sessionA);
    owner.show(sessionA);
    const first = owner.begin("load", sessionA);
    const latest = owner.begin("load", sessionA);

    expect(owner.owns(first, sessionA)).toBe(false);
    expect(owner.owns(latest, sessionA)).toBe(true);

    owner.show(sessionA);
    expect(owner.owns(latest, sessionA)).toBe(false);
    const afterShow = owner.begin("load", sessionA);
    expect(owner.owns(afterShow, sessionA)).toBe(true);

    owner.unload();
    expect(owner.owns(afterShow, sessionA)).toBe(false);
    expect(owner.show(sessionA)).toBe(false);
  });
});
