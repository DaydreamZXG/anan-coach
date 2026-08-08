import { ApiClientError } from "../../../miniprogram/api/client";
import type {
  CommunityCandidateConfirmCommand,
  CommunityCardWorkspace,
  CommunityDiscoveryCard,
  CommunityFeedbackReason,
  CommunityFeedbackVote,
  CommunityReportCategory,
  CommunityRewardLedgerResponse,
  CommunityRightsDeclaration,
  CommunitySourceType,
  CommunityStructureCandidate,
  CommunityTrialCompleteCommand,
} from "../../api/community-method-cards";

export const commercialPageStates = [
  "default",
  "loading",
  "empty",
  "partial",
  "error",
  "expired",
  "conflict",
  "recovery",
  "processing",
] as const;

export type CommercialPageState = (typeof commercialPageStates)[number];

export const communitySourceOptions: ReadonlyArray<{
  value: CommunitySourceType;
  label: string;
  description: string;
}> = [
  { value: "text", label: "文字", description: "直接写下你的做题方法" },
  { value: "voice_transcript", label: "语音", description: "使用已转写的私有录音" },
  {
    value: "handwriting_extraction",
    label: "手写笔记",
    description: "使用已识别的私有手写内容",
  },
  { value: "reflection", label: "复盘", description: "从个人错题复盘开始整理" },
];

export const rightsOptions: ReadonlyArray<{
  value: CommunityRightsDeclaration;
  label: string;
  publishable: boolean;
}> = [
  { value: "original", label: "这是我的原创表达", publishable: true },
  {
    value: "authorized_derivative",
    label: "我有权公开这份改编表达",
    publishable: true,
  },
  {
    value: "private_third_party",
    label: "含第三方内容，仅供我自己使用",
    publishable: false,
  },
];

export const feedbackReasons: ReadonlyArray<{
  value: CommunityFeedbackReason;
  label: string;
  votes: readonly CommunityFeedbackVote[];
}> = [
  { value: "clear", label: "讲得清楚", votes: ["helpful"] },
  { value: "transferable", label: "能迁移到新题", votes: ["helpful"] },
  { value: "too_specific", label: "只适合这一题", votes: ["not_helpful"] },
  { value: "unclear", label: "表达不清楚", votes: ["not_helpful"] },
  { value: "incorrect", label: "方法可能有误", votes: ["not_helpful"] },
  { value: "unsafe", label: "内容不合适", votes: ["not_helpful"] },
];

export const reportCategories: ReadonlyArray<{
  value: CommunityReportCategory;
  label: string;
}> = [
  { value: "copyright", label: "疑似侵权" },
  { value: "incorrect", label: "方法错误" },
  { value: "unsafe", label: "不安全内容" },
  { value: "advertising", label: "广告营销" },
  { value: "contact_information", label: "引导私下联系" },
  { value: "other_fixed", label: "其他平台规则问题" },
];

export interface CommercialErrorView {
  state: Exclude<CommercialPageState, "default" | "loading" | "empty" | "partial" | "processing">;
  title: string;
  message: string;
  requestId: string;
}

export function mapCommunityError(error: unknown): CommercialErrorView {
  if (!(error instanceof ApiClientError)) {
    return {
      state: "error",
      title: "内容没有载入",
      message: "页面遇到无法识别的问题。请保留当前操作并重新进入。",
      requestId: "",
    };
  }
  if (error.statusCode === 401 || error.statusCode === 403) {
    return {
      state: "expired",
      title: "登录状态已失效",
      message: "私有草稿不会跨账号展示，请重新登录后继续。",
      requestId: error.requestId,
    };
  }
  if (error.statusCode === 409) {
    return {
      state: "conflict",
      title: "内容刚刚有更新",
      message: "另一项操作已更新这张方法卡。刷新最新内容后再继续。",
      requestId: error.requestId,
    };
  }
  if (
    error.statusCode === 503 &&
    ["community_policy_unavailable", "community_service_unavailable"].includes(
      error.payload?.code ?? "",
    )
  ) {
    return {
      state: "error",
      title: "社区试用还没开放",
      message: "试用规则尚未发布，这次没有创建或提交任何记录。",
      requestId: error.requestId,
    };
  }
  if (error.statusCode === 0 || error.statusCode >= 500) {
    return {
      state: "recovery",
      title: "还没收到操作结果",
      message: "页面会核对刚才那次操作，不会重复记录。",
      requestId: error.requestId,
    };
  }
  return {
    state: "error",
    title: "操作没有完成",
    message: error.message,
    requestId: error.requestId,
  };
}

export function assertStructureCandidate(
  candidate: CommunityStructureCandidate,
  workspace: CommunityCardWorkspace,
): CommunityStructureCandidate {
  if (!candidate.candidateId.trim() || candidate.baseVersionId !== workspace.headVersionId) {
    throw new Error("AI 整理结果与当前草稿不一致，请重新生成。");
  }
  if (candidate.status === "ready") {
    if (
      !candidate.title.trim() ||
      !candidate.recognition.trim() ||
      candidate.methodSteps.length === 0 ||
      candidate.methodSteps.some((step) => !step.trim())
    ) {
      throw new Error("AI 候选结构不完整。");
    }
  }
  return candidate;
}

export function buildConfirmation(
  workspace: CommunityCardWorkspace,
  candidate: CommunityStructureCandidate,
  values: {
    title: string;
    recognition: string;
    methodStepsText: string;
    applicabilityText: string;
    exclusionsText: string;
    pitfallsText: string;
    rightsDeclaration: CommunityRightsDeclaration;
  },
): CommunityCandidateConfirmCommand {
  if (candidate.status !== "ready") throw new Error("AI 候选尚未就绪。");
  if (candidate.baseVersionId !== workspace.headVersionId) {
    throw new Error("AI 候选已过期，请重新生成。");
  }
  const title = values.title.trim();
  const recognition = values.recognition.trim();
  const methodSteps = splitLines(values.methodStepsText);
  const applicability = splitTags(values.applicabilityText);
  const exclusions = splitLines(values.exclusionsText);
  const pitfalls = splitLines(values.pitfallsText);
  if (!title || !recognition || methodSteps.length === 0) {
    throw new Error("标题、识别信号和方法步骤都需要由你确认。");
  }
  return {
    cardId: workspace.cardId,
    baseVersionId: workspace.headVersionId,
    candidateId: candidate.candidateId,
    title,
    recognition,
    methodSteps,
    applicability,
    exclusions,
    pitfalls,
    rightsDeclaration: values.rightsDeclaration,
  };
}

export function assertFeedbackChoice(
  vote: CommunityFeedbackVote,
  reason: CommunityFeedbackReason,
): void {
  const option = feedbackReasons.find((item) => item.value === reason);
  if (!option?.votes.includes(vote)) throw new Error("反馈原因与选择不一致。");
}

export function assertEligibleTrial(command: CommunityTrialCompleteCommand): void {
  if (
    !command.exposureId.trim() ||
    !command.questionAttemptId.trim() ||
    !command.questionVersionId.trim()
  ) {
    throw new Error("缺少独立试题的有效记录，暂时不能提交试用反馈。");
  }
}

export function assertTrialCard(card: CommunityDiscoveryCard): void {
  if (card.isAuthor === true) throw new Error("作者不能试用或评价自己的方法卡。");
  if (!card.cardVersionId.trim() || !card.evidencePolicyVersion.trim()) {
    throw new Error("这张方法卡还没有完成独立试用。");
  }
  if (
    !card.title.trim() ||
    !card.recognition.trim() ||
    card.methodSteps.length === 0 ||
    card.methodSteps.some((step) => !step.trim()) ||
    !Number.isInteger(card.evidenceCount) ||
    card.evidenceCount < 0
  ) {
    throw new Error("方法卡详情或效果证据不完整。");
  }
}

export function assertRewardLedgerTruth(ledger: CommunityRewardLedgerResponse): void {
  for (const [label, values] of [
    ["当前奖励规则", ledger.activePolicyVersions],
    ["奖励批次", ledger.awardIds],
    ["奖励流水", ledger.entryIds],
  ] as const) {
    if (values.some((value) => !value.trim()) || new Set(values).size !== values.length) {
      throw new Error(`${label}记录无效。`);
    }
  }
  if (
    !Number.isInteger(ledger.aiQuotaBalance) ||
    ledger.aiQuotaBalance < 0 ||
    !Number.isInteger(ledger.membershipSecondsBalance) ||
    ledger.membershipSecondsBalance < 0
  ) {
    throw new Error("奖励权益余额无效。");
  }
  if (!ledger.entitlementVersion.trim()) throw new Error("奖励权益记录缺失。");
}

export function splitTags(value: string): string[] {
  return unique(
    value
      .split(/[，,、\n]/u)
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

export function splitLines(value: string): string[] {
  return unique(
    value
      .split(/\n/u)
      .map((item) => item.replace(/^\s*\d+[.、)]\s*/u, "").trim())
      .filter(Boolean),
  );
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}
