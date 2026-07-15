import { parseEssayTaskStage, writableEssayStages } from "../../features/essay-task/capabilities";
import type { OnboardingNextStep } from "../../features/onboarding/types";
import {
  isValidPlanTaskId,
  parseXingceTaskMode,
} from "../../features/xingce/real_attempt/contracts";

export const routes = {
  login: "/pages/login/index",
  today: "/pages/today/index",
  positions: "/pages/positions/index",
  positionSearch: "/pages/positions/search/index",
  positionDetail: "/pages/positions/detail/index",
  positionCompare: "/pages/positions/compare/index",
  positionGoal: "/pages/positions/goal/index",
  positionBriefing: "/pages/positions/briefing/index",
  profile: "/pages/profile/index",
  onboarding: "/pages/onboarding/index",
  onboardingStudyPreference: "/pages/onboarding/study-preference/index",
  onboardingResult: "/pages/onboarding/result/index",
  diagnostic: "/pages/diagnostic/index",
  practice: "/pages/practice/index",
  reflection: "/pages/reflection/index",
  essayTask: "/pages/essay-task/index",
  captureImport: "/pages/capture-import/index",
  learningPackets: "/pages/learning-packets/index",
  answerIngestion: "/pages/answer-ingestion/index",
} as const;

export type RouteName = keyof typeof routes;
export type AppRoute = (typeof routes)[RouteName];

export interface RegisteredNavigationTarget {
  mode: "navigateTo" | "switchTab";
  url: string;
}

export interface PositionSearchRouteFilters {
  query?: string;
  examYear?: string;
  region?: string;
  city?: string;
}

export interface AnswerIngestionRouteOptions {
  packetVersionId?: string;
  ingestionId?: string;
}

const registeredRoutePaths = new Set<string>(Object.values(routes));
const tabRoutePaths = new Set<string>([routes.today, routes.positions, routes.profile]);
const taskExecutionRoutePaths = new Set<string>([routes.practice, routes.essayTask]);

export function registeredNavigationTarget(startUrl: string): RegisteredNavigationTarget | null {
  const normalized = startUrl.trim();
  if (!normalized.startsWith("/") || normalized.includes("#")) return null;
  const [path] = normalized.split("?", 1);
  if (!path || !registeredRoutePaths.has(path)) return null;
  if (tabRoutePaths.has(path)) {
    return normalized === path ? { mode: "switchTab", url: path } : null;
  }
  return { mode: "navigateTo", url: normalized };
}

export function onboardingNavigationTarget(step: OnboardingNextStep): RegisteredNavigationTarget {
  const path = {
    qualification: routes.onboarding,
    study_preference: routes.onboardingStudyPreference,
    result: routes.onboardingResult,
    workspace: routes.today,
  }[step];
  const target = registeredNavigationTarget(path);
  if (!target) throw new Error(`Onboarding step is not registered: ${step}`);
  return target;
}

export function registeredTaskNavigationTarget(
  startUrl: string,
): RegisteredNavigationTarget | null {
  const navigation = registeredNavigationTarget(startUrl);
  if (!navigation) return null;
  const [path] = navigation.url.split("?", 1);
  if (!path || !taskExecutionRoutePaths.has(path)) return null;
  if (path === routes.essayTask) {
    const query = parseQuery(navigation.url);
    const stage = parseEssayTaskStage(query?.stage);
    if (
      !query ||
      !stage ||
      !writableEssayStages.has(stage) ||
      !query.taskVersionId ||
      !query.planTaskId
    ) {
      return null;
    }
  }
  if (path === routes.practice) {
    const query = parseQuery(navigation.url);
    if (!query || !parseXingceTaskMode(query.mode)) return null;
    if (query.planTaskId !== undefined && !isValidPlanTaskId(query.planTaskId)) return null;
  }
  return navigation;
}

export function positionSearchUrl(filters: PositionSearchRouteFilters = {}): string {
  return withQuery(routes.positionSearch, {
    q: filters.query,
    examYear: filters.examYear,
    region: filters.region,
    city: filters.city,
  });
}

export function positionDetailUrl(positionId: string): string {
  return withQuery(routes.positionDetail, { positionId: requiredIdentifier(positionId) });
}

export function positionCompareUrl(positionIds: string[]): string {
  const normalized = positionIds.map((positionId) => positionId.trim());
  const unique = normalized.filter(
    (positionId, index) => positionId.length > 0 && normalized.indexOf(positionId) === index,
  );
  if (normalized.length < 2 || normalized.length > 3 || unique.length !== normalized.length) {
    throw new Error("Position comparison requires two or three unique position IDs");
  }
  return withQuery(routes.positionCompare, { ids: unique.join(",") });
}

export function positionGoalUrl(positionId: string): string {
  return withQuery(routes.positionGoal, { positionId: requiredIdentifier(positionId) });
}

export function positionBriefingUrl(positionId: string): string {
  return withQuery(routes.positionBriefing, { positionId: requiredIdentifier(positionId) });
}

export function captureImportUrl(importId?: string): string {
  return withQuery(routes.captureImport, {
    importId: importId === undefined ? undefined : requiredIdentifier(importId),
  });
}

export function answerIngestionUrl(options: AnswerIngestionRouteOptions = {}): string {
  if (options.packetVersionId && options.ingestionId) {
    throw new Error("Answer ingestion route accepts a packet or ingestion ID, not both");
  }
  return withQuery(routes.answerIngestion, {
    packetVersionId:
      options.packetVersionId === undefined
        ? undefined
        : requiredIdentifier(options.packetVersionId),
    ingestionId:
      options.ingestionId === undefined ? undefined : requiredIdentifier(options.ingestionId),
  });
}

function parseQuery(url: string): Record<string, string> | null {
  const query = url.split("?", 2)[1] ?? "";
  const result: Record<string, string> = {};
  try {
    for (const pair of query.split("&").filter(Boolean)) {
      const [rawKey = "", rawValue = ""] = pair.split("=", 2);
      const key = decodeURIComponent(rawKey);
      if (!key || Object.hasOwn(result, key)) return null;
      result[key] = decodeURIComponent(rawValue);
    }
  } catch {
    return null;
  }
  return result;
}

function withQuery(path: AppRoute, values: Record<string, string | undefined>): string {
  const query = Object.entries(values)
    .filter(
      (entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].length > 0,
    )
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join("&");
  return query ? `${path}?${query}` : path;
}

function requiredIdentifier(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Position route requires an explicit position ID");
  return normalized;
}
