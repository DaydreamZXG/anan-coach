import { config } from "../config/env";
import { request, setUnauthorizedHandler } from "./client";
import type { WechatAuthSessionData, WechatAuthSessionEnvelope } from "./generated/types.gen";

interface StoredSession {
  accessToken: string;
  expiresAt: string;
  onboardingComplete: boolean;
}

let cachedSession: StoredSession | null = null;
let loginPromise: Promise<StoredSession> | null = null;

function readStoredSession(): StoredSession | null {
  if (cachedSession) return cachedSession;
  try {
    const raw = wx.getStorageSync(config.storageKeys.session) as StoredSession | "";
    if (raw && raw.accessToken && new Date(raw.expiresAt).getTime() > Date.now() + 60_000) {
      cachedSession = raw;
      return raw;
    }
  } catch {
    // 存储损坏视为未登录
  }
  return null;
}

function persistSession(data: WechatAuthSessionData): StoredSession {
  const session: StoredSession = {
    accessToken: data.accessToken,
    expiresAt: data.expiresAt,
    onboardingComplete: data.onboardingComplete,
  };
  cachedSession = session;
  wx.setStorageSync(config.storageKeys.session, session);
  return session;
}

export function clearSession(): void {
  cachedSession = null;
  wx.removeStorageSync(config.storageKeys.session);
}

/** 引导流程完成后同步本地会话标记，避免下次启动重复进引导 */
export function markOnboardingComplete(): void {
  const session = readStoredSession();
  if (!session) return;
  const updated: StoredSession = { ...session, onboardingComplete: true };
  cachedSession = updated;
  wx.setStorageSync(config.storageKeys.session, updated);
}

function getDeviceId(): string {
  const existing = wx.getStorageSync(config.storageKeys.deviceId) as string | "";
  if (existing) return existing;
  const created = `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  wx.setStorageSync(config.storageKeys.deviceId, created);
  return created;
}

function wxLoginCode(): Promise<string> {
  return new Promise((resolvePromise, rejectPromise) => {
    wx.login({
      success: (res) => {
        if (res.code) resolvePromise(res.code);
        else rejectPromise(new Error("wx.login 未返回 code"));
      },
      fail: () => rejectPromise(new Error("wx.login 失败")),
    });
  });
}

/**
 * 确保存在有效会话：有效直接返回，否则微信登录换新会话。
 * 并发调用共享同一次登录流程。
 */
export function ensureSession(): Promise<StoredSession> {
  const existing = readStoredSession();
  if (existing) return Promise.resolve(existing);
  if (loginPromise) return loginPromise;

  loginPromise = (async () => {
    const code = await wxLoginCode();
    const envelope = await request<WechatAuthSessionEnvelope>({
      method: "POST",
      path: "/api/v2/auth/wechat",
      body: { code, deviceId: getDeviceId() },
    });
    return persistSession(envelope.data);
  })()
    .catch((err: unknown) => {
      // 登录失败时输出真实原因（常见于域名未入白名单 / 后端拒绝 code）
      console.warn("[anan] 微信登录失败：", err);
      throw err;
    })
    .finally(() => {
      loginPromise = null;
    });
  return loginPromise;
}

export function getToken(): string | null {
  return readStoredSession()?.accessToken ?? null;
}

export function installUnauthorizedReset(): void {
  setUnauthorizedHandler(() => {
    clearSession();
  });
}
