import type { ApiEnvelope } from "../domain/contracts";
import type {
  ErrorPatternDetail,
  LearningBrainProfileVersion,
  ModuleAbility,
  StrategyCandidateCommand,
  StrategyCandidateResult,
  StrategyDecisionCommand,
  StrategyDecisionResult,
} from "./generated/types.gen";
import type { ApiClient } from "./client";

export class LearningBrainApi {
  constructor(private readonly client: ApiClient) {}

  getProfile(): Promise<ApiEnvelope<LearningBrainProfileVersion>> {
    return this.client.get<LearningBrainProfileVersion>("/api/v2/learning-brain/profile");
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
