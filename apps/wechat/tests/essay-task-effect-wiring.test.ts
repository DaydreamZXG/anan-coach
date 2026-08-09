import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import pageSource from "../miniprogram/pages/essay-task/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import runtimeSource from "../miniprogram/pages/essay-task/features/page-runtime.ts?raw";

const pageText = pageSource as unknown as string;
const runtimeText = runtimeSource as unknown as string;

describe("essay task per-lane page wiring", () => {
  it("uses five explicit lanes without the global request epoch", () => {
    for (const lane of ["workspace", "submission", "review-poll", "voice", "handwriting"]) {
      expect(pageText).toContain(`beginEffect(this, runtime, "${lane}")`);
    }
    expect(pageText).not.toContain("requestEpoch");
    expect(runtimeText).not.toContain("requestEpoch");
  });

  it("guards preview, recorder, upload, poll and handwriting callbacks", () => {
    expect(pageText).toContain("voicePreview.play(");
    expect(pageText).toContain("voiceRecorder.start(");
    expect(pageText).toContain("createRecognition(");
    expect(pageText).toContain("pollEssayTransferReview(");
    expect(pageText.match(/isEffectCurrent\(this, runtime, effect\)/gu)?.length).toBeGreaterThan(
      20,
    );
  });

  it("invalidates visible effects and resets transient state on hide", () => {
    expect(pageText).toContain("pageRuntimes.hide(runtime)");
    expect(pageText).toContain("cancelledVisibleEffectPatch(");
    expect(pageText).toContain("voiceRecorder?.stop()");
    expect(pageText).toContain("voicePreview?.stop()");
    expect(pageText).toContain("stopReviewPoll(runtime)");
  });

  it("keeps the existing 401 recovery path behind an owned effect", () => {
    expect(pageText).toContain("enterEssayAuthRequiredForEffect(this, runtime, effect, error)");
    expect(pageText).toContain("pageRuntimes.isEffectCurrentOrSessionCleared(");
    expect(pageText).toContain("pageRuntimes.invalidateAll(runtime)");
    expect(pageText).toContain('status: "unavailable"');
    expect(pageText).toContain("showLoginAction: true");
  });
});
