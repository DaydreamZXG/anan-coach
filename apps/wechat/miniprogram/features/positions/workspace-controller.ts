import type { QualificationApi } from "../../api/qualification";
import { abilityEstimateView, type AbilityEstimateView } from "../ability/presentation";
import {
  deliveryView,
  positionRouteSummary,
  type PositionDeliveryView,
  type PositionOperationError,
  type PositionRemoteStatus,
} from "./contracts";
import { positionOperationError } from "./error-state";
import {
  positionWorkspaceItemView,
  qualificationGapView,
  type PositionWorkspaceItemView,
  type QualificationGapView,
} from "./presentation";
import type { PositionSelectionResult } from "./discovery-controller";
import type { PositionStateStore } from "./state-store";

export type WorkspaceSectionStatus = "idle" | "loading" | "ready" | "error";

export interface SelectablePositionWorkspaceItemView extends PositionWorkspaceItemView {
  comparisonSelected: boolean;
}

export interface PositionWorkspaceState {
  status: PositionRemoteStatus;
  error: PositionOperationError | null;
  refreshError: PositionOperationError | null;
  items: SelectablePositionWorkspaceItemView[];
  primaryGoal: SelectablePositionWorkspaceItemView | null;
  candidateItems: SelectablePositionWorkspaceItemView[];
  selectedIds: string[];
  examYear: number;
  learnerProfileVersion: number;
  ruleSetVersion: string;
  positionDataVersion: string;
  hasMore: boolean;
  nextCursor: string;
  delivery: PositionDeliveryView | null;
  ability: AbilityEstimateView | null;
  qualificationStatus: WorkspaceSectionStatus;
  qualificationError: PositionOperationError | null;
  qualificationGaps: QualificationGapView[];
  qualificationComplete: boolean;
  totalAffectedPositions: number;
  qualificationDelivery: PositionDeliveryView | null;
}

export interface PositionWorkspaceControllerOptions {
  qualificationApi: Pick<QualificationApi, "getPositionWorkspace" | "getQualificationGaps">;
  store: PositionStateStore;
  onChange: (state: PositionWorkspaceState) => void;
}

export class PositionWorkspaceController {
  private readonly qualificationApi: Pick<
    QualificationApi,
    "getPositionWorkspace" | "getQualificationGaps"
  >;
  private readonly store: PositionStateStore;
  private readonly onChange: (state: PositionWorkspaceState) => void;
  private disposed = false;
  private workspaceEpoch = 0;
  private qualificationEpoch = 0;
  private state: PositionWorkspaceState;

  constructor(options: PositionWorkspaceControllerOptions) {
    this.qualificationApi = options.qualificationApi;
    this.store = options.store;
    this.onChange = options.onChange;
    this.state = {
      status: "idle",
      error: null,
      refreshError: null,
      items: [],
      primaryGoal: null,
      candidateItems: [],
      selectedIds: this.store.loadComparison(),
      examYear: 0,
      learnerProfileVersion: 0,
      ruleSetVersion: "",
      positionDataVersion: "",
      hasMore: false,
      nextCursor: "",
      delivery: null,
      ability: null,
      qualificationStatus: "idle",
      qualificationError: null,
      qualificationGaps: [],
      qualificationComplete: false,
      totalAffectedPositions: 0,
      qualificationDelivery: null,
    };
    this.publish();
  }

  snapshot(): PositionWorkspaceState {
    return cloneWorkspaceState(this.state);
  }

  async load(): Promise<void> {
    await Promise.all([this.refreshPositions(), this.loadQualificationGaps()]);
  }

  async refreshPositions(): Promise<void> {
    if (this.disposed) return;
    const epoch = ++this.workspaceEpoch;
    const selectedIds = this.store.loadComparison();
    const retainedItems = this.state.items.map((item) =>
      selectableWorkspaceItem(item, selectedIds),
    );
    const hasRetainedWorkspace = retainedItems.length > 0 || this.state.delivery !== null;
    this.state = {
      ...this.state,
      selectedIds,
      items: retainedItems,
      primaryGoal: retainedItems.find((item) => item.isPrimaryGoal) ?? null,
      candidateItems: retainedItems.filter((item) => !item.isPrimaryGoal).slice(0, 4),
      status: hasRetainedWorkspace ? this.state.status : "loading",
      error: null,
      refreshError: null,
      delivery:
        hasRetainedWorkspace && this.state.delivery
          ? {
              ...this.state.delivery,
              processing: true,
              processingLabel: "正在读取当前岗位工作台版本",
            }
          : this.state.delivery,
    };
    this.publish();
    try {
      const response = await this.qualificationApi.getPositionWorkspace({ limit: 8 });
      if (!this.isWorkspaceCurrent(epoch)) return;
      const currentSelectedIds = this.store.loadComparison();
      const items = response.data.items.map((item) => {
        this.store.saveSummary(positionRouteSummary(item));
        return selectableWorkspaceItem(positionWorkspaceItemView(item), currentSelectedIds);
      });
      const delivery = workspaceDelivery(response, items);
      this.state = {
        ...this.state,
        status: items.length > 0 ? "ready" : "empty",
        error: null,
        refreshError: null,
        items,
        selectedIds: currentSelectedIds,
        primaryGoal: items.find((item) => item.isPrimaryGoal) ?? null,
        candidateItems: items.filter((item) => !item.isPrimaryGoal).slice(0, 4),
        examYear: response.data.examYear,
        learnerProfileVersion: response.data.learnerProfileVersion,
        ruleSetVersion: response.data.ruleSetVersion,
        positionDataVersion: response.data.positionDataVersion,
        hasMore: response.data.hasMore,
        nextCursor: response.data.nextCursor ?? "",
        delivery,
        ability: abilityEstimateView(response.data.abilityEstimate),
      };
      this.publish();
    } catch (error) {
      if (!this.isWorkspaceCurrent(epoch)) return;
      const operationError = positionOperationError(error);
      if (hasRetainedWorkspace) {
        this.state = {
          ...this.state,
          status: this.state.items.length > 0 ? "ready" : "empty",
          error: null,
          refreshError: operationError,
          items: retainedItems,
          delivery: this.state.delivery
            ? {
                ...this.state.delivery,
                stale: true,
                processing: false,
                staleLabel: "刷新失败，当前保留上次已加载的工作台版本",
                processingLabel: "",
              }
            : null,
        };
      } else {
        this.state = {
          ...this.state,
          status: operationError.status,
          error: operationError,
        };
      }
      this.publish();
    }
  }

  async loadQualificationGaps(): Promise<void> {
    if (this.disposed) return;
    const epoch = ++this.qualificationEpoch;
    this.state = {
      ...this.state,
      qualificationStatus: "loading",
      qualificationError: null,
    };
    this.publish();
    try {
      const response = await this.qualificationApi.getQualificationGaps();
      if (!this.isQualificationCurrent(epoch)) return;
      this.state = {
        ...this.state,
        qualificationStatus: "ready",
        qualificationError: null,
        qualificationGaps: response.data.gaps.map(qualificationGapView),
        qualificationComplete: response.data.complete,
        totalAffectedPositions: response.data.totalAffectedPositions,
        qualificationDelivery: deliveryView(response),
      };
      this.publish();
    } catch (error) {
      if (!this.isQualificationCurrent(epoch)) return;
      this.state = {
        ...this.state,
        qualificationStatus: "error",
        qualificationError: positionOperationError(error),
      };
      this.publish();
    }
  }

  toggleComparison(positionId: string): PositionSelectionResult {
    if (!this.state.items.some((item) => item.positionId === positionId)) {
      return { accepted: false, reason: "missing" };
    }
    const exists = this.state.selectedIds.includes(positionId);
    if (!exists && this.state.selectedIds.length >= 3) {
      return { accepted: false, reason: "limit" };
    }
    const selectedIds = exists
      ? this.state.selectedIds.filter((item) => item !== positionId)
      : [...this.state.selectedIds, positionId];
    this.store.saveComparison(selectedIds);
    const items = this.state.items.map((item) => selectableWorkspaceItem(item, selectedIds));
    this.state = {
      ...this.state,
      selectedIds,
      items,
      primaryGoal: items.find((item) => item.isPrimaryGoal) ?? null,
      candidateItems: items.filter((item) => !item.isPrimaryGoal).slice(0, 4),
    };
    this.publish();
    return { accepted: true, reason: exists ? "removed" : "selected" };
  }

  clearComparison(): void {
    this.store.saveComparison([]);
    const items = this.state.items.map((item) => selectableWorkspaceItem(item, []));
    this.state = {
      ...this.state,
      selectedIds: [],
      items,
      primaryGoal: items.find((item) => item.isPrimaryGoal) ?? null,
      candidateItems: items.filter((item) => !item.isPrimaryGoal).slice(0, 4),
    };
    this.publish();
  }

  summary(positionId: string) {
    const item = this.state.items.find((candidate) => candidate.positionId === positionId);
    return item ? positionRouteSummary(item) : null;
  }

  dispose(): void {
    this.disposed = true;
    this.workspaceEpoch += 1;
    this.qualificationEpoch += 1;
  }

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }

  private isWorkspaceCurrent(epoch: number): boolean {
    return !this.disposed && epoch === this.workspaceEpoch;
  }

  private isQualificationCurrent(epoch: number): boolean {
    return !this.disposed && epoch === this.qualificationEpoch;
  }
}

function selectableWorkspaceItem(
  item: PositionWorkspaceItemView,
  selectedIds: string[],
): SelectablePositionWorkspaceItemView {
  return { ...item, comparisonSelected: selectedIds.includes(item.positionId) };
}

function workspaceDelivery(
  response: Awaited<ReturnType<QualificationApi["getPositionWorkspace"]>>,
  items: SelectablePositionWorkspaceItemView[],
): PositionDeliveryView {
  const delivery = deliveryView(response);
  const hasPartialEvidence = items.some((item) => item.fitView.evidenceStatus === "partial");
  const hasStaleEvidence = items.some((item) => item.fitView.evidenceStatus === "stale");
  return {
    ...delivery,
    partial: hasPartialEvidence,
    stale: hasStaleEvidence,
    partialLabel: hasPartialEvidence ? "部分岗位因行测与申论证据未完整，暂不形成分带与分差" : "",
    staleLabel: hasStaleEvidence ? "能力证据已过期，相关岗位分带与分差已停用" : "",
  };
}

function cloneWorkspaceState(state: PositionWorkspaceState): PositionWorkspaceState {
  const items = state.items.map(cloneWorkspaceItem);
  return {
    ...state,
    error: state.error ? { ...state.error } : null,
    refreshError: state.refreshError ? { ...state.refreshError } : null,
    items,
    primaryGoal: state.primaryGoal ? cloneWorkspaceItem(state.primaryGoal) : null,
    candidateItems: state.candidateItems.map(cloneWorkspaceItem),
    selectedIds: [...state.selectedIds],
    delivery: state.delivery ? { ...state.delivery } : null,
    ability: state.ability ? { ...state.ability } : null,
    qualificationError: state.qualificationError ? { ...state.qualificationError } : null,
    qualificationGaps: state.qualificationGaps.map((item) => ({ ...item })),
    qualificationDelivery: state.qualificationDelivery ? { ...state.qualificationDelivery } : null,
  };
}

function cloneWorkspaceItem(
  item: SelectablePositionWorkspaceItemView,
): SelectablePositionWorkspaceItemView {
  return {
    ...item,
    pendingRequirementIds: [...item.pendingRequirementIds],
    notMetRequirementIds: [...item.notMetRequirementIds],
    reasonCodes: [...item.reasonCodes],
    fit: cloneFitAssessment(item.fit),
    fitView: {
      ...item.fitView,
      evidenceReasonCodes: [...item.fitView.evidenceReasonCodes],
      requirementsBurden: {
        ...item.fitView.requirementsBurden,
        pendingRequirementIds: [...item.fitView.requirementsBurden.pendingRequirementIds],
        hardConditionNotMetRequirementIds: [
          ...item.fitView.requirementsBurden.hardConditionNotMetRequirementIds,
        ],
      },
      scoreGap: item.fitView.scoreGap ? { ...item.fitView.scoreGap } : null,
    },
  };
}

function cloneFitAssessment(
  fit: SelectablePositionWorkspaceItemView["fit"],
): SelectablePositionWorkspaceItemView["fit"] {
  return {
    ...fit,
    evidenceReasonCodes: [...fit.evidenceReasonCodes],
    requirementsBurden: {
      ...fit.requirementsBurden,
      pendingRequirementIds: [...fit.requirementsBurden.pendingRequirementIds],
      hardConditionNotMetRequirementIds: [
        ...fit.requirementsBurden.hardConditionNotMetRequirementIds,
      ],
    },
    scoreGap: fit.scoreGap ? { ...fit.scoreGap } : null,
  };
}
