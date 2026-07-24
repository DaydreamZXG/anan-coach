import type { CompanionAskResponse, CompanionCitation } from "../../api/generated/types.gen";

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
