import type {
  CommunityCardState,
  CommunityCardVersion,
  CommunityDiscoveryCard,
} from "../../api/community-method-cards";
import type { CommercialPageState } from "./contracts";

export interface CommercialStateView {
  title: string;
  description: string;
  actionLabel: string;
}

export interface ContributionVersionView {
  versionId: string;
  versionLabel: string;
  title: string;
  confirmedLabel: string;
}

export interface TrialCardView {
  cardVersionId: string;
  title: string;
  recognition: string;
  methodSteps: string[];
  exclusions: string[];
  pitfalls: string[];
  applicabilityLabel: string;
  evidenceLabel: string;
  riskLabel: string;
  canTrial: boolean;
}

const stateCopies: Record<CommercialPageState, CommercialStateView> = {
  default: { title: "", description: "", actionLabel: "" },
  loading: {
    title: "正在读取权威版本",
    description: "只读取当前账号与当前版本的数据。",
    actionLabel: "",
  },
  empty: {
    title: "这里还没有内容",
    description: "完成上一项明确操作后，内容才会出现在这里。",
    actionLabel: "",
  },
  partial: {
    title: "部分证据尚未就绪",
    description: "已就绪内容会保留，缺失证据不会由客户端补造。",
    actionLabel: "重新读取",
  },
  error: {
    title: "操作没有完成",
    description: "请检查页面提示，修正后再继续。",
    actionLabel: "重新读取",
  },
  expired: {
    title: "当前会话已失效",
    description: "私有草稿不会跨账号展示，请重新登录。",
    actionLabel: "重新登录",
  },
  conflict: {
    title: "版本已经变化",
    description: "刷新服务端权威版本后再继续编辑。",
    actionLabel: "刷新版本",
  },
  recovery: {
    title: "等待恢复原操作",
    description: "不要重复创建。系统会按原请求检查处理结果。",
    actionLabel: "恢复",
  },
  processing: {
    title: "正在处理",
    description: "内容尚未成为可发布事实，请等待结果。",
    actionLabel: "",
  },
};

export function commercialStateView(state: CommercialPageState): CommercialStateView {
  return stateCopies[state];
}

export function cardStateLabel(state: CommunityCardState): string {
  const labels: Record<CommunityCardState, string> = {
    private_draft: "私有草稿",
    automated_review: "五项检查中",
    trial: "独立试用中",
    community_validated: "已有独立证据",
    revision_required: "需要修改",
    rejected: "未通过检查",
    restricted: "已限制展示",
    retired: "已停用",
  };
  return labels[state];
}

export function presentTrialCard(card: CommunityDiscoveryCard): TrialCardView {
  return {
    cardVersionId: card.cardVersionId,
    title: card.title,
    recognition: card.recognition,
    methodSteps: [...card.methodSteps],
    exclusions: [...card.exclusions],
    pitfalls: [...card.pitfalls],
    applicabilityLabel: card.applicability.length
      ? card.applicability.join(" · ")
      : "适用范围未标注",
    evidenceLabel:
      card.conservativeScore === null
        ? `${String(card.evidenceCount)} 条独立证据 · 证据积累中`
        : `${String(card.evidenceCount)} 条独立证据 · 已按保守效果政策排序`,
    riskLabel: card.riskFlags.length
      ? `风险提示：${card.riskFlags.join("、")}`
      : "当前版本无新增风险标记",
    canTrial: card.isAuthor !== true,
  };
}

export function presentVersion(version: CommunityCardVersion): ContributionVersionView {
  return {
    versionId: version.versionId,
    versionLabel: `V${String(version.versionNumber)}`,
    title: version.title?.trim() || "未确认结构",
    confirmedLabel: version.authorConfirmedAt
      ? formatDateTime(version.authorConfirmedAt)
      : "尚未确认",
  };
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return `${two(date.getMonth() + 1)}.${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}
