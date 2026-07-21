import type { StudyPreferenceWrite } from "../../api/generated/types.gen";

export const STUDY_MINUTES_MIN = 0;
export const STUDY_MINUTES_MAX = 720;
export const STUDY_MINUTES_STEP = 30;

export type Weekday =
  "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday";

export type StudyMinutesField = "weekdayMinutes" | "weekendMinutes";

export interface StudyTimeOption {
  minutes: number;
  label: string;
  caption: string;
}

export interface StudyPreferenceDraft {
  weekdayMinutes: number;
  weekendMinutes: number;
  fixedRestDay: Weekday;
  mockExamDay: Weekday;
}

export type StudyPreferenceCommandPayload = StudyPreferenceWrite;

export interface WeeklyRhythmSummary {
  focusedTrainingDays: number;
  mockExamDays: 1;
  recoveryDays: 1;
  weeklyMinutes: number;
}

export interface StudyPreferencePresentation {
  weekdayDurationLabel: string;
  weekendDurationLabel: string;
  weeklyDurationLabel: string;
}

export const defaultStudyPreferenceDraft: Readonly<StudyPreferenceDraft> = {
  weekdayMinutes: 60,
  weekendMinutes: 120,
  fixedRestDay: "sunday",
  mockExamDay: "saturday",
};

export const studyTimeOptions: ReadonlyArray<StudyTimeOption> = [
  0, 30, 60, 90, 120, 150, 180, 240, 300, 360, 480, 600, 720,
].map((minutes) => ({
  minutes,
  label: formatStudyDuration(minutes),
  caption: studyDurationCaption(minutes),
}));

export const weekdayOptions: ReadonlyArray<{ value: Weekday; label: string }> = [
  { value: "monday", label: "一" },
  { value: "tuesday", label: "二" },
  { value: "wednesday", label: "三" },
  { value: "thursday", label: "四" },
  { value: "friday", label: "五" },
  { value: "saturday", label: "六" },
  { value: "sunday", label: "日" },
];

export function adjustStudyMinutes(
  draft: StudyPreferenceDraft,
  field: StudyMinutesField,
  delta: number,
): StudyPreferenceDraft {
  if (!Number.isSafeInteger(delta) || delta === 0) {
    throw new Error("Study-minute adjustment requires a non-zero integer delta");
  }
  const current = draft[field];
  return {
    ...draft,
    [field]: clampMinutes(current + delta),
  };
}

export function setStudyMinutes(
  draft: StudyPreferenceDraft,
  field: StudyMinutesField,
  minutes: number,
): StudyPreferenceDraft {
  if (!validMinutes(minutes)) {
    throw new Error("Study time must stay within the server minute contract");
  }
  return {
    ...draft,
    [field]: minutes,
  };
}

export function selectSpecialDay(
  draft: StudyPreferenceDraft,
  field: "fixedRestDay" | "mockExamDay",
  weekday: Weekday,
): StudyPreferenceDraft {
  if (!weekdayOptions.some((option) => option.value === weekday)) {
    throw new Error("Unknown weekly-study weekday");
  }
  return { ...draft, [field]: weekday };
}

export function validateStudyPreferenceDraft(draft: StudyPreferenceDraft): string {
  if (!validMinutes(draft.weekdayMinutes) || !validMinutes(draft.weekendMinutes)) {
    return "每日学习时间需要在 0–12 小时之间。";
  }
  if (draft.weekdayMinutes === 0 && draft.weekendMinutes === 0) {
    return "工作日和周末至少保留一段可学习时间。";
  }
  if (draft.fixedRestDay === draft.mockExamDay) {
    return "固定休息日和模考日不能是同一天。";
  }
  return "";
}

export function buildStudyPreferenceCommand(
  draft: StudyPreferenceDraft,
  context: {
    expectedPreferenceVersion: number;
    expectedProgressVersion: number;
    effectiveFrom: string;
  },
): StudyPreferenceCommandPayload {
  const validation = validateStudyPreferenceDraft(draft);
  if (validation) throw new Error(validation);
  if (
    !Number.isSafeInteger(context.expectedPreferenceVersion) ||
    context.expectedPreferenceVersion < 0
  ) {
    throw new Error("Study preference requires a valid expected preference version");
  }
  if (
    !Number.isSafeInteger(context.expectedProgressVersion) ||
    context.expectedProgressVersion < 1
  ) {
    throw new Error("Study preference requires a valid onboarding progress version");
  }
  if (!/^\d{4}-\d{2}-\d{2}$/u.test(context.effectiveFrom)) {
    throw new Error("Study preference requires an ISO effective date");
  }
  const dailyMaximum = Math.max(draft.weekdayMinutes, draft.weekendMinutes);
  return {
    expectedPreferenceVersion: context.expectedPreferenceVersion,
    expectedProgressVersion: context.expectedProgressVersion,
    timezone: "Asia/Shanghai",
    effectiveFrom: context.effectiveFrom,
    weekdayMinutes: draft.weekdayMinutes,
    weekendMinutes: draft.weekendMinutes,
    longBlockMinutes: Math.min(120, dailyMaximum),
    fragmentMinutes: Math.min(25, draft.weekdayMinutes),
    fixedRestDay: draft.fixedRestDay,
    mockExamDay: draft.mockExamDay,
  };
}

export function weeklyRhythmSummary(draft: StudyPreferenceDraft): WeeklyRhythmSummary {
  const weekdays = weekdayOptions.map((option) => option.value);
  const weeklyMinutes = weekdays.reduce((total, weekday) => {
    if (weekday === draft.fixedRestDay) return total;
    const minutes =
      weekday === "saturday" || weekday === "sunday" ? draft.weekendMinutes : draft.weekdayMinutes;
    return total + minutes;
  }, 0);
  return {
    focusedTrainingDays: 5,
    mockExamDays: 1,
    recoveryDays: 1,
    weeklyMinutes,
  };
}

export function studyPreferencePresentation(
  draft: StudyPreferenceDraft,
  summary: WeeklyRhythmSummary = weeklyRhythmSummary(draft),
): StudyPreferencePresentation {
  return {
    weekdayDurationLabel: formatStudyDuration(draft.weekdayMinutes),
    weekendDurationLabel: formatStudyDuration(draft.weekendMinutes),
    weeklyDurationLabel: formatStudyDuration(summary.weeklyMinutes),
  };
}

export function formatStudyDuration(minutes: number): string {
  if (!validMinutes(minutes)) return "时间待确认";
  if (minutes === 0) return "不安排";
  if (minutes % 60 === 0) return `${String(minutes / 60)} 小时`;
  if (minutes % STUDY_MINUTES_STEP === 0) return `${trimHalfHour(minutes / 60)} 小时`;
  return `${String(minutes)} 分钟`;
}

function clampMinutes(value: number): number {
  return Math.min(STUDY_MINUTES_MAX, Math.max(STUDY_MINUTES_MIN, value));
}

function validMinutes(value: number): boolean {
  return Number.isSafeInteger(value) && value >= STUDY_MINUTES_MIN && value <= STUDY_MINUTES_MAX;
}

function trimHalfHour(hours: number): string {
  return hours.toFixed(1).replace(/\.0$/u, "");
}

function studyDurationCaption(minutes: number): string {
  if (minutes === 0) return "只保留恢复";
  if (minutes <= 60) return "轻量日";
  if (minutes <= 180) return "常规训练";
  if (minutes <= 360) return "集中训练";
  return "高强度";
}
