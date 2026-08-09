import { describe, expect, it, vi } from "vitest";

import { PrivateKnowledgeRequestOwner } from "../miniprogram/pages/private-knowledge/features/page-runtime/request-owner";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import pageSource from "../miniprogram/pages/private-knowledge/index.ts?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import pageStyles from "../miniprogram/pages/private-knowledge/index.wxss?raw";

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

  it("guards list, search, import and deletion polling at the page boundary", () => {
    expect(pageSource).toContain('beginRequest(this, "sources")');
    expect(pageSource).toContain('beginRequest(this, "search")');
    expect(pageSource).toContain('beginRequest(this, "import")');
    expect(pageSource).toContain('beginRequest(this, "delete")');
    expect(pageSource).toContain("pageRuntimes.get(this)?.requests.hide()");
    expect(pageSource).toContain("request.runtime.requests.delay(");
    expect(pageSource).toContain(
      "request.runtime.requests.owns(request.lease, currentAuthSession())",
    );
  });

  it("uses width-only compact, medium and wide layout boundaries", () => {
    expect(pageStyles).toContain("@media (min-width: 600px)");
    expect(pageStyles).toContain("@media (min-width: 1024px)");
    expect(pageStyles).not.toMatch(/min-width:\s*(700|768)px/u);
    expect(pageStyles).not.toContain("orientation:");
  });
});
