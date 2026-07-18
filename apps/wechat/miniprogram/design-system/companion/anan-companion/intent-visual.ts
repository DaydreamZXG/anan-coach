import { ANAN_ANIMATIONS, type AnanIntent, type AnanSize } from "./runtime";

type PackagedAnanIntent = Exclude<AnanIntent, "companion">;
type StaticVariant = "inline-64@2x" | "card-160@2x" | "hero-240@2x";

export interface IntentVisualRegistration {
  intent: PackagedAnanIntent;
  packageRoot: string;
}

interface IntentVisualData {
  staticSrc: string;
  sheetSrc: string;
  motionActive: boolean;
  sheetWidthPercent: number;
  frameOffsetPercent: number;
}

interface IntentVisualHost {
  data: IntentVisualData & { size: string; animate: boolean };
  setData(patch: Partial<IntentVisualData>): void;
}

interface AnimationHost {
  setData(patch: { frameOffsetPercent: number }): void;
}

type AnimationConfig =
  | { frames: number; fps: number; playback: "loop"; holdFrame: null }
  | { frames: number; fps: number; playback: "once-hold"; holdFrame: number };

interface ActiveAnimation {
  timer: ReturnType<typeof setInterval>;
  frameIndex: number;
}

const activeAnimations = new WeakMap<object, ActiveAnimation>();

export function registerAnanIntentVisual(registration: IntentVisualRegistration): void {
  const animation = ANAN_ANIMATIONS[registration.intent];
  Component({
    options: {
      virtualHost: true,
    },

    properties: {
      size: { type: String, value: "medium" },
      animate: { type: Boolean, value: false },
    },

    data: {
      staticSrc: staticSource(registration.packageRoot, "inline-64@2x"),
      sheetSrc: `${registration.packageRoot}/assets/v1.0.0/spritesheet.webp`,
      motionActive: false,
      sheetWidthPercent: animation.frames * 100,
      frameOffsetPercent: 0,
    },

    observers: {
      "size, animate"() {
        syncIntentVisual(this, registration);
      },
    },

    lifetimes: {
      attached() {
        syncIntentVisual(this, registration);
      },
      detached() {
        stopIntentAnimation(this);
      },
    },

    pageLifetimes: {
      show() {
        syncIntentVisual(this, registration);
      },
      hide() {
        stopIntentAnimation(this);
      },
    },
  });
}

function syncIntentVisual(host: IntentVisualHost, registration: IntentVisualRegistration): void {
  stopIntentAnimation(host);
  const variant = staticVariant(host.data.size);
  host.setData({
    staticSrc: staticSource(registration.packageRoot, variant),
    sheetSrc: `${registration.packageRoot}/assets/v1.0.0/spritesheet.webp`,
    motionActive: host.data.animate,
    sheetWidthPercent: ANAN_ANIMATIONS[registration.intent].frames * 100,
    frameOffsetPercent: 0,
  });
  if (host.data.animate) startIntentAnimation(host, registration.intent);
}

function startIntentAnimation(host: IntentVisualHost, intent: PackagedAnanIntent): void {
  startAnanAnimation(host, ANAN_ANIMATIONS[intent]);
}

export function startAnanAnimation(host: AnimationHost, config: AnimationConfig): void {
  stopAnanAnimation(host);
  const active: ActiveAnimation = {
    frameIndex: 0,
    timer: setInterval(() => {
      const current = activeAnimations.get(host);
      if (!current) return;
      const next = current.frameIndex + 1;
      if (next < config.frames) {
        current.frameIndex = next;
        host.setData({ frameOffsetPercent: (next * 100) / config.frames });
        return;
      }
      if (config.playback === "loop") {
        current.frameIndex = 0;
        host.setData({ frameOffsetPercent: 0 });
        return;
      }
      current.frameIndex = config.holdFrame;
      host.setData({ frameOffsetPercent: (config.holdFrame * 100) / config.frames });
      stopAnanAnimation(host, false);
    }, 1000 / config.fps),
  };
  activeAnimations.set(host, active);
}

function stopIntentAnimation(host: IntentVisualHost, reset = true): void {
  stopAnanAnimation(host, reset);
}

export function stopAnanAnimation(host: AnimationHost, reset = true): void {
  const active = activeAnimations.get(host);
  if (active) clearInterval(active.timer);
  activeAnimations.delete(host);
  if (reset) host.setData({ frameOffsetPercent: 0 });
}

function staticVariant(size: string): StaticVariant {
  if (size === "hero-240") return "hero-240@2x";
  if (size === "large" || size === "animated-120" || size === "card-160") {
    return "card-160@2x";
  }
  return "inline-64@2x";
}

function staticSource(packageRoot: string, variant: StaticVariant): string {
  return `${packageRoot}/assets/v1.0.0/${variant}.webp`;
}

export function isAnanSize(value: string): value is AnanSize {
  return new Set<AnanSize>([
    "small",
    "medium",
    "large",
    "avatar-32",
    "inline-64",
    "animated-120",
    "card-160",
    "hero-240",
  ]).has(value as AnanSize);
}
