import type { QualificationApi } from "../../api/qualification";
import type { PositionDiscoveryDetail } from "../../api/generated/types.gen";
import { abilityEstimateView } from "../ability/presentation";
import {
  requestTrace,
  targetValidityView,
  type PositionOperationError,
  type PositionRequestTrace,
} from "./contracts";
import { positionOperationError } from "./error-state";

export type PositionBriefingStatus =
  "idle" | "loading" | "ready" | "network" | "notFound" | "error";

export interface PositionBriefingView {
  examLabel: string;
  positionName: string;
  departmentFull: string;
  locationLabel: string;
  positionCodeLabel: string;
  paperTypeLabel: string;
  subjectSummary: string;
  extraExamLabel: string;
  headcountLabel: string;
  targetScoreLabel: string;
  targetEvidenceLabel: string;
  targetValidityLabel: string;
  abilityStatusLabel: string;
  abilityNextStepLabel: string;
}

export interface PositionBriefingState {
  status: PositionBriefingStatus;
  positionId: string;
  view: PositionBriefingView | null;
  error: PositionOperationError | null;
  trace: PositionRequestTrace | null;
}

export interface PositionBriefingControllerOptions {
  api: Pick<QualificationApi, "getPositionDetail">;
  positionId: string;
  onChange: (state: PositionBriefingState) => void;
}

export class PositionBriefingController {
  private readonly api: Pick<QualificationApi, "getPositionDetail">;
  private readonly onChange: (state: PositionBriefingState) => void;
  private requestEpoch = 0;
  private disposed = false;
  private state: PositionBriefingState;

  constructor(options: PositionBriefingControllerOptions) {
    this.api = options.api;
    this.onChange = options.onChange;
    const positionId = options.positionId.trim();
    this.state = {
      status: positionId ? "idle" : "error",
      positionId,
      view: null,
      error: positionId ? null : invalidBriefingRouteError(),
      trace: null,
    };
    this.publish();
  }

  snapshot(): PositionBriefingState {
    return {
      ...this.state,
      view: this.state.view ? { ...this.state.view } : null,
      error: this.state.error ? { ...this.state.error } : null,
      trace: this.state.trace ? { ...this.state.trace } : null,
    };
  }

  async load(): Promise<void> {
    if (this.disposed || !this.state.positionId) return;
    const epoch = ++this.requestEpoch;
    this.state = { ...this.state, status: "loading", error: null };
    this.publish();
    try {
      const response = await this.api.getPositionDetail(this.state.positionId);
      if (!this.isCurrent(epoch)) return;
      this.state = {
        ...this.state,
        status: "ready",
        view: positionBriefingView(response.data),
        error: null,
        trace: requestTrace(response),
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const operationError = positionOperationError(error);
      this.state = {
        ...this.state,
        status: briefingStatus(operationError),
        view: null,
        error: operationError,
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

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }
}

export function positionBriefingView(detail: PositionDiscoveryDetail): PositionBriefingView {
  const target = detail.targetScore;
  const validity = targetValidityView(target);
  const ability = abilityEstimateView(detail.abilityEstimate);
  return {
    examLabel: `${String(detail.examYear)} 国考`,
    positionName: detail.name,
    departmentFull: detail.departmentFull,
    locationLabel: [detail.region, detail.city].filter(Boolean).join(" · "),
    positionCodeLabel: `职位代码 ${detail.positionCode}`,
    paperTypeLabel: paperTypeLabel(detail.paperType),
    subjectSummary: `公共科目按行测和申论两条证据链准备，当前岗位对应${paperTypeLabel(detail.paperType)}。`,
    extraExamLabel: detail.hasExtraExam
      ? "岗位数据标记了专业科目或额外考试，具体科目、占比和时间必须以当年官方公告为准。"
      : "当前岗位数据未标记专业科目；报名和考试前仍需核对当年官方公告。",
    headcountLabel: `计划招录 ${String(detail.headcount)} 人`,
    targetScoreLabel: target
      ? `${target.lowerBound.toFixed(1)}–${target.upperBound.toFixed(1)} 分，建议点 ${target.recommended.toFixed(1)} 分`
      : "当前没有可用的目标分证据",
    targetEvidenceLabel: target
      ? `${String(target.sampleCount)} 条样本 · ${targetEvidenceLabel(target.evidenceLevel)}`
      : "不会用招录人数或客户端估算代替目标分",
    targetValidityLabel: validity.available
      ? `${validity.generatedLabel} · ${validity.label}`
      : validity.label,
    abilityStatusLabel: ability.statusLabel,
    abilityNextStepLabel: ability.canCalculatePositionGap
      ? "行测、申论正式证据已完整，系统可在同一考试年度内计算岗位分差。"
      : `${ability.missingLabel}；补齐前只展示岗位事实，不生成录取概率或伪造总分分差。`,
  };
}

function paperTypeLabel(paperType: string | null | undefined): string {
  if (!paperType) return "试卷类型待确认";
  return (
    {
      administrativeLaw: "行政执法类试卷",
      subProvincial: "副省级试卷",
      prefecture: "地市级试卷",
    }[paperType] ?? paperType
  );
}

function targetEvidenceLabel(level: "direct" | "strong" | "limited" | "unavailable"): string {
  return {
    direct: "岗位直接证据",
    strong: "强关联样本",
    limited: "有限样本",
    unavailable: "证据不可用",
  }[level];
}

function briefingStatus(error: PositionOperationError): PositionBriefingStatus {
  if (error.status === "network") return "network";
  if (error.status === "notFound") return "notFound";
  return "error";
}

function invalidBriefingRouteError(): PositionOperationError {
  return {
    status: "error",
    title: "考情说明地址无效",
    message: "当前地址缺少明确的 positionId，客户端不会根据缓存猜测岗位。",
    requestId: "",
    retryable: false,
  };
}
