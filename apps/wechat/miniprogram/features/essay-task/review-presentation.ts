import type {
  EssayReviewIntentView,
  EssayReviewJobView,
  EssayReviewResponse,
} from "../../api/essay-intelligence";
import { presentFinding, type FindingView } from "./presentation";

export type ReviewDisplayState =
  "idle" | "waiting_quota" | "queued" | "running" | "completed" | "blocked" | "failed";

export interface ReviewDimensionView {
  criterionCode: string;
  label: string;
  scoreRangeLabel: string;
  maximumLabel: string;
  rationale: string;
  answerEvidence: string[];
  materialEvidence: string[];
}

export interface GroundedReviewView {
  reviewVersionId: string;
  trainingEstimateLabel: string;
  recommendedLabel: string;
  maximumLabel: string;
  confidenceLabel: string;
  confidenceLow: boolean;
  confidenceReasons: string[];
  dimensions: ReviewDimensionView[];
  findings: FindingView[];
  auditLabel: string;
}

export interface ReviewDisplay {
  state: ReviewDisplayState;
  title: string;
  message: string;
  result: GroundedReviewView | null;
}

export function presentReviewIntent(intent: EssayReviewIntentView): ReviewDisplay {
  if (intent.status === "waiting_quota") {
    if (!intent.retryAfter || !intent.quota) {
      return safeTerminal(
        "failed",
        "评阅额度状态不完整",
        "服务端没有返回额度恢复时间或额度快照，页面不会猜测等待时间。请重新加载核对状态。",
      );
    }
    const retryLabel = formatRetryAfter(intent.retryAfter);
    if (!retryLabel) {
      return safeTerminal(
        "failed",
        "评阅额度状态不完整",
        "服务端返回的额度恢复时间不可解析，页面不会猜测等待时间。请重新加载核对状态。",
      );
    }
    const quotaLabel = `今日已使用 ${String(intent.quota.used)}/${String(intent.quota.limit)} 次。`;
    return {
      state: "waiting_quota",
      title: "答案已保存，评阅额度待恢复",
      message: `${quotaLabel}预计 ${retryLabel} 后可继续建立评阅；答案、复盘和评阅意图都已保存在你的账户中。`,
      result: null,
    };
  }
  if (intent.status === "pending") {
    return pendingDisplay(
      "queued",
      "正在建立评阅任务",
      "答案和复盘已保存，正在确认本版答案唯一对应的证据化评阅任务。",
    );
  }
  if (intent.jobStatus === "failed") {
    return safeTerminal(
      "failed",
      "评阅任务失败",
      "本次没有形成完整评阅版本，因此不展示任何部分分数。可基于已保存的 job 重新评阅。",
    );
  }
  if (intent.jobStatus === "running") {
    return pendingDisplay(
      "running",
      "AI 正在核对证据",
      "正在逐项核对答案原句、材料原句和评分点，请保持页面打开。",
    );
  }
  return pendingDisplay(
    "queued",
    intent.jobStatus === "succeeded" ? "评阅已完成，正在同步" : "评阅已排队",
    "评阅任务已绑定本版答案，页面正在同步完整终态，不会展示未完成的分数。",
  );
}

export function presentReviewJob(job: EssayReviewJobView): ReviewDisplay {
  if (job.status === "queued") {
    return pendingDisplay(
      "queued",
      "评阅已排队",
      "已绑定本版答案，正在等待证据化评阅。不会展示未完成的分数。",
    );
  }
  if (job.status === "running") {
    return pendingDisplay(
      "running",
      "AI 正在核对证据",
      "正在逐项核对答案原句、材料原句和评分点，请保持页面打开。",
    );
  }
  if (job.status === "failed") {
    return safeTerminal(
      "failed",
      "评阅任务失败",
      "本次没有形成完整评阅版本，因此不显示任何部分分数。答案版本和 jobId 已保留，可重新加载确认终态。",
    );
  }
  if (!job.review) {
    return safeTerminal(
      "failed",
      "评阅结果不完整",
      "任务已结束但没有返回完整评阅版本，页面不会推测或拼接分数。",
    );
  }
  return presentGroundedReview(job.review);
}

export function presentGroundedReview(review: EssayReviewResponse): ReviewDisplay {
  if (review.status === "blocked") {
    const codes = review.readiness.blockingCodes.join("、");
    return safeTerminal(
      "blocked",
      "本版无法可靠估分",
      codes
        ? `证据门禁未通过：${codes}。本次不展示部分分数，请先补全材料、题干、答案或 OCR 确认。`
        : "证据门禁未通过，本次不展示部分分数。",
    );
  }
  if (review.status === "failed") {
    return safeTerminal(
      "failed",
      "评阅未形成",
      "模型输出没有通过完整合同校验，本次不展示任何部分分数。",
    );
  }
  if (!review.score || !review.audit) {
    return safeTerminal(
      "failed",
      "评阅合同不完整",
      "已完成状态缺少总分区间或审计版本，本页拒绝展示不完整结果。",
    );
  }
  const score = review.score;
  return {
    state: "completed",
    title: "证据化评阅完成",
    message: "以下为 AI 训练估分区间，不等同于人工阅卷或正式考试成绩。",
    result: {
      reviewVersionId: review.reviewVersionId,
      trainingEstimateLabel: formatRange(score.scoreRange),
      recommendedLabel: formatScore(score.scoreRange.recommended),
      maximumLabel: formatScore(score.maximum),
      confidenceLabel: score.confidence === "low" ? "低置信度" : "中等置信度",
      confidenceLow: score.confidence === "low",
      confidenceReasons: score.confidenceReasons,
      dimensions: review.dimensions.map((dimension) => ({
        criterionCode: dimension.criterionCode,
        label: dimension.label,
        scoreRangeLabel: formatRange(dimension.scoreRange),
        maximumLabel: formatScore(dimension.maximum),
        rationale: dimension.rationale,
        answerEvidence: dimension.answerEvidenceRefs.map((item) => item.quote),
        materialEvidence: dimension.materialEvidenceRefs.map((item) => item.quote),
      })),
      findings: review.findings.map(presentFinding),
      auditLabel: `${review.audit.provider} · ${review.audit.model} · ${review.audit.contractVersion}`,
    },
  };
}

function pendingDisplay(
  state: "queued" | "running",
  title: string,
  message: string,
): ReviewDisplay {
  return { state, title, message, result: null };
}

function safeTerminal(state: "blocked" | "failed", title: string, message: string): ReviewDisplay {
  return { state, title, message, result: null };
}

function formatRange(range: { lower: number; upper: number }): string {
  return `${formatScore(range.lower)}–${formatScore(range.upper)}`;
}

function formatScore(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/u, "");
}

function formatRetryAfter(value: string): string | null {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const chinaTime = new Date(date.getTime() + 8 * 60 * 60 * 1_000);
  const month = String(chinaTime.getUTCMonth() + 1).padStart(2, "0");
  const day = String(chinaTime.getUTCDate()).padStart(2, "0");
  const hour = String(chinaTime.getUTCHours()).padStart(2, "0");
  const minute = String(chinaTime.getUTCMinutes()).padStart(2, "0");
  return `${month}月${day}日 ${hour}:${minute}`;
}
