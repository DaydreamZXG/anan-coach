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
        "评阅次数暂时用完了，恢复时间没拿到，请重新加载看看。",
      );
    }
    if (intent.quota.accessMode !== "entitlement_quota" || intent.quota.limit === null) {
      return safeTerminal("failed", "评阅次数没有同步", "请重新加载后再试。");
    }
    const retryLabel = formatRetryAfter(intent.retryAfter);
    if (!retryLabel) {
      return safeTerminal("failed", "评阅额度状态不完整", "恢复时间没读出来，请重新加载看看。");
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
    return pendingDisplay("queued", "正在准备本次评阅", "答案和复盘已保存，岸岸正在开始完整评阅。");
  }
  if (intent.jobStatus === "failed") {
    return safeTerminal(
      "failed",
      "这次评阅没有完成",
      "本次没有形成完整评阅，因此不展示部分分数。答案已保存，可以重新评阅。",
    );
  }
  if (intent.jobStatus === "running") {
    return pendingDisplay(
      "running",
      "AI 正在逐项核对",
      "正在逐项核对答案原句、材料原句和评分点，请保持页面打开。",
    );
  }
  return pendingDisplay(
    "queued",
    intent.jobStatus === "succeeded" ? "评阅已完成，正在同步" : "评阅已排队",
    "评阅正在处理这份答案，完成前不会展示不完整分数。",
  );
}

export function presentReviewJob(job: EssayReviewJobView): ReviewDisplay {
  if (job.status === "queued") {
    return pendingDisplay(
      "queued",
      "评阅已排队",
      "这份答案正在等待完整评阅，完成前不会展示不完整分数。",
    );
  }
  if (job.status === "running") {
    return pendingDisplay(
      "running",
      "AI 正在逐项核对",
      "正在逐项核对答案原句、材料原句和评分点，请保持页面打开。",
    );
  }
  if (job.status === "failed") {
    return safeTerminal(
      "failed",
      "这次评阅没有完成",
      "本次没有形成完整评阅，因此不显示部分分数。答案已保存，可以重新加载或重评。",
    );
  }
  if (!job.review) {
    return safeTerminal(
      "failed",
      "评阅结果不完整",
      "这次没有返回完整评阅，页面不会猜测或拼接分数。",
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
        ? `还差一些材料没补全，部分分数暂时看不了。请先补全材料、题干或答案确认。`
        : "还差一些材料没补全，部分分数暂时看不了。",
    );
  }
  if (review.status === "failed") {
    return safeTerminal("failed", "评阅未形成", "这次评阅内容不完整，因此不展示任何部分分数。");
  }
  if (!review.score || !review.audit) {
    return safeTerminal("failed", "评阅结果不完整", "这次缺少完整分数区间，因此不展示不完整结果。");
  }
  const score = review.score;
  return {
    state: "completed",
    title: "完整评阅已完成",
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
