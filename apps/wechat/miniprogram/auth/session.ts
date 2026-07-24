const authSessionKey = "gongkao.auth.session.v1";
const legacyAccessTokenKey = "gongkao.auth.accessToken";
const deviceIdKey = "gongkao.installation.deviceId";

interface SessionAppOptions {
  globalData: { accessToken?: string; userId?: string; displayName?: string };
}

export interface AuthSession {
  accessToken: string;
  userId: string;
  displayName?: string;
}

export function currentAuthSession(): AuthSession | undefined {
  const globalData = getApp<SessionAppOptions>().globalData;
  if (validIdentifier(globalData.accessToken) && validIdentifier(globalData.userId)) {
    const displayName = normalizedDisplayName(globalData.displayName);
    return {
      accessToken: globalData.accessToken,
      userId: globalData.userId,
      ...(displayName ? { displayName } : {}),
    };
  }
  const persisted: unknown = wx.getStorageSync(authSessionKey);
  if (!isAuthSession(persisted)) {
    wx.removeStorageSync(legacyAccessTokenKey);
    return undefined;
  }
  wx.removeStorageSync(legacyAccessTokenKey);
  globalData.accessToken = persisted.accessToken;
  globalData.userId = persisted.userId;
  if (persisted.displayName) globalData.displayName = persisted.displayName;
  return persisted;
}

export function currentAccessToken(): string | undefined {
  return currentAuthSession()?.accessToken;
}

export function currentUserId(): string | undefined {
  return currentAuthSession()?.userId;
}

export function storeAuthSession(
  accessToken: string,
  userId: string,
  displayName?: string | null,
): void {
  const session = validatedAuthSession(accessToken, userId, displayName);
  wx.setStorageSync(authSessionKey, session);
  wx.removeStorageSync(legacyAccessTokenKey);
  const globalData = getApp<SessionAppOptions>().globalData;
  globalData.accessToken = session.accessToken;
  globalData.userId = session.userId;
  if (session.displayName) globalData.displayName = session.displayName;
  else delete globalData.displayName;
}

export function updateAuthDisplayName(displayName?: string | null): void {
  const session = currentAuthSession();
  if (!session) return;
  storeAuthSession(session.accessToken, session.userId, displayName);
}

export function clearAuthSession(): void {
  const globalData = getApp<SessionAppOptions>().globalData;
  delete globalData.accessToken;
  delete globalData.userId;
  delete globalData.displayName;
  wx.removeStorageSync(authSessionKey);
  wx.removeStorageSync(legacyAccessTokenKey);
}

export function currentDeviceId(): string {
  const stored = wx.getStorageSync<string>(deviceIdKey);
  if (stored) return stored;
  const generated = `wechat-${String(Date.now())}-${Math.random().toString(36).slice(2, 14)}`;
  wx.setStorageSync(deviceIdKey, generated);
  return generated;
}

function validatedAuthSession(
  accessToken: string,
  userId: string,
  displayName?: string | null,
): AuthSession {
  const normalizedName = normalizedDisplayName(displayName);
  const session = {
    accessToken: accessToken.trim(),
    userId: userId.trim(),
    ...(normalizedName ? { displayName: normalizedName } : {}),
  };
  if (!isAuthSession(session)) {
    throw new Error("Authenticated session requires a stable user and access token");
  }
  return session;
}

function isAuthSession(value: unknown): value is AuthSession {
  return (
    typeof value === "object" &&
    value !== null &&
    validIdentifier((value as Partial<AuthSession>).accessToken) &&
    validIdentifier((value as Partial<AuthSession>).userId)
  );
}

function validIdentifier(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function normalizedDisplayName(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().replace(/\s+/gu, " ");
  return normalized || undefined;
}
