import type { BrainEvidenceRef } from "../../../api/generated/types.gen";
import { dateTimeLabel } from "../../../features/learning-brain/presentation";

export function presentErrorPatternEvidence(item: BrainEvidenceRef): {
  label: string;
  meta: string;
} {
  const label =
    {
      confirmed_pattern: "支持当前判断的作答记录",
      counterevidence: "证明已经纠正的复测记录",
      immediate_verification_source: "即时验证记录",
      due_verification: "到期复测记录",
    }[item.role?.trim() ?? ""] ??
    {
      question_attempt: "真实作答记录",
      task_execution: "任务完成记录",
      reflection: "错题复盘",
      essay_review: "申论评阅",
      verification: "复测记录",
      xingce_confirmed_cause_version: "已确认的错因记录",
      xingce_mastery_state_version: "掌握状态记录",
      essay_mastery_state_version: "申论掌握记录",
    }[item.entityType] ??
    "相关学习记录";
  return {
    label,
    meta: item.occurredAt ? dateTimeLabel(item.occurredAt) : "已纳入当前判断",
  };
}
