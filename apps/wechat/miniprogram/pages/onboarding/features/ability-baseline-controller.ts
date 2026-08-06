import type { OnboardingApi } from "../../../api/onboarding";
import { createIdempotencyKey } from "../../../platform/command-id";
import {
  abilityBaselineDraftFromCommand,
  buildAbilityBaselineCommand,
  emptyAbilityBaselineDraft,
  validateAbilityBaselineDraft,
  type AbilityBaselineDraft,
  type AbilityBaselineMode,
} from "../../../features/onboarding/ability-baseline";
import {
  clearPendingAbilityBaseline,
  loadPendingAbilityBaseline,
  storePendingAbilityBaseline,
} from "../../../features/onboarding/pending-store";
import {
  onboardingCommandError,
  type OnboardingCommandError,
} from "../../../features/onboarding/command-error";

type AbilityBaselineApi = Pick<OnboardingApi, "getAbilityBaseline" | "putAbilityBaseline">;

export interface AbilityBaselineControllerState {
  status: "idle" | "loading" | "ready" | "error";
  draft: AbilityBaselineDraft;
  baselineVersion: number;
  saving: boolean;
  pendingRecovery: boolean;
  validationError: string;
  error: OnboardingCommandError | null;
}

interface AbilityBaselineControllerOptions {
  userId: string;
  api: AbilityBaselineApi;
  onChange: (state: AbilityBaselineControllerState) => void;
  onAdvance: () => void;
  isSessionCurrent?: () => boolean;
  createCommandKey?: () => Promise<string>;
}

export class AbilityBaselineController {
  private readonly userId: string;
  private readonly api: AbilityBaselineApi;
  private readonly onChange: (state: AbilityBaselineControllerState) => void;
  private readonly onAdvance: () => void;
  private readonly currentSession: () => boolean;
  private readonly createCommandKey: () => Promise<string>;
  private disposed = false;
  private operationEpoch = 0;
  private state: AbilityBaselineControllerState = {
    status: "idle",
    draft: { ...emptyAbilityBaselineDraft },
    baselineVersion: 0,
    saving: false,
    pendingRecovery: false,
    validationError: "",
    error: null,
  };

  constructor(options: AbilityBaselineControllerOptions) {
    this.userId = required(options.userId);
    this.api = options.api;
    this.onChange = options.onChange;
    this.onAdvance = options.onAdvance;
    this.currentSession = options.isSessionCurrent ?? (() => true);
    this.createCommandKey =
      options.createCommandKey ?? (() => createIdempotencyKey("ability-baseline"));
  }

  snapshot(): AbilityBaselineControllerState {
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
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({
      status: "loading",
      saving: false,
      validationError: "",
      error: null,
    });
    try {
      let currentExists = false;
      try {
        await this.api.getAbilityBaseline();
        currentExists = true;
      } catch (error) {
        const commandError = onboardingCommandError(error);
        if (commandError.code !== "ability_baseline_missing") throw error;
      }
      if (!this.isCurrent(epoch)) return;
      if (currentExists) {
        clearPendingAbilityBaseline(this.userId);
        this.onAdvance();
        return;
      }
      const pending = loadPendingAbilityBaseline(this.userId);
      this.state = {
        ...this.state,
        status: "ready",
        draft: pending
          ? abilityBaselineDraftFromCommand(pending.command)
          : { ...emptyAbilityBaselineDraft },
        baselineVersion: pending?.command.expectedBaselineVersion ?? 0,
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

  selectMode(mode: AbilityBaselineMode): void {
    if (!this.canEdit()) return;
    this.patch({
      draft: {
        mode,
        xingceScore: mode === "unknown" ? "" : this.state.draft.xingceScore,
        shenlunScore: mode === "unknown" ? "" : this.state.draft.shenlunScore,
      },
      validationError: "",
      error: null,
    });
  }

  setScore(field: "xingceScore" | "shenlunScore", value: string): void {
    if (!this.canEdit()) return;
    this.patch({
      draft: { ...this.state.draft, [field]: value },
      validationError: "",
      error: null,
    });
  }

  async submit(): Promise<void> {
    if (!this.isActive() || this.state.status !== "ready" || this.state.saving) return;
    const validationError = validateAbilityBaselineDraft(this.state.draft);
    if (validationError) {
      this.patch({ validationError });
      return;
    }
    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, validationError: "", error: null });
    try {
      let pending = loadPendingAbilityBaseline(this.userId);
      if (!pending) {
        pending = {
          idempotencyKey: await this.createCommandKey(),
          command: buildAbilityBaselineCommand(this.state.draft, this.state.baselineVersion),
        };
        if (!this.isCurrent(epoch)) return;
        storePendingAbilityBaseline(this.userId, pending);
      }
      if (!this.isCurrent(epoch)) return;
      this.patch({ pendingRecovery: true });
      await this.api.putAbilityBaseline(pending.command, pending.idempotencyKey);
      if (!this.isCurrent(epoch)) return;
      clearPendingAbilityBaseline(this.userId);
      this.patch({
        saving: false,
        pendingRecovery: false,
        validationError: "",
        error: null,
      });
      this.onAdvance();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const commandError = onboardingCommandError(error);
      if (commandError.code === "ability_baseline_version_conflict") {
        clearPendingAbilityBaseline(this.userId);
        await this.load();
        return;
      }
      if (!commandError.retryable) clearPendingAbilityBaseline(this.userId);
      this.patch({
        saving: false,
        pendingRecovery: commandError.retryable && loadPendingAbilityBaseline(this.userId) !== null,
        error: commandError,
      });
    }
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

  private patch(patch: Partial<AbilityBaselineControllerState>): void {
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

function required(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Ability baseline requires an authenticated user");
  return normalized;
}
