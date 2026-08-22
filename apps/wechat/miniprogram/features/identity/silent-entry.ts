import type { OnboardingProgress } from "../../api/generated/types.gen";
import {
  onboardingNavigationTarget,
  registeredNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../app/navigation/routes";

export const SILENT_WECHAT_LEGAL_ACCEPTED = false;

export interface SilentWechatSessionController {
  resume(): Promise<void>;
  login(): Promise<void>;
}

export function silentEntryErrorMessage(message: string): string {
  return message
    .replace(/上次登录/gu, "上次连接")
    .replace(/本次登录/gu, "本次连接")
    .replace(/重新微信登录/gu, "重新进入岸岸")
    .replace(/重新登录/gu, "重新进入岸岸")
    .replace(/登录操作/gu, "进入操作")
    .replace(/登录服务/gu, "微信身份服务")
    .replace(/登录凭证/gu, "微信身份凭证")
    .replace(/登录状态/gu, "微信身份状态");
}

export async function bootstrapSilentWechatSession(
  controller: SilentWechatSessionController,
  hasCurrentSession: () => boolean,
): Promise<void> {
  await controller.resume();
  if (!hasCurrentSession()) await controller.login();
}

export function learningEvidenceStateFromCoachContext(context: unknown): boolean | null {
  if (typeof context !== "object" || context === null) return null;
  const recognition = (context as Record<string, unknown>).recognition;
  if (typeof recognition !== "object" || recognition === null) return null;
  const evidenceCount = (recognition as Record<string, unknown>).evidenceCount;
  if (typeof evidenceCount !== "number" || !Number.isFinite(evidenceCount)) return null;
  return evidenceCount > 0;
}

export function silentEntryNavigationTarget(
  nextStep: OnboardingProgress["nextStep"],
  pendingReturn: RegisteredNavigationTarget | null,
  hasLearningEvidence: boolean | null = false,
): RegisteredNavigationTarget {
  const workspaceReady = nextStep === "workspace" || hasLearningEvidence === true;
  if (!workspaceReady) return onboardingNavigationTarget(nextStep);
  if (pendingReturn) return pendingReturn;
  const target = registeredNavigationTarget(routes.today);
  if (!target) throw new Error("Today route is not registered");
  return target;
}
