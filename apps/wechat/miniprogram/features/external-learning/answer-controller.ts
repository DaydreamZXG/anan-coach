import type { LearningTrack } from "../../api/external-learning-contracts";
import { createIdempotencyKey } from "../../platform/command-id";
import type {
  AnswerIngestionWorkspace,
  ExternalLearningGateway,
  LocalAnswerImageFile,
  UploadProgress,
} from "./contracts";
import { externalLearningError, type ExternalLearningError } from "./error";
import {
  answerCandidateView,
  answerTargetView,
  type AnswerCandidateView,
  type AnswerTargetView,
} from "./presentation";

export const MAX_ANSWER_IMAGE_BYTES = 20 * 1024 * 1024;

export interface AnswerIngestionState {
  status:
    | "idle"
    | "loading"
    | "empty"
    | "draft"
    | "uploading"
    | "processing"
    | "correction"
    | "confirmation"
    | "success"
    | "expired"
    | "error";
  targets: AnswerTargetView[];
  selectedPacketVersionId: string;
  track: LearningTrack;
  inputMode: "answer_string" | "answer_image";
  answerText: string;
  selectedFile: LocalAnswerImageFile | null;
  uploadProgress: UploadProgress | null;
  workspace: AnswerIngestionWorkspace | null;
  candidateItems: AnswerCandidateView[];
  dirty: boolean;
  confirmationOpen: boolean;
  submitting: boolean;
  pendingRecovery: boolean;
  generatedAt: string;
  requestId: string;
  validationError: string;
  error: ExternalLearningError | null;
}

export interface AnswerIngestionControllerOptions {
  gateway: Pick<
    ExternalLearningGateway,
    | "listAnswerTargets"
    | "getAnswerIngestion"
    | "createAnswerString"
    | "createAnswerImage"
    | "reviseAnswer"
    | "confirmAnswer"
  >;
  onChange: (state: AnswerIngestionState) => void;
  createCommandKey?: (prefix: string) => Promise<string>;
}

interface PendingCandidate {
  kind: "answer_string" | "answer_image";
  command: {
    packetVersionId: string;
    track: LearningTrack;
    answerString?: string;
    file?: LocalAnswerImageFile;
  };
  idempotencyKey: string;
}

interface PendingCommit {
  revisionKey: string;
  confirmationKey: string;
}

export class AnswerIngestionController {
  private readonly gateway: AnswerIngestionControllerOptions["gateway"];
  private readonly onChange: AnswerIngestionControllerOptions["onChange"];
  private readonly createCommandKey: (prefix: string) => Promise<string>;
  private state: AnswerIngestionState;
  private disposed = false;
  private operationEpoch = 0;
  private candidatePreparing = false;
  private commitPreparing = false;
  private pendingCandidate: PendingCandidate | null = null;
  private pendingCommit: PendingCommit | null = null;

  constructor(options: AnswerIngestionControllerOptions) {
    this.gateway = options.gateway;
    this.onChange = options.onChange;
    this.createCommandKey = options.createCommandKey ?? createIdempotencyKey;
    this.state = {
      status: "idle",
      targets: [],
      selectedPacketVersionId: "",
      track: "xingce",
      inputMode: "answer_string",
      answerText: "",
      selectedFile: null,
      uploadProgress: null,
      workspace: null,
      candidateItems: [],
      dirty: false,
      confirmationOpen: false,
      submitting: false,
      pendingRecovery: false,
      generatedAt: "",
      requestId: "",
      validationError: "",
      error: null,
    };
  }

  snapshot(): AnswerIngestionState {
    return cloneState(this.state);
  }

  async loadTargets(): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "loading", error: null });
    try {
      const result = await this.gateway.listAnswerTargets();
      if (!this.isCurrent(epoch)) return;
      const targets = result.packets.map(answerTargetView);
      const selected = targets.find(
        (target) => target.packetVersionId === this.state.selectedPacketVersionId,
      );
      const current = selected ?? targets.at(0) ?? null;
      this.state = {
        ...this.state,
        status: current ? "draft" : "empty",
        targets,
        selectedPacketVersionId: current?.packetVersionId ?? "",
        track: current?.track ?? "xingce",
        generatedAt: result.generatedAt,
        requestId: result.requestId,
        error: null,
      };
      this.publish();
    } catch (error) {
      if (this.isCurrent(epoch))
        this.patch({ status: "error", error: externalLearningError(error) });
    }
  }

  async loadIngestion(ingestionId: string): Promise<void> {
    const normalized = ingestionId.trim();
    if (!normalized || !this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "processing", error: null });
    try {
      const workspace = await this.gateway.getAnswerIngestion(normalized);
      if (this.isCurrent(epoch)) this.applyWorkspace(workspace);
    } catch (error) {
      if (this.isCurrent(epoch))
        this.patch({ status: "error", error: externalLearningError(error) });
    }
  }

  selectPacket(packetVersionId: string): void {
    if (!this.canEditDraft()) return;
    const target = this.state.targets.find((item) => item.packetVersionId === packetVersionId);
    if (!target) return;
    this.patch({
      selectedPacketVersionId: target.packetVersionId,
      track: target.track,
      validationError: "",
    });
  }

  setInputMode(inputMode: "answer_string" | "answer_image"): void {
    if (this.canEditDraft()) this.patch({ inputMode, validationError: "" });
  }

  setAnswerText(answerText: string): void {
    if (this.canEditDraft()) this.patch({ answerText, validationError: "" });
  }

  setAnswerImage(file: LocalAnswerImageFile): void {
    if (!this.canEditDraft()) return;
    const validationError = validateAnswerImage(file);
    this.patch({ selectedFile: file, validationError });
  }

  async createCandidate(): Promise<void> {
    if (!this.canEditDraft() || this.candidatePreparing) return;
    const target = this.state.targets.find(
      (item) => item.packetVersionId === this.state.selectedPacketVersionId,
    );
    if (!target) {
      this.patch({ validationError: "请先选择要回传的学习包版本。" });
      return;
    }
    this.candidatePreparing = true;
    let candidate: PendingCandidate | null;
    try {
      candidate = await this.pendingCandidateFor(target.track);
    } catch (error) {
      this.patch({ status: "error", error: externalLearningError(error), pendingRecovery: false });
      return;
    } finally {
      this.candidatePreparing = false;
    }
    if (!candidate || !this.isActive()) return;
    this.pendingCandidate = candidate;
    const epoch = ++this.operationEpoch;
    this.patch({
      status: candidate.kind === "answer_image" ? "uploading" : "processing",
      submitting: true,
      pendingRecovery: false,
      validationError: "",
      error: null,
      uploadProgress:
        candidate.kind === "answer_image" && candidate.command.file
          ? { sentBytes: 0, totalBytes: candidate.command.file.byteSize, percent: 0 }
          : null,
    });
    try {
      const workspace =
        candidate.kind === "answer_string"
          ? await this.gateway.createAnswerString(
              {
                packetVersionId: candidate.command.packetVersionId,
                track: candidate.command.track,
                answerString: candidate.command.answerString ?? "",
              },
              candidate.idempotencyKey,
            )
          : await this.gateway.createAnswerImage(
              {
                packetVersionId: candidate.command.packetVersionId,
                track: candidate.command.track,
                file: requiredFile(candidate),
              },
              candidate.idempotencyKey,
              (progress) => {
                if (this.isCurrent(epoch))
                  this.patch({ uploadProgress: normalizeProgress(progress) });
              },
            );
      if (!this.isCurrent(epoch)) return;
      this.pendingCandidate = null;
      this.applyWorkspace(workspace);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const view = externalLearningError(error);
      if (!view.uncertain) this.pendingCandidate = null;
      this.patch({
        status: "error",
        submitting: false,
        pendingRecovery: view.uncertain,
        uploadProgress: null,
        error: view,
      });
    }
  }

  updateCandidate(itemId: string, answer: string): void {
    if (this.state.status !== "correction" || this.state.submitting) return;
    const normalized = this.state.track === "xingce" ? answer.trim().toUpperCase() : answer.trim();
    const valid = isValidAnswer(normalized, this.state.track);
    this.patch({
      candidateItems: this.state.candidateItems.map((item) =>
        item.itemId === itemId
          ? {
              ...item,
              answer: normalized,
              requiresCorrection: !valid,
              tone: valid ? "success" : "warning",
            }
          : item,
      ),
      dirty: true,
      validationError: "",
    });
  }

  requestConfirmation(): void {
    if (this.state.status !== "correction" || this.state.submitting) return;
    const invalid = this.state.candidateItems.find(
      (item) => !isValidAnswer(item.answer, this.state.track),
    );
    if (invalid) {
      this.patch({ validationError: `请修正第 ${String(invalid.orderNo)} 题答案。` });
      return;
    }
    this.patch({ status: "confirmation", confirmationOpen: true, validationError: "" });
  }

  cancelConfirmation(): void {
    if (this.state.status === "confirmation" && !this.state.submitting) {
      this.patch({ status: "correction", confirmationOpen: false });
    }
  }

  async confirmAndCommit(): Promise<void> {
    const workspace = this.state.workspace;
    if (
      !workspace ||
      this.state.status !== "confirmation" ||
      this.state.submitting ||
      this.commitPreparing
    )
      return;
    this.commitPreparing = true;
    try {
      this.pendingCommit ??= {
        revisionKey: await this.createCommandKey("external-answer-revision"),
        confirmationKey: await this.createCommandKey("external-answer-confirm"),
      };
    } catch (error) {
      this.patch({ error: externalLearningError(error), pendingRecovery: false });
      return;
    } finally {
      this.commitPreparing = false;
    }
    if (!this.isActive()) return;
    const pending = this.pendingCommit;
    const epoch = ++this.operationEpoch;
    this.patch({ submitting: true, pendingRecovery: false, error: null });
    try {
      let current = workspace;
      if (this.state.dirty) {
        current = await this.gateway.reviseAnswer(
          {
            ingestionId: current.ingestion.ingestionId,
            expectedVersionNo: current.ingestion.versionNo,
            answers: this.state.candidateItems.map(({ itemId, answerBindingCode, answer }) => ({
              itemId,
              answerBindingCode,
              answer,
            })),
          },
          pending.revisionKey,
        );
        if (!this.isCurrent(epoch)) return;
      }
      if (current.ingestion.state === "candidate") {
        current = await this.gateway.confirmAnswer(
          {
            ingestionId: current.ingestion.ingestionId,
            expectedVersionNo: current.ingestion.versionNo,
          },
          pending.confirmationKey,
        );
        if (!this.isCurrent(epoch)) return;
      }
      if (current.ingestion.state !== "committed") {
        throw new Error("Confirmed answer command did not return a committed ingestion version");
      }
      this.pendingCommit = null;
      this.applyWorkspace(current);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const view = externalLearningError(error);
      if (!view.uncertain) this.pendingCommit = null;
      this.patch({
        status: "confirmation",
        submitting: false,
        pendingRecovery: view.uncertain,
        error: view,
      });
    }
  }

  resetDraft(): void {
    if (this.state.submitting) return;
    this.pendingCandidate = null;
    this.pendingCommit = null;
    this.patch({
      status: this.state.targets.length ? "draft" : "empty",
      answerText: "",
      selectedFile: null,
      uploadProgress: null,
      workspace: null,
      candidateItems: [],
      dirty: false,
      confirmationOpen: false,
      pendingRecovery: false,
      validationError: "",
      error: null,
    });
  }

  dispose(): void {
    this.disposed = true;
    this.candidatePreparing = false;
    this.commitPreparing = false;
    this.operationEpoch += 1;
  }

  private async pendingCandidateFor(track: LearningTrack): Promise<PendingCandidate | null> {
    if (this.pendingCandidate) return this.pendingCandidate;
    if (this.state.inputMode === "answer_string") {
      const parsed = parseAnswerString(this.state.answerText);
      if (typeof parsed === "string") {
        this.patch({ validationError: parsed });
        return null;
      }
      return {
        kind: "answer_string",
        command: {
          packetVersionId: this.state.selectedPacketVersionId,
          track,
          answerString: parsed.join(""),
        },
        idempotencyKey: await this.createCommandKey("external-answer-string"),
      };
    }
    const file = this.state.selectedFile;
    const validationError = file ? validateAnswerImage(file) : "请先拍摄或选择答题卡图片。";
    if (!file || validationError) {
      this.patch({ validationError });
      return null;
    }
    return {
      kind: "answer_image",
      command: { packetVersionId: this.state.selectedPacketVersionId, track, file },
      idempotencyKey: await this.createCommandKey("external-answer-sheet"),
    };
  }

  private applyWorkspace(workspace: AnswerIngestionWorkspace): void {
    const status: AnswerIngestionState["status"] =
      workspace.ingestion.state === "committed"
        ? "success"
        : workspace.ingestion.state === "conflict"
          ? "error"
          : workspace.ingestion.state === "rejected"
            ? "expired"
            : "correction";
    this.state = {
      ...this.state,
      status,
      workspace,
      candidateItems: workspace.items.map(answerCandidateView),
      dirty: false,
      confirmationOpen: false,
      submitting: false,
      pendingRecovery: false,
      uploadProgress: null,
      error:
        workspace.ingestion.state === "conflict"
          ? {
              code: "answer_ingestion_conflict",
              title: "答案版本冲突",
              message: "学习包或答案版本已经变更，请核对后重新提交。",
              requestId: workspace.requestId,
              retryable: false,
              uncertain: false,
            }
          : null,
    };
    this.publish();
  }

  private canEditDraft(): boolean {
    return (
      this.isActive() && !this.state.submitting && ["draft", "error"].includes(this.state.status)
    );
  }

  private patch(patch: Partial<AnswerIngestionState>): void {
    this.state = { ...this.state, ...patch };
    this.publish();
  }

  private publish(): void {
    if (this.isActive()) this.onChange(this.snapshot());
  }

  private isActive(): boolean {
    return !this.disposed;
  }

  private isCurrent(epoch: number): boolean {
    return this.isActive() && this.operationEpoch === epoch;
  }
}

export function parseAnswerString(value: string): string[] | string {
  const normalized = value.trim().toUpperCase();
  if (!normalized) return "请输入答案串。";
  const hasSeparator = /[\s,，;；|/]/u.test(normalized);
  const answers = hasSeparator
    ? normalized
        .split(/[\s,，;；|/]+/u)
        .map((item) => item.trim())
        .filter(Boolean)
    : Array.from(normalized);
  if (answers.length === 0) return "请输入答案串。";
  if (answers.length > 200) return "一次最多录入 200 题。";
  const invalidIndex = answers.findIndex((answer) => !isValidAnswer(answer, "xingce"));
  if (invalidIndex >= 0)
    return `第 ${String(invalidIndex + 1)} 个答案格式不正确，请使用 A–H 中的一个选项。`;
  return answers;
}

export function validateAnswerImage(file: LocalAnswerImageFile): string {
  if (!file.localPath.trim() || !file.displayName.trim()) return "未读取到答题卡图片。";
  if (!Number.isSafeInteger(file.byteSize) || file.byteSize <= 0) return "图片大小无效。";
  if (file.byteSize > MAX_ANSWER_IMAGE_BYTES) return "单张答题卡图片不能超过 20 MB。";
  return "";
}

function isValidAnswer(answer: string, track: LearningTrack): boolean {
  if (track === "essay") return answer.length > 0 && answer.length <= 20_000;
  return /^[A-H]$/u.test(answer);
}

function requiredFile(pending: PendingCandidate): LocalAnswerImageFile {
  if (!pending.command.file) throw new Error("Answer sheet command has no file");
  return pending.command.file;
}

function normalizeProgress(progress: UploadProgress): UploadProgress {
  return {
    sentBytes: Math.max(0, progress.sentBytes),
    totalBytes: Math.max(0, progress.totalBytes),
    percent: Math.min(100, Math.max(0, Math.round(progress.percent))),
  };
}

function cloneState(state: AnswerIngestionState): AnswerIngestionState {
  return {
    ...state,
    targets: state.targets.map((target) => ({ ...target })),
    selectedFile: state.selectedFile ? { ...state.selectedFile } : null,
    uploadProgress: state.uploadProgress ? { ...state.uploadProgress } : null,
    workspace: state.workspace
      ? {
          ...state.workspace,
          items: state.workspace.items.map((item) => ({ ...item })),
          partialReasons: [...state.workspace.partialReasons],
        }
      : null,
    candidateItems: state.candidateItems.map((item) => ({ ...item })),
    error: state.error ? { ...state.error } : null,
  };
}
