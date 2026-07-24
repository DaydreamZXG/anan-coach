import { clearAuthSession, storeAuthSession } from "../auth/session";
import { setUnauthorizedHandler } from "./client";
import type { WechatAuthSessionData } from "./generated/types.gen";

export function storeWechatSession(data: WechatAuthSessionData): void {
  storeAuthSession(data.accessToken, data.user.userId, data.user.displayName);
}

export function clearSession(): void {
  clearAuthSession();
}

export function installUnauthorizedReset(): void {
  setUnauthorizedHandler(() => {
    clearSession();
  });
}
