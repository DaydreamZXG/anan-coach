export const ANAN_RELEASE_VERSION = "1.0.0" as const;

export type AnanIntent =
  "companion" | "thinking" | "reminder" | "encouragement" | "rest" | "deprioritize";
export type AnanVisualStage = "stage-1" | "stage-2" | "stage-3" | "stage-4";
export type AnanMotion = "allowed" | "reduced";
export type AnanContext =
  "home" | "plan" | "question-reading" | "post-submit" | "review" | "report" | "emotion" | "empty";
export type AnanSize =
  | "small"
  | "medium"
  | "large"
  | "avatar-32"
  | "inline-64"
  | "animated-120"
  | "card-160"
  | "hero-240";

type StaticVariant = "avatar" | "inline" | "card" | "hero";

interface SizePolicy {
  variant: StaticVariant;
  allowsMotion: boolean;
}

export interface AnanVisualRequest {
  intent: string;
  size: string;
  label: string;
  animate: boolean;
  motion: string;
  context: string;
  visualStage: string;
}

export interface ResolvedAnanVisual {
  intent: AnanIntent;
  size: AnanSize;
  context: AnanContext;
  visualStage: AnanVisualStage | "";
  label: string;
  staticVariant: StaticVariant;
  staticSrc: string;
  renderCoreStatic: boolean;
  renderCoreMotion: boolean;
  renderGrowth: boolean;
  renderIntentPackage: boolean;
  motionAllowed: boolean;
}

export const ANAN_INTENT_LABELS: Readonly<Record<AnanIntent, string>> = {
  companion: "岸岸正在陪你继续学习",
  thinking: "岸岸正在梳理当前信息",
  reminder: "岸岸有一项提醒需要你确认",
  encouragement: "岸岸正在为这次进步开心",
  rest: "岸岸建议现在安心休息一下",
  deprioritize: "岸岸建议暂时降低这件事的优先级",
};

export const ANAN_ANIMATIONS = {
  companion: { frames: 6, fps: 8, playback: "loop", holdFrame: null },
  thinking: { frames: 6, fps: 8, playback: "loop", holdFrame: null },
  reminder: { frames: 6, fps: 6, playback: "once-hold", holdFrame: 3 },
  encouragement: { frames: 5, fps: 12, playback: "once-hold", holdFrame: 4 },
  rest: { frames: 6, fps: 5, playback: "loop", holdFrame: null },
  deprioritize: { frames: 5, fps: 8, playback: "once-hold", holdFrame: 4 },
} as const;

const intents = new Set<AnanIntent>(Object.keys(ANAN_INTENT_LABELS) as AnanIntent[]);
const stages = new Set<AnanVisualStage>(["stage-1", "stage-2", "stage-3", "stage-4"]);
const motions = new Set<AnanMotion>(["allowed", "reduced"]);
const contexts = new Set<AnanContext>([
  "home",
  "plan",
  "question-reading",
  "post-submit",
  "review",
  "report",
  "emotion",
  "empty",
]);
const sizePolicies: Readonly<Record<AnanSize, SizePolicy>> = {
  small: { variant: "inline", allowsMotion: false },
  medium: { variant: "inline", allowsMotion: true },
  large: { variant: "card", allowsMotion: true },
  "avatar-32": { variant: "avatar", allowsMotion: false },
  "inline-64": { variant: "inline", allowsMotion: false },
  "animated-120": { variant: "card", allowsMotion: true },
  "card-160": { variant: "card", allowsMotion: false },
  "hero-240": { variant: "hero", allowsMotion: false },
};
const sizes = new Set<AnanSize>(Object.keys(sizePolicies) as AnanSize[]);
const coreRoot = "/design-system/companion/anan-companion/assets/v1.0.0/core";

export function resolveAnanVisual(request: AnanVisualRequest): ResolvedAnanVisual | null {
  if (!isIntent(request.intent) || !isSize(request.size)) return null;
  if (!isMotion(request.motion) || !isContext(request.context)) return null;
  if (request.visualStage && !isStage(request.visualStage)) return null;

  const policy = sizePolicies[request.size];
  const visualStage = request.visualStage as AnanVisualStage | "";
  const motionAllowed =
    request.animate &&
    request.motion === "allowed" &&
    request.context !== "question-reading" &&
    policy.allowsMotion;
  const isAvatar = policy.variant === "avatar";
  const renderGrowth = request.intent === "companion" && policy.variant === "hero";
  const renderIntentPackage = !isAvatar && request.intent !== "companion";
  const renderCoreMotion = request.intent === "companion" && !renderGrowth && motionAllowed;
  const renderCoreStatic = !renderGrowth && !renderIntentPackage && !renderCoreMotion;

  return {
    intent: request.intent,
    size: request.size,
    context: request.context,
    visualStage,
    label: request.label.trim() || ANAN_INTENT_LABELS[request.intent],
    staticVariant: policy.variant,
    staticSrc: coreStaticSource(policy.variant),
    renderCoreStatic,
    renderCoreMotion,
    renderGrowth,
    renderIntentPackage,
    motionAllowed,
  };
}

function coreStaticSource(variant: StaticVariant): string {
  if (variant === "avatar") return `${coreRoot}/avatar-32@3x.webp`;
  if (variant === "card") return `${coreRoot}/card-160@2x.webp`;
  return `${coreRoot}/inline-64@2x.webp`;
}

function isIntent(value: string): value is AnanIntent {
  return intents.has(value as AnanIntent);
}

function isSize(value: string): value is AnanSize {
  return sizes.has(value as AnanSize);
}

function isStage(value: string): value is AnanVisualStage {
  return stages.has(value as AnanVisualStage);
}

function isMotion(value: string): value is AnanMotion {
  return motions.has(value as AnanMotion);
}

function isContext(value: string): value is AnanContext {
  return contexts.has(value as AnanContext);
}
