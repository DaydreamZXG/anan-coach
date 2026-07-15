import type { LearningTrack } from "../../api/external-learning-contracts";
import { createIdempotencyKey } from "../../platform/command-id";
import type {
  ExternalLearningGateway,
  ImportWorkspace,
  LocalImportFile,
  UploadProgress,
} from "./contracts";
import { externalLearningError, type ExternalLearningError } from "./error";
import { recognitionSegmentView, type RecognitionSegmentView } from "./presentation";

export const MAX_IMPORT_IMAGE_BYTES = 20 * 1024 * 1024;
export const MAX_IMPORT_PDF_BYTES = 50 * 1024 * 1024;

export interface CaptureImportState {
  status:
    | "idle"
    | "uploading"
    | "processing"
    | "review"
    | "success"
    | "error"
    | "expired"
    | "quarantined";
  learningTrack: LearningTrack;
  selectedFile: LocalImportFile | null;
  uploadProgress: UploadProgress | null;
  workspace: ImportWorkspace | null;
  reviewStep: "recognition" | "match";
  segments: RecognitionSegmentView[];
  selectedContentVersionId: string;
  dirty: boolean;
  submitting: boolean;
  pendingRecovery: boolean;
  validationError: string;
  error: ExternalLearningError | null;
}

export interface CaptureImportControllerOptions {
  gateway: Pick<
    ExternalLearningGateway,
    "beginImport" | "getImport" | "reviseRecognition" | "confirmRecognition" | "confirmMatch"
  >;
  onChange: (state: CaptureImportState) => void;
  createCommandKey?: (prefix: string) => Promise<string>;
  schedule?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  cancelSchedule?: (handle: ReturnType<typeof setTimeout>) => void;
}

interface PendingImport {
  command: { file: LocalImportFile; learningTrack: LearningTrack };
  idempotencyKey: string;
}

interface PendingConfirmation {
  revisionKey: string;
  recognitionKey: string;
  matchKey: string;
}

export class CaptureImportController {
  private readonly gateway: CaptureImportControllerOptions["gateway"];
  private readonly onChange: CaptureImportControllerOptions["onChange"];
  private readonly createCommandKey: (prefix: string) => Promise<string>;
  private readonly schedule: NonNullable<CaptureImportControllerOptions["schedule"]>;
  private readonly cancelSchedule: NonNullable<CaptureImportControllerOptions["cancelSchedule"]>;
  private state: CaptureImportState;
  private disposed = false;
  private operationEpoch = 0;
  private commandPreparing = false;
  private pollHandle: ReturnType<typeof setTimeout> | undefined;
  private pendingImport: PendingImport | null = null;
  private pendingConfirmation: PendingConfirmation | null = null;

  constructor(options: CaptureImportControllerOptions) {
    this.gateway = options.gateway;
    this.onChange = options.onChange;
    this.createCommandKey = options.createCommandKey ?? createIdempotencyKey;
    this.schedule = options.schedule ?? ((callback, delayMs) => setTimeout(callback, delayMs));
    this.cancelSchedule = options.cancelSchedule ?? clearTimeout;
    this.state = {
      status: "idle",
      learningTrack: "xingce",
      selectedFile: null,
      uploadProgress: null,
      workspace: null,
      reviewStep: "recognition",
      segments: [],
      selectedContentVersionId: "",
      dirty: false,
      submitting: false,
      pendingRecovery: false,
      validationError: "",
      error: null,
    };
  }

  snapshot(): CaptureImportState {
    return cloneState(this.state);
  }

  setTrack(track: LearningTrack): void {
    if (!this.canEdit()) return;
    this.patch({ learningTrack: track, validationError: "" });
  }

  async startImport(file: LocalImportFile): Promise<void> {
    if (!this.canEdit() || this.commandPreparing) return;
    const validationError = validateImportFile(file);
    if (validationError) {
      this.patch({ selectedFile: file, validationError });
      return;
    }
    const command = { file, learningTrack: this.state.learningTrack };
    this.commandPreparing = true;
    let pending: PendingImport;
    try {
      pending = this.pendingImport ?? {
        command,
        idempotencyKey: await this.createCommandKey("external-import"),
      };
    } catch (error) {
      this.patch({ status: "error", error: externalLearningError(error), pendingRecovery: false });
      return;
    } finally {
      this.commandPreparing = false;
    }
    if (!this.isActive()) return;
    this.pendingImport = pending;
    const epoch = ++this.operationEpoch;
    this.patch({
      status: "uploading",
      selectedFile: file,
      uploadProgress: { sentBytes: 0, totalBytes: file.byteSize, percent: 0 },
      validationError: "",
      error: null,
      pendingRecovery: false,
    });
    try {
      const workspace = await this.gateway.beginImport(
        pending.command,
        pending.idempotencyKey,
        (progress) => {
          if (this.isCurrent(epoch)) this.patch({ uploadProgress: normalizeProgress(progress) });
        },
      );
      if (!this.isCurrent(epoch)) return;
      this.pendingImport = null;
      this.pendingConfirmation = null;
      this.applyWorkspace(workspace);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const view = externalLearningError(error);
      if (!view.uncertain) this.pendingImport = null;
      this.patch({
        status: "error",
        error: view,
        pendingRecovery: view.uncertain,
        uploadProgress: null,
      });
    }
  }

  async retryPendingImport(): Promise<void> {
    const pending = this.pendingImport;
    if (!pending || !this.isActive() || this.state.status !== "error") return;
    await this.startImport(pending.command.file);
  }

  async load(importId: string): Promise<void> {
    const normalized = importId.trim();
    if (!normalized || !this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ status: "processing", error: null, pendingRecovery: false });
    try {
      const workspace = await this.gateway.getImport(normalized);
      if (this.isCurrent(epoch)) {
        this.pendingConfirmation = null;
        this.applyWorkspace(workspace);
      }
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({ status: "error", error: externalLearningError(error) });
    }
  }

  async refresh(): Promise<void> {
    const importId = this.state.workspace?.importId;
    if (!importId || this.state.submitting) return;
    await this.load(importId);
  }

  updateSegment(segmentId: string, text: string): void {
    if (
      !this.canEdit() ||
      this.state.status !== "review" ||
      this.state.workspace?.recognition?.state !== "candidate"
    )
      return;
    const segments = this.state.segments.map((segment) =>
      segment.segmentId === segmentId
        ? {
            ...segment,
            text,
            requiresCorrection: text.trim().length === 0,
            tone: "warning" as const,
          }
        : segment,
    );
    this.patch({ segments, dirty: true, validationError: "" });
  }

  selectMatch(contentVersionId: string): void {
    if (
      !this.canEdit() ||
      this.state.status !== "review" ||
      this.state.workspace?.recognition?.state !== "confirmed"
    )
      return;
    const exists = this.state.workspace.match?.candidates.some(
      (candidate) => candidate.contentVersionId === contentVersionId,
    );
    if (exists) this.patch({ selectedContentVersionId: contentVersionId, validationError: "" });
  }

  async confirm(): Promise<void> {
    const workspace = this.state.workspace;
    const recognition = workspace?.recognition;
    if (
      !workspace ||
      !recognition ||
      this.state.submitting ||
      this.commandPreparing ||
      !this.isActive()
    )
      return;
    const confirmingRecognition = recognition.state === "candidate";
    if (
      confirmingRecognition &&
      this.state.segments.some((segment) => segment.text.trim().length === 0)
    ) {
      this.patch({ validationError: "请补全所有标记为待确认的题目文本。" });
      return;
    }
    if (!confirmingRecognition && !workspace.match) {
      this.patch({ validationError: "识别已确认，但服务端尚未返回可复核的匹配版本。" });
      return;
    }
    if (!confirmingRecognition && !this.state.selectedContentVersionId) {
      this.patch({ validationError: "请选择一个匹配结果；没有可确认结果时不能写入学习证据。" });
      return;
    }
    this.commandPreparing = true;
    try {
      this.pendingConfirmation ??= {
        revisionKey: await this.createCommandKey("external-ocr-revision"),
        recognitionKey: await this.createCommandKey("external-ocr-confirm"),
        matchKey: await this.createCommandKey("external-match-confirm"),
      };
    } catch (error) {
      this.patch({ status: "error", error: externalLearningError(error), pendingRecovery: false });
      return;
    } finally {
      this.commandPreparing = false;
    }
    if (!this.isActive()) return;
    const pending = this.pendingConfirmation;
    const epoch = ++this.operationEpoch;
    this.patch({ submitting: true, pendingRecovery: false, error: null, validationError: "" });
    try {
      let current = workspace;
      if (confirmingRecognition && this.state.dirty) {
        current = await this.gateway.reviseRecognition(
          {
            importId: current.importId,
            recognitionRevisionId: recognition.recognitionRevisionId,
            expectedRevisionNo: recognition.revisionNo,
            segments: this.state.segments.map(({ segmentId, text }) => ({
              segmentId,
              text: text.trim(),
            })),
          },
          pending.revisionKey,
        );
        if (!this.isCurrent(epoch)) return;
      }
      const currentRecognition = requiredRecognition(current);
      if (confirmingRecognition && currentRecognition.state === "candidate") {
        current = await this.gateway.confirmRecognition(
          {
            importId: current.importId,
            recognitionRevisionId: currentRecognition.recognitionRevisionId,
            expectedRevisionNo: currentRecognition.revisionNo,
            learningTrack: this.state.learningTrack,
          },
          pending.recognitionKey,
        );
        if (!this.isCurrent(epoch)) return;
        this.applyWorkspace(current);
        return;
      }
      if (currentRecognition.state !== "confirmed") {
        throw new Error("Recognition must be confirmed before content matching");
      }
      const currentMatch = requiredMatch(current);
      if (currentMatch.state === "candidate") {
        current = await this.gateway.confirmMatch(
          {
            importId: current.importId,
            contentMatchVersionId: currentMatch.contentMatchVersionId,
            expectedVersionNo: currentMatch.versionNo,
            confirmedContentVersionId: this.state.selectedContentVersionId,
          },
          pending.matchKey,
        );
        if (!this.isCurrent(epoch)) return;
      }
      this.pendingConfirmation = null;
      this.applyWorkspace(current);
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const view = externalLearningError(error);
      if (!view.uncertain) this.pendingConfirmation = null;
      this.patch({
        status: "error",
        submitting: false,
        pendingRecovery: view.uncertain,
        error: view,
      });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.commandPreparing = false;
    this.operationEpoch += 1;
    this.clearPoll();
  }

  private applyWorkspace(workspace: ImportWorkspace): void {
    this.clearPoll();
    const status = captureStatus(workspace.state);
    const selected =
      workspace.match?.confirmedContentVersionId ??
      workspace.match?.candidates.at(0)?.contentVersionId ??
      "";
    this.state = {
      ...this.state,
      status,
      workspace,
      reviewStep: workspace.recognition?.state === "confirmed" ? "match" : "recognition",
      segments: workspace.recognitionSegments.map(recognitionSegmentView),
      selectedContentVersionId: selected,
      dirty: false,
      submitting: false,
      pendingRecovery: false,
      uploadProgress: null,
      validationError: "",
      error:
        workspace.state === "failed"
          ? {
              code: "processing_failed",
              title: "识别处理失败",
              message: workspace.failureReason ?? "文件没有生成可确认的识别结果。",
              requestId: workspace.requestId,
              retryable: true,
              uncertain: false,
            }
          : null,
    };
    this.publish();
    if (workspace.state === "processing" || workspace.state === "uploading") {
      const delay = Math.min(Math.max(workspace.pollAfterMs ?? 2_000, 800), 10_000);
      this.pollHandle = this.schedule(() => {
        this.pollHandle = undefined;
        void this.refresh();
      }, delay);
    }
  }

  private patch(patch: Partial<CaptureImportState>): void {
    this.state = { ...this.state, ...patch };
    this.publish();
  }

  private publish(): void {
    if (this.isActive()) this.onChange(this.snapshot());
  }

  private canEdit(): boolean {
    return (
      this.isActive() &&
      !this.commandPreparing &&
      !this.state.submitting &&
      this.state.status !== "uploading"
    );
  }

  private isActive(): boolean {
    return !this.disposed;
  }

  private isCurrent(epoch: number): boolean {
    return this.isActive() && this.operationEpoch === epoch;
  }

  private clearPoll(): void {
    if (this.pollHandle !== undefined) this.cancelSchedule(this.pollHandle);
    this.pollHandle = undefined;
  }
}

export function validateImportFile(file: LocalImportFile): string {
  if (!file.localPath.trim() || !file.displayName.trim()) return "未读取到可上传的文件。";
  const allowed = ["image/jpeg", "image/png", "application/pdf"];
  if (!allowed.includes(file.mimeType)) return "仅支持 JPG、PNG 或 PDF 文件。";
  if (!Number.isSafeInteger(file.byteSize) || file.byteSize <= 0) return "文件大小无效。";
  const max = file.mimeType === "application/pdf" ? MAX_IMPORT_PDF_BYTES : MAX_IMPORT_IMAGE_BYTES;
  if (file.byteSize > max) {
    return file.mimeType === "application/pdf"
      ? "PDF 不能超过 50 MB。"
      : "单张图片不能超过 20 MB。";
  }
  if (file.mimeType === "application/pdf" && file.sourceKind !== "pdf_upload") {
    return "PDF 必须使用文件导入流程。";
  }
  if (file.mimeType !== "application/pdf" && file.sourceKind === "pdf_upload") {
    return "图片必须使用拍照或相册导入流程。";
  }
  return "";
}

function requiredRecognition(workspace: ImportWorkspace) {
  if (!workspace.recognition) throw new Error("Import workspace has no recognition revision");
  return workspace.recognition;
}

function requiredMatch(workspace: ImportWorkspace) {
  if (!workspace.match) throw new Error("Import workspace has no content match version");
  return workspace.match;
}

function normalizeProgress(progress: UploadProgress): UploadProgress {
  return {
    sentBytes: Math.max(0, progress.sentBytes),
    totalBytes: Math.max(0, progress.totalBytes),
    percent: Math.min(100, Math.max(0, Math.round(progress.percent))),
  };
}

function captureStatus(state: ImportWorkspace["state"]): CaptureImportState["status"] {
  return {
    uploading: "uploading",
    processing: "processing",
    review: "review",
    ready: "success",
    failed: "error",
    quarantined: "quarantined",
    expired: "expired",
  }[state] as CaptureImportState["status"];
}

function cloneState(state: CaptureImportState): CaptureImportState {
  return {
    ...state,
    selectedFile: state.selectedFile ? { ...state.selectedFile } : null,
    uploadProgress: state.uploadProgress ? { ...state.uploadProgress } : null,
    workspace: state.workspace
      ? {
          ...state.workspace,
          recognitionSegments: state.workspace.recognitionSegments.map((item) => ({ ...item })),
          partialReasons: [...state.workspace.partialReasons],
        }
      : null,
    segments: state.segments.map((segment) => ({ ...segment })),
    error: state.error ? { ...state.error } : null,
  };
}
