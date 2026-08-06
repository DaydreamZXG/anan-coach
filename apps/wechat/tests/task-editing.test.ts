import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  TaskEditingApi,
  TaskEditLease,
} from "../miniprogram/pages/practice/shared/api/task-editing";
import {
  acquireTaskEditOwnership,
  resolveRevisionConflict,
  TaskEditTakeoverDeclined,
} from "../miniprogram/pages/practice/shared/features/task-editing/coordinator";

const firstLease: TaskEditLease = {
  state: "takeover_required",
  resourceType: "xingce_task_session",
  resourceId: "00000000-0000-4000-8000-000000000010",
  leaseId: "00000000-0000-4000-8000-000000000020",
  generation: 1,
  deviceId: "device-a-0000000000000000000001",
  deviceLabel: "旧 iPhone",
  serverRevision: 3,
  localBaseRevision: null,
  acquiredAt: "2026-07-19T00:00:00Z",
  lastSeenAt: "2026-07-19T00:00:00Z",
  takenOverAt: null,
};
const showModal = vi.fn();

describe("task editing coordinator", () => {
  beforeEach(() => {
    showModal.mockReset();
    const storage = new Map<string, unknown>();
    vi.stubGlobal("wx", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      getDeviceInfo: () => ({ model: "iPad Pro", platform: "ios" }),
      showModal,
    });
  });

  it("takes over explicitly and records the user's local conflict choice", async () => {
    const claim = vi.fn().mockResolvedValue(firstLease);
    const takeover: TaskEditLease = {
      ...firstLease,
      state: "conflict_choice_required",
      leaseId: "00000000-0000-4000-8000-000000000021",
      generation: 2,
      deviceId: "device-b-0000000000000000000002",
      deviceLabel: "iPad Pro · ios",
      localBaseRevision: 1,
      takenOverAt: "2026-07-19T00:01:00Z",
    };
    const resolved: TaskEditLease = {
      ...takeover,
      state: "owned",
      localBaseRevision: null,
    };
    const takeOver = vi.fn().mockResolvedValue(takeover);
    const resolve = vi.fn().mockResolvedValue(resolved);
    const api = {
      claim,
      takeOver,
      resolve,
      beginConflict: vi.fn(),
    } as unknown as TaskEditingApi;
    showModal
      .mockResolvedValueOnce({ confirm: true, cancel: false })
      .mockResolvedValueOnce({ confirm: true, cancel: false });

    const result = await acquireTaskEditOwnership(
      api,
      "xingce_task_session",
      firstLease.resourceId,
      1,
      true,
    );

    expect(takeOver).toHaveBeenCalledOnce();
    expect(resolve).toHaveBeenCalledWith(takeover, "use_this_device");
    expect(result).toEqual({
      lease: resolved,
      token: { leaseId: resolved.leaseId, generation: 2 },
      conflictChoice: "use_this_device",
    });
  });

  it("does not mutate ownership when the user declines takeover", async () => {
    const takeOver = vi.fn();
    const api = {
      claim: vi.fn().mockResolvedValue(firstLease),
      takeOver,
      resolve: vi.fn(),
      beginConflict: vi.fn(),
    } as unknown as TaskEditingApi;
    showModal.mockResolvedValueOnce({ confirm: false, cancel: true });

    await expect(
      acquireTaskEditOwnership(api, "xingce_task_session", firstLease.resourceId, 3, false),
    ).rejects.toBeInstanceOf(TaskEditTakeoverDeclined);
    expect(takeOver).not.toHaveBeenCalled();
  });

  it("opens a server conflict before asking which revision to keep", async () => {
    const conflicted: TaskEditLease = {
      ...firstLease,
      state: "conflict_choice_required",
      serverRevision: 4,
      localBaseRevision: 3,
    };
    const resolved: TaskEditLease = {
      ...conflicted,
      state: "owned",
      localBaseRevision: null,
    };
    const beginConflict = vi.fn().mockResolvedValue(conflicted);
    const resolve = vi.fn().mockResolvedValue(resolved);
    const api = {
      claim: vi.fn(),
      takeOver: vi.fn(),
      resolve,
      beginConflict,
    } as unknown as TaskEditingApi;
    showModal.mockResolvedValueOnce({ confirm: false, cancel: true });

    const result = await resolveRevisionConflict(api, firstLease, 3);

    expect(beginConflict).toHaveBeenCalledWith(firstLease, 3);
    expect(resolve).toHaveBeenCalledWith(conflicted, "use_server");
    expect(result.conflictChoice).toBe("use_server");
  });
});
