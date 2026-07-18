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
  supply_information: new Set([routes.captureImport, routes.positions, routes.today]),
  open_training: new Set([routes.today]),
  open_position_workspace: new Set([routes.positions]),
};

export function resolveCompanionAction(action: CompanionAllowedAction): CompanionActionResolution {
  if (action.kind === "view_evidence") return { kind: "show_evidence" };
  if (action.kind === "refresh_context") return { kind: "refresh_context" };
  const target = action.targetRef ? registeredNavigationTarget(action.targetRef) : null;
  if (!target) return { kind: "unavailable" };
  const [path] = target.url.split("?", 1);
  if (!path || !allowedTargets[action.kind]?.has(path)) return { kind: "unavailable" };
  return {
    kind: "navigate",
    target,
    requiresConfirmation: action.requiresConfirmation,
  };
}
