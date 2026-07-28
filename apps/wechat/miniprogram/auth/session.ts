const authSessionKey = "gongkao.auth.session.v1";
const legacyAccessTokenKey = "gongkao.auth.accessToken";
const deviceIdKey = "gongkao.installation.deviceId";

interface SessionGlobalData {
  accessToken?: string;
  userId?: string;
  displayName?: string;
  generation?: string;
}

interface SessionAppOptions {
  globalData: SessionGlobalData;
}

interface OptionalSessionStorage {
  getStorageSync?: (key: string) => unknown;
  setStorageSync?: (key: string, value: unknown) => void;
  removeStorageSync?: (key: string) => void;
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
  const globalData = activeSessionGlobalData();
  if (!globalData) return undefined;

  if (hasSessionIdentity(globalData)) {
    if (!validIdentifier(globalData.accessToken) || !validIdentifier(globalData.userId)) {
      return undefined;
    }
    if (globalData.generation === undefined) {
      return migrateLegacyGlobalSession(globalData);
    }
    if (!validIdentifier(globalData.generation)) {
      return undefined;
    }

    const session = validatedAuthSession(
      globalData.accessToken,
      globalData.userId,
      globalData.displayName,
      globalData.generation,
    );
    applySessionToGlobalData(globalData, session);
    return session;
  }

  const persisted = readStorage(authSessionKey);
  const session = readPersistedSession(persisted);
  if (!session) {
    if (!isMissingStorageValue(persisted)) removeStorage(authSessionKey);
    removeStorage(legacyAccessTokenKey);
    return undefined;
  }
  if (!isAuthSession(persisted)) persistSession(session);
  else removeStorage(legacyAccessTokenKey);
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
  const globalData = activeSessionGlobalData();
  if (globalData) {
    delete globalData.accessToken;
    delete globalData.userId;
    delete globalData.displayName;
    delete globalData.generation;
  }
  removeStorage(authSessionKey);
  removeStorage(legacyAccessTokenKey);
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
  if (isAuthSession(value)) {
    return validatedAuthSession(
      value.accessToken,
      value.userId,
      value.displayName,
      value.generation,
    );
  }
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
    validIdentifier((value as Partial<AuthSession>).userId) &&
    (value as Partial<AuthSession>).generation === undefined
  );
}

function writeAuthSession(session: ActiveAuthSession): void {
  persistSession(session);
  const globalData = activeSessionGlobalData();
  if (globalData) applySessionToGlobalData(globalData, session);
}

function applySessionToGlobalData(globalData: SessionGlobalData, session: ActiveAuthSession): void {
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

function activeSessionGlobalData(): SessionGlobalData | undefined {
  if (typeof getApp !== "function") return undefined;
  let app: unknown;
  try {
    app = getApp<SessionAppOptions>();
  } catch (error: unknown) {
    if (error instanceof Error) return undefined;
    throw error;
  }
  if (!isRecord(app) || !isRecord(app.globalData)) return undefined;
  return app.globalData;
}

function migrateLegacyGlobalSession(globalData: SessionGlobalData): ActiveAuthSession {
  const persisted = readPersistedSession(readStorage(authSessionKey));
  const samePersistedOwner =
    persisted !== undefined &&
    persisted.accessToken.trim() === globalData.accessToken?.trim() &&
    persisted.userId.trim() === globalData.userId?.trim();
  const displayName =
    globalData.displayName === undefined && samePersistedOwner
      ? persisted.displayName
      : globalData.displayName;
  const session = validatedAuthSession(
    globalData.accessToken ?? "",
    globalData.userId ?? "",
    displayName,
    samePersistedOwner ? persisted.generation : createSessionGeneration(),
  );
  applySessionToGlobalData(globalData, session);
  persistSession(session);
  return session;
}

function persistSession(session: ActiveAuthSession): void {
  if (writeStorage(authSessionKey, session)) {
    removeStorage(legacyAccessTokenKey);
  }
}

function sessionStorage(): OptionalSessionStorage | undefined {
  if (typeof wx === "undefined") return undefined;
  return wx;
}

function readStorage(key: string): unknown {
  const storage = sessionStorage();
  return typeof storage?.getStorageSync === "function" ? storage.getStorageSync(key) : undefined;
}

function writeStorage(key: string, value: unknown): boolean {
  const storage = sessionStorage();
  if (typeof storage?.setStorageSync !== "function") return false;
  storage.setStorageSync(key, value);
  return true;
}

function removeStorage(key: string): void {
  const storage = sessionStorage();
  if (typeof storage?.removeStorageSync === "function") {
    storage.removeStorageSync(key);
  }
}

function hasSessionIdentity(globalData: SessionGlobalData): boolean {
  return (
    globalData.accessToken !== undefined ||
    globalData.userId !== undefined ||
    globalData.generation !== undefined
  );
}

function isMissingStorageValue(value: unknown): boolean {
  return value === undefined || value === null || value === "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
