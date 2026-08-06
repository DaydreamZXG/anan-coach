import type { QualificationApi } from "../../../api/qualification";
import type { RequirementCheck } from "../../../api/generated/types.gen";
import { abilityEstimateView, type AbilityEstimateView } from "./ability/presentation";
import {
  deliveryView,
  positionRouteSummary,
  requestTrace,
  type PositionDeliveryView,
  type PositionOperationError,
  type PositionRemoteStatus,
  type PositionRequestTrace,
  type PositionRouteSummary,
} from "./contracts";
import { positionOperationError, shouldRetainPositionCommand } from "./error-state";
import {
  positionRouteSummaryView,
  positionFitView,
  requirementCheckView,
  type PositionFitView,
  type PositionRouteSummaryView,
  type RequirementCheckView,
} from "./presentation";
import type { PositionStateStore } from "../../../features/positions/state-store";

export interface PositionDetailState {
  status: PositionRemoteStatus;
  error: PositionOperationError | null;
  positionId: string;
  examYear: number;
  summary: PositionRouteSummary | null;
  summaryView: PositionRouteSummaryView | null;
  snapshotId: string;
  learnerProfileVersion: number;
  ruleSetVersion: string;
  positionDataVersion: string;
  ability: AbilityEstimateView | null;
  fit: PositionFitView | null;
  checks: RequirementCheckView[];
  eligibilityStatus: PositionRouteSummary["eligibilityStatus"] | "unknown";
  delivery: PositionDeliveryView | null;
  confirmingRequirementId: string;
  commandError: PositionOperationError | null;
  commandTrace: PositionRequestTrace | null;
  detailTrace: PositionRequestTrace | null;
  confirmationMessage: string;
}

export interface PositionDetailControllerOptions {
  api: Pick<QualificationApi, "getPositionDetail" | "evaluatePosition" | "confirmRequirement">;
  store: PositionStateStore;
  positionId: string;
  onChange: (state: PositionDetailState) => void;
}

export class PositionDetailController {
  private readonly api: Pick<
    QualificationApi,
    "getPositionDetail" | "evaluatePosition" | "confirmRequirement"
  >;
  private readonly store: PositionStateStore;
  private readonly onChange: (state: PositionDetailState) => void;
  private requestEpoch = 0;
  private disposed = false;
  private state: PositionDetailState;

  constructor(options: PositionDetailControllerOptions) {
    this.api = options.api;
    this.store = options.store;
    this.onChange = options.onChange;
    const positionId = options.positionId.trim();
    const summary = positionId ? this.store.loadSummary(positionId) : null;
    const routeError = positionId ? null : missingPositionIdError("岗位详情");
    this.state = {
      status: routeError ? "error" : "idle",
      error: routeError,
      positionId,
      examYear: 0,
      summary,
      summaryView: summary ? positionRouteSummaryView(summary) : null,
      snapshotId: "",
      learnerProfileVersion: 0,
      ruleSetVersion: "",
      positionDataVersion: "",
      ability: null,
      fit: null,
      checks: [],
      eligibilityStatus: summary?.eligibilityStatus ?? "unknown",
      delivery: null,
      confirmingRequirementId: "",
      commandError: null,
      commandTrace: null,
      detailTrace: null,
      confirmationMessage: "",
    };
    this.publish();
  }

  snapshot(): PositionDetailState {
    return cloneDetailState(this.state);
  }

  async load(options: { silent?: boolean } = {}): Promise<void> {
    if (this.disposed || !this.state.positionId) return;
    const epoch = ++this.requestEpoch;
    const silentReplace = options.silent === true && this.state.status === "ready";
    if (!silentReplace) {
      this.state = {
        ...this.state,
        status: "loading",
        error: null,
        checks: [],
        snapshotId: "",
        commandError: null,
      };
      this.publish();
    }
    try {
      const [detailResponse, response] = await Promise.all([
        this.api.getPositionDetail(this.state.positionId),
        this.api.evaluatePosition(this.state.positionId),
      ]);
      if (!this.isCurrent(epoch)) return;
      if (!versionsAlign(detailResponse.data, response.data)) {
        this.state = {
          ...this.state,
          status: "versionConflict",
          error: {
            status: "versionConflict",
            title: "岗位信息刚刚有更新",
            message: "你的资格资料或岗位条件有变化，请重新加载后再判断。",
            requestId: `${detailResponse.requestId} / ${response.requestId}`,
            retryable: true,
          },
        };
        this.publish();
        return;
      }
      const summary = positionRouteSummary(detailResponse.data);
      this.store.saveSummary(summary);
      this.state = {
        ...this.state,
        status: "ready",
        error: null,
        summary,
        summaryView: positionRouteSummaryView(summary),
        examYear: detailResponse.data.examYear,
        snapshotId: response.data.snapshotId,
        learnerProfileVersion: response.data.learnerProfileVersion,
        ruleSetVersion: detailResponse.data.ruleSetVersion,
        positionDataVersion: detailResponse.data.positionDataVersion,
        ability: abilityEstimateView(detailResponse.data.abilityEstimate),
        fit: positionFitView(detailResponse.data.fit),
        checks: response.data.evaluation.checks.map(requirementCheckView),
        eligibilityStatus: response.data.evaluation.status,
        delivery: deliveryView(response),
        detailTrace: requestTrace(detailResponse),
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const operationError = positionOperationError(error);
      this.state = { ...this.state, status: operationError.status, error: operationError };
      this.publish();
    }
  }

  async confirmRequirement(
    requirementId: string,
    response: "satisfied" | "not_satisfied" | "uncertain",
  ): Promise<void> {
    if (
      this.disposed ||
      this.state.status !== "ready" ||
      !this.state.snapshotId ||
      this.state.confirmingRequirementId
    ) {
      return;
    }
    const statement = learnerStatement(response);
    const payload = { response, statement } as const;
    const scope = `${this.state.snapshotId}.${requirementId}`;
    this.state = {
      ...this.state,
      confirmingRequirementId: requirementId,
      commandError: null,
      confirmationMessage: "",
    };
    this.publish();
    try {
      const idempotencyKey = await this.store.commandKey("requirement", scope, payload);
      const result = await this.api.confirmRequirement(
        this.state.positionId,
        requirementId,
        payload,
        idempotencyKey,
      );
      if (!this.isActive()) return;
      this.store.completeCommand("requirement", scope);
      const evaluation = result.data.eligibility.evaluation;
      const summary = updateSummary(this.state.summary, evaluation);
      if (summary) this.store.saveSummary(summary);
      this.state = {
        ...this.state,
        status: "ready",
        summary,
        summaryView: summary ? positionRouteSummaryView(summary) : null,
        snapshotId: result.data.eligibility.snapshotId,
        learnerProfileVersion: result.data.confirmation.profileVersion,
        checks: evaluation.checks.map(requirementCheckView),
        eligibilityStatus: evaluation.status,
        confirmingRequirementId: "",
        commandError: null,
        commandTrace: requestTrace(result),
        confirmationMessage: "已记录，正在用最新资料重新核验。",
      };
      this.publish();
      await this.load({ silent: true });
    } catch (error) {
      if (!this.isActive()) return;
      const operationError = positionOperationError(error);
      if (!shouldRetainPositionCommand(error)) {
        this.store.completeCommand("requirement", scope);
      }
      this.state = {
        ...this.state,
        confirmingRequirementId: "",
        commandError: operationError,
      };
      this.publish();
    }
  }

  dispose(): void {
    this.disposed = true;
    this.requestEpoch += 1;
  }

  private isCurrent(epoch: number): boolean {
    return !this.disposed && epoch === this.requestEpoch;
  }

  private isActive(): boolean {
    return !this.disposed;
  }

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }
}

function missingPositionIdError(pageName: string): PositionOperationError {
  return {
    status: "error",
    title: `还不能打开${pageName}`,
    message: "当前页面缺少明确的岗位信息，请返回岗位列表后重新进入。",
    requestId: "",
    retryable: false,
  };
}

function learnerStatement(response: "satisfied" | "not_satisfied" | "uncertain"): string {
  return {
    satisfied: "用户针对该岗位原文明确选择：符合",
    not_satisfied: "用户针对该岗位原文明确选择：不符合",
    uncertain: "用户针对该岗位原文明确选择：不确定",
  }[response];
}

function updateSummary(
  summary: PositionRouteSummary | null,
  evaluation: { status: PositionRouteSummary["eligibilityStatus"]; checks: RequirementCheck[] },
): PositionRouteSummary | null {
  return summary ? { ...summary, eligibilityStatus: evaluation.status } : null;
}

function cloneDetailState(state: PositionDetailState): PositionDetailState {
  return {
    ...state,
    error: state.error ? { ...state.error } : null,
    summary: state.summary ? { ...state.summary } : null,
    summaryView: state.summaryView ? { ...state.summaryView } : null,
    ability: state.ability ? { ...state.ability } : null,
    fit: state.fit
      ? {
          ...state.fit,
          evidenceReasonCodes: [...state.fit.evidenceReasonCodes],
          requirementsBurden: {
            ...state.fit.requirementsBurden,
            pendingRequirementIds: [...state.fit.requirementsBurden.pendingRequirementIds],
            hardConditionNotMetRequirementIds: [
              ...state.fit.requirementsBurden.hardConditionNotMetRequirementIds,
            ],
          },
          scoreGap: state.fit.scoreGap ? { ...state.fit.scoreGap } : null,
        }
      : null,
    checks: state.checks.map((item) => ({ ...item })),
    delivery: state.delivery ? { ...state.delivery } : null,
    commandError: state.commandError ? { ...state.commandError } : null,
    commandTrace: state.commandTrace ? { ...state.commandTrace } : null,
    detailTrace: state.detailTrace ? { ...state.detailTrace } : null,
  };
}

function versionsAlign(
  detail: {
    learnerProfileVersion: number;
    ruleSetVersion: string;
    positionDataVersion: string;
  },
  snapshot: {
    learnerProfileVersion: number;
    evaluation: { ruleSetVersion: string; positionDataVersion: string };
  },
): boolean {
  return (
    detail.learnerProfileVersion === snapshot.learnerProfileVersion &&
    detail.ruleSetVersion === snapshot.evaluation.ruleSetVersion &&
    detail.positionDataVersion === snapshot.evaluation.positionDataVersion
  );
}
