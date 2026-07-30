import {
  STUDY_MINUTES_MAX,
  STUDY_MINUTES_MIN,
  STUDY_MINUTES_STEP,
  type StudyMinutesField,
} from "../../features/onboarding/study-preference";

interface StudyMinutesSelection {
  weekdayMinutes: number;
  weekendMinutes: number;
}

export interface StudyMinutesScrollTargets {
  weekdayMinutesTarget: string;
  weekendMinutesTarget: string;
}

export function studyMinutesOptionId(field: StudyMinutesField, minutes: number): string {
  if (!validStudyMinutes(minutes)) {
    throw new Error("Study-minute scroll target must identify a rendered half-hour option");
  }
  const prefix = field === "weekdayMinutes" ? "weekday" : "weekend";
  return `${prefix}-minutes-${String(minutes)}`;
}

export function studyMinutesScrollTargets(
  selection: StudyMinutesSelection,
): StudyMinutesScrollTargets {
  return {
    weekdayMinutesTarget: studyMinutesOptionId("weekdayMinutes", selection.weekdayMinutes),
    weekendMinutesTarget: studyMinutesOptionId("weekendMinutes", selection.weekendMinutes),
  };
}

function validStudyMinutes(minutes: number): boolean {
  return (
    Number.isSafeInteger(minutes) &&
    minutes >= STUDY_MINUTES_MIN &&
    minutes <= STUDY_MINUTES_MAX &&
    minutes % STUDY_MINUTES_STEP === 0
  );
}
