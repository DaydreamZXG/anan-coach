import type { ApiClient } from "./client";
import type {
  AbilityBaselineProfile,
  AbilityBaselineWrite,
  OnboardingProgress,
  OnboardingResultAcknowledgement,
  OnboardingResultProjection,
  PositionPreferenceProfile,
  PositionPreferenceWrite,
  PreparationExperienceProfile,
  PreparationExperienceResult,
  PreparationExperienceWrite,
  QualificationMilestoneCommand,
  StudyPreferenceResult,
  StudyPreferenceWrite,
  WeeklyStudyPreference,
} from "./generated/types.gen";
import type { ApiEnvelope } from "../domain/contracts";

export class OnboardingApi {
  constructor(private readonly client: ApiClient) {}

  getProgress(): Promise<ApiEnvelope<OnboardingProgress>> {
    return this.client.get<OnboardingProgress>("/api/v2/onboarding/progress");
  }

  getPreparationExperience(): Promise<ApiEnvelope<PreparationExperienceProfile>> {
    return this.client.get<PreparationExperienceProfile>(
      "/api/v2/onboarding/preparation-experience",
    );
  }

  putPreparationExperience(
    command: PreparationExperienceWrite,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<PreparationExperienceResult>> {
    return this.client.put<PreparationExperienceResult>(
      "/api/v2/onboarding/preparation-experience",
      command,
      { idempotencyKey },
    );
  }

  completeQualification(
    command: QualificationMilestoneCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<OnboardingProgress>> {
    return this.client.post<OnboardingProgress>(
      "/api/v2/onboarding/qualification/complete",
      command,
      { idempotencyKey },
    );
  }

  getPositionPreference(): Promise<ApiEnvelope<PositionPreferenceProfile>> {
    return this.client.get<PositionPreferenceProfile>("/api/v2/onboarding/position-preference");
  }

  putPositionPreference(
    command: PositionPreferenceWrite,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<PositionPreferenceProfile>> {
    return this.client.put<PositionPreferenceProfile>(
      "/api/v2/onboarding/position-preference",
      command,
      { idempotencyKey },
    );
  }

  getAbilityBaseline(): Promise<ApiEnvelope<AbilityBaselineProfile>> {
    return this.client.get<AbilityBaselineProfile>("/api/v2/onboarding/ability-baseline");
  }

  putAbilityBaseline(
    command: AbilityBaselineWrite,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<AbilityBaselineProfile>> {
    return this.client.put<AbilityBaselineProfile>("/api/v2/onboarding/ability-baseline", command, {
      idempotencyKey,
    });
  }

  getStudyPreference(): Promise<ApiEnvelope<WeeklyStudyPreference>> {
    return this.client.get<WeeklyStudyPreference>("/api/v2/onboarding/study-preference");
  }

  putStudyPreference(
    command: StudyPreferenceWrite,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<StudyPreferenceResult>> {
    return this.client.put<StudyPreferenceResult>("/api/v2/onboarding/study-preference", command, {
      idempotencyKey,
    });
  }

  getResult(): Promise<ApiEnvelope<OnboardingResultProjection>> {
    return this.client.get<OnboardingResultProjection>("/api/v2/onboarding/result");
  }

  acknowledgeResult(
    command: OnboardingResultAcknowledgement,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<OnboardingProgress>> {
    return this.client.post<OnboardingProgress>("/api/v2/onboarding/complete", command, {
      idempotencyKey,
    });
  }
}
