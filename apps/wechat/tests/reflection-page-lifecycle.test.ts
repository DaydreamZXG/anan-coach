import { describe, expect, it, vi } from "vitest";

import {
  ReflectionSessionLifecycle,
  type ReflectionActivityCleanup,
} from "../miniprogram/pages/reflection/features/session-owner";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import pageSource from "../miniprogram/pages/reflection/index.ts?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import pageStyles from "../miniprogram/pages/reflection/index.wxss?raw";

const sessionA = {
  userId: "user-a",
  accessToken: "token-a",
  generation: "generation-a",
};

describe("reflection page session lifecycle", () => {
  it("binds every request and media effect to user, token, generation and visibility", () => {
    const lifecycle = new ReflectionSessionLifecycle(sessionA);
    const request = lifecycle.beginRequest(sessionA);
    const media = lifecycle.beginMedia(sessionA);

    expect(request).not.toBeNull();
    expect(media).not.toBeNull();
    expect(lifecycle.ownsRequest(request, sessionA)).toBe(true);
    expect(lifecycle.ownsMedia(media, sessionA)).toBe(true);
    expect(lifecycle.ownsRequest(request, { ...sessionA, accessToken: "token-b" })).toBe(false);
    expect(lifecycle.ownsRequest(request, { ...sessionA, generation: "generation-b" })).toBe(false);
    expect(lifecycle.ownsRequest(request, { ...sessionA, userId: "user-b" })).toBe(false);
  });

  it("keeps the onLoad request current through the first framework onShow", () => {
    const lifecycle = new ReflectionSessionLifecycle(sessionA);
    const request = lifecycle.beginRequest(sessionA);

    expect(lifecycle.show(sessionA)).toBe(true);
    expect(lifecycle.ownsRequest(request, sessionA)).toBe(true);

    lifecycle.show(sessionA);
    expect(lifecycle.ownsRequest(request, sessionA)).toBe(false);
  });

  it("invalidates late work and cleans poll, recording, previews, speech and replay on hide/unload", () => {
    const lifecycle = new ReflectionSessionLifecycle(sessionA);
    const request = lifecycle.beginRequest(sessionA);
    const media = lifecycle.beginMedia(sessionA);
    const cleanup = cleanupSpies();

    lifecycle.hide(cleanup);

    expect(lifecycle.ownsRequest(request, sessionA)).toBe(false);
    expect(lifecycle.ownsMedia(media, sessionA)).toBe(false);
    expect(Object.values(cleanup).every((spy) => spy.mock.calls.length === 1)).toBe(true);
    expect(lifecycle.show(sessionA)).toBe(true);
    expect(lifecycle.beginRequest(sessionA)).not.toBeNull();

    lifecycle.unload(cleanup);
    expect(lifecycle.show(sessionA)).toBe(false);
    expect(Object.values(cleanup).every((spy) => spy.mock.calls.length === 2)).toBe(true);
  });

  it("refuses to reactivate the old page after an exact-owner change", () => {
    const lifecycle = new ReflectionSessionLifecycle(sessionA);
    expect(lifecycle.show({ ...sessionA, accessToken: "token-b" })).toBe(false);
    expect(lifecycle.beginRequest(sessionA)).toBeNull();
  });

  it("wires hide/unload and every late request or media commit through the lifecycle", () => {
    expect(pageSource).toContain("lifecycle: new ReflectionSessionLifecycle(auth)");
    expect(pageSource).toContain("runtime.lifecycle.hide(reflectionCleanup(this, runtime))");
    expect(pageSource).toContain("runtime.lifecycle.unload(reflectionCleanup(this, runtime))");
    expect(pageSource).toContain("runtime.lifecycle.ownsRequest(epoch, currentAuthSession())");
    expect(pageSource).toContain("runtime.lifecycle.ownsMedia(epoch, currentAuthSession())");
    expect(pageSource).not.toContain("currentAuthSession()?.userId ===");
  });

  it("uses the width-only 600px medium layout boundary", () => {
    expect(pageStyles).toContain("@media (min-width: 600px)");
    expect(pageStyles).not.toMatch(/min-width:\s*(700|768)px/u);
    expect(pageStyles).not.toContain("orientation:");
  });
});

function cleanupSpies(): ReflectionActivityCleanup & Record<string, ReturnType<typeof vi.fn>> {
  return {
    clearCausePoll: vi.fn(),
    stopRecorder: vi.fn(),
    stopVoicePreview: vi.fn(),
    stopSpeechPreview: vi.fn(),
    stopReplay: vi.fn(),
  };
}
