import type { PositionPreferenceProfile } from "../../../api/generated/types.gen";
import type { OnboardingApi } from "../../../api/onboarding";
import { createIdempotencyKey } from "../../../platform/command-id";
import {
  buildPositionPreferenceCommand,
  emptyPositionPreferenceDraft,
  positionPreferenceDraftFromProfile,
  validatePositionPreferenceDraft,
  type PositionLevel,
  type PositionPreferenceDraft,
} from "../../../features/onboarding/position-preference";
import {
  clearPendingPositionPreference,
  loadPendingPositionPreference,
  storePendingPositionPreference,
} from "../../../features/onboarding/pending-store";
import {
  onboardingCommandError,
  type OnboardingCommandError,
} from "../shared/features/onboarding/command-error";

type PositionPreferenceApi = Pick<OnboardingApi, "getPositionPreference" | "putPositionPreference">;

export interface PositionPreferenceControllerState {
  status: "idle" | "loading" | "ready" | "error";
  draft: PositionPreferenceDraft;
  preferenceVersion: number;
  saving: boolean;
  pendingRecovery: boolean;
  validationError: string;
  error: OnboardingCommandError | null;
}

interface PositionPreferenceControllerOptions {
  userId: string;
  api: PositionPreferenceApi;
  onChange: (state: PositionPreferenceControllerState) => void;
  onAdvance: () => void;
  isSessionCurrent?: () => boolean;
  createCommandKey?: () => Promise<string>;
}

export class PositionPreferenceController {
  private readonly userId: string;
  private readonly api: PositionPreferenceApi;
  private readonly onChange: (state: PositionPreferenceControllerState) => void;
  private readonly onAdvance: () => void;
  private readonly currentSession: () => boolean;
  private readonly createCommandKey: () => Promise<string>;
  private disposed = false;
  private operationEpoch = 0;
  private state: PositionPreferenceControllerState = {
    status: "idle",
    draft: cloneDraft(emptyPositionPreferenceDraft),
    preferenceVersion: 0,
    saving: false,
    pendingRecovery: false,
    validationError: "",
    error: null,
  };

  constructor(options: PositionPreferenceControllerOptions) {
    this.userId = required(options.userId);
    this.api = options.api;
    this.onChange = options.onChange;
    this.onAdvance = options.onAdvance;
    this.currentSession = options.isSessionCurrent ?? (() => true);
    this.createCommandKey =
      options.createCommandKey ?? (() => createIdempotencyKey("position-preference"));
  }

  snapshot(): PositionPreferenceControllerState {
    return {
      ...this.state,
      draft: cloneDraft(this.state.draft),
      error: this.state.error ? { ...this.state.error } : null,
    };
  }

  sessionIsCurrent(): boolean {
    return this.isActive();
  }

  async load(): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({
      status: "loading",
      saving: false,
      validationError: "",
      error: null,
    });
    try {
      let profile: PositionPreferenceProfile | null = null;
      try {
        const response = await this.api.getPositionPreference();
        profile = response.data;
      } catch (error) {
        const commandError = onboardingCommandError(error);
        if (commandError.code !== "position_preference_missing") throw error;
      }
      if (!this.isCurrent(epoch)) return;
      const pending = loadPendingPositionPreference(this.userId);
      this.state = {
        ...this.state,
        status: "ready",
        draft: pending
          ? cloneDraft(pending.draft)
          : profile
            ? positionPreferenceDraftFromProfile(profile)
            : cloneDraft(emptyPositionPreferenceDraft),
        preferenceVersion:
          pending?.command.expectedPreferenceVersion ?? profile?.preferenceVersion ?? 0,
        saving: false,
        pendingRecovery: pending !== null,
        validationError: "",
        error: null,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({
        status: "error",
        saving: false,
        error: onboardingCommandError(error),
      });
    }
  }

  selectRegion(region: string): void {
    if (!this.canEdit()) return;
    const normalized = region.trim();
    if (!normalized) return;
    this.patch({
      draft: { ...this.state.draft, preferredRegions: [normalized] },
      validationError: "",
      error: null,
    });
  }

  toggleRelocation(): void {
    if (!this.canEdit()) return;
    this.patch({
      draft: {
        ...this.state.draft,
        acceptsRelocation: !this.state.draft.acceptsRelocation,
      },
      validationError: "",
      error: null,
    });
  }

  toggleLevel(level: PositionLevel): void {
    if (!this.canEdit()) return;
    const selected = this.state.draft.acceptedPositionLevels.includes(level);
    this.patch({
      draft: {
        ...this.state.draft,
        acceptedPositionLevels: selected
          ? this.state.draft.acceptedPositionLevels.filter((item) => item !== level)
          : [...this.state.draft.acceptedPositionLevels, level],
      },
      validationError: "",
      error: null,
    });
  }

  async submit(): Promise<void> {
    if (!this.canSubmit()) return;
    const validationError = validatePositionPreferenceDraft(this.state.draft);
    if (validationError) {
      this.patch({ validationError });
      return;
    }
    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, validationError: "", error: null });
    try {
      let pending = loadPendingPositionPreference(this.userId);
      if (!pending) {
        const command = buildPositionPreferenceCommand(
          this.state.draft,
          this.state.preferenceVersion,
        );
        const idempotencyKey = await this.createCommandKey();
        if (!this.isCurrent(epoch)) return;
        pending = {
          idempotencyKey,
          draft: cloneDraft(this.state.draft),
          command,
        };
        storePendingPositionPreference(this.userId, pending);
      }
      if (!this.isCurrent(epoch)) return;
      this.patch({ pendingRecovery: true });
      const response = await this.api.putPositionPreference(
        pending.command,
        pending.idempotencyKey,
      );
      if (!this.isCurrent(epoch)) return;
      clearPendingPositionPreference(this.userId);
      this.state = {
        ...this.state,
        status: "ready",
        draft: positionPreferenceDraftFromProfile(response.data),
        preferenceVersion: response.data.preferenceVersion,
        saving: false,
        pendingRecovery: false,
        validationError: "",
        error: null,
      };
      this.publish();
      this.onAdvance();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const commandError = onboardingCommandError(error);
      if (commandError.code === "position_preference_version_conflict") {
        clearPendingPositionPreference(this.userId);
        await this.load();
        return;
      }
      if (!commandError.retryable) clearPendingPositionPreference(this.userId);
      this.patch({
        saving: false,
        pendingRecovery:
          commandError.retryable && loadPendingPositionPreference(this.userId) !== null,
        error: commandError,
      });
    }
  }

  continueWithoutChange(): void {
    if (!this.isActive() || this.state.preferenceVersion < 1) return;
    this.onAdvance();
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private canEdit(): boolean {
    return (
      this.isActive() &&
      this.state.status === "ready" &&
      !this.state.saving &&
      !this.state.pendingRecovery
    );
  }

  private canSubmit(): boolean {
    return this.isActive() && this.state.status === "ready" && !this.state.saving;
  }

  private patch(patch: Partial<PositionPreferenceControllerState>): void {
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

function cloneDraft(draft: Readonly<PositionPreferenceDraft>): PositionPreferenceDraft {
  return {
    preferredRegions: [...draft.preferredRegions],
    acceptsRelocation: draft.acceptsRelocation,
    acceptedPositionLevels: [...draft.acceptedPositionLevels],
  };
}

function required(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Position preference requires an authenticated user");
  return normalized;
}
