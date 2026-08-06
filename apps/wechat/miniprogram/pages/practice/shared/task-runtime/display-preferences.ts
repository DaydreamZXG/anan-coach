import {
  defaultContentPreferences,
  normalizeContentPreferences,
  type ContentDisplayPreferences,
} from "./content-renderer/model";

const storageKey = "gongkao.task-display-preferences.v1";

export function loadTaskDisplayPreferences(): ContentDisplayPreferences {
  const value: unknown = wx.getStorageSync(storageKey);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { ...defaultContentPreferences };
  }
  const candidate = value as Partial<ContentDisplayPreferences>;
  if (typeof candidate.fontScale !== "number" || typeof candidate.colorMode !== "string") {
    wx.removeStorageSync(storageKey);
    return { ...defaultContentPreferences };
  }
  try {
    return normalizeContentPreferences({
      fontScale: candidate.fontScale,
      colorMode: candidate.colorMode,
    });
  } catch {
    wx.removeStorageSync(storageKey);
    return { ...defaultContentPreferences };
  }
}

export function storeTaskDisplayPreferences(preferences: ContentDisplayPreferences): void {
  wx.setStorageSync(storageKey, normalizeContentPreferences(preferences));
}
