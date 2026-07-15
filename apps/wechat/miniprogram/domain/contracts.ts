import type {
  AppDomainsPlanningSchemasTodayTask as GeneratedTodayTask,
  PublicApiError,
  TodayDashboard as GeneratedTodayDashboard,
} from "../api/generated/types.gen";

export type EvidenceLevel = "unknown" | "low" | "medium" | "high";
export type TodayDashboard = GeneratedTodayDashboard;
export type TodayTask = GeneratedTodayTask;
export type ScoreReadiness = TodayDashboard["goalGap"]["readiness"];
export type TaskKind = TodayTask["kind"];
export type TaskStatus = TodayTask["status"];
export type LearningTrack = TodayTask["track"];
export type TaskStage = TodayTask["stage"];

export interface ScoreRange {
  lower: number;
  upper: number;
  confidence: EvidenceLevel;
}

export interface PositionCandidate {
  id: string;
  name: string;
  department: string;
  region: string;
  bucket: "safe" | "balanced" | "stretch";
  eligibility: "confirmed" | "pending";
  targetScore: ScoreRange;
  scoreGap: number;
  evidenceSummary: string;
}

export interface PositionWorkspace {
  profileCompleteness: number;
  primaryGoalId: string;
  unverifiedConditions: string[];
  candidates: PositionCandidate[];
}

export interface AbilityModule {
  name: string;
  accuracy: number;
  averageSeconds: number;
  trend: "up" | "stable" | "down";
}

export interface LearningProfile {
  xingce: ScoreRange;
  shenlun: ScoreRange;
  evidenceCount: number;
  modules: AbilityModule[];
  errorPatterns: Array<{ name: string; count: number; evidenceLevel: EvidenceLevel }>;
  weeklyClosedLoops: number;
  weeklyPlanCompletion: number;
}

export type ApiErrorPayload = PublicApiError;

export interface ApiEnvelope<T> {
  data: T;
  requestId: string;
  generatedAt: string;
}
