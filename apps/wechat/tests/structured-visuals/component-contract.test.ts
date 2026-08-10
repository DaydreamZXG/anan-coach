import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program component logic through its raw asset loader.
import componentLogic from "../../miniprogram/design-system/structured-visuals/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import componentTemplate from "../../miniprogram/design-system/structured-visuals/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import componentStyles from "../../miniprogram/design-system/structured-visuals/index.wxss?raw";
// @ts-expect-error Vite exposes prototype documentation through its raw asset loader.
import componentReadme from "../../miniprogram/design-system/structured-visuals/README.md?raw";

describe("structured visual component contract", () => {
  it("keeps Canvas 2D as a thin renderer with resize rebinding", () => {
    expect(componentTemplate).toContain('type="2d"');
    expect(componentLogic).toContain("buildStructuredVisualScene");
    expect(componentLogic).toContain("paintStructuredVisualScene");
    expect(componentLogic).toContain("pageLifetimes");
    expect(componentLogic).toContain("preferredCanvasHeight");
  });

  it("shows explicit valid, empty, invalid, and missing-evidence states", () => {
    expect(componentTemplate).toContain("status === 'valid'");
    expect(componentTemplate).toContain("structured-visual__state--{{status}}");
    expect(componentTemplate).toContain("warningText");
    expect(componentLogic).toContain('triggerEvent("validationchange"');
  });

  it("uses the existing component token bridge and tablet density boundary", () => {
    expect(componentStyles).toContain('@import "../tokens/component-host.tokens.wxss"');
    expect(componentStyles).toContain("@media (min-width: 600px)");
    expect(componentStyles).toContain("--rpx: 0.5px");
  });

  it("documents a structured-only API with no OCR, network, model, or route integration", () => {
    expect(componentReadme).toContain("one structured object");
    expect(componentReadme).toContain("does no OCR, network request");
    expect(componentReadme).toContain("Unknown versions render an error state");
    expect(componentLogic).not.toMatch(/wx\.request|uploadFile|downloadFile|navigateTo|switchTab/u);
  });
});
