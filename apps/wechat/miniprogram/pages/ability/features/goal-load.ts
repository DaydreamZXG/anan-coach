import { ApiClientError } from "../../../api/client";
import type { TodayDashboard } from "../../../api/generated/types.gen";

export type AbilityGoalLoadResult =
  | { kind: "ready"; dashboard: TodayDashboard }
  | { kind: "missing" }
  | { kind: "failed"; message: string; requestId: string };

export async function loadAbilityGoal(
  ready: boolean,
  load: () => Promise<TodayDashboard>,
): Promise<AbilityGoalLoadResult> {
  if (!ready) return { kind: "missing" };
  try {
    return { kind: "ready", dashboard: await load() };
  } catch (error) {
    if (isAuthenticationFailure(error)) throw error;
    if (error instanceof ApiClientError && error.statusCode === 404) {
      return { kind: "missing" };
    }
    return {
      kind: "failed",
      message: error instanceof ApiClientError ? error.message : "岗位对照暂时没加载出来。",
      requestId: error instanceof ApiClientError ? error.requestId : "",
    };
  }
}

export function isAuthenticationFailure(error: unknown): boolean {
  return (
    error instanceof ApiClientError &&
    (error.statusCode === 401 || error.payload?.code === "authentication_required")
  );
}
