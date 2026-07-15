import type { ReportedScoreEvidenceRequest } from "../../api/generated/types.gen";

export interface ReportedScoreDraft {
  xingceScore: string;
  shenlunScore: string;
  scoreContext: string;
  scoreTimedStatus: string;
  scoreDate: string;
  supersedesEvidenceId?: string;
}

export type ReportedScoreRequestResult =
  { request: ReportedScoreEvidenceRequest; error: "" } | { request: null; error: string };

export function reportedScoreRequestFromDraft(
  draft: ReportedScoreDraft,
): ReportedScoreRequestResult {
  const xingce = optionalScore(draft.xingceScore);
  const shenlun = optionalScore(draft.shenlunScore);
  if (xingce === undefined && shenlun === undefined) {
    return { request: null, error: "请至少填写行测或申论一项成绩。" };
  }
  if (xingce === null || shenlun === null) {
    return { request: null, error: "行测和申论都应填写 0–100 分。" };
  }
  if (!isReportedContext(draft.scoreContext) || !isTimedStatus(draft.scoreTimedStatus)) {
    return { request: null, error: "成绩场景或限时状态无效，请重新选择。" };
  }
  const occurredAt = new Date(`${draft.scoreDate}T00:00:00+08:00`);
  if (Number.isNaN(occurredAt.getTime())) {
    return { request: null, error: "请选择有效的考试或作答日期。" };
  }
  const request: ReportedScoreEvidenceRequest = {
    reportedContext: draft.scoreContext,
    timedStatus: draft.scoreTimedStatus,
    occurredAt: occurredAt.toISOString(),
  };
  if (xingce !== undefined) request.xingceScore = xingce;
  if (shenlun !== undefined) request.shenlunScore = shenlun;
  if (draft.supersedesEvidenceId) {
    request.supersedesEvidenceId = draft.supersedesEvidenceId;
  }
  return { request, error: "" };
}

function optionalScore(value: string): number | null | undefined {
  if (!value.trim()) return undefined;
  const score = Number(value);
  return Number.isFinite(score) && score >= 0 && score <= 100 ? score : null;
}

function isReportedContext(
  value: string,
): value is ReportedScoreEvidenceRequest["reportedContext"] {
  return ["official_exam", "full_mock", "historical_estimate"].includes(value);
}

function isTimedStatus(value: string): value is ReportedScoreEvidenceRequest["timedStatus"] {
  return ["timed", "untimed", "unknown"].includes(value);
}
