import type { ScoreEvidenceHistoryItem } from "../../api/generated/types.gen";
import type { ReportedScoreDraft } from "./reporting";

export interface ScoreEvidenceHistoryItemView {
  evidenceId: string;
  active: boolean;
  statusLabel: string;
  contextLabel: string;
  timedLabel: string;
  dateLabel: string;
  scoreLabel: string;
  correctionDraft: ReportedScoreDraft;
}

export function scoreEvidenceHistoryItemView(
  item: ScoreEvidenceHistoryItem,
): ScoreEvidenceHistoryItemView {
  return {
    evidenceId: item.evidenceId,
    active: item.active,
    statusLabel: item.active ? "当前有效" : "已被新版本替代",
    contextLabel: contextLabel(item.reportedContext, item.sourceType),
    timedLabel: {
      timed: "限时完成",
      untimed: "未限时",
      unknown: "限时状态不确定",
    }[item.timedStatus],
    dateLabel: chinaDate(item.occurredAt),
    scoreLabel: scoreLabel(item),
    correctionDraft: {
      xingceScore: optionalScoreText(item.xingceScore),
      shenlunScore: optionalScoreText(item.shenlunScore),
      scoreContext: item.reportedContext ?? "full_mock",
      scoreTimedStatus: item.timedStatus,
      scoreDate: chinaDate(item.occurredAt),
      supersedesEvidenceId: item.evidenceId,
    },
  };
}

function contextLabel(
  context: ScoreEvidenceHistoryItem["reportedContext"],
  sourceType: ScoreEvidenceHistoryItem["sourceType"],
): string {
  if (context) {
    return {
      official_exam: "正式考试",
      full_mock: "整卷模考",
      historical_estimate: "历史估分",
    }[context];
  }
  return {
    self_report: "本人录入",
    placement_test: "入门测试",
    controlled_mock: "受控模考",
    external_mock: "外部模考",
    official_exam: "正式考试",
  }[sourceType];
}

function scoreLabel(item: ScoreEvidenceHistoryItem): string {
  const components: string[] = [];
  if (typeof item.xingceScore === "number") {
    components.push(`行测 ${formatScore(item.xingceScore)}`);
  }
  if (typeof item.shenlunScore === "number") {
    components.push(`申论 ${formatScore(item.shenlunScore)}`);
  }
  if (typeof item.totalScore === "number") {
    components.push(`总分 ${formatScore(item.totalScore)}`);
  }
  return components.join(" · ");
}

function optionalScoreText(score: number | null | undefined): string {
  return typeof score === "number" ? String(score) : "";
}

function formatScore(score: number): string {
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

function chinaDate(isoTimestamp: string): string {
  const timestamp = new Date(isoTimestamp).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const china = new Date(timestamp + 8 * 60 * 60 * 1000);
  const year = String(china.getUTCFullYear());
  const month = String(china.getUTCMonth() + 1).padStart(2, "0");
  const day = String(china.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
