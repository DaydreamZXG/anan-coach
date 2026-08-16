import type { EssayTaskStage } from "../../../features/essay-task/capabilities";

interface CompletionPlanTask {
  id: string;
  status: "ready" | "in_progress" | "complete" | "abandoned";
  title: string;
}

interface CompletionPlan {
  tasks: CompletionPlanTask[];
}

export interface EssayCompletionPresentation {
  title: string;
  message: string;
  nextAction: string;
  truthNote: string;
}

const stageEvidenceLabels: Partial<Record<EssayTaskStage, string>> = {
  essay_material_reading: "材料梳理",
  essay_point_extraction: "答案点",
  essay_outlining: "提纲",
  essay_review: "复盘确认",
};

export function presentEssayArtifactCompletion(
  stage: EssayTaskStage,
  plan: CompletionPlan,
  completedPlanTaskId: string,
  replanned: boolean,
): EssayCompletionPresentation {
  const evidenceLabel = stageEvidenceLabels[stage] ?? "这次练习";
  const nextTask = plan.tasks.find(
    (task) =>
      task.id !== completedPlanTaskId && (task.status === "ready" || task.status === "in_progress"),
  );
  return {
    title: "这一步已经收好",
    message: replanned
      ? `你的${evidenceLabel}已经保存，今日计划也已根据这次记录更新。`
      : `你的${evidenceLabel}已经保存，今日计划已同步。`,
    nextAction: nextTask?.title ?? "回到今日查看最新安排",
    truthNote: "这里确认的是“已保存并完成任务”，不代表内容已经判断正确；后续练习会继续验证。",
  };
}

export const essayCompletionReturnDelayMs = 900;
