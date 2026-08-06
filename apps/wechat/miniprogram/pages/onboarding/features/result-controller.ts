import type { OnboardingResultProjection } from "../../../api/generated/types.gen";
import type { OnboardingApi } from "../../../api/onboarding";
import { createIdempotencyKey } from "../../../platform/command-id";
import {
  clearPendingResultAcknowledgement,
  loadPendingResultAcknowledgement,
  storePendingResultAcknowledgement,
} from "../../../features/onboarding/pending-store";
import {
  onboardingResultView,
  type OnboardingResultAction,
  type OnboardingResultView,
} from "./result-presentation";
import {
  isOnboardingVersionConflict,
  onboardingCommandError,
  type OnboardingCommandError,
} from "../shared/features/onboarding/command-error";
import type { OnboardingNextStep } from "../../../features/onboarding/types";

type ResultApi = Pick<OnboardingApi, "getProgress" | "getResult" | "acknowledgeResult">;

export type OnboardingResultNavigation =
  { kind: "step"; step: OnboardingNextStep } | { kind: "action"; action: OnboardingResultAction };

export interface OnboardingResultControllerState {
  status: "idle" | "loading" | "ready" | "error";
  result: OnboardingResultProjection | null;
  view: OnboardingResultView | null;
  saving: boolean;
  pendingRecovery: boolean;
  error: OnboardingCommandError | null;
}

export interface OnboardingResultControllerOptions {
  userId: string;
  api: ResultApi;
  onChange: (state: OnboardingResultControllerState) => void;
  onAdvance: (navigation: OnboardingResultNavigation) => void;
  createCommandKey?: () => Promise<string>;
  isSessionCurrent?: () => boolean;
}

export class OnboardingResultController {
  private readonly userId: string;
  private readonly api: ResultApi;
  private readonly onChange: (state: OnboardingResultControllerState) => void;
  private readonly onAdvance: (navigation: OnboardingResultNavigation) => void;
  private readonly createCommandKey: () => Promise<string>;
  private readonly currentSession: () => boolean;
  private disposed = false;
  private operationEpoch = 0;
  private state: OnboardingResultControllerState = {
    status: "idle",
    result: null,
    view: null,
    saving: false,
    pendingRecovery: false,
    error: null,
  };

  constructor(options: OnboardingResultControllerOptions) {
    this.userId = required(options.userId);
    this.api = options.api;
    this.onChange = options.onChange;
    this.onAdvance = options.onAdvance;
    this.createCommandKey =
      options.createCommandKey ?? (() => createIdempotencyKey("onboarding-result"));
    this.currentSession = options.isSessionCurrent ?? (() => true);
  }

  snapshot(): OnboardingResultControllerState {
    return {
      ...this.state,
      result: this.state.result ? cloneProjection(this.state.result) : null,
      view: this.state.view ? { ...this.state.view } : null,
      error: this.state.error ? { ...this.state.error } : null,
    };
  }

  sessionIsCurrent(): boolean {
    return this.isActive();
  }

  async load(): Promise<void> {
    await this.loadCanonical(null);
  }

  private async loadCanonical(
    reconciledError: OnboardingCommandError | null,
    completedAction?: OnboardingResultAction,
  ): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "loading", saving: false, error: null });
    try {
      const progressResponse = await this.api.getProgress();
      if (!this.isCurrent(epoch)) return;
      if (progressResponse.data.nextStep !== "result") {
        clearPendingResultAcknowledgement(this.userId);
        if (progressResponse.data.nextStep === "workspace" && completedAction) {
          this.onAdvance({ kind: "action", action: completedAction });
        } else {
          this.onAdvance({ kind: "step", step: progressResponse.data.nextStep });
        }
        return;
      }
      const response = await this.api.getResult();
      if (!this.isCurrent(epoch)) return;
      const result = response.data;
      this.state = {
        ...this.state,
        status: "ready",
        result,
        view: onboardingResultView(result),
        saving: false,
        pendingRecovery: loadPendingResultAcknowledgement(this.userId) !== null,
        error: reconciledError,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({ status: "error", error: onboardingCommandError(error), saving: false });
    }
  }

  async acknowledge(): Promise<void> {
    if (
      !this.isActive() ||
      this.state.status !== "ready" ||
      !this.state.result ||
      !this.state.view ||
      this.state.saving
    ) {
      return;
    }
    const result = this.state.result;
    const action = this.state.view.action;
    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, error: null });
    try {
      const existing = loadPendingResultAcknowledgement(this.userId);
      const pending = existing ?? {
        idempotencyKey: await this.createCommandKey(),
        command: {
          expectedProgressVersion: result.progressVersion,
          acknowledgeResult: true as const,
        },
      };
      if (!this.isCurrent(epoch)) return;
      if (!existing) storePendingResultAcknowledgement(this.userId, pending);
      this.patch({ pendingRecovery: true });
      const response = await this.api.acknowledgeResult(pending.command, pending.idempotencyKey);
      if (!this.isCurrent(epoch)) return;
      if (!response.data.completed || response.data.nextStep !== "workspace") {
        throw new Error("Onboarding acknowledgement did not complete the server-owned progress");
      }
      clearPendingResultAcknowledgement(this.userId);
      this.patch({ saving: false, pendingRecovery: false, error: null });
      this.onAdvance({ kind: "action", action });
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const commandError = onboardingCommandError(error);
      if (isOnboardingVersionConflict(commandError)) {
        clearPendingResultAcknowledgement(this.userId);
        await this.loadCanonical(commandError, action);
        return;
      }
      if (!commandError.retryable) clearPendingResultAcknowledgement(this.userId);
      this.patch({
        saving: false,
        pendingRecovery:
          commandError.retryable && loadPendingResultAcknowledgement(this.userId) !== null,
        error: commandError,
      });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private patch(patch: Partial<OnboardingResultControllerState>): void {
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
  if (!normalized) throw new Error("Onboarding result controller requires an authenticated user");
  return normalized;
}

function cloneProjection(result: OnboardingResultProjection): OnboardingResultProjection {
  return JSON.parse(JSON.stringify(result)) as OnboardingResultProjection;
}
