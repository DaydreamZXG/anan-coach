import type { TodayDashboard } from "../../../api/generated/types.gen";
import {
  registeredTaskNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../../app/navigation/routes";
import type { CoachRecognitionPath } from "../../../features/onboarding/coach-entry";

export interface QuickDiagnosticResolution {
  target: RegisteredNavigationTarget;
  source: "objective_baseline" | "existing_xingce_task";
}

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
  return resolveQuickDiagnostic(dashboard)?.target ?? null;
}

export function resolveQuickDiagnostic(
  dashboard: TodayDashboard,
): QuickDiagnosticResolution | null {
  const baseline = executableXingceTask(dashboard, (stage) => stage === "objective_baseline");
  if (baseline) return { target: baseline, source: "objective_baseline" };

  // Mixed-state accounts can already own real attempts and a current verification
  // task while a legacy onboarding checklist still says "incomplete". In that
  // case the current task is more truthful than manufacturing a second baseline.
  const existing = executableXingceTask(dashboard, (stage) => stage !== "objective_baseline");
  return existing ? { target: existing, source: "existing_xingce_task" } : null;
}

function executableXingceTask(
  dashboard: TodayDashboard,
  acceptsStage: (stage: TodayDashboard["tasks"][number]["stage"]) => boolean,
): RegisteredNavigationTarget | null {
  for (const task of dashboard.tasks) {
    // A malformed candidate must not hide a later executable baseline task.
    if (
      task.track !== "xingce" ||
      !acceptsStage(task.stage) ||
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
