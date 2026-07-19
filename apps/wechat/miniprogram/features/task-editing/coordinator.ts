import type {
  TaskDeviceIdentity,
  TaskEditConflictChoice,
  TaskEditLease,
  TaskEditLeaseToken,
  TaskEditResourceType,
  TaskEditingApi,
} from "../../api/task-editing";

const deviceStorageKey = "gongkao.task-editing.device.v1";

export interface TaskEditOwnership {
  lease: TaskEditLease;
  token: TaskEditLeaseToken;
  conflictChoice: TaskEditConflictChoice | null;
}

export class TaskEditTakeoverDeclined extends Error {
  constructor() {
    super("用户没有接管当前任务");
    this.name = "TaskEditTakeoverDeclined";
  }
}

export async function acquireTaskEditOwnership(
  api: TaskEditingApi,
  resourceType: TaskEditResourceType,
  resourceId: string,
  localBaseRevision: number | null,
  hasLocalChanges: boolean,
): Promise<TaskEditOwnership> {
  const identity = taskDeviceIdentity();
  let lease = await api.claim(
    resourceType,
    resourceId,
    identity,
    localBaseRevision,
    hasLocalChanges,
  );
  if (lease.state === "takeover_required") {
    const answer = await wx.showModal({
      title: "另一台设备正在作答",
      content: `${lease.deviceLabel} 持有编辑权。接管前会先同步云端答案、草稿和计时；原设备随后只能查看。`,
      confirmText: "接管作答",
      cancelText: "暂不接管",
    });
    if (!answer.confirm) throw new TaskEditTakeoverDeclined();
    lease = await api.takeOver(lease, identity, localBaseRevision, hasLocalChanges);
  }
  return finishConflictChoice(api, lease);
}

export async function resolveRevisionConflict(
  api: TaskEditingApi,
  lease: TaskEditLease,
  localBaseRevision: number,
): Promise<TaskEditOwnership> {
  const conflicted = await api.beginConflict(lease, localBaseRevision);
  return finishConflictChoice(api, conflicted);
}

function taskDeviceIdentity(): TaskDeviceIdentity {
  const stored: unknown = wx.getStorageSync(deviceStorageKey);
  if (isIdentity(stored)) return stored;
  let deviceLabel = "当前微信设备";
  try {
    if (typeof wx.getDeviceInfo === "function") {
      const info = wx.getDeviceInfo();
      const parts = [info.model, info.platform].filter(
        (item): item is string => typeof item === "string" && Boolean(item.trim()),
      );
      if (parts.length > 0) deviceLabel = parts.join(" · ").slice(0, 120);
    }
  } catch {
    deviceLabel = "当前微信设备";
  }
  const identity = {
    deviceId: `mp-device-${randomSegment()}-${randomSegment()}-${randomSegment()}`,
    deviceLabel,
  } satisfies TaskDeviceIdentity;
  wx.setStorageSync(deviceStorageKey, identity);
  return identity;
}

async function finishConflictChoice(
  api: TaskEditingApi,
  lease: TaskEditLease,
): Promise<TaskEditOwnership> {
  if (lease.state !== "conflict_choice_required") {
    return { lease, token: token(lease), conflictChoice: null };
  }
  const answer = await wx.showModal({
    title: "发现两份作答进度",
    content: "本机离线草稿和云端进度来自不同版本。请选择保留哪一份；计时仍以服务端记录为准。",
    confirmText: "保留本机",
    cancelText: "使用云端",
  });
  const conflictChoice: TaskEditConflictChoice = answer.confirm ? "use_this_device" : "use_server";
  const resolved = await api.resolve(lease, conflictChoice);
  return { lease: resolved, token: token(resolved), conflictChoice };
}

function token(lease: TaskEditLease): TaskEditLeaseToken {
  return { leaseId: lease.leaseId, generation: lease.generation };
}

function randomSegment(): string {
  return Math.random().toString(36).slice(2, 12).padEnd(10, "0");
}

function isIdentity(value: unknown): value is TaskDeviceIdentity {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<TaskDeviceIdentity>;
  return (
    typeof candidate.deviceId === "string" &&
    candidate.deviceId.length >= 24 &&
    typeof candidate.deviceLabel === "string" &&
    candidate.deviceLabel.length >= 2
  );
}
