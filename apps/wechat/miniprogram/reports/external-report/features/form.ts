import type { RecognitionRevision } from "../../../api/external-learning-contracts";
import type { ExternalMockReportDraftCommand } from "../../../api/generated/types.gen";

export interface ExternalScoreForm {
  platformName: string;
  examName: string;
  occurredDate: string;
  timedStatus: "timed" | "untimed" | "unknown";
  durationMinutes: string;
  totalScore: string;
  xingceScore: string;
  shenlunScore: string;
  moduleScoresText: string;
}

export type ExternalScoreFormField = keyof ExternalScoreForm;

export class ExternalScoreFormError extends Error {
  constructor(
    readonly field: ExternalScoreFormField,
    message: string,
  ) {
    super(message);
    this.name = "ExternalScoreFormError";
  }
}

export function initialExternalScoreForm(now = new Date()): ExternalScoreForm {
  return {
    platformName: "粉笔",
    examName: "",
    occurredDate: `${String(now.getFullYear())}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`,
    timedStatus: "timed",
    durationMinutes: "120",
    totalScore: "",
    xingceScore: "",
    shenlunScore: "",
    moduleScoresText: "",
  };
}

export function buildExternalScoreDraftCommand(
  form: ExternalScoreForm,
  revision: RecognitionRevision,
): ExternalMockReportDraftCommand {
  const platformName = required(form.platformName, "platformName", "平台名称");
  const examName = required(form.examName, "examName", "模考名称");
  const occurredAt = occurredDate(form.occurredDate);
  const durationMinutes = optionalNumber(form.durationMinutes, "durationMinutes", "作答时长");
  if (form.timedStatus === "timed" && durationMinutes === undefined) {
    throw new ExternalScoreFormError("durationMinutes", "计时模考必须填写作答时长。");
  }
  return {
    artifactVersionId: revision.artifactVersionId,
    recognitionRevisionId: revision.recognitionRevisionId,
    platformName,
    examName,
    occurredAt,
    timedStatus: form.timedStatus,
    durationMinutes: form.timedStatus === "timed" ? (durationMinutes ?? null) : null,
    totalScore: optionalNumber(form.totalScore, "totalScore", "总分") ?? null,
    xingceScore: optionalNumber(form.xingceScore, "xingceScore", "行测分") ?? null,
    shenlunScore: optionalNumber(form.shenlunScore, "shenlunScore", "申论分") ?? null,
    moduleScores: parseModuleScores(form.moduleScoresText),
  };
}

function occurredDate(value: string): string {
  const normalized = value.trim();
  const parsed = new Date(`${normalized}T12:00:00+08:00`);
  if (!/^\d{4}-\d{2}-\d{2}$/u.test(normalized) || Number.isNaN(parsed.getTime())) {
    throw new ExternalScoreFormError("occurredDate", "请选择有效的作答日期。");
  }
  return parsed.toISOString();
}

function parseModuleScores(value: string): Record<string, number> {
  const normalized = value.trim();
  if (!normalized) return {};
  return Object.fromEntries(
    normalized.split(/[,，\n]/).map((part) => {
      const [name = "", raw = ""] = part.split(/[:：]/, 2);
      const key = required(name, "moduleScoresText", "模块名称");
      const score = optionalNumber(raw, "moduleScoresText", `${key}分数`);
      if (score === undefined) {
        throw new ExternalScoreFormError("moduleScoresText", `${key}缺少分数。`);
      }
      return [key, score];
    }),
  );
}

function optionalNumber(
  value: string,
  field: ExternalScoreFormField,
  label: string,
): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new ExternalScoreFormError(field, `${label}必须是非负数字。`);
  }
  return parsed;
}

function required(value: string, field: ExternalScoreFormField, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new ExternalScoreFormError(field, `请填写${label}。`);
  return normalized;
}
