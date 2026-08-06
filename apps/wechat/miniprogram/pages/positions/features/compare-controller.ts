import type { QualificationApi } from "../../../api/qualification";
import {
  deliveryView,
  positionRouteSummary,
  type PositionDeliveryView,
  type PositionOperationError,
  type PositionRemoteStatus,
} from "./contracts";
import { positionOperationError } from "./error-state";
import {
  positionComparisonItemView,
  positionFitView,
  type PositionComparisonItemView,
  type PositionFitView,
} from "./presentation";
import type { PositionStateStore } from "../../../features/positions/state-store";

export interface PositionComparisonFitItemView extends PositionComparisonItemView {
  fitView: PositionFitView;
  fitRequestId: string;
}

export interface PositionCompareState {
  status: PositionRemoteStatus;
  error: PositionOperationError | null;
  positionIds: string[];
  items: PositionComparisonFitItemView[];
  learnerProfileVersion: number;
  ruleSetVersion: string;
  positionDataVersion: string;
  delivery: PositionDeliveryView | null;
}

export interface PositionCompareControllerOptions {
  api: Pick<QualificationApi, "comparePositions" | "getPositionFit">;
  store: PositionStateStore;
  positionIds: string[];
  onChange: (state: PositionCompareState) => void;
}

export class PositionCompareController {
  private readonly api: Pick<QualificationApi, "comparePositions" | "getPositionFit">;
  private readonly store: PositionStateStore;
  private readonly onChange: (state: PositionCompareState) => void;
  private requestEpoch = 0;
  private disposed = false;
  private state: PositionCompareState;

  constructor(options: PositionCompareControllerOptions) {
    const selection = validateSelection(options.positionIds);
    this.api = options.api;
    this.store = options.store;
    this.onChange = options.onChange;
    this.state = {
      status: selection.error ? "error" : "idle",
      error: selection.error,
      positionIds: selection.positionIds,
      items: [],
      learnerProfileVersion: 0,
      ruleSetVersion: "",
      positionDataVersion: "",
      delivery: null,
    };
    this.publish();
  }

  snapshot(): PositionCompareState {
    return {
      ...this.state,
      error: this.state.error ? { ...this.state.error } : null,
      positionIds: [...this.state.positionIds],
      items: this.state.items.map((item) => ({
        ...item,
        highlightLabels: [...item.highlightLabels],
        fitView: cloneFitView(item.fitView),
      })),
      delivery: this.state.delivery ? { ...this.state.delivery } : null,
    };
  }

  async load(): Promise<void> {
    if (this.disposed) return;
    if (this.state.error && !this.state.error.retryable) return;
    const epoch = ++this.requestEpoch;
    this.state = { ...this.state, status: "loading", error: null };
    this.publish();
    try {
      const [response, fitResponses] = await Promise.all([
        this.api.comparePositions(this.state.positionIds),
        Promise.all(
          this.state.positionIds.map((positionId) => this.api.getPositionFit(positionId)),
        ),
      ]);
      if (!this.isCurrent(epoch)) return;
      if (
        !fitResponsesAlign(
          response.data,
          fitResponses.map((item) => item.data),
        )
      ) {
        this.state = {
          ...this.state,
          status: "versionConflict",
          error: {
            status: "versionConflict",
            title: "对比内容刚刚有更新",
            message: "岗位、资格或能力记录有变化，请重新加载后再比较。",
            requestId: [response.requestId, ...fitResponses.map((item) => item.requestId)].join(
              " / ",
            ),
            retryable: true,
          },
        };
        this.publish();
        return;
      }
      const fitByPositionId = new Map(
        fitResponses.map((fitResponse) => [fitResponse.data.position.positionId, fitResponse]),
      );
      const items = response.data.items.map((item) => {
        this.store.saveSummary(positionRouteSummary(item));
        const fitResponse = fitByPositionId.get(item.positionId);
        if (!fitResponse) throw new Error("岗位对比缺少对应的正式分差结果。");
        return {
          ...positionComparisonItemView(item, response.data.highlights),
          fitView: positionFitView(fitResponse.data.fit),
          fitRequestId: fitResponse.requestId,
        };
      });
      const delivery = comparisonDelivery(response, items);
      this.state = {
        ...this.state,
        status: items.length > 0 ? "ready" : "empty",
        error: null,
        items,
        learnerProfileVersion: response.data.learnerProfileVersion,
        ruleSetVersion: response.data.ruleSetVersion,
        positionDataVersion: response.data.positionDataVersion,
        delivery,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const operationError = positionOperationError(error);
      this.state = { ...this.state, status: operationError.status, error: operationError };
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

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }
}

function fitResponsesAlign(
  comparison: {
    examYear: number;
    learnerProfileVersion: number;
    ruleSetVersion: string;
    positionDataVersion: string;
    items: Array<{ positionId: string }>;
  },
  fits: Array<{
    examYear: number;
    learnerProfileVersion: number;
    ruleSetVersion: string;
    positionDataVersion: string;
    position: { positionId: string };
  }>,
): boolean {
  const expectedIds = new Set(comparison.items.map((item) => item.positionId));
  return (
    fits.length === expectedIds.size &&
    fits.every(
      (fit) =>
        expectedIds.has(fit.position.positionId) &&
        fit.examYear === comparison.examYear &&
        fit.learnerProfileVersion === comparison.learnerProfileVersion &&
        fit.ruleSetVersion === comparison.ruleSetVersion &&
        fit.positionDataVersion === comparison.positionDataVersion,
    )
  );
}

function comparisonDelivery(
  response: Awaited<ReturnType<QualificationApi["comparePositions"]>>,
  items: PositionComparisonFitItemView[],
): PositionDeliveryView {
  const delivery = deliveryView(response);
  const hasPartialEvidence = items.some((item) => item.fitView.evidenceStatus === "partial");
  const hasStaleEvidence = items.some((item) => item.fitView.evidenceStatus === "stale");
  return {
    ...delivery,
    partial: hasPartialEvidence,
    stale: hasStaleEvidence,
    partialLabel: hasPartialEvidence ? "部分岗位的行测与申论证据未完整，分带与分差不可用" : "",
    staleLabel: hasStaleEvidence ? "部分岗位的能力数据已过期，分数区间与分差已停用" : "",
  };
}

function cloneFitView(fit: PositionFitView): PositionFitView {
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

function validateSelection(values: string[]): {
  positionIds: string[];
  error: PositionOperationError | null;
} {
  const positionIds = values.map((value) => value.trim());
  const uniqueCount = new Set(positionIds).size;
  if (
    positionIds.length < 2 ||
    positionIds.length > 3 ||
    positionIds.some((value) => !value) ||
    uniqueCount !== positionIds.length
  ) {
    return {
      positionIds,
      error: {
        status: "error",
        title: "还不能打开岗位对比",
        message: "请返回岗位列表，重新选择 2–3 个不同岗位后再对比。",
        requestId: "",
        retryable: false,
      },
    };
  }
  return { positionIds, error: null };
}
