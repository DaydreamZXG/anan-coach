import type { CompanionAllowedAction } from "../../api/generated/types.gen";
import {
  registeredNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../app/navigation/routes";

export type CompanionActionResolution =
  | { kind: "show_evidence" }
  | { kind: "refresh_context" }
  | {
      kind: "navigate";
      target: RegisteredNavigationTarget;
      requiresConfirmation: boolean;
    }
  | { kind: "unavailable" };

const allowedTargets: Partial<Record<CompanionAllowedAction["kind"], ReadonlySet<string>>> = {
  add_reflection: new Set([routes.reflection]),
  adjust_plan_preview: new Set([routes.dayAdjustment]),
  supply_information: new Set([
    routes.captureImport,
    routes.positions,
    routes.privateKnowledge,
    routes.profileArchive,
    routes.qualificationProfile,
    routes.studyPreference,
    routes.weeklyPlan,
    routes.today,
  ]),
  open_training: new Set([routes.today]),
  open_position_workspace: new Set([routes.positions, routes.positionGoal]),
};

export function resolveCompanionAction(action: CompanionAllowedAction): CompanionActionResolution {
  if (action.kind === "view_evidence") return { kind: "show_evidence" };
  if (action.kind === "refresh_context") return { kind: "refresh_context" };
  const target = action.targetRef ? registeredNavigationTarget(action.targetRef) : null;
  if (!target) return { kind: "unavailable" };
  const [path] = target.url.split("?", 1);
  if (!path || !allowedTargets[action.kind]?.has(path)) return { kind: "unavailable" };
  if (action.kind === "adjust_plan_preview" && !validPlanAdjustmentTarget(target.url)) {
    return { kind: "unavailable" };
  }
  if (
    action.kind === "supply_information" &&
    path === routes.studyPreference &&
    !validWeeklyPreferenceTarget(target.url)
  ) {
    return { kind: "unavailable" };
  }
  if (
    action.kind === "open_position_workspace" &&
    path === routes.positionGoal &&
    !validPositionGoalTarget(target.url)
  ) {
    return { kind: "unavailable" };
  }
  return {
    kind: "navigate",
    target,
    requiresConfirmation: action.requiresConfirmation,
  };
}

function validPlanAdjustmentTarget(url: string): boolean {
  const query = url.split("?", 2)[1];
  if (query === undefined) return true;
  const params = query.split("&").map((part) => part.split("=", 2));
  const allowedKeys = new Set([
    "source",
    "availableMinutes",
    "preferredXingceModule",
    "xingceOnly",
  ]);
  if (
    params.some(([key]) => !allowedKeys.has(key ?? "")) ||
    new Set(params.map(([key]) => key)).size !== params.length
  ) {
    return false;
  }
  const source = params.find(([key]) => key === "source")?.[1];
  const minutesText = params.find(([key]) => key === "availableMinutes")?.[1];
  const moduleText = params.find(([key]) => key === "preferredXingceModule")?.[1];
  const onlyText = params.find(([key]) => key === "xingceOnly")?.[1];
  if (source !== "anan" || (minutesText === undefined && moduleText === undefined)) return false;
  if (minutesText !== undefined) {
    if (!/^\d{1,4}$/.test(minutesText)) return false;
    const minutes = Number(minutesText);
    if (!Number.isSafeInteger(minutes) || minutes < 0 || minutes > 1440) return false;
  }
  if (moduleText === undefined) return onlyText === undefined;
  if (onlyText !== "0" && onlyText !== "1") return false;
  let module: string;
  try {
    module = decodeURIComponent(moduleText);
  } catch {
    return false;
  }
  return new Set([
    "言语理解与表达",
    "数量关系",
    "判断推理",
    "资料分析",
    "常识判断",
    "政治理论",
  ]).has(module);
}

function validWeeklyPreferenceTarget(url: string): boolean {
  const query = url.split("?", 2)[1];
  if (query === undefined) return true;
  const params = query.split("&").map((part) => part.split("=", 2));
  if (params.some(([key]) => key !== "source" && key !== "fixedRestDay" && key !== "mockExamDay")) {
    return false;
  }
  if (params.find(([key]) => key === "source")?.[1] !== "anan") return false;
  const weekdays = new Set([
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
  ]);
  const restDay = params.find(([key]) => key === "fixedRestDay")?.[1];
  const mockDay = params.find(([key]) => key === "mockExamDay")?.[1];
  if (restDay === undefined && mockDay === undefined) return false;
  if (
    (restDay !== undefined && !weekdays.has(restDay)) ||
    (mockDay !== undefined && !weekdays.has(mockDay))
  ) {
    return false;
  }
  return restDay === undefined || mockDay === undefined || restDay !== mockDay;
}

function validPositionGoalTarget(url: string): boolean {
  const query = url.split("?", 2)[1];
  if (!query) return false;
  const params = query.split("&").map((part) => part.split("=", 2));
  if (params.length !== 1 || params[0]?.[0] !== "positionId") return false;
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu.test(
    params[0][1] ?? "",
  );
}
