import type { OnboardingApi } from "../../../api/onboarding";
import { createIdempotencyKey } from "../../../platform/command-id";
import {
  isOnboardingVersionConflict,
  onboardingCommandError,
  type OnboardingCommandError,
} from "../shared/features/onboarding/command-error";
import {
  clearPendingPreparationExperience,
  loadPendingPreparationExperience,
  storePendingPreparationExperience,
} from "../../../features/onboarding/pending-store";
import {
  buildPreparationExperienceCommand,
  emptyPreparationExperienceDraft,
  togglePreparationExperience,
  validatePreparationExperience,
  type PreparationExperienceChoice,
  type PreparationCurrentStatus,
  type PreparationExperienceDraft,
} from "../../../features/onboarding/preparation-experience";
import type { OnboardingNextStep } from "../../../features/onboarding/types";

type PreparationExperienceApi = Pick<OnboardingApi, "getProgress" | "putPreparationExperience">;

export interface PreparationExperienceControllerState {
  status: "idle" | "loading" | "ready" | "error";
  draft: PreparationExperienceDraft;
  progressVersion: number;
  experienceVersion: number;
  saving: boolean;
  pendingRecovery: boolean;
  validationError: string;
  error: OnboardingCommandError | null;
}

export interface PreparationExperienceControllerOptions {
  userId: string;
  api: PreparationExperienceApi;
  onChange: (state: PreparationExperienceControllerState) => void;
  onAdvance: (nextStep: OnboardingNextStep) => void;
  createCommandKey?: () => Promise<string>;
  isSessionCurrent?: () => boolean;
}

export class PreparationExperienceController {
  private readonly userId: string;
  private readonly api: PreparationExperienceApi;
  private readonly onChange: (state: PreparationExperienceControllerState) => void;
  private readonly onAdvance: (nextStep: OnboardingNextStep) => void;
  private readonly createCommandKey: () => Promise<string>;
  private readonly currentSession: () => boolean;
  private disposed = false;
  private operationEpoch = 0;
  private state: PreparationExperienceControllerState = {
    status: "idle",
    draft: { ...emptyPreparationExperienceDraft },
    progressVersion: 0,
    experienceVersion: 0,
    saving: false,
    pendingRecovery: false,
    validationError: "",
    error: null,
  };

  constructor(options: PreparationExperienceControllerOptions) {
    this.userId = required(options.userId);
    this.api = options.api;
    this.onChange = options.onChange;
    this.onAdvance = options.onAdvance;
    this.createCommandKey =
      options.createCommandKey ?? (() => createIdempotencyKey("onboarding-experience"));
    this.currentSession = options.isSessionCurrent ?? (() => true);
  }

  snapshot(): PreparationExperienceControllerState {
    return {
      ...this.state,
      draft: { ...this.state.draft },
      error: this.state.error ? { ...this.state.error } : null,
    };
  }

  sessionIsCurrent(): boolean {
    return this.isActive();
  }

  async load(): Promise<void> {
    await this.loadCanonical(null);
  }

  select(choice: PreparationExperienceChoice): void {
    if (!this.canEdit()) return;
    const draft = togglePreparationExperience(this.state.draft, choice);
    this.patch({ draft, validationError: "", error: null });
  }

  selectCurrentStatus(status: PreparationCurrentStatus): void {
    if (!this.canEdit()) return;
    this.patch({
      draft: { ...this.state.draft, currentStatus: status },
      validationError: "",
      error: null,
    });
  }

  async submit(): Promise<void> {
    if (!this.isActive() || this.state.status !== "ready" || this.state.saving) return;
    const validationError = validatePreparationExperience(this.state.draft);
    if (validationError) {
      this.patch({ validationError });
      return;
    }
    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, error: null, validationError: "" });
    try {
      const existing = loadPendingPreparationExperience(this.userId);
      const pending = existing ?? {
        idempotencyKey: await this.createCommandKey(),
        draft: { ...this.state.draft },
        command: buildPreparationExperienceCommand(this.state.draft, {
          expectedExperienceVersion: this.state.experienceVersion,
          expectedProgressVersion: this.state.progressVersion,
        }),
      };
      if (!this.isCurrent(epoch)) return;
      if (!existing) storePendingPreparationExperience(this.userId, pending);
      this.patch({ pendingRecovery: true });
      const response = await this.api.putPreparationExperience(
        pending.command,
        pending.idempotencyKey,
      );
      if (!this.isCurrent(epoch)) return;
      clearPendingPreparationExperience(this.userId);
      this.patch({
        draft: { ...pending.draft },
        progressVersion: response.data.progress.progressVersion,
        experienceVersion: response.data.experience.experienceVersion,
        saving: false,
        pendingRecovery: false,
        error: null,
      });
      this.onAdvance(response.data.progress.nextStep);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const commandError = onboardingCommandError(error);
      if (isOnboardingVersionConflict(commandError)) {
        clearPendingPreparationExperience(this.userId);
        await this.loadCanonical(commandError);
        return;
      }
      if (!commandError.retryable) clearPendingPreparationExperience(this.userId);
      this.patch({
        saving: false,
        pendingRecovery:
          commandError.retryable && loadPendingPreparationExperience(this.userId) !== null,
        error: commandError,
      });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private async loadCanonical(reconciledError: OnboardingCommandError | null): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "loading", saving: false, error: null, validationError: "" });
    try {
      const response = await this.api.getProgress();
      if (!this.isCurrent(epoch)) return;
      const progress = response.data;
      if (progress.nextStep !== "preparation_experience") {
        clearPendingPreparationExperience(this.userId);
        this.onAdvance(progress.nextStep);
        return;
      }
      const pending = loadPendingPreparationExperience(this.userId);
      this.state = {
        ...this.state,
        status: "ready",
        draft: pending ? { ...pending.draft } : { ...emptyPreparationExperienceDraft },
        progressVersion: progress.progressVersion,
        experienceVersion: progress.experienceProfileVersion ?? 0,
        saving: false,
        pendingRecovery: pending !== null,
        validationError: "",
        error: reconciledError,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({ status: "error", saving: false, error: onboardingCommandError(error) });
    }
  }

  private canEdit(): boolean {
    return (
      this.isActive() &&
      this.state.status === "ready" &&
      !this.state.saving &&
      !this.state.pendingRecovery
    );
  }

  private patch(patch: Partial<PreparationExperienceControllerState>): void {
    if (!this.isActive()) return;
    this.state = { ...this.state, ...patch };
    this.publish();
  }

  private publish(): void {
    if (this.isActive()) this.onChange(this.snapshot());
  }

  private isCurrent(epoch: number): boolean {
    return this.isActive() && epoch === this.operationEpoch;
  }

  private isActive(): boolean {
    return !this.disposed && this.currentSession();
  }
}

function required(userId: string): string {
  const normalized = userId.trim();
  if (!normalized) throw new Error("Preparation experience controller requires a user");
  return normalized;
}
