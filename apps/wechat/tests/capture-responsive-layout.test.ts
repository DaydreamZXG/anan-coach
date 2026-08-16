import { describe, expect, it } from "vitest";

// @ts-expect-error JavaScript visual fixtures intentionally run in Node and do not ship declarations.
import { visualScenarios } from "../e2e/visual-scenarios.mjs";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import captureTemplateRaw from "../miniprogram/pages/capture-import/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import captureStylesRaw from "../miniprogram/pages/capture-import/index.wxss?raw";

interface VisualScenario {
  id: string;
  data: {
    cameraError?: string;
    cameraReady?: boolean;
    permissionDenied?: boolean;
    state?: { status?: string };
  };
}

interface DeviceProfile {
  width: number;
  height: number;
  safeInsetBottom: number;
}

interface Rectangle {
  top: number;
  bottom: number;
}

const captureTemplate = String(captureTemplateRaw);
const captureStyles = String(captureStylesRaw);
const scenarios = visualScenarios as VisualScenario[];
const cameraErrorCopy = "相机没有启动成功，请检查系统相机权限后重试。";

const supportedProfiles: DeviceProfile[] = [
  { width: 320, height: 568, safeInsetBottom: 0 },
  { width: 375, height: 667, safeInsetBottom: 0 },
  { width: 363, height: 817, safeInsetBottom: 0 },
  { width: 390, height: 844, safeInsetBottom: 34 },
  { width: 768, height: 1024, safeInsetBottom: 0 },
  { width: 1024, height: 768, safeInsetBottom: 0 },
];

function ruleFor(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const match = captureStyles.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "u"));
  if (!match?.[1]) throw new Error(`Missing ${selector} rule`);
  return match[1];
}

function rpx(width: number): number {
  return width >= 768 ? 0.5 : width / 750;
}

function minimumCameraWorkspace(profile: DeviceProfile): number {
  const scale = rpx(profile.width);
  const navigationChrome = 96;
  const tools = 88 * scale;
  const error = 82 * scale;
  const controls = 196 * scale;
  const privacy = 96 * scale + profile.safeInsetBottom;
  return profile.height - navigationChrome - tools - error - controls - privacy;
}

function bottomPanelGeometry(profile: DeviceProfile): {
  error: Rectangle;
  album: Rectangle;
  shutter: Rectangle;
  privacy: Rectangle;
  safeAreaBottom: number;
} {
  const scale = rpx(profile.width);
  const privacyHeight = 96 * scale + profile.safeInsetBottom;
  const controlsHeight = 196 * scale;
  const errorHeight = 82 * scale;
  const privacy = {
    top: profile.height - privacyHeight,
    bottom: profile.height,
  };
  const controlsTop = privacy.top - controlsHeight;
  const error = {
    top: controlsTop - errorHeight,
    bottom: controlsTop,
  };
  const centeredControl = (height: number): Rectangle => {
    const top = controlsTop + (controlsHeight - height) / 2;
    return { top, bottom: top + height };
  };
  return {
    error,
    album: centeredControl(96 * scale),
    shutter: centeredControl(144 * scale),
    privacy,
    safeAreaBottom: profile.height - profile.safeInsetBottom,
  };
}

function overlaps(first: Rectangle, second: Rectangle): boolean {
  return first.top < second.bottom && first.bottom > second.top;
}

describe("capture camera responsive error layout", () => {
  it("keeps the error, recovery controls, and privacy copy in one ordered flow", () => {
    const panelStart = captureTemplate.indexOf('class="camera-bottom-panel"');
    const error = captureTemplate.indexOf('class="camera-error"', panelStart);
    const controls = captureTemplate.indexOf('class="camera-controls"', panelStart);
    const privacy = captureTemplate.indexOf('class="camera-privacy"', panelStart);

    expect(panelStart).toBeGreaterThan(-1);
    expect(error).toBeGreaterThan(panelStart);
    expect(controls).toBeGreaterThan(error);
    expect(privacy).toBeGreaterThan(controls);
    expect(ruleFor(".camera-bottom-panel")).toMatch(/flex:\s*0 0 auto;/u);
    expect(ruleFor(".camera-error")).not.toMatch(/position:\s*absolute;/u);
    expect(ruleFor(".camera-error")).toMatch(/overflow-wrap:\s*anywhere;/u);
    expect(captureTemplate).toContain('class="control-center camera-error__action"');
    expect(captureTemplate).toContain('bindtap="retryCamera"');
    expect(ruleFor(".camera-error__action")).toMatch(/min-height:\s*44px;/u);
    expect(ruleFor(".camera-privacy")).toContain("env(safe-area-inset-bottom)");
  });

  it("keeps permission recovery and its privacy explanation in the scrollable safe area", () => {
    expect(captureTemplate).toContain("capture-page--permission");
    expect(captureTemplate).toContain('class="permission-privacy"');
    expect(captureTemplate).toContain("不会进公共题库，也不会拿去训练模型");
    expect(ruleFor(".capture-page--permission")).toMatch(/overflow-y:\s*auto;/u);
    expect(ruleFor(".capture-page--permission .permission-screen")).toContain(
      "env(safe-area-inset-bottom)",
    );
  });

  it("lets the viewport own the camera page while preserving a scroll fallback", () => {
    const pageRule = ruleFor(".capture-page--camera");
    const workspaceRule = ruleFor(".capture-page--camera .camera-workspace");

    expect(pageRule).toMatch(/display:\s*flex;/u);
    expect(pageRule).toMatch(/height:\s*100vh;/u);
    expect(pageRule).toMatch(/overflow-y:\s*auto;/u);
    expect(workspaceRule).toMatch(/flex:\s*1 1 auto;/u);
    expect(workspaceRule).toMatch(/min-height:\s*0;/u);
  });

  it.each(supportedProfiles)(
    "retains usable camera space at $width×$height after reserving the error and safe-area controls",
    (profile) => {
      expect(minimumCameraWorkspace(profile)).toBeGreaterThanOrEqual(220);
    },
  );

  it.each(supportedProfiles)(
    "keeps the error, album, shutter, and privacy geometry disjoint at $width×$height",
    (profile) => {
      const geometry = bottomPanelGeometry(profile);

      expect(overlaps(geometry.error, geometry.album)).toBe(false);
      expect(overlaps(geometry.error, geometry.shutter)).toBe(false);
      expect(overlaps(geometry.album, geometry.privacy)).toBe(false);
      expect(overlaps(geometry.shutter, geometry.privacy)).toBe(false);
      expect(geometry.album.bottom).toBeLessThanOrEqual(geometry.safeAreaBottom);
      expect(geometry.shutter.bottom).toBeLessThanOrEqual(geometry.safeAreaBottom);
    },
  );

  it("provides a deterministic camera-init-error render without replacing normal or permission states", () => {
    const errorScenario = scenarios.find(
      (scenario) => scenario.id === "h17-capture-camera-init-error",
    );
    const normalScenario = scenarios.find((scenario) => scenario.id === "h01-capture-search-idle");
    const permissionScenario = scenarios.find(
      (scenario) => scenario.id === "h06-capture-camera-permission",
    );

    expect(errorScenario?.data).toMatchObject({
      cameraError: cameraErrorCopy,
      cameraReady: false,
      state: { status: "idle" },
    });
    expect(normalScenario?.data.cameraError ?? "").toBe("");
    expect(permissionScenario?.data.permissionDenied).toBe(true);
  });

  it("keeps whole-book progress in normal flow above candidate cards", () => {
    const progress = captureTemplate.indexOf('class="book-progress"');
    const candidates = captureTemplate.indexOf('class="match-list"', progress);

    expect(captureTemplate).toContain("整页/题本");
    expect(progress).toBeGreaterThan(-1);
    expect(candidates).toBeGreaterThan(progress);
    expect(ruleFor(".book-progress")).not.toMatch(/position:\s*(?:fixed|absolute);/u);
    expect(ruleFor(".book-progress")).toMatch(/box-sizing:\s*border-box;/u);
  });
});
