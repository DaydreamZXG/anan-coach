import type { QualificationApi } from "../../../../api/qualification";
import { createIdempotencyKey } from "../../../../platform/command-id";
import {
  emptyQualificationDraft,
  qualificationDraftFromProfile,
  qualificationFactsFingerprint,
  qualificationFactsFromDraft,
  validateQualificationDraft,
  validateQualificationStep,
  type QualificationDraft,
} from "../../../../features/qualification/onboarding";
import {
  clearPendingQualificationProfileEdit,
  loadPendingQualificationProfileEdit,
  storePendingQualificationProfileEdit,
  type PendingQualificationProfileEdit,
} from "../../../../features/qualification/profile-edit-pending";
import { ApiClientError } from "../../../../api/client";

type QualificationProfileApi = Pick<QualificationApi, "getProfile" | "updateProfile">;

export interface QualificationProfileEditorState {
  status: "idle" | "loading" | "ready" | "error";
  current: number;
  draft: QualificationDraft;
  profileVersion: number;
  loadedFactsFingerprint: string;
  saving: boolean;
  saved: boolean;
  pendingRecovery: boolean;
  validationError: string;
  errorMessage: string;
  requestId: string;
}

export interface QualificationProfileEditorOptions {
  userId: string;
  profileApi: QualificationProfileApi;
  onChange: (state: QualificationProfileEditorState) => void;
  createCommandKey?: () => Promise<string>;
  isSessionCurrent?: () => boolean;
}

export class QualificationProfileEditorController {
  private readonly userId: string;
  private readonly profileApi: QualificationProfileApi;
  private readonly onChange: (state: QualificationProfileEditorState) => void;
  private readonly createCommandKey: () => Promise<string>;
  private readonly currentSession: () => boolean;
  private disposed = false;
  private operationEpoch = 0;
  private state: QualificationProfileEditorState = {
    status: "idle",
    current: 0,
    draft: { ...emptyQualificationDraft },
    profileVersion: 0,
    loadedFactsFingerprint: "",
    saving: false,
    saved: false,
    pendingRecovery: false,
    validationError: "",
    errorMessage: "",
    requestId: "",
  };

  constructor(options: QualificationProfileEditorOptions) {
    this.userId = required(options.userId);
    this.profileApi = options.profileApi;
    this.onChange = options.onChange;
    this.createCommandKey =
      options.createCommandKey ?? (() => createIdempotencyKey("qualification-profile-edit"));
    this.currentSession = options.isSessionCurrent ?? (() => true);
  }

  snapshot(): QualificationProfileEditorState {
    return {
      ...this.state,
      draft: { ...this.state.draft },
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
      saved: false,
      validationError: "",
      errorMessage: "",
      requestId: "",
    });
    try {
      const response = await this.profileApi.getProfile();
      if (!this.isCurrent(epoch)) return;
      const profileDraft = qualificationDraftFromProfile(response.data);
      const pending = loadPendingQualificationProfileEdit(this.userId);
      const draft = pending ? cloneDraft(pending.draft) : profileDraft;
      this.state = {
        ...this.state,
        status: "ready",
        current: pending ? 3 : 0,
        draft,
        profileVersion: response.data.profileVersion,
        loadedFactsFingerprint: qualificationFactsFingerprint(
          qualificationFactsFromDraft(profileDraft),
        ),
        saving: false,
        saved: false,
        pendingRecovery: pending !== null,
        validationError: "",
        errorMessage: pending ? "上一次资格档案保存结果还不确定，请先确认刚才那次保存。" : "",
        requestId: "",
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.patch({
        status: "error",
        saving: false,
        pendingRecovery: loadPendingQualificationProfileEdit(this.userId) !== null,
        errorMessage: apiError?.message ?? "资格档案没有加载成功，请重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  }

  changeDraft(changes: Partial<QualificationDraft>): void {
    if (!this.canEdit()) return;
    this.state = {
      ...this.state,
      draft: { ...this.state.draft, ...changes },
      saved: false,
      validationError: "",
      errorMessage: "",
      requestId: "",
    };
    this.publish();
  }

  previous(): void {
    if (!this.canEdit() || this.state.current === 0) return;
    this.patch({ current: this.state.current - 1, validationError: "", errorMessage: "" });
  }

  next(): void {
    if (
      !this.isActive() ||
      this.state.saving ||
      this.state.status !== "ready" ||
      this.state.saved
    ) {
      return;
    }
    if (this.state.pendingRecovery) {
      void this.submit();
      return;
    }
    const validationError = validateQualificationStep(this.state.draft, this.state.current);
    if (validationError) {
      this.patch({ validationError });
      return;
    }
    if (this.state.current < 3) {
      this.patch({ current: this.state.current + 1, validationError: "", errorMessage: "" });
      return;
    }
    void this.submit();
  }

  async submit(): Promise<void> {
    if (!this.isActive() || this.state.saving || this.state.status !== "ready") return;
    const validationError = validateQualificationDraft(this.state.draft);
    if (validationError) {
      this.patch({ validationError });
      return;
    }

    const epoch = ++this.operationEpoch;
    this.patch({ saving: true, validationError: "", errorMessage: "", requestId: "" });
    try {
      let pending = loadPendingQualificationProfileEdit(this.userId);
      if (!pending) {
        const draft = cloneDraft(this.state.draft);
        const facts = qualificationFactsFromDraft(draft);
        const fingerprint = qualificationFactsFingerprint(facts);
        if (fingerprint === this.state.loadedFactsFingerprint) {
          this.patch({
            saving: false,
            saved: true,
            pendingRecovery: false,
            errorMessage: "资料没有变化，岗位建议无需重新计算。",
          });
          return;
        }
        const idempotencyKey = await this.createCommandKey();
        if (!this.isCurrent(epoch)) return;
        pending = { idempotencyKey, draft, facts };
        storePendingQualificationProfileEdit(this.userId, pending);
      }
      if (!this.isCurrent(epoch)) return;
      this.patch({ pendingRecovery: true });
      await this.executePending(pending, epoch);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const apiError = error instanceof ApiClientError ? error : undefined;
      const retryable = !apiError || apiError.statusCode === 0 || apiError.statusCode >= 500;
      if (!retryable) clearPendingQualificationProfileEdit(this.userId);
      this.patch({
        saving: false,
        pendingRecovery: retryable && loadPendingQualificationProfileEdit(this.userId) !== null,
        errorMessage: apiError?.message ?? "资格档案没有保存成功，请重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  }

  editAgain(): void {
    if (!this.isActive() || this.state.status !== "ready" || this.state.saving) return;
    this.patch({ saved: false, current: 0, validationError: "", errorMessage: "", requestId: "" });
  }

  dispose(): void {
    this.disposed = true;
    this.operationEpoch += 1;
  }

  private async executePending(
    pending: PendingQualificationProfileEdit,
    epoch: number,
  ): Promise<void> {
    const response = await this.profileApi.updateProfile(pending.facts, pending.idempotencyKey);
    if (!this.isCurrent(epoch)) return;
    clearPendingQualificationProfileEdit(this.userId);
    const canonicalDraft = qualificationDraftFromProfile(response.data);
    this.state = {
      ...this.state,
      status: "ready",
      current: 3,
      draft: canonicalDraft,
      profileVersion: response.data.profileVersion,
      loadedFactsFingerprint: qualificationFactsFingerprint(
        qualificationFactsFromDraft(canonicalDraft),
      ),
      saving: false,
      saved: true,
      pendingRecovery: false,
      validationError: "",
      errorMessage: "",
      requestId: "",
    };
    this.publish();
  }

  private canEdit(): boolean {
    return (
      this.isActive() &&
      this.state.status === "ready" &&
      !this.state.saving &&
      !this.state.pendingRecovery &&
      !this.state.saved
    );
  }

  private patch(changes: Partial<QualificationProfileEditorState>): void {
    this.state = { ...this.state, ...changes };
    this.publish();
  }

  private publish(): void {
    if (!this.disposed) this.onChange(this.snapshot());
  }

  private isActive(): boolean {
    return !this.disposed && this.currentSession();
  }

  private isCurrent(epoch: number): boolean {
    return this.operationEpoch === epoch && this.isActive();
  }
}

function cloneDraft(draft: QualificationDraft): QualificationDraft {
  return { ...draft, certifications: [...draft.certifications] };
}

function required(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Qualification profile editor requires userId");
  return normalized;
}
