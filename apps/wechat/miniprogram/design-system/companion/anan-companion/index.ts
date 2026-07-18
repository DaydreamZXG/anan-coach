import {
  resolveAnanVisual,
  type AnanContext,
  type AnanIntent,
  type AnanMotion,
  type AnanSize,
  type AnanVisualStage,
  type ResolvedAnanVisual,
} from "./runtime";
import { startAnanAnimation, stopAnanAnimation } from "./intent-visual";

interface CompanionData {
  renderable: boolean;
  resolvedIntent: string;
  resolvedSize: string;
  resolvedLabel: string;
  staticSrc: string;
  renderCoreStatic: boolean;
  renderCoreMotion: boolean;
  renderGrowth: boolean;
  renderIntentPackage: boolean;
  motionAllowed: boolean;
  frameCount: number;
  frameOffsetPercent: number;
  sheetWidthPercent: number;
}

interface CompanionHost {
  data: CompanionData & {
    intent: string;
    size: string;
    label: string;
    animate: boolean;
    motion: string;
    context: string;
    visualStage: string;
  };
  setData(patch: Partial<CompanionData>): void;
}

Component({
  options: {
    virtualHost: true,
  },

  properties: {
    intent: { type: String, value: "companion" },
    size: { type: String, value: "medium" },
    label: { type: String, value: "" },
    animate: { type: Boolean, value: true },
    motion: { type: String, value: "allowed" },
    context: { type: String, value: "home" },
    visualStage: { type: String, value: "" },
  },

  data: {
    renderable: true,
    resolvedIntent: "companion",
    resolvedSize: "medium",
    resolvedLabel: "岸岸正在陪你继续学习",
    staticSrc: "",
    renderCoreStatic: true,
    renderCoreMotion: false,
    renderGrowth: false,
    renderIntentPackage: false,
    motionAllowed: false,
    frameCount: 6,
    frameOffsetPercent: 0,
    sheetWidthPercent: 600,
  },

  observers: {
    "intent, size, label, animate, motion, context, visualStage"() {
      syncVisual(this);
    },
  },

  lifetimes: {
    attached() {
      syncVisual(this);
    },
    detached() {
      stopCoreAnimation(this);
    },
  },

  pageLifetimes: {
    show() {
      syncVisual(this);
    },
    hide() {
      stopCoreAnimation(this);
    },
  },
});

function syncVisual(host: CompanionHost): void {
  const resolved = resolveAnanVisual({
    intent: host.data.intent,
    size: host.data.size,
    label: host.data.label,
    animate: host.data.animate,
    motion: host.data.motion,
    context: host.data.context,
    visualStage: host.data.visualStage,
  });
  stopCoreAnimation(host);
  if (!resolved) {
    console.error("岸岸视觉请求不符合运行时契约", {
      intent: host.data.intent,
      size: host.data.size,
      motion: host.data.motion,
      context: host.data.context,
      visualStage: host.data.visualStage,
    });
    host.setData({ renderable: false });
    return;
  }
  applyResolved(host, resolved);
  if (resolved.renderCoreMotion) startCoreAnimation(host);
}

function applyResolved(host: CompanionHost, resolved: ResolvedAnanVisual): void {
  host.setData({
    renderable: true,
    resolvedIntent: resolved.intent,
    resolvedSize: resolved.size,
    resolvedLabel: resolved.label,
    staticSrc: resolved.staticSrc,
    renderCoreStatic: resolved.renderCoreStatic,
    renderCoreMotion: resolved.renderCoreMotion,
    renderGrowth: resolved.renderGrowth,
    renderIntentPackage: resolved.renderIntentPackage,
    motionAllowed: resolved.motionAllowed,
    frameCount: 6,
    frameOffsetPercent: 0,
    sheetWidthPercent: 600,
  });
}

function startCoreAnimation(host: CompanionHost): void {
  startAnanAnimation(host, {
    frames: 6,
    fps: 8,
    playback: "loop",
    holdFrame: null,
  });
}

function stopCoreAnimation(host: CompanionHost): void {
  stopAnanAnimation(host);
}

export type { AnanContext, AnanIntent, AnanMotion, AnanSize, AnanVisualStage };
