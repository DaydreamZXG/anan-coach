import type { OnboardingProgress } from "../../api/generated/types.gen";
import type { OnboardingApi } from "../../api/onboarding";
import type { QualificationApi } from "../../api/qualification";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  emptyQualificationDraft,
  qualificationDraftFromProfile,
  qualificationFactsFingerprint,
  qualificationFactsFromDraft,
  validateOnboardingQualificationDraft,
  validateQualificationStep,
  type QualificationDraft,
} from "../qualification/onboarding";
import {
  clearPendingQualification,
  loadPendingQualification,
  storePendingQualification,
  type PendingQualificationCommand,
  type PendingQualificationMilestoneCommand,
} from "./pending-store";
import {
  isOnboardingVersionConflict,
  onboardingCommandError,
  type OnboardingCommandError,
} from "./command-error";
import type { OnboardingNextStep } from "./types";

type QualificationProfileApi = Pick<QualificationApi, "getProfile" | "updateProfile">;
type QualificationProgressApi = Pick<OnboardingApi, "getProgress" | "completeQualification">;
type QualificationCommandKind = "profile" | "milestone";

export interface QualificationControllerState {
  status: "idle" | "loading" | "ready" | "error";
  current: number;
  draft: QualificationDraft;
  profileVersion: number;
  loadedFactsFingerprint: string;
  saving: boolean;
  saved: boolean;
  pendingRecovery: boolean;
  validationError: string;
  error: OnboardingCommandError | null;
}

export interface QualificationControllerOptions {
  userId: string;
  profileApi: QualificationProfileApi;
  progressApi: QualificationProgressApi;
  onChange: (state: QualificationControllerState) => void;
  onAdvance: (nextStep: OnboardingNextStep) => void;
  createCommandKey?: (kind: QualificationCommandKind) => Promise<string>;
  isSessionCurrent?: () => boolean;
}

interface QualificationSubmitResult {
  progress: OnboardingProgress;
  profileVersion: number;
  draft: QualificationDraft;
}

export class QualificationController {
  private readonly userId: string;
  private readonly profileApi: QualificationProfileApi;
  private readonly progressApi: QualificationProgressApi;
  private readonly onChange: (state: QualificationControllerState) => void;
  private readonly onAdvance: (nextStep: OnboardingNextStep) => void;
  private readonly createCommandKey: (kind: QualificationCommandKind) => Promise<string>;
  private readonly currentSession: () => boolean;
  private disposed = false;
  private operationEpoch = 0;
  private state: QualificationControllerState = {
    status: "idle",
    current: 0,
    draft: { ...emptyQualificationDraft },
    profileVersion: 0,
    loadedFactsFingerprint: "",
    saving: false,
    saved: false,
    pendingRecovery: false,
    validationError: "",
    error: null,
  };

  constructor(options: QualificationControllerOptions) {
    this.userId = required(options.userId);
    this.profileApi = options.profileApi;
    this.progressApi = options.progressApi;
    this.onChange = options.onChange;
    this.onAdvance = options.onAdvance;
    this.createCommandKey =
      options.createCommandKey ??
      ((kind) =>
        createIdempotencyKey(
          kind === "profile" ? "qualification-profile" : "onboarding-qualification",
        ));
    this.currentSession = options.isSessionCurrent ?? (() => true);
  }

  snapshot(): QualificationControllerState {
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

  changeDraft(changes: Partial<QualificationDraft>): void {
    if (!this.canEdit()) return;
    this.state = {
      ...this.state,
      draft: { ...this.state.draft, ...changes },
      validationError: "",
      error: null,
    };
    this.publish();
  }

  previous(): void {
    if (!this.canEdit() || this.state.current === 0) return;
    this.patch({ current: this.state.current - 1, validationError: "", error: null });
  }

  next(): void {
    if (
      !this.isActive() ||
      this.state.saving ||
      this.state.saved ||
      this.state.status !== "ready"
    ) {
      return;
    }
    if (this.state.pendingRecovery) {
      void this.submit();
      return;
    }
    if (!this.canEdit()) return;
    const validationError = validateQualificationStep(this.state.draft, this.state.current);
    if (validationError) {
      this.patch({ validationError });
      return;
    }
    if (this.state.current < 3) {
      this.patch({ current: this.state.current + 1, validationError: "", error: null });
      return;
    }
    void this.submit();
  }

  async submit(): Promise<void> {
    if (
      !this.isActive() ||
      this.state.saving ||
      this.state.saved ||
      this.state.status !== "ready"
    ) {
      return;
    }
    const validationError = validateOnboardingQualificationDraft(this.state.draft);
    if (validationError) {
      this.patch({ validationError });
      return;
    }

    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, error: null, validationError: "" });
    try {
      let pending = loadPendingQualification(this.userId);
      if (!pending) {
        const draft = cloneDraft(this.state.draft);
        const facts = qualificationFactsFromDraft(draft);
        const fingerprint = qualificationFactsFingerprint(facts);
        if (this.state.profileVersion > 0 && fingerprint === this.state.loadedFactsFingerprint) {
          pending = await this.prepareMilestone(this.state.profileVersion, draft, epoch);
          if (!pending) return;
        } else {
          const idempotencyKey = await this.createCommandKey("profile");
          if (!this.isCurrent(epoch)) return;
          pending = { phase: "profile", idempotencyKey, draft, facts };
          storePendingQualification(this.userId, pending);
        }
      }
      if (!this.isCurrent(epoch)) return;
      this.patch({ pendingRecovery: true });
      const result = await this.executePending(pending, epoch);
      if (!result || !this.isCurrent(epoch)) return;

      clearPendingQualification(this.userId);
      if (result.progress.nextStep === "study_preference") {
        this.state = {
          ...this.state,
          status: "ready",
          current: 3,
          draft: cloneDraft(result.draft),
          profileVersion: result.profileVersion,
          loadedFactsFingerprint: qualificationFactsFingerprint(
            qualificationFactsFromDraft(result.draft),
          ),
          saving: false,
          saved: true,
          pendingRecovery: false,
          validationError: "",
          error: null,
        };
        this.publish();
        return;
      }
      if (result.progress.nextStep === "qualification") {
        throw new Error("Qualification milestone did not advance the server-owned progress");
      }
      this.patch({ saving: false, pendingRecovery: false });
      this.onAdvance(result.progress.nextStep);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const commandError = onboardingCommandError(error);
      if (isOnboardingVersionConflict(commandError)) {
        clearPendingQualification(this.userId);
        await this.loadCanonical(commandError);
        return;
      }
      if (!commandError.retryable) clearPendingQualification(this.userId);
      this.patch({
        saving: false,
        pendingRecovery: commandError.retryable && loadPendingQualification(this.userId) !== null,
        error: commandError,
      });
    }
  }

  continue(): void {
    if (!this.isActive() || !this.state.saved) return;
    this.onAdvance("study_preference");
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private async loadCanonical(reconciledError: OnboardingCommandError | null): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({
      status: "loading",
      saving: false,
      saved: false,
      validationError: "",
      error: null,
    });
    try {
      const progressResponse = await this.progressApi.getProgress();
      if (!this.isCurrent(epoch)) return;
      const progress = progressResponse.data;
      if (progress.nextStep !== "qualification") {
        clearPendingQualification(this.userId);
        this.onAdvance(progress.nextStep);
        return;
      }

      const profileResponse = await this.profileApi.getProfile();
      if (!this.isCurrent(epoch)) return;
      const profileDraft = qualificationDraftFromProfile(profileResponse.data);
      const pending = loadPendingQualification(this.userId);
      const draft = pending ? cloneDraft(pending.draft) : profileDraft;
      this.state = {
        ...this.state,
        status: "ready",
        current: pending ? 3 : 0,
        draft,
        profileVersion: profileResponse.data.profileVersion,
        loadedFactsFingerprint: qualificationFactsFingerprint(
          qualificationFactsFromDraft(profileDraft),
        ),
        saving: false,
        saved: false,
        pendingRecovery: pending !== null,
        validationError: "",
        error: reconciledError,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({
        status: "error",
        saving: false,
        pendingRecovery: loadPendingQualification(this.userId) !== null,
        error: onboardingCommandError(error),
      });
    }
  }

  private async executePending(
    pending: PendingQualificationCommand,
    epoch: number,
  ): Promise<QualificationSubmitResult | null> {
    let milestone: PendingQualificationMilestoneCommand;
    if (pending.phase === "profile") {
      const response = await this.profileApi.updateProfile(pending.facts, pending.idempotencyKey);
      if (!this.isCurrent(epoch)) return null;
      const prepared = await this.prepareMilestone(
        response.data.profileVersion,
        pending.draft,
        epoch,
      );
      if (!prepared) return null;
      milestone = prepared;
    } else {
      milestone = pending;
    }

    if (!this.isCurrent(epoch)) return null;
    this.patch({ pendingRecovery: true });
    const response = await this.progressApi.completeQualification(
      milestone.command,
      milestone.idempotencyKey,
    );
    if (!this.isCurrent(epoch)) return null;
    return {
      progress: response.data,
      profileVersion: milestone.command.qualificationProfileVersion,
      draft: cloneDraft(milestone.draft),
    };
  }

  private async prepareMilestone(
    profileVersion: number,
    draft: QualificationDraft,
    epoch: number,
  ): Promise<PendingQualificationMilestoneCommand | null> {
    const progressResponse = await this.progressApi.getProgress();
    if (!this.isCurrent(epoch)) return null;
    if (progressResponse.data.nextStep !== "qualification") {
      clearPendingQualification(this.userId);
      this.patch({ saving: false, pendingRecovery: false });
      this.onAdvance(progressResponse.data.nextStep);
      return null;
    }
    const idempotencyKey = await this.createCommandKey("milestone");
    if (!this.isCurrent(epoch)) return null;
    const pending: PendingQualificationMilestoneCommand = {
      phase: "milestone",
      idempotencyKey,
      draft: cloneDraft(draft),
      command: {
        expectedProgressVersion: progressResponse.data.progressVersion,
        qualificationProfileVersion: profileVersion,
      },
    };
    storePendingQualification(this.userId, pending);
    return pending;
  }

  private canEdit(): boolean {
    return (
      this.isActive() &&
      this.state.status === "ready" &&
      !this.state.saving &&
      !this.state.saved &&
      !this.state.pendingRecovery
    );
  }

  private patch(patch: Partial<QualificationControllerState>): void {
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
  if (!normalized) throw new Error("Qualification controller requires an authenticated user");
  return normalized;
}

function cloneDraft(draft: QualificationDraft): QualificationDraft {
  return { ...draft, certifications: [...draft.certifications] };
}
