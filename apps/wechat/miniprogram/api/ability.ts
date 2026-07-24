import type { ApiClient } from "./client";
import type {
  AbilityEstimateCurrent,
  ReportedScoreEvidenceRequest,
  ReportedScoreEvidenceResult,
  ScoreEvidenceHistory,
} from "./generated/types.gen";
import type { ApiEnvelope } from "../domain/contracts";

export class AbilityApi {
  constructor(private readonly client: ApiClient) {}

  getCurrentEstimate(): Promise<ApiEnvelope<AbilityEstimateCurrent>> {
    return this.client.get<AbilityEstimateCurrent>("/api/v2/ability-estimates/current");
  }

  getScoreEvidenceHistory(): Promise<ApiEnvelope<ScoreEvidenceHistory>> {
    return this.client.get<ScoreEvidenceHistory>("/api/v2/score-evidence");
  }

  reportScore(
    report: ReportedScoreEvidenceRequest,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<ReportedScoreEvidenceResult>> {
    return this.client.post<ReportedScoreEvidenceResult>(
      "/api/v2/score-evidence/reported",
      report,
      { idempotencyKey },
    );
  }
}
