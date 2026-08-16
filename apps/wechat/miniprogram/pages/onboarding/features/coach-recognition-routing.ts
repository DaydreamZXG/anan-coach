import type { TodayDashboard } from "../../../api/generated/types.gen";
import {
  registeredTaskNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../../app/navigation/routes";
import type { CoachRecognitionPath } from "../../../features/onboarding/coach-entry";

export function staticCoachRecognitionTarget(
  path: Exclude<CoachRecognitionPath, "quick_diagnostic">,
): RegisteredNavigationTarget {
  const url = {
    score_report: routes.externalReport,
    wrong_question: routes.captureImport,
    position_first: routes.onboardingPositionPreference,
  }[path];
  return { mode: "navigateTo", url };
}

export function quickDiagnosticTarget(
  dashboard: TodayDashboard,
): RegisteredNavigationTarget | null {
  for (const task of dashboard.tasks) {
    // A malformed candidate must not hide a later executable baseline task.
    if (
      task.track !== "xingce" ||
      task.stage !== "objective_baseline" ||
      task.status === "complete" ||
      task.status === "abandoned"
    ) {
      continue;
    }
    const target = registeredTaskNavigationTarget(task.startUrl, task.id);
    if (target) return target;
  }
  return null;
}
