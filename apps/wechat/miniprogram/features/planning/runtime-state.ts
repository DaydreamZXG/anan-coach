import type { TodayDashboard } from "../../api/generated/types.gen";
import { currentUserId } from "../../auth/session";

interface OwnedTodayDashboard {
  ownerUserId: string;
  dashboard: TodayDashboard;
}

let latestTodayDashboard: OwnedTodayDashboard | null = null;

export function publishLatestTodayDashboard(dashboard: TodayDashboard): void {
  const ownerUserId = currentUserId();
  latestTodayDashboard = ownerUserId ? { ownerUserId, dashboard } : null;
}

export function consumeLatestTodayDashboard(): TodayDashboard | null {
  const ownerUserId = currentUserId();
  const owned = latestTodayDashboard;
  latestTodayDashboard = null;
  if (!ownerUserId || owned?.ownerUserId !== ownerUserId) return null;
  return owned.dashboard;
}
