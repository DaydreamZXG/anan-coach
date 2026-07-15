import type { OnboardingApi } from "../../api/onboarding";
import type { OnboardingNextStep } from "./types";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  isOnboardingVersionConflict,
  onboardingCommandError,
  type OnboardingCommandError,
} from "./command-error";
import {
  clearPendingStudyPreference,
  loadPendingStudyPreference,
  storePendingStudyPreference,
} from "./pending-store";
import {
  adjustStudyMinutes,
  buildStudyPreferenceCommand,
  defaultStudyPreferenceDraft,
  selectSpecialDay,
  validateStudyPreferenceDraft,
  weeklyRhythmSummary,
  type StudyMinutesField,
  type StudyPreferenceDraft,
  type Weekday,
  type WeeklyRhythmSummary,
} from "./study-preference";

type StudyPreferenceApi = Pick<
  OnboardingApi,
  "getProgress" | "getStudyPreference" | "putStudyPreference"
>;

export type { OnboardingCommandError } from "./command-error";
export { isOnboardingVersionConflict, onboardingCommandError } from "./command-error";

export interface StudyPreferenceControllerState {
  status: "idle" | "loading" | "ready" | "error";
  draft: StudyPreferenceDraft;
  summary: WeeklyRhythmSummary;
  progressVersion: number;
  preferenceVersion: number;
  saving: boolean;
  pendingRecovery: boolean;
  validationError: string;
  error: OnboardingCommandError | null;
}

export interface StudyPreferenceControllerOptions {
  userId: string;
  api: StudyPreferenceApi;
  onChange: (state: StudyPreferenceControllerState) => void;
  onAdvance: (nextStep: OnboardingNextStep) => void;
  createCommandKey?: () => Promise<string>;
  today?: () => string;
  isSessionCurrent?: () => boolean;
}

export class StudyPreferenceController {
  private readonly userId: string;
  private readonly api: StudyPreferenceApi;
  private readonly onChange: (state: StudyPreferenceControllerState) => void;
  private readonly onAdvance: (nextStep: OnboardingNextStep) => void;
  private readonly createCommandKey: () => Promise<string>;
  private readonly today: () => string;
  private readonly currentSession: () => boolean;
  private disposed = false;
  private operationEpoch = 0;
  private state: StudyPreferenceControllerState;

  constructor(options: StudyPreferenceControllerOptions) {
    this.userId = required(options.userId);
    this.api = options.api;
    this.onChange = options.onChange;
    this.onAdvance = options.onAdvance;
    this.createCommandKey =
      options.createCommandKey ?? (() => createIdempotencyKey("onboarding-preference"));
    this.today = options.today ?? localDate;
    this.currentSession = options.isSessionCurrent ?? (() => true);
    this.state = {
      status: "idle",
      draft: { ...defaultStudyPreferenceDraft },
      summary: weeklyRhythmSummary(defaultStudyPreferenceDraft),
      progressVersion: 0,
      preferenceVersion: 0,
      saving: false,
      pendingRecovery: false,
      validationError: "",
      error: null,
    };
  }

  snapshot(): StudyPreferenceControllerState {
    return {
      ...this.state,
      draft: { ...this.state.draft },
      summary: { ...this.state.summary },
      error: this.state.error ? { ...this.state.error } : null,
    };
  }

  sessionIsCurrent(): boolean {
    return this.isActive();
  }

  async load(): Promise<void> {
    await this.loadCanonical(null);
  }

  private async loadCanonical(reconciledError: OnboardingCommandError | null): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "loading", saving: false, error: null, validationError: "" });
    try {
      const progressResponse = await this.api.getProgress();
      if (!this.isCurrent(epoch)) return;
      const progress = progressResponse.data;
      if (progress.nextStep !== "study_preference") {
        clearPendingStudyPreference(this.userId);
        this.onAdvance(progress.nextStep);
        return;
      }

      let draft = { ...defaultStudyPreferenceDraft };
      let preferenceVersion = progress.studyPreferenceVersion ?? 0;
      if (progress.studyPreferenceReady) {
        const preferenceResponse = await this.api.getStudyPreference();
        if (!this.isCurrent(epoch)) return;
        const preference = preferenceResponse.data;
        preferenceVersion = preference.preferenceVersion;
        draft = {
          weekdayMinutes: preference.weekdayMinutes,
          weekendMinutes: preference.weekendMinutes,
          fixedRestDay: preference.fixedRestDay,
          mockExamDay: preference.mockExamDay,
        };
      }

      const pending = loadPendingStudyPreference(this.userId);
      if (pending) draft = { ...pending.draft };
      this.state = {
        ...this.state,
        status: "ready",
        draft,
        summary: weeklyRhythmSummary(draft),
        progressVersion: progress.progressVersion,
        preferenceVersion,
        pendingRecovery: pending !== null,
        saving: false,
        validationError: "",
        error: reconciledError,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({ status: "error", error: onboardingCommandError(error), saving: false });
    }
  }

  adjustMinutes(field: StudyMinutesField, delta: number): void {
    if (!this.canEdit()) return;
    this.setDraft(adjustStudyMinutes(this.state.draft, field, delta));
  }

  selectDay(field: "fixedRestDay" | "mockExamDay", weekday: Weekday): void {
    if (!this.canEdit()) return;
    this.setDraft(selectSpecialDay(this.state.draft, field, weekday));
  }

  async submit(): Promise<void> {
    if (!this.isActive() || this.state.saving || this.state.status !== "ready") return;
    const validationError = validateStudyPreferenceDraft(this.state.draft);
    if (validationError) {
      this.patch({ validationError });
      return;
    }

    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, error: null, validationError: "" });
    try {
      const existing = loadPendingStudyPreference(this.userId);
      const pending = existing ?? {
        idempotencyKey: await this.createCommandKey(),
        draft: { ...this.state.draft },
        command: buildStudyPreferenceCommand(this.state.draft, {
          expectedPreferenceVersion: this.state.preferenceVersion,
          expectedProgressVersion: this.state.progressVersion,
          effectiveFrom: this.today(),
        }),
      };
      if (!this.isCurrent(epoch)) return;
      if (!existing) storePendingStudyPreference(this.userId, pending);
      this.patch({ pendingRecovery: true });
      const response = await this.api.putStudyPreference(pending.command, pending.idempotencyKey);
      if (!this.isCurrent(epoch)) return;
      clearPendingStudyPreference(this.userId);
      this.state = {
        ...this.state,
        status: "ready",
        draft: { ...pending.draft },
        summary: weeklyRhythmSummary(pending.draft),
        progressVersion: response.data.progress.progressVersion,
        preferenceVersion: response.data.preference.preferenceVersion,
        saving: false,
        pendingRecovery: false,
        error: null,
      };
      this.publish();
      this.onAdvance(response.data.progress.nextStep);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const commandError = onboardingCommandError(error);
      if (isOnboardingVersionConflict(commandError)) {
        clearPendingStudyPreference(this.userId);
        await this.loadCanonical(commandError);
        return;
      }
      if (!commandError.retryable) clearPendingStudyPreference(this.userId);
      this.patch({
        saving: false,
        pendingRecovery: commandError.retryable && loadPendingStudyPreference(this.userId) !== null,
        error: commandError,
      });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private setDraft(draft: StudyPreferenceDraft): void {
    this.state = {
      ...this.state,
      draft,
      summary: weeklyRhythmSummary(draft),
      validationError: "",
      error: null,
    };
    this.publish();
  }

  private canEdit(): boolean {
    return (
      this.isActive() &&
      this.state.status === "ready" &&
      !this.state.saving &&
      !this.state.pendingRecovery
    );
  }

  private patch(patch: Partial<StudyPreferenceControllerState>): void {
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
  if (!normalized) throw new Error("Study preference controller requires an authenticated user");
  return normalized;
}

function localDate(): string {
  const date = new Date();
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
