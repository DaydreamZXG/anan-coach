const authSessionKey = "gongkao.auth.session.v1";
const legacyAccessTokenKey = "gongkao.auth.accessToken";
const deviceIdKey = "gongkao.installation.deviceId";

interface SessionAppOptions {
  globalData: { accessToken?: string; userId?: string; displayName?: string; generation?: string };
}

export interface AuthSession {
  accessToken: string;
  userId: string;
  displayName?: string;
  /** Optional only for legacy typed fixtures; persisted/current sessions always contain it. */
  generation?: string;
}

export interface ActiveAuthSession extends AuthSession {
  generation: string;
}

export function currentAuthSession(): ActiveAuthSession | undefined {
  const globalData = getApp<SessionAppOptions>().globalData;
  if (
    validIdentifier(globalData.accessToken) &&
    validIdentifier(globalData.userId) &&
    validIdentifier(globalData.generation)
  ) {
    const displayName = normalizedDisplayName(globalData.displayName);
    return {
      accessToken: globalData.accessToken,
      userId: globalData.userId,
      ...(displayName ? { displayName } : {}),
      generation: globalData.generation,
    };
  }
  const persisted: unknown = wx.getStorageSync(authSessionKey);
  const session = readPersistedSession(persisted);
  if (!session) {
    wx.removeStorageSync(legacyAccessTokenKey);
    return undefined;
  }
  if (!isAuthSession(persisted)) wx.setStorageSync(authSessionKey, session);
  wx.removeStorageSync(legacyAccessTokenKey);
  applySessionToGlobalData(globalData, session);
  return session;
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
  writeAuthSession(
    validatedAuthSession(accessToken, userId, displayName, createSessionGeneration()),
  );
}

export function updateAuthDisplayName(displayName?: string | null): void {
  const session = currentAuthSession();
  if (!session) return;
  writeAuthSession(
    validatedAuthSession(session.accessToken, session.userId, displayName, session.generation),
  );
}

export function clearAuthSession(): void {
  const globalData = getApp<SessionAppOptions>().globalData;
  delete globalData.accessToken;
  delete globalData.userId;
  delete globalData.displayName;
  delete globalData.generation;
  wx.removeStorageSync(authSessionKey);
  wx.removeStorageSync(legacyAccessTokenKey);
}

/** Clears only the session that owned a failed request. */
export function clearAuthSessionIfCurrent(origin: ActiveAuthSession | undefined): boolean {
  if (!origin || !sameSessionOwner(currentAuthSession(), origin)) return false;
  clearAuthSession();
  return true;
}

/** Session ownership intentionally excludes mutable presentation metadata. */
export function sameSessionOwner(
  current: ActiveAuthSession | undefined,
  origin: ActiveAuthSession | undefined,
): boolean {
  return (
    current !== undefined &&
    origin !== undefined &&
    current.accessToken === origin.accessToken &&
    current.userId === origin.userId &&
    current.generation === origin.generation
  );
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
  generation?: string,
): ActiveAuthSession {
  const normalizedName = normalizedDisplayName(displayName);
  const session = {
    accessToken: accessToken.trim(),
    userId: userId.trim(),
    ...(normalizedName ? { displayName: normalizedName } : {}),
    generation: generation?.trim() ?? "",
  };
  if (!isAuthSession(session)) {
    throw new Error("Authenticated session requires a stable user and access token");
  }
  return session;
}

function isAuthSession(value: unknown): value is ActiveAuthSession {
  return (
    typeof value === "object" &&
    value !== null &&
    validIdentifier((value as Partial<AuthSession>).accessToken) &&
    validIdentifier((value as Partial<AuthSession>).userId) &&
    validIdentifier((value as Partial<AuthSession>).generation)
  );
}

function readPersistedSession(value: unknown): ActiveAuthSession | undefined {
  if (isAuthSession(value)) return value;
  if (!isLegacyAuthSession(value)) return undefined;
  return validatedAuthSession(
    value.accessToken,
    value.userId,
    value.displayName,
    createSessionGeneration(),
  );
}

function isLegacyAuthSession(value: unknown): value is Omit<AuthSession, "generation"> {
  return (
    typeof value === "object" &&
    value !== null &&
    validIdentifier((value as Partial<AuthSession>).accessToken) &&
    validIdentifier((value as Partial<AuthSession>).userId)
  );
}

function writeAuthSession(session: ActiveAuthSession): void {
  wx.setStorageSync(authSessionKey, session);
  wx.removeStorageSync(legacyAccessTokenKey);
  applySessionToGlobalData(getApp<SessionAppOptions>().globalData, session);
}

function applySessionToGlobalData(
  globalData: SessionAppOptions["globalData"],
  session: ActiveAuthSession,
): void {
  globalData.accessToken = session.accessToken;
  globalData.userId = session.userId;
  globalData.generation = session.generation;
  if (session.displayName) globalData.displayName = session.displayName;
  else delete globalData.displayName;
}

function createSessionGeneration(): string {
  return `session-${String(Date.now())}-${Math.random().toString(36).slice(2, 14)}`;
}

function validIdentifier(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function normalizedDisplayName(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().replace(/\s+/gu, " ");
  return normalized || undefined;
}
