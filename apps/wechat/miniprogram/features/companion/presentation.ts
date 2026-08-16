import type {
  CompanionAskResponse,
  CompanionCitation,
  LearnerCoachContext,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { ApiClientError } from "../../api/client";

export type CompanionCitationView = CompanionCitation & {
  sourceLabel: string;
  summaryItems: string[];
};

export type CompanionAnswerView = Omit<CompanionAskResponse, "citations"> & {
  citations: CompanionCitationView[];
  quotaRemaining: number | null;
  quotaUnlimited: boolean;
  quotaSummaryLabel: string;
  stateLabel: string;
  confidenceLabel: string;
  agentLabel: string;
  answerTypeLabel: string;
};

export interface CompanionFailureView {
  semanticState: CompanionAskResponse["semanticState"];
  message: string;
  requestId: string;
}

export type CompanionContextSourceState<T> =
  | { status: "loading" }
  | { status: "ready"; value: T }
  | { status: "stale"; value: T; message: string; requestId: string }
  | { status: "unavailable"; message: string; requestId: string };

export interface CompanionContextView {
  contextStatus: "loading" | "ready" | "partial" | "error";
  askAvailable: boolean;
  learnerStateVersion: string;
  contextCoreCopy: string;
  contextGoalLabel: string;
  contextTaskLabel: string;
  contextReviewLabel: string;
  contextCapacityLabel: string;
  contextEvidenceLabel: string;
  contextNoticeVisible: boolean;
  contextNoticeTitle: string;
  contextNoticeCopy: string;
  profileIssueVisible: boolean;
  profileSourceLabel: string;
  profileSourceMessage: string;
  profileSourceRequestId: string;
  todayIssueVisible: boolean;
  todaySourceLabel: string;
  todaySourceMessage: string;
  todaySourceRequestId: string;
  retryProfileVisible: boolean;
  retryTodayVisible: boolean;
  insightVisible: boolean;
  insightTitle: string;
  insightCopy: string;
  insightActionLabel: string;
  insightAction: "mastery" | "positions" | "today" | "adjustment";
}

const sourceLabels: Record<string, string> = {
  learner_activity: "练习与模考记录",
  learning_brain: "学习档案",
  daily_plan: "今日计划",
  essay_learning: "申论学习记录",
  target_position: "目标岗位资料",
  method_card: "已验证学习方法",
  user_material: "你的私人资料",
};

const answerTypeLabels: Record<string, string> = {
  operation_help: "使用说明",
  strategic_deprioritization: "学习取舍建议",
  position_explanation: "岗位判断说明",
  cause_explanation: "错因解释",
  plan_explanation: "计划说明",
  learning_explanation: "学习解答",
};

const citationFallbackSummaries: Record<string, string> = {
  learner_activity: "已读取当前账号的练习、错题与模考记录。",
  learning_brain: "已读取当前学习档案中的能力、错因与计划记录。",
  essay_learning: "已读取当前账号的申论作答、批改与复盘记录。",
  target_position: "已读取当前账号已确认的考试目标与岗位资料。",
  method_card: "已读取与当前问题相关的学习方法。",
  user_material: "已读取你授权给本次回答使用的私人资料片段。",
};

function publicCitationSummary(citation: CompanionCitation): string {
  const summary = citation.summary.replace(/\s+/gu, " ").trim();
  const exposesStructuredPayload =
    /^(?:\{|\[)/u.test(summary) ||
    /"(?:[A-Za-z][A-Za-z0-9_]*|[a-z]+_[a-z0-9_]+)"\s*:/u.test(summary);
  if (summary && !exposesStructuredPayload) return summary;
  return citationFallbackSummaries[citation.kind] ?? "已读取与当前问题相关的学习依据。";
}

interface DailyPlanGroup {
  track: string;
  title: string;
  count: number;
  totalMinutes: number | null;
  completedCount: number;
}

interface CitationPresentation {
  summary: string;
  summaryItems: string[];
}

function normalizedPlanTaskTitle(value: string): string {
  return value.replace(/\s*[（(]第\s*\d+\s*组[）)]\s*$/u, "").trim();
}

function parseDailyPlanGroup(value: string): DailyPlanGroup | null {
  const grouped =
    /^([^「]+)「(.+?)」(\d+)\s*组，共\s*(\d+)\s*分钟(?:，\s*(\d+)\s*组已完成)?$/u.exec(value);
  if (grouped?.[1] && grouped[2] && grouped[3] && grouped[4]) {
    return {
      track: grouped[1].trim(),
      title: normalizedPlanTaskTitle(grouped[2]),
      count: Number(grouped[3]),
      totalMinutes: Number(grouped[4]),
      completedCount: Number(grouped[5] ?? 0),
    };
  }

  const single = /^([^「]+)「(.+?)」(?:，\s*(\d+)\s*分钟)?(?:，\s*(已完成))?$/u.exec(value);
  if (!single?.[1] || !single[2]) return null;
  return {
    track: single[1].trim(),
    title: normalizedPlanTaskTitle(single[2]),
    count: 1,
    totalMinutes: single[3] ? Number(single[3]) : null,
    completedCount: single[4] ? 1 : 0,
  };
}

function compactDailyPlanCitation(summary: string): CitationPresentation | null {
  const normalized = summary
    .replace(/\s+/gu, " ")
    .trim()
    .replace(/[。.]\s*$/u, "");
  const match = /^今日共\s*(\d+)\s*项(?:任务)?(?:，预计\s*(\d+)\s*分钟)?[:：](.+)$/u.exec(
    normalized,
  );
  if (!match?.[1] || !match[3]) return null;
  const parts = match[3]
    .split("；")
    .map((item) => item.trim())
    .filter(Boolean);
  const continuationItems = parts.filter((item) => item.startsWith("另有 "));
  const parsed = parts.filter((item) => !item.startsWith("另有 ")).map(parseDailyPlanGroup);
  if (!parsed.length || parsed.some((item) => item === null)) return null;

  const groups = new Map<string, DailyPlanGroup>();
  for (const item of parsed) {
    if (!item) continue;
    const key = item.track + "\n" + item.title;
    const current = groups.get(key);
    if (!current) {
      groups.set(key, { ...item });
      continue;
    }
    current.count += item.count;
    current.completedCount += item.completedCount;
    current.totalMinutes =
      current.totalMinutes === null || item.totalMinutes === null
        ? null
        : current.totalMinutes + item.totalMinutes;
  }

  const taskCount = Number(match[1]);
  const planGroups = [...groups.values()];
  const parsedMinutes = planGroups.reduce<number | null>(
    (total, item) =>
      total === null || item.totalMinutes === null ? null : total + item.totalMinutes,
    0,
  );
  const totalMinutes = match[2] ? Number(match[2]) : parsedMinutes;
  const completedCount = planGroups.reduce((total, item) => total + item.completedCount, 0);
  const headlineParts = ["今日 " + String(taskCount) + " 项"];
  if (totalMinutes !== null) headlineParts.push("预计 " + String(totalMinutes) + " 分钟");
  if (completedCount > 0) headlineParts.push("已完成 " + String(completedCount) + " 项");

  const visibleGroups = planGroups.length > 4 ? planGroups.slice(0, 3) : planGroups;
  const summaryItems = visibleGroups.map((item) => {
    const parts = [item.track, item.title];
    if (item.count > 1) parts.push(String(item.count) + " 组");
    if (item.totalMinutes !== null) parts.push(String(item.totalMinutes) + " 分钟");
    if (item.completedCount > 0) parts.push(String(item.completedCount) + " 组已完成");
    return parts.join(" · ");
  });
  if (planGroups.length > visibleGroups.length) {
    summaryItems.push(
      "另有 " + String(planGroups.length - visibleGroups.length) + " 类任务，完整安排在今日计划中",
    );
  } else if (continuationItems[0]) {
    summaryItems.push(continuationItems[0]);
  }
  return { summary: headlineParts.join(" · "), summaryItems };
}

function citationPresentation(citation: CompanionCitation): CitationPresentation {
  const summary = publicCitationSummary(citation);
  if (citation.kind !== "daily_plan") return { summary, summaryItems: [] };
  return compactDailyPlanCitation(summary) ?? { summary, summaryItems: [] };
}

export function companionAnswerView(
  response: CompanionAskResponse,
  stateLabel: string,
): CompanionAnswerView {
  const quotaRemaining =
    response.quota.limit === null ? null : Math.max(0, response.quota.limit - response.quota.used);
  const quotaUnlimited = response.quota.accessMode === "unlimited";
  return {
    ...response,
    citations: response.citations.map((citation) => {
      const presentation = citationPresentation(citation);
      return {
        ...citation,
        title: citation.kind === "daily_plan" ? "今日计划概览" : citation.title,
        summary: presentation.summary,
        summaryItems: presentation.summaryItems,
        sourceLabel: sourceLabels[citation.kind] ?? "学习依据",
      };
    }),
    quotaRemaining,
    quotaUnlimited,
    quotaSummaryLabel: quotaUnlimited ? "" : "今日还可提问 " + String(quotaRemaining ?? 0) + " 次",
    stateLabel,
    confidenceLabel: { low: "依据较少", medium: "依据一般", high: "依据充分" }[response.confidence],
    agentLabel: "岸岸",
    answerTypeLabel: answerTypeLabels[response.answerType] ?? "学习解答",
  };
}

export function companionFailureView(error: unknown): CompanionFailureView {
  if (!(error instanceof ApiClientError)) {
    return {
      semanticState: "failed",
      message: "岸岸这次没有完成回答。请检查网络后编辑问题再试一次。",
      requestId: "",
    };
  }
  if (error.statusCode === 429) {
    return {
      semanticState: "quota_exhausted",
      message: "今天的提问额度已经用完，本次没有继续处理。明天可以再次提问。",
      requestId: error.requestId,
    };
  }
  if (error.statusCode === 403) {
    return {
      semanticState: "permission_denied",
      message: "当前页面里的资料不能用于这次回答。请返回原页面重新发起提问。",
      requestId: error.requestId,
    };
  }
  if (error.statusCode === 502 || error.statusCode === 503) {
    return {
      semanticState: "provider_pending",
      message: "岸岸的回答服务暂时不可用，本次没有完成回答。请稍后重试；若仍失败，请提供问题编号。",
      requestId: error.requestId,
    };
  }
  if (error.statusCode === 0) {
    return {
      semanticState: "failed",
      message: "网络连接中断，问题没有发送成功。请检查网络后编辑问题再试一次。",
      requestId: error.requestId,
    };
  }
  return {
    semanticState: "failed",
    message: "岸岸这次没有完成回答。请编辑问题后再试；若仍失败，请提供问题编号。",
    requestId: error.requestId,
  };
}

export function companionContextView(
  profileSource: CompanionContextSourceState<LearnerCoachContext>,
  todaySource: CompanionContextSourceState<TodayDashboard>,
): CompanionContextView {
  const profile = contextSourceValue(profileSource);
  const dashboard = contextSourceValue(todaySource);
  const profileReady = profileSource.status === "ready";
  const todayReady = todaySource.status === "ready";
  const hasContextValue = profile !== undefined || dashboard !== undefined;
  const contextStatus: CompanionContextView["contextStatus"] =
    profileSource.status === "loading" && todaySource.status === "loading" && !hasContextValue
      ? "loading"
      : profileReady && todayReady
        ? "ready"
        : hasContextValue
          ? "partial"
          : "error";
  const learnerStateVersion = profile?.profileVersionId.trim() ?? "";
  const askAvailable = profileReady && learnerStateVersion.length > 0;
  const evidenceCount = profile?.recognition.evidenceCount ?? 0;
  const activeTaskCount =
    dashboard?.tasks.filter((task) => task.status === "ready" || task.status === "in_progress")
      .length ?? 0;
  const goalName = dashboard?.goalGap.positionName?.trim() ?? "";
  const dueCount =
    profile && dashboard
      ? (dashboard.dueReflectionCount ?? 0) +
        (dashboard.dueVerificationCount ?? 0) +
        profile.profile.decisionSummary.reviewDueCount
      : null;
  const profileStalePrefix = profileSource.status === "stale" ? "上次同步 · " : "";
  const todayStalePrefix = todaySource.status === "stale" ? "上次同步 · " : "";
  const reviewStalePrefix =
    profileSource.status === "stale" || todaySource.status === "stale" ? "上次同步 · " : "";
  const base = {
    contextStatus,
    askAvailable,
    learnerStateVersion,
    contextCoreCopy: contextCoreCopy(profileSource, todaySource),
    contextGoalLabel: dashboard
      ? `${todayStalePrefix}${goalName ? "主目标已确认" : "主目标待确认"}`
      : "主目标状态不可用",
    contextTaskLabel: dashboard
      ? `${todayStalePrefix}今日 ${String(activeTaskCount)} 项任务`
      : "今日计划不可用",
    contextReviewLabel:
      dueCount === null
        ? "复测信息不完整"
        : `${reviewStalePrefix}${dueCount > 0 ? `${String(dueCount)} 项待复测` : "暂无到期复测"}`,
    contextCapacityLabel: dashboard
      ? `${todayStalePrefix}${durationLabel(dashboard.capacity.availableMinutes)}`
      : "可用时间不可用",
    contextEvidenceLabel: profile
      ? `${profileStalePrefix}${String(evidenceCount)} 条学习记录`
      : "学习档案不可用",
    ...contextNotice(profileSource, todaySource),
  };

  if (!profileReady || !todayReady || !profile || !dashboard) {
    return {
      ...base,
      insightVisible: false,
      insightTitle: "同步完整后再给出备考判断",
      insightCopy: "缺失或过期的来源不会被当成空数据。",
      insightActionLabel: "查看今天",
      insightAction: "today",
    };
  }

  if (dueCount !== null && dueCount > 0) {
    return {
      ...base,
      insightVisible: true,
      insightTitle: `${String(dueCount)} 项复测或学习取舍已经到期`,
      insightCopy: "继续学新内容前，先处理到期项，才能判断之前的问题有没有真正修好。",
      insightActionLabel: "查看到期项",
      insightAction: "mastery",
    };
  }
  if (!goalName) {
    return {
      ...base,
      insightVisible: true,
      insightTitle: "还没有确认主目标岗位",
      insightCopy: "没有目标岗位时，岸岸只能按通用国考能力安排，无法判断分差和学习取舍。",
      insightActionLabel: "去确认岗位",
      insightAction: "positions",
    };
  }
  if (activeTaskCount > 0) {
    return {
      ...base,
      insightVisible: true,
      insightTitle: `今天有 ${String(activeTaskCount)} 项任务可以推进`,
      insightCopy: "岸岸会结合刚完成的作答更新能力；任何计划调整都会先展示变化，再由你确认。",
      insightActionLabel: "查看今天",
      insightAction: "today",
    };
  }
  return {
    ...base,
    insightVisible: true,
    insightTitle: "今天还没有形成可执行安排",
    insightCopy: "确认今天可用时间、精力和压力后，岸岸才能生成真实任务。",
    insightActionLabel: "调整今天",
    insightAction: "adjustment",
  };
}

function contextSourceValue<T>(source: CompanionContextSourceState<T>): T | undefined {
  return source.status === "ready" || source.status === "stale" ? source.value : undefined;
}

function contextCoreCopy(
  profileSource: CompanionContextSourceState<LearnerCoachContext>,
  todaySource: CompanionContextSourceState<TodayDashboard>,
): string {
  if (profileSource.status === "loading" && todaySource.status === "loading") {
    return "正在同步你的学习档案和今日计划。";
  }
  if (profileSource.status === "ready" && todaySource.status === "ready") {
    return "我已经同步了你的岗位、能力和今天的计划。";
  }
  if (profileSource.status === "ready") {
    return todaySource.status === "stale"
      ? "学习档案可用，今日计划正在显示上次同步结果。"
      : "学习档案可用，今日计划暂时没有载入。";
  }
  if (profileSource.status === "stale") {
    return "正在显示上次的学习档案；刷新成功前暂时不能发送问题。";
  }
  if (todaySource.status === "ready" || todaySource.status === "stale") {
    return "今日计划可查看，学习档案暂时不可用，不能发送问题。";
  }
  return "学习档案和今日计划都没有载入。";
}

function contextNotice(
  profileSource: CompanionContextSourceState<LearnerCoachContext>,
  todaySource: CompanionContextSourceState<TodayDashboard>,
): Pick<
  CompanionContextView,
  | "contextNoticeVisible"
  | "contextNoticeTitle"
  | "contextNoticeCopy"
  | "profileIssueVisible"
  | "profileSourceLabel"
  | "profileSourceMessage"
  | "profileSourceRequestId"
  | "todayIssueVisible"
  | "todaySourceLabel"
  | "todaySourceMessage"
  | "todaySourceRequestId"
  | "retryProfileVisible"
  | "retryTodayVisible"
> {
  const profileIssue = profileSource.status === "stale" || profileSource.status === "unavailable";
  const todayIssue = todaySource.status === "stale" || todaySource.status === "unavailable";
  const profileReady = profileSource.status === "ready";
  const todayStale = todaySource.status === "stale";
  const profileSourceMessage =
    profileSource.status === "stale" || profileSource.status === "unavailable"
      ? profileSource.message
      : "";
  const profileSourceRequestId =
    profileSource.status === "stale" || profileSource.status === "unavailable"
      ? profileSource.requestId
      : "";
  const todaySourceMessage =
    todaySource.status === "stale" || todaySource.status === "unavailable"
      ? todaySource.message
      : "";
  const todaySourceRequestId =
    todaySource.status === "stale" || todaySource.status === "unavailable"
      ? todaySource.requestId
      : "";
  let contextNoticeCopy = "";
  if (profileReady && todayIssue) {
    contextNoticeCopy = todayStale
      ? "仍可基于学习档案提问；岸岸不会把上次的计划当成最新安排。"
      : "仍可基于学习档案提问；岸岸不会把未载入的计划当成 0 项，也不会据此判断主目标。";
  } else if (profileIssue) {
    contextNoticeCopy =
      profileSource.status === "stale"
        ? "保留上次同步的数据供查看；学习档案刷新成功前，提问入口保持关闭。"
        : todaySource.status === "stale" || todaySource.status === "ready"
          ? "今日计划即使可查看，也不能替代学习档案版本；请先重新读取学习档案。"
          : todaySource.status === "unavailable"
            ? "学习档案和今日计划都没有载入；请分别重新读取两个失败来源。"
            : "学习档案还没有载入；请先重新读取学习档案。";
  }

  return {
    contextNoticeVisible: profileIssue || todayIssue,
    contextNoticeTitle:
      profileIssue && todayIssue
        ? "学习档案与今日计划未就绪"
        : profileIssue
          ? "学习档案未就绪"
          : "今日计划未完整同步",
    contextNoticeCopy,
    profileIssueVisible: profileIssue,
    profileSourceLabel:
      profileSource.status === "stale"
        ? "学习档案 · 刷新失败，显示上次数据"
        : profileSource.status === "unavailable"
          ? "学习档案 · 暂时不可用"
          : profileSource.status === "loading"
            ? "学习档案 · 正在读取"
            : "学习档案 · 已同步",
    profileSourceMessage,
    profileSourceRequestId,
    todayIssueVisible: todayIssue,
    todaySourceLabel:
      todaySource.status === "stale"
        ? "今日计划 · 刷新失败，显示上次数据"
        : todaySource.status === "unavailable"
          ? "今日计划 · 暂时不可用"
          : todaySource.status === "loading"
            ? "今日计划 · 正在读取"
            : "今日计划 · 已同步",
    todaySourceMessage,
    todaySourceRequestId,
    retryProfileVisible: profileIssue,
    retryTodayVisible: todayIssue,
  };
}

function durationLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return "可用时间待确认";
  if (value === 0) return "当前没有可用时间";
  const hours = value / 60;
  return `当前可用 ${Number.isInteger(hours) ? String(hours) : hours.toFixed(1)} 小时`;
}
