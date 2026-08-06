import type { ApiClient } from "../../../../api/client";

export type TaskEditResourceType = "xingce_task_session" | "mock_exam_session";
export type TaskEditConflictChoice = "use_server" | "use_this_device";

export interface TaskDeviceIdentity {
  deviceId: string;
  deviceLabel: string;
}

export interface TaskEditLeaseToken {
  leaseId: string;
  generation: number;
}

export interface TaskEditLease extends TaskEditLeaseToken {
  state: "owned" | "takeover_required" | "conflict_choice_required";
  resourceType: TaskEditResourceType;
  resourceId: string;
  deviceId: string;
  deviceLabel: string;
  serverRevision: number;
  localBaseRevision: number | null;
  acquiredAt: string;
  lastSeenAt: string;
  takenOverAt: string | null;
}

export class TaskEditingApi {
  constructor(private readonly client: ApiClient) {}

  async claim(
    resourceType: TaskEditResourceType,
    resourceId: string,
    identity: TaskDeviceIdentity,
    localBaseRevision: number | null,
    hasLocalChanges: boolean,
  ): Promise<TaskEditLease> {
    const response = await this.client.post<unknown>("/api/v2/task-editing/leases", {
      resourceType,
      resourceId,
      ...identity,
      localBaseRevision,
      hasLocalChanges,
    });
    return mapLease(response.data);
  }

  async takeOver(
    lease: TaskEditLease,
    identity: TaskDeviceIdentity,
    localBaseRevision: number | null,
    hasLocalChanges: boolean,
  ): Promise<TaskEditLease> {
    const response = await this.client.post<unknown>(`${leasePath(lease)}/takeover`, {
      expectedLeaseId: lease.leaseId,
      expectedGeneration: lease.generation,
      ...identity,
      localBaseRevision,
      hasLocalChanges,
    });
    return mapLease(response.data);
  }

  async beginConflict(lease: TaskEditLease, localBaseRevision: number): Promise<TaskEditLease> {
    const response = await this.client.post<unknown>(`${leasePath(lease)}/conflict`, {
      expectedLeaseId: lease.leaseId,
      expectedGeneration: lease.generation,
      localBaseRevision,
    });
    return mapLease(response.data);
  }

  async resolve(lease: TaskEditLease, choice: TaskEditConflictChoice): Promise<TaskEditLease> {
    const response = await this.client.post<unknown>(`${leasePath(lease)}/resolve`, {
      expectedLeaseId: lease.leaseId,
      expectedGeneration: lease.generation,
      expectedServerRevision: lease.serverRevision,
      choice,
    });
    return mapLease(response.data);
  }
}

function leasePath(lease: TaskEditLease): string {
  return `/api/v2/task-editing/leases/${lease.resourceType}/${encodeURIComponent(lease.resourceId)}`;
}

function mapLease(value: unknown): TaskEditLease {
  if (!isRecord(value)) throw new Error("设备编辑权响应格式无效");
  const state = requiredString(value, "state");
  const resourceType = requiredString(value, "resourceType");
  const generation = requiredInteger(value, "generation");
  const serverRevision = requiredInteger(value, "serverRevision");
  if (state !== "owned" && state !== "takeover_required" && state !== "conflict_choice_required")
    throw new Error("设备编辑权状态无效");
  if (resourceType !== "xingce_task_session" && resourceType !== "mock_exam_session")
    throw new Error("设备编辑资源类型无效");
  if (generation < 1 || serverRevision < 1) throw new Error("设备编辑权版本无效");
  const localBaseRevision = value.localBaseRevision;
  const takenOverAt = value.takenOverAt;
  return {
    state,
    resourceType,
    resourceId: requiredString(value, "resourceId"),
    leaseId: requiredString(value, "leaseId"),
    generation,
    deviceId: requiredString(value, "deviceId"),
    deviceLabel: requiredString(value, "deviceLabel"),
    serverRevision,
    localBaseRevision:
      localBaseRevision === null || localBaseRevision === undefined
        ? null
        : requiredInteger(value, "localBaseRevision"),
    acquiredAt: requiredString(value, "acquiredAt"),
    lastSeenAt: requiredString(value, "lastSeenAt"),
    takenOverAt:
      takenOverAt === null || takenOverAt === undefined
        ? null
        : requiredString(value, "takenOverAt"),
  };
}

function requiredString(value: Record<string, unknown>, field: string): string {
  const result = value[field];
  if (typeof result !== "string" || !result.trim()) throw new Error(`设备编辑权缺少 ${field}`);
  return result;
}

function requiredInteger(value: Record<string, unknown>, field: string): number {
  const result = value[field];
  if (!Number.isInteger(result)) throw new Error(`设备编辑权缺少 ${field}`);
  return result as number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
