import { describe, expect, it, vi } from "vitest";

import { PrivateKnowledgeRequestOwner } from "../miniprogram/pages/private-knowledge/features/page-runtime/request-owner";

const sessionA = {
  userId: "user-a",
  accessToken: "token-a",
  generation: "generation-a",
};

describe("private knowledge request owner", () => {
  it("deduplicates repeated onShow and rejects cross-session late responses", () => {
    const owner = new PrivateKnowledgeRequestOwner(sessionA);
    owner.show(sessionA);
    const first = owner.begin("sources", sessionA);
    owner.show(sessionA);
    const latest = owner.begin("sources", sessionA);

    expect(owner.owns(first, sessionA)).toBe(false);
    expect(owner.owns(latest, sessionA)).toBe(true);
    expect(owner.owns(latest, { ...sessionA, accessToken: "token-b" })).toBe(false);
    expect(owner.owns(latest, { ...sessionA, generation: "generation-b" })).toBe(false);
    expect(owner.owns(latest, { ...sessionA, userId: "user-b" })).toBe(false);
  });

  it("clears deletion polling timers and resolves the wait as cancelled on hide/unload", async () => {
    vi.useFakeTimers();
    try {
      const owner = new PrivateKnowledgeRequestOwner(sessionA);
      owner.show(sessionA);
      const request = owner.begin("delete", sessionA);
      const hiddenWait = owner.delay(500, request, sessionA);

      owner.hide();
      await expect(hiddenWait).resolves.toBe(false);
      expect(vi.getTimerCount()).toBe(0);

      owner.show(sessionA);
      const nextRequest = owner.begin("delete", sessionA);
      const unloadedWait = owner.delay(500, nextRequest, sessionA);
      owner.unload();
      await expect(unloadedWait).resolves.toBe(false);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
