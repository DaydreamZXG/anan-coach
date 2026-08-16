import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import masteryContractsRaw from "../../../miniprogram/xingce/shared/features/xingce/mastery/contracts.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionTemplateRaw from "../../../miniprogram/pages/reflection/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import replayRuntimeRaw from "../../../miniprogram/pages/reflection/features/replay-runtime.ts?raw";

describe("beta flow discoverability 030 replay path", () => {
  it("reopens the same pending review and keeps question/material strokes replayable", () => {
    const contracts = String(masteryContractsRaw);
    expect(contracts).toContain('parsed.values.get("sessionId") !== item.sessionId');
    expect(contracts).toContain('parsed.values.get("reviewId") !== item.reviewId');
    expect(contracts).toContain("`${item.action.startUrl}&returnTo=mastery`");

    expect(String(reflectionTemplateRaw)).toContain("回放我当时的作答");
    expect(String(replayRuntimeRaw)).toContain("splitAnnotationSurfaces");
    expect(String(replayRuntimeRaw)).toContain('label: "材料"');
  });
});
