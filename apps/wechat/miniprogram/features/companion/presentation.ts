import type { CompanionAskResponse, CompanionCitation } from "../../api/generated/types.gen";
import { ApiClientError } from "../../api/client";

export type CompanionCitationView = CompanionCitation & {
  sourceLabel: string;
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

const sourceLabels: Record<string, string> = {
  learner_activity: "练习与模考记录",
  learning_brain: "学习档案",
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

export function companionAnswerView(
  response: CompanionAskResponse,
  stateLabel: string,
): CompanionAnswerView {
  const quotaRemaining =
    response.quota.limit === null ? null : Math.max(0, response.quota.limit - response.quota.used);
  const quotaUnlimited = response.quota.accessMode === "unlimited";
  return {
    ...response,
    citations: response.citations.map((citation) => ({
      ...citation,
      summary: publicCitationSummary(citation),
      sourceLabel: sourceLabels[citation.kind] ?? "学习依据",
    })),
    quotaRemaining,
    quotaUnlimited,
    quotaSummaryLabel: quotaUnlimited
      ? "冷启动期间可自由使用"
      : `今日还可提问 ${String(quotaRemaining ?? 0)} 次`,
    stateLabel,
    confidenceLabel: { low: "依据较少", medium: "依据一般", high: "依据充分" }[response.confidence],
    agentLabel: {
      position: "岗位顾问",
      tutor: "学习教练",
      planner: "计划教练",
    }[response.respondingAgent],
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
      message:
        "岸岸的回答服务尚未完成安全接入，本次没有把你的学习资料发给外部模型。请保留问题编号，服务恢复后再试。",
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
