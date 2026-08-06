import type { QualificationApi } from "../../../api/qualification";
import type { PrimaryGoalPreview } from "../../../api/generated/types.gen";
import {
  deliveryView,
  planInvalidationViews,
  positionRouteSummary,
  requestTrace,
  targetValidityView,
  type PlanInvalidationView,
  type PositionDeliveryView,
  type PositionOperationError,
  type PositionRequestTrace,
  type PositionRouteSummary,
  type PositionTargetValidityView,
} from "./contracts";
import { positionOperationError, shouldRetainPositionCommand } from "./error-state";
import { primaryGoalPreviewView, type PrimaryGoalPreviewView } from "./presentation";
import type {
  PendingPositionCommandView,
  PositionStateStore,
} from "../../../features/positions/state-store";

export type PositionGoalStatus =
  | "idle"
  | "loading"
  | "previewing"
  | "ready"
  | "confirming"
  | "confirmed"
  | "needsProfile"
  | "noActiveRelease"
  | "versionConflict"
  | "notFound"
  | "network"
  | "error";

export interface PositionGoalState {
  status: PositionGoalStatus;
  error: PositionOperationError | null;
  positionId: string;
  summary: PositionRouteSummary | null;
  preview: PrimaryGoalPreview | null;
  previewView: PrimaryGoalPreviewView | null;
  targetValidity: PositionTargetValidityView | null;
  invalidations: PlanInvalidationView[];
  delivery: PositionDeliveryView | null;
  commandTrace: PositionRequestTrace | null;
  restoredPreview: boolean;
  offerExamBriefing: boolean;
}

export interface PositionGoalControllerOptions {
  api: Pick<
    QualificationApi,
    "getPositionDetail" | "evaluatePosition" | "previewPrimaryGoal" | "confirmPrimaryGoal"
  >;
  store: PositionStateStore;
  positionId: string;
  onChange: (state: PositionGoalState) => void;
  now?: () => number;
}

export class PositionGoalController {
  private readonly api: Pick<
    QualificationApi,
    "getPositionDetail" | "evaluatePosition" | "previewPrimaryGoal" | "confirmPrimaryGoal"
  >;
  private readonly store: PositionStateStore;
  private readonly onChange: (state: PositionGoalState) => void;
  private readonly now: () => number;
  private requestEpoch = 0;
  private disposed = false;
  private state: PositionGoalState;

  constructor(options: PositionGoalControllerOptions) {
    this.api = options.api;
    this.store = options.store;
    this.onChange = options.onChange;
    this.now = options.now ?? Date.now;
    const positionId = options.positionId.trim();
    const summary = positionId ? this.store.loadSummary(positionId) : null;
    const routeError = positionId ? null : missingPositionIdError();
    this.state = {
      status: routeError ? "error" : "idle",
      error: routeError,
      positionId,
      summary,
      preview: null,
      previewView: null,
      targetValidity: null,
      invalidations: [],
      delivery: null,
      commandTrace: null,
      restoredPreview: false,
      offerExamBriefing: false,
    };
    this.publish();
  }

  snapshot(): PositionGoalState {
    return {
      ...this.state,
      error: this.state.error ? { ...this.state.error } : null,
      summary: this.state.summary ? { ...this.state.summary } : null,
      preview: this.state.preview ? { ...this.state.preview } : null,
      previewView: this.state.previewView ? { ...this.state.previewView } : null,
      targetValidity: this.state.targetValidity ? { ...this.state.targetValidity } : null,
      invalidations: this.state.invalidations.map((item) => ({ ...item })),
      delivery: this.state.delivery ? { ...this.state.delivery } : null,
      commandTrace: this.state.commandTrace ? { ...this.state.commandTrace } : null,
    };
  }

  async load(): Promise<void> {
    if (this.disposed || !this.state.positionId) return;
    const epoch = ++this.requestEpoch;
    this.state = { ...this.state, status: "loading", error: null };
    this.publish();
    const summaryVersions = await this.loadSummary(epoch);
    if (!summaryVersions) return;
    if (await this.recoverPendingConfirmation(epoch)) return;
    const stored = this.store.loadGoalPreview(this.state.positionId);
    if (!this.isCurrent(epoch)) return;
    if (
      stored &&
      new Date(stored.expiresAt).getTime() > this.now() &&
      stored.learnerProfileVersion === summaryVersions.learnerProfileVersion &&
      stored.positionDataVersion === summaryVersions.positionDataVersion
    ) {
      this.setPreview(stored, null, true);
      return;
    }
    if (stored) this.store.clearGoalPreview(this.state.positionId);
    await this.generatePreview(epoch);
  }

  async regenerate(): Promise<void> {
    this.store.clearGoalPreview(this.state.positionId);
    this.state = {
      ...this.state,
      preview: null,
      previewView: null,
      invalidations: [],
      restoredPreview: false,
      offerExamBriefing: false,
    };
    await this.load();
  }

  async confirm(): Promise<void> {
    const preview = this.state.preview;
    if (this.disposed || !preview || !["ready", "network", "error"].includes(this.state.status)) {
      return;
    }
    const payload = {
      previewId: preview.previewId,
      changeReason: "用户查看计划影响预览后确认主报目标",
    } as const;
    const commandPayload = goalConfirmationCommand(this.state.positionId, payload);
    const pendingConfirmations = pendingGoalConfirmations(
      this.store.pendingCommands("goal-confirm"),
      this.state.positionId,
    );
    const pendingConfirmation = pendingConfirmations[0];
    if (
      pendingConfirmations.length > 1 ||
      (pendingConfirmation !== undefined &&
        !sameGoalConfirmation(pendingConfirmation, commandPayload))
    ) {
      this.state = {
        ...this.state,
        status: "versionConflict",
        error: pendingConfirmationConflictError(),
      };
      this.publish();
      return;
    }
    const recovering = pendingConfirmation !== undefined;
    if (!recovering && !isFutureTimestamp(preview.expiresAt, this.now())) {
      this.store.clearGoalPreview(this.state.positionId);
      this.state = {
        ...this.state,
        status: "versionConflict",
        error: expiredPreviewError(),
        preview: null,
        previewView: null,
        targetValidity: null,
        invalidations: [],
        restoredPreview: false,
      };
      this.publish();
      return;
    }
    const epoch = ++this.requestEpoch;
    this.state = { ...this.state, status: "confirming", error: null };
    this.publish();
    try {
      const idempotencyKey = await this.store.commandKey(
        "goal-confirm",
        preview.previewId,
        commandPayload,
      );
      if (!this.isCurrent(epoch)) return;
      const response = await this.api.confirmPrimaryGoal(payload, idempotencyKey);
      if (!this.isCurrent(epoch)) return;
      this.store.completeCommand("goal-confirm", preview.previewId);
      this.applyConfirmation(response);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const operationError = positionOperationError(error);
      if (!shouldRetainPositionCommand(error)) {
        this.store.completeCommand("goal-confirm", preview.previewId);
      }
      if (operationError.status === "versionConflict") {
        this.store.clearGoalPreview(this.state.positionId);
      }
      this.state = {
        ...this.state,
        status: operationError.status,
        error: operationError,
        preview: operationError.status === "versionConflict" ? null : preview,
        previewView: operationError.status === "versionConflict" ? null : this.state.previewView,
        targetValidity:
          operationError.status === "versionConflict" ? null : this.state.targetValidity,
        invalidations: operationError.status === "versionConflict" ? [] : this.state.invalidations,
        restoredPreview:
          operationError.status === "versionConflict" ? false : this.state.restoredPreview,
      };
      this.publish();
    }
  }

  dispose(): void {
    this.disposed = true;
    this.requestEpoch += 1;
  }

  private async generatePreview(epoch: number): Promise<void> {
    if (!this.isCurrent(epoch)) return;
    this.state = {
      ...this.state,
      status: "loading",
      error: null,
      preview: null,
      previewView: null,
      invalidations: [],
    };
    this.publish();
    try {
      const pendingPayload = this.store.pendingCommandPayload(
        "goal-preview",
        this.state.positionId,
      );
      const restoredPayload = goalPreviewPayload(pendingPayload, this.state.positionId);
      let payload = restoredPayload;
      if (!payload) {
        const eligibility = await this.api.evaluatePosition(this.state.positionId);
        if (!this.isCurrent(epoch)) return;
        if (eligibility.data.evaluation.status !== "eligible") {
          this.state = {
            ...this.state,
            status: "versionConflict",
            error: {
              status: "versionConflict",
              title: "该岗位尚未确认可报",
              message: "返回岗位详情，逐条处理待确认条件后再生成目标影响预览。",
              requestId: eligibility.requestId,
              retryable: true,
            },
            delivery: deliveryView(eligibility),
          };
          this.publish();
          return;
        }
        payload = {
          positionId: this.state.positionId,
          eligibilitySnapshotId: eligibility.data.snapshotId,
        };
        this.state = { ...this.state, status: "previewing", delivery: deliveryView(eligibility) };
      } else {
        this.state = { ...this.state, status: "previewing" };
      }
      this.publish();
      const idempotencyKey = await this.store.commandKey(
        "goal-preview",
        this.state.positionId,
        payload,
      );
      if (!this.isCurrent(epoch)) return;
      const response = await this.api.previewPrimaryGoal(payload, idempotencyKey);
      if (!this.isCurrent(epoch)) return;
      this.store.completeCommand("goal-preview", this.state.positionId);
      if (!isFutureTimestamp(response.data.expiresAt, this.now())) {
        this.store.clearGoalPreview(this.state.positionId);
        this.state = {
          ...this.state,
          status: "versionConflict",
          error: expiredPreviewError(),
          preview: null,
          previewView: null,
          targetValidity: null,
          invalidations: [],
          restoredPreview: false,
        };
        this.publish();
        return;
      }
      this.store.saveGoalPreview(response.data);
      this.setPreview(response.data, deliveryView(response), false);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const operationError = positionOperationError(error);
      if (!shouldRetainPositionCommand(error)) {
        this.store.completeCommand("goal-preview", this.state.positionId);
      }
      this.state = { ...this.state, status: operationError.status, error: operationError };
      this.publish();
    }
  }

  private async recoverPendingConfirmation(epoch: number): Promise<boolean> {
    if (!this.isCurrent(epoch)) return true;
    const pending = pendingGoalConfirmations(
      this.store.pendingCommands("goal-confirm"),
      this.state.positionId,
    );
    if (pending.length === 0) return false;
    if (pending.length > 1) {
      this.state = {
        ...this.state,
        status: "versionConflict",
        error: pendingConfirmationConflictError(),
        preview: null,
        previewView: null,
        targetValidity: null,
        invalidations: [],
        restoredPreview: false,
      };
      this.publish();
      return true;
    }
    const command = pending[0];
    if (!command) return false;
    this.state = {
      ...this.state,
      status: "confirming",
      error: null,
      preview: null,
      previewView: null,
      targetValidity: null,
      invalidations: [],
      restoredPreview: false,
    };
    this.publish();
    try {
      const idempotencyKey = await this.store.commandKey(
        "goal-confirm",
        command.scope,
        command.payload,
      );
      if (!this.isCurrent(epoch)) return true;
      const response = await this.api.confirmPrimaryGoal(command.request, idempotencyKey);
      if (!this.isCurrent(epoch)) return true;
      this.store.completeCommand("goal-confirm", command.scope);
      this.applyConfirmation(response);
    } catch (error) {
      if (!this.isCurrent(epoch)) return true;
      const operationError = positionOperationError(error);
      if (!shouldRetainPositionCommand(error)) {
        this.store.completeCommand("goal-confirm", command.scope);
      }
      if (operationError.status === "versionConflict") {
        this.store.clearGoalPreview(this.state.positionId);
      }
      this.state = {
        ...this.state,
        status: operationError.status,
        error: operationError,
      };
      this.publish();
    }
    return true;
  }

  private applyConfirmation(
    response: Awaited<ReturnType<QualificationApi["confirmPrimaryGoal"]>>,
  ): void {
    this.store.clearGoalPreview(this.state.positionId);
    this.state = {
      ...this.state,
      status: "confirmed",
      error: null,
      preview: null,
      previewView: null,
      targetValidity: null,
      invalidations: planInvalidationViews(response.data.planImpact.invalidates),
      commandTrace: requestTrace(response),
      restoredPreview: false,
      offerExamBriefing: response.data.planImpact.changeType === "goal_selected",
    };
    this.publish();
  }

  private async loadSummary(epoch: number): Promise<{
    learnerProfileVersion: number;
    positionDataVersion: string;
  } | null> {
    try {
      const response = await this.api.getPositionDetail(this.state.positionId);
      if (!this.isCurrent(epoch)) return null;
      const summary = positionRouteSummary(response.data);
      this.store.saveSummary(summary);
      this.state = {
        ...this.state,
        summary,
        delivery: deliveryView(response),
      };
      this.publish();
      return {
        learnerProfileVersion: response.data.learnerProfileVersion,
        positionDataVersion: response.data.positionDataVersion,
      };
    } catch (error) {
      if (!this.isCurrent(epoch)) return null;
      const operationError = positionOperationError(error);
      this.state = { ...this.state, status: operationError.status, error: operationError };
      this.publish();
      return null;
    }
  }

  private setPreview(
    preview: PrimaryGoalPreview,
    delivery: PositionDeliveryView | null,
    restoredPreview: boolean,
  ): void {
    this.state = {
      ...this.state,
      status: "ready",
      error: null,
      preview,
      previewView: primaryGoalPreviewView(preview),
      targetValidity: targetValidityView(preview.targetScore, this.now()),
      invalidations: planInvalidationViews(preview.planImpact.invalidates),
      delivery: delivery ?? this.state.delivery,
      restoredPreview,
    };
    this.publish();
  }

  private isCurrent(epoch: number): boolean {
    return !this.disposed && epoch === this.requestEpoch;
  }

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }
}

function missingPositionIdError(): PositionOperationError {
  return {
    status: "error",
    title: "还不能确认主目标",
    message: "当前页面缺少明确的岗位信息，请返回岗位列表后重新进入。",
    requestId: "",
    retryable: false,
  };
}

interface GoalConfirmationRequest {
  previewId: string;
  changeReason: string;
}

interface GoalConfirmationCommand {
  positionId: string;
  request: GoalConfirmationRequest;
}

interface RecoverableGoalConfirmation extends GoalConfirmationCommand {
  scope: string;
  payload: GoalConfirmationCommand;
}

function goalConfirmationCommand(
  positionId: string,
  request: GoalConfirmationRequest,
): GoalConfirmationCommand {
  return { positionId, request };
}

function pendingGoalConfirmations(
  commands: PendingPositionCommandView[],
  positionId: string,
): RecoverableGoalConfirmation[] {
  return commands.flatMap((command) => {
    const payload = parseGoalConfirmationCommand(command.payload);
    if (
      !payload ||
      payload.positionId !== positionId ||
      payload.request.previewId !== command.scope
    ) {
      return [];
    }
    return [{ ...payload, scope: command.scope, payload }];
  });
}

function parseGoalConfirmationCommand(value: unknown): GoalConfirmationCommand | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as { positionId?: unknown; request?: unknown };
  if (typeof candidate.positionId !== "string" || !candidate.positionId) return null;
  if (!candidate.request || typeof candidate.request !== "object") return null;
  const request = candidate.request as { previewId?: unknown; changeReason?: unknown };
  if (
    typeof request.previewId !== "string" ||
    !request.previewId ||
    typeof request.changeReason !== "string" ||
    !request.changeReason
  ) {
    return null;
  }
  return {
    positionId: candidate.positionId,
    request: { previewId: request.previewId, changeReason: request.changeReason },
  };
}

function sameGoalConfirmation(
  pending: RecoverableGoalConfirmation,
  expected: GoalConfirmationCommand,
): boolean {
  return (
    pending.scope === expected.request.previewId &&
    pending.positionId === expected.positionId &&
    pending.request.previewId === expected.request.previewId &&
    pending.request.changeReason === expected.request.changeReason
  );
}

function isFutureTimestamp(value: string, now: number): boolean {
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) && timestamp > now;
}

function expiredPreviewError(): PositionOperationError {
  return {
    status: "versionConflict",
    title: "主目标影响预览已过期",
    message: "这份影响预览已失效，请重新核验资格并查看最新影响。",
    requestId: "",
    retryable: true,
  };
}

function pendingConfirmationConflictError(): PositionOperationError {
  return {
    status: "versionConflict",
    title: "存在结果未确定的主目标确认",
    message: "刚才的确认结果还不确定，请先恢复原操作，再决定是否重新选择主目标。",
    requestId: "",
    retryable: true,
  };
}

function goalPreviewPayload(
  value: unknown,
  positionId: string,
): { positionId: string; eligibilitySnapshotId: string } | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as { positionId?: unknown; eligibilitySnapshotId?: unknown };
  if (
    candidate.positionId !== positionId ||
    typeof candidate.eligibilitySnapshotId !== "string" ||
    !candidate.eligibilitySnapshotId
  ) {
    return null;
  }
  return { positionId, eligibilitySnapshotId: candidate.eligibilitySnapshotId };
}
