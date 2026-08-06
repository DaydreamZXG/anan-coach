import type { PositionRouteSummary } from "../../pages/positions/features/contracts";
import type { PrimaryGoalPreview } from "../../api/generated/types.gen";
import { createIdempotencyKey } from "../../platform/command-id";

const storagePrefix = "gongkao.positions.v2";

export interface PositionSearchDraft {
  query: string;
  examYear: string;
  region: string;
  city: string;
}

interface PendingPositionCommand {
  fingerprint: string;
  idempotencyKey: string;
  createdAt: string;
  expiresAt: string;
  payload: unknown;
}

export interface PendingPositionCommandView {
  scope: string;
  payload: unknown;
  createdAt: string;
}

const commandTtlMs = 7 * 24 * 60 * 60 * 1000;

export class PositionStateStore {
  constructor(private readonly userId: string) {}

  loadSearchDraft(): PositionSearchDraft | null {
    return readObject<PositionSearchDraft>(this.key("search"), isSearchDraft);
  }

  saveSearchDraft(draft: PositionSearchDraft): void {
    wx.setStorageSync(this.key("search"), draft);
  }

  loadComparison(): string[] {
    const value: unknown = wx.getStorageSync(this.key("comparison"));
    if (!Array.isArray(value)) return [];
    return uniqueIds(
      value.filter((item): item is string => typeof item === "string" && item.length > 0),
    ).slice(0, 3);
  }

  saveComparison(positionIds: string[]): void {
    wx.setStorageSync(this.key("comparison"), uniqueIds(positionIds).slice(0, 3));
  }

  saveSummary(summary: PositionRouteSummary): void {
    wx.setStorageSync(this.key(`summary.${summary.positionId}`), summary);
  }

  loadSummary(positionId: string): PositionRouteSummary | null {
    return readObject<PositionRouteSummary>(
      this.key(`summary.${positionId}`),
      isPositionRouteSummary,
    );
  }

  saveGoalPreview(preview: PrimaryGoalPreview): void {
    wx.setStorageSync(this.key(`goal-preview.${preview.positionId}`), preview);
  }

  loadGoalPreview(positionId: string): PrimaryGoalPreview | null {
    const key = this.key(`goal-preview.${positionId}`);
    const preview = readObject<PrimaryGoalPreview>(key, isPrimaryGoalPreview);
    if (!preview || preview.positionId !== positionId || !isFutureTimestamp(preview.expiresAt)) {
      if (preview) wx.removeStorageSync(key);
      return null;
    }
    return preview;
  }

  clearGoalPreview(positionId: string): void {
    wx.removeStorageSync(this.key(`goal-preview.${positionId}`));
  }

  async commandKey(
    operation: "requirement" | "goal-preview" | "goal-confirm" | "score-evidence",
    scope: string,
    payload: unknown,
  ): Promise<string> {
    const key = this.key(`command.${operation}.${scope}`);
    const fingerprint = stableFingerprint(payload);
    const existing = readObject<PendingPositionCommand>(key, isPendingCommand);
    if (existing) {
      if (isFutureTimestamp(existing.expiresAt)) {
        if (existing.fingerprint !== fingerprint) {
          throw new Error("同一岗位操作绑定了不同输入，必须先完成或明确取消上一次操作。");
        }
        return existing.idempotencyKey;
      }
      wx.removeStorageSync(key);
    }
    const idempotencyKey = await createIdempotencyKey(`position-${operation}`);
    const createdAt = new Date();
    wx.setStorageSync(key, {
      fingerprint,
      idempotencyKey,
      createdAt: createdAt.toISOString(),
      expiresAt: new Date(createdAt.getTime() + commandTtlMs).toISOString(),
      payload,
    } satisfies PendingPositionCommand);
    return idempotencyKey;
  }

  pendingCommandPayload(
    operation: "requirement" | "goal-preview" | "goal-confirm" | "score-evidence",
    scope: string,
  ): unknown {
    const key = this.key(`command.${operation}.${scope}`);
    const command = readObject<PendingPositionCommand>(key, isPendingCommand);
    if (!command) return null;
    if (!isFutureTimestamp(command.expiresAt)) {
      wx.removeStorageSync(key);
      return null;
    }
    return command.payload;
  }

  pendingCommands(
    operation: "requirement" | "goal-preview" | "goal-confirm" | "score-evidence",
  ): PendingPositionCommandView[] {
    const commandPrefix = this.key(`command.${operation}.`);
    return wx
      .getStorageInfoSync()
      .keys.filter((key) => key.startsWith(commandPrefix))
      .flatMap((key) => {
        const command = readObject<PendingPositionCommand>(key, isPendingCommand);
        if (!command || !isFutureTimestamp(command.expiresAt)) {
          wx.removeStorageSync(key);
          return [];
        }
        return [
          {
            scope: key.slice(commandPrefix.length),
            payload: command.payload,
            createdAt: command.createdAt,
          },
        ];
      })
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  }

  completeCommand(
    operation: "requirement" | "goal-preview" | "goal-confirm" | "score-evidence",
    scope: string,
  ): void {
    wx.removeStorageSync(this.key(`command.${operation}.${scope}`));
  }

  private key(suffix: string): string {
    return `${storagePrefix}.${encodeURIComponent(this.userId)}.${suffix}`;
  }
}

export function positionStoragePrefix(): string {
  return storagePrefix;
}

export function clearPositionStateForUser(userId: string): void {
  removeKeysWithPrefix(`${storagePrefix}.${encodeURIComponent(userId)}.`);
}

export function clearAllPositionState(): void {
  removeKeysWithPrefix(`${storagePrefix}.`);
}

function uniqueIds(values: string[]): string[] {
  return values.filter((value, index) => value.length > 0 && values.indexOf(value) === index);
}

function stableFingerprint(value: unknown): string {
  const canonical = stableJson(value);
  let hash = 2_166_136_261;
  for (let index = 0; index < canonical.length; index += 1) {
    hash ^= canonical.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return `${canonical.length.toString(16)}-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    const serialized = JSON.stringify(value);
    return typeof serialized === "string" ? serialized : "undefined";
  }
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  return `{${Object.entries(value)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
    .join(",")}}`;
}

function readObject<T>(key: string, guard: (value: unknown) => value is T): T | null {
  const value: unknown = wx.getStorageSync(key);
  return guard(value) ? value : null;
}

function isPendingCommand(value: unknown): value is PendingPositionCommand {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PendingPositionCommand>;
  return (
    typeof candidate.fingerprint === "string" &&
    typeof candidate.idempotencyKey === "string" &&
    typeof candidate.createdAt === "string" &&
    typeof candidate.expiresAt === "string" &&
    "payload" in candidate
  );
}

function isSearchDraft(value: unknown): value is PositionSearchDraft {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PositionSearchDraft>;
  return [candidate.query, candidate.examYear, candidate.region, candidate.city].every(
    (item) => typeof item === "string",
  );
}

function isPositionRouteSummary(value: unknown): value is PositionRouteSummary {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PositionRouteSummary>;
  return (
    typeof candidate.positionId === "string" &&
    typeof candidate.positionCode === "string" &&
    typeof candidate.name === "string" &&
    typeof candidate.departmentFull === "string" &&
    typeof candidate.region === "string" &&
    typeof candidate.headcount === "number" &&
    typeof candidate.isPrimaryGoal === "boolean"
  );
}

function isPrimaryGoalPreview(value: unknown): value is PrimaryGoalPreview {
  if (!value || typeof value !== "object") return false;
  const candidate = value as {
    previewId?: unknown;
    positionId?: unknown;
    eligibilitySnapshotId?: unknown;
    expiresAt?: unknown;
    planImpact?: unknown;
  };
  return (
    typeof candidate.previewId === "string" &&
    typeof candidate.positionId === "string" &&
    typeof candidate.eligibilitySnapshotId === "string" &&
    typeof candidate.expiresAt === "string" &&
    typeof candidate.planImpact === "object" &&
    candidate.planImpact !== null
  );
}

function removeKeysWithPrefix(prefix: string): void {
  const storage = wx.getStorageInfoSync();
  storage.keys
    .filter((key) => key.startsWith(prefix))
    .forEach((key) => {
      wx.removeStorageSync(key);
    });
}

function isFutureTimestamp(value: string): boolean {
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) && timestamp > Date.now();
}
