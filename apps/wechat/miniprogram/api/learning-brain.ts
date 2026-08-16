import type { ApiEnvelope } from "../domain/contracts";
import type {
  DailyStrategySuggestionResult,
  ErrorPatternDetail,
  LearningBrainProfileVersion,
  LearnerCoachContext,
  ModuleAbility,
  StrategyCandidateCommand,
  StrategyCandidateResult,
  StrategyDecisionCommand,
  StrategyDecisionResult,
} from "./generated/types.gen";
import type { ApiClient } from "./client";

export interface CoachUnderstanding {
  contractVersion: "learner-coach-context@v2";
  coachContext: LearnerCoachContext;
  explicitPreferences: Array<{
    memoryId: string;
    revision: number;
    category: "explanation_preference" | "study_constraint" | "personal_note";
    content: string;
    effect: "applied_to_answers" | "needs_confirmation" | "context_only";
    actionUrl?: string | null;
  }>;
  pendingRechecks: Array<{
    requestId: string;
    revision: number;
    profileVersionId: string;
    patternId: string;
    state: "pending_reverification";
    note?: string | null;
    createdAt: string;
    retestUrl: string;
  }>;
  retest: { dueNow: number; scheduled: number; stable: number; actionUrl: string };
}

export interface CoachRecheckCommand {
  commandId: string;
  profileVersionId: string;
  patternId: string;
  note?: string | null;
}

export class LearningBrainApi {
  constructor(private readonly client: ApiClient) {}

  getProfile(): Promise<ApiEnvelope<LearningBrainProfileVersion>> {
    return this.client.get<LearningBrainProfileVersion>("/api/v2/learning-brain/profile");
  }

  getCoachContext(): Promise<ApiEnvelope<LearnerCoachContext>> {
    return this.client.get<LearnerCoachContext>("/api/v2/learning-brain/coach-context");
  }

  getCoachUnderstanding(): Promise<ApiEnvelope<CoachUnderstanding>> {
    return this.client.get<CoachUnderstanding>("/api/v2/companion/coach-understanding");
  }

  requestCoachRecheck(command: CoachRecheckCommand): Promise<
    ApiEnvelope<{
      requestId: string;
      state: "pending_reverification";
      revision: number;
      retestUrl: string;
    }>
  > {
    return this.client.post("/api/v2/companion/coach-rechecks", command);
  }

  suggestDailyStrategy(): Promise<ApiEnvelope<DailyStrategySuggestionResult>> {
    return this.client.post<DailyStrategySuggestionResult>(
      "/api/v2/learning-brain/strategy-suggestions/daily",
      {},
      { timeoutMs: 60_000 },
    );
  }

  getModule(track: "xingce" | "essay", moduleKey: string): Promise<ApiEnvelope<ModuleAbility>> {
    return this.client.get<ModuleAbility>(
      `/api/v2/learning-brain/modules/${track}/${encodeURIComponent(moduleKey)}`,
    );
  }

  getErrorPattern(patternId: string): Promise<ApiEnvelope<ErrorPatternDetail>> {
    return this.client.get<ErrorPatternDetail>(
      `/api/v2/learning-brain/error-patterns/${encodeURIComponent(patternId)}`,
    );
  }

  createStrategyCandidate(
    command: StrategyCandidateCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<StrategyCandidateResult>> {
    return this.client.post<StrategyCandidateResult>(
      "/api/v2/learning-brain/strategy-candidates",
      command,
      { idempotencyKey },
    );
  }

  decideStrategyCandidate(
    candidateId: string,
    command: StrategyDecisionCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<StrategyDecisionResult>> {
    return this.client.post<StrategyDecisionResult>(
      `/api/v2/learning-brain/strategy-candidates/${encodeURIComponent(candidateId)}/decision`,
      command,
      { idempotencyKey },
    );
  }
}
