import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import pageSource from "../miniprogram/pages/essay-task/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import runtimeSource from "../miniprogram/pages/essay-task/features/page-runtime.ts?raw";

describe("essay task per-lane page wiring", () => {
  it("uses five explicit lanes without the global request epoch", () => {
    for (const lane of ["workspace", "submission", "review-poll", "voice", "handwriting"]) {
      expect(pageSource).toContain(`beginEffect(this, runtime, "${lane}")`);
    }
    expect(pageSource).not.toContain("requestEpoch");
    expect(runtimeSource).not.toContain("requestEpoch");
  });

  it("guards preview, recorder, upload, poll and handwriting callbacks", () => {
    expect(pageSource).toContain("voicePreview.play(");
    expect(pageSource).toContain("voiceRecorder.start(");
    expect(pageSource).toContain("createRecognition(");
    expect(pageSource).toContain("pollEssayTransferReview(");
    expect(pageSource.match(/isEffectCurrent\(this, runtime, effect\)/gu)?.length).toBeGreaterThan(
      20,
    );
  });

  it("invalidates visible effects and resets transient state on hide", () => {
    expect(pageSource).toContain("pageRuntimes.hide(runtime)");
    expect(pageSource).toContain("cancelledVisibleEffectPatch(");
    expect(pageSource).toContain("voiceRecorder?.stop()");
    expect(pageSource).toContain("voicePreview?.stop()");
    expect(pageSource).toContain("stopReviewPoll(runtime)");
  });

  it("keeps the existing 401 recovery path behind an owned effect", () => {
    expect(pageSource).toContain("enterEssayAuthRequiredForEffect(this, runtime, effect, error)");
    expect(pageSource).toContain("pageRuntimes.isEffectCurrentOrSessionCleared(");
    expect(pageSource).toContain("pageRuntimes.invalidateAll(runtime)");
    expect(pageSource).toContain('status: "unavailable"');
    expect(pageSource).toContain("showLoginAction: true");
  });
});
