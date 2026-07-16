import type { XingceBriefingMetric, XingceTaskBriefing } from "../../../api/xingce-learning";

export interface XingceTaskBriefingView {
  title: string;
  reason: string;
  modeLabel: string;
  scopeLabel: string;
  questionCountLabel: string;
  targetTimeLabel: string;
  perQuestionTimeLabel: string;
  evidenceItems: Array<{ code: XingceBriefingMetric; label: string }>;
  verificationStageLabel: string;
  sourceMasteryLabel: string;
  validUntilLabel: string;
  planVersionLabel: string;
}

const metricLabels: Record<XingceBriefingMetric, string> = {
  answer_verdict: "客观对错",
  confidence_evidence: "作答信心",
  timing_evidence: "单题用时",
  reflection: "用户原话复盘",
  cause_decision: "用户确认的错因决定",
};

export function presentXingceTaskBriefing(briefing: XingceTaskBriefing): XingceTaskBriefingView {
  return {
    title: briefing.title,
    reason: briefing.reason,
    modeLabel:
      briefing.mode === "baseline"
        ? "受控基线"
        : briefing.mode === "verification"
          ? "独立复测"
          : "针对训练",
    scopeLabel: briefing.moduleName?.trim() || (briefing.mode === "baseline" ? "行测基线" : "行测"),
    questionCountLabel: `${String(briefing.questionCount)} 题`,
    targetTimeLabel: formatDuration(briefing.timing.targetSeconds),
    perQuestionTimeLabel: `${formatDuration(briefing.timing.perQuestionTargetSeconds)}/题`,
    evidenceItems: briefing.completion.requiredMetrics.map((code) => ({
      code,
      label: metricLabels[code],
    })),
    verificationStageLabel: verificationStageLabel(briefing.verificationStage),
    sourceMasteryLabel: briefing.sourceMasteryVersionId
      ? `来源掌握版本 ${shortVersion(briefing.sourceMasteryVersionId)}`
      : "",
    validUntilLabel: `说明有效至 ${dateTimeLabel(briefing.validUntil)}`,
    planVersionLabel: `计划版本 ${shortVersion(briefing.planVersionId)} · 定义 ${shortVersion(briefing.taskDefinitionVersion)}`,
  };
}

function verificationStageLabel(stage: XingceTaskBriefing["verificationStage"]): string {
  if (stage === "immediate") return "即时变式验证";
  if (stage === "first_review") return "1–3 天首次复测";
  if (stage === "delayed_review") return "7–14 天延时验证";
  return "";
}

function formatDuration(seconds: number): string {
  if (seconds >= 60 && seconds % 60 === 0) return `${String(seconds / 60)} 分钟`;
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    return `${String(minutes)} 分 ${String(remainder)} 秒`;
  }
  return `${String(seconds)} 秒`;
}

function dateTimeLabel(value: string): string {
  const date = new Date(value);
  return `${two(date.getMonth() + 1)}.${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

function shortVersion(value: string): string {
  return value.length <= 12 ? value : `${value.slice(0, 8)}…`;
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}
