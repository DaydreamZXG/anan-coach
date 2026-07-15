import {
  positionCompareUrl,
  positionDetailUrl,
  positionGoalUrl,
  positionSearchUrl,
  registeredNavigationTarget,
  registeredTaskNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "./routes";

export const AUTH_RETURN_SCHEMA_VERSION = 1 as const;
export const AUTH_RETURN_TTL_MS = 7 * 24 * 60 * 60 * 1_000;

const storageRoot = `gongkao.navigation.auth-return.v${String(AUTH_RETURN_SCHEMA_VERSION)}`;
const identifierLimit = 200;
const searchValueLimit = 100;

interface PendingAuthReturnEnvelope {
  schemaVersion: typeof AUTH_RETURN_SCHEMA_VERSION;
  ownerUserId: string;
  createdAtMs: number;
  expiresAtMs: number;
  url: string;
}

export function loginUrlForReturn(startUrl: string): string {
  const target = authenticatedReturnTarget(startUrl);
  if (!target) throw new Error("Login return target is not an authenticated app destination");
  return `${routes.login}?returnTo=${encodeURIComponent(target.url)}`;
}

export function loginReturnTarget(rawValue: string | undefined): RegisteredNavigationTarget | null {
  const raw = rawValue?.trim();
  if (!raw) return null;
  const direct = authenticatedReturnTarget(raw);
  if (direct) return direct;
  try {
    const decoded = decodeURIComponent(raw);
    return decoded === raw ? null : authenticatedReturnTarget(decoded);
  } catch {
    return null;
  }
}

export function authenticatedReturnTarget(startUrl: string): RegisteredNavigationTarget | null {
  const navigation = registeredNavigationTarget(startUrl);
  if (!navigation || navigation.url.length > 1_024) return null;
  const [path = "", rawQuery] = navigation.url.split("?", 2);

  if (path === routes.today || path === routes.positions || path === routes.profile) {
    return rawQuery === undefined ? navigation : null;
  }
  if (path === routes.diagnostic || path === routes.reflection) {
    return rawQuery === undefined ? navigation : null;
  }
  if (path === routes.essayTask) return registeredTaskNavigationTarget(navigation.url);

  const query = parseExactQuery(rawQuery);
  if (!query) return null;
  if (path === routes.positionSearch) {
    if (!hasOnlyKeys(query, ["q", "examYear", "region", "city"])) return null;
    const values = [query.q, query.examYear, query.region, query.city];
    if (values.some((value) => value !== undefined && value.length > searchValueLimit)) return null;
    return requiredNavigationTarget(
      positionSearchUrl({
        ...(query.q !== undefined ? { query: query.q } : {}),
        ...(query.examYear !== undefined ? { examYear: query.examYear } : {}),
        ...(query.region !== undefined ? { region: query.region } : {}),
        ...(query.city !== undefined ? { city: query.city } : {}),
      }),
    );
  }
  if (path === routes.positionDetail || path === routes.positionGoal) {
    if (!hasOnlyKeys(query, ["positionId"]) || !validIdentifier(query.positionId)) return null;
    return requiredNavigationTarget(
      path === routes.positionDetail
        ? positionDetailUrl(query.positionId)
        : positionGoalUrl(query.positionId),
    );
  }
  if (path === routes.positionCompare) {
    if (!hasOnlyKeys(query, ["ids"]) || query.ids === undefined) return null;
    const ids = query.ids.split(",");
    if (!ids.every(validIdentifier)) return null;
    try {
      return requiredNavigationTarget(positionCompareUrl(ids));
    } catch {
      return null;
    }
  }
  return null;
}

export function storePendingAuthReturn(
  ownerUserId: string,
  startUrl: string,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  const target = authenticatedReturnTarget(startUrl);
  if (!target) throw new Error("Refusing to persist an invalid authenticated return target");
  const createdAtMs = validNow(nowMs);
  const envelope: PendingAuthReturnEnvelope = {
    schemaVersion: AUTH_RETURN_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + AUTH_RETURN_TTL_MS,
    url: target.url,
  };
  wx.setStorageSync(storageKey(owner), envelope);
}

export function loadPendingAuthReturn(
  ownerUserId: string,
  nowMs = Date.now(),
): RegisteredNavigationTarget | null {
  const owner = required(ownerUserId, "userId");
  const key = storageKey(owner);
  const stored: unknown = wx.getStorageSync(key);
  if (stored === undefined || stored === null || stored === "") return null;
  if (!isPendingEnvelope(stored, owner, validNow(nowMs))) {
    wx.removeStorageSync(key);
    return null;
  }
  const target = authenticatedReturnTarget(stored.url);
  if (!target) {
    wx.removeStorageSync(key);
    return null;
  }
  return target;
}

export function consumePendingAuthReturn(
  ownerUserId: string,
  nowMs = Date.now(),
): RegisteredNavigationTarget | null {
  const owner = required(ownerUserId, "userId");
  const target = loadPendingAuthReturn(owner, nowMs);
  if (target) wx.removeStorageSync(storageKey(owner));
  return target;
}

export function clearPendingAuthReturn(ownerUserId: string): void {
  wx.removeStorageSync(storageKey(required(ownerUserId, "userId")));
}

export function clearAllPendingAuthReturns(): void {
  for (const key of wx.getStorageInfoSync().keys) {
    if (key.startsWith(`${storageRoot}.`)) wx.removeStorageSync(key);
  }
}

function parseExactQuery(rawQuery: string | undefined): Record<string, string> | null {
  if (rawQuery === undefined || rawQuery === "") return {};
  const result: Record<string, string> = {};
  try {
    for (const pair of rawQuery.split("&")) {
      const separator = pair.indexOf("=");
      const rawKey = separator < 0 ? pair : pair.slice(0, separator);
      const rawValue = separator < 0 ? "" : pair.slice(separator + 1);
      const key = decodeURIComponent(rawKey);
      const value = decodeURIComponent(rawValue);
      if (!key || Object.hasOwn(result, key)) return null;
      result[key] = value;
    }
  } catch {
    return null;
  }
  return result;
}

function hasOnlyKeys(query: Record<string, string>, allowedKeys: string[]): boolean {
  const allowed = new Set(allowedKeys);
  return Object.keys(query).every((key) => allowed.has(key));
}

function validIdentifier(value: string | undefined): value is string {
  return value !== undefined && value.trim().length > 0 && value.length <= identifierLimit;
}

function requiredNavigationTarget(url: string): RegisteredNavigationTarget {
  const target = registeredNavigationTarget(url);
  if (!target) throw new Error(`Authenticated return target is not registered: ${url}`);
  return target;
}

function isPendingEnvelope(
  value: unknown,
  ownerUserId: string,
  nowMs: number,
): value is PendingAuthReturnEnvelope {
  if (!isRecord(value)) return false;
  return (
    Object.keys(value).length === 5 &&
    value.schemaVersion === AUTH_RETURN_SCHEMA_VERSION &&
    value.ownerUserId === ownerUserId &&
    Number.isSafeInteger(value.createdAtMs) &&
    Number.isSafeInteger(value.expiresAtMs) &&
    Number(value.createdAtMs) >= 0 &&
    Number(value.expiresAtMs) > Number(value.createdAtMs) &&
    Number(value.expiresAtMs) > nowMs &&
    typeof value.url === "string"
  );
}

function storageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}`;
}

function required(value: string, field: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${field} is required`);
  return normalized;
}

function validNow(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0)
    throw new Error("nowMs must be a non-negative integer");
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
