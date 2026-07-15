import type {
  OfflinePacketVersion,
  RecognitionRevision,
  SourceArtifactVersion,
} from "./external-learning-contracts";
import { ApiClientError } from "./client";
import type { ApiClient } from "./client";
import type {
  ConfirmAnswerCandidateCommand as ApiConfirmAnswerCandidateCommand,
  ConfirmImportMatchCommand as ApiConfirmImportMatchCommand,
  ConfirmImportRecognitionCommand as ApiConfirmImportRecognitionCommand,
  CorrectAnswerCandidateCommand as ApiCorrectAnswerCandidateCommand,
  CorrectImportRecognitionCommand as ApiCorrectImportRecognitionCommand,
  CreateAnswerCandidateCommand as ApiCreateAnswerCandidateCommand,
  ProcessImportCommand as ApiProcessImportCommand,
  RenewPacketCommand as ApiRenewPacketCommand,
  RetryPacketCommand as ApiRetryPacketCommand,
  StartRecognitionCommand as ApiStartRecognitionCommand,
  StartUploadCommand as ApiStartUploadCommand,
  UploadMode,
  UploadState,
} from "./generated/types.gen";
import { sha256Hex } from "./sha256";
import type { ApiEnvelope } from "../domain/contracts";
import type {
  AnswerIngestionWorkspace,
  AnswerTargetSnapshot,
  BeginImportCommand,
  ConfirmAnswerCommand,
  CreateAnswerImageCommand,
  CreateAnswerStringCommand,
  CreatePacketCommand,
  ExternalLearningGateway,
  ImportWorkspace,
  LocalAnswerImageFile,
  LocalImportFile,
  MatchConfirmationCommand,
  PacketAccessGrant,
  PacketListSnapshot,
  RecognitionConfirmationCommand,
  RecognitionCorrectionCommand,
  RenewPacketCommand,
  RetryPacketCommand,
  ReviseAnswerCommand,
  UploadProgress,
} from "../features/external-learning/contracts";

type ImportWorkspaceData = Omit<ImportWorkspace, "requestId">;
type AnswerWorkspaceData = Omit<AnswerIngestionWorkspace, "requestId">;
type PacketListData = Omit<PacketListSnapshot, "requestId">;
type AnswerTargetData = Omit<AnswerTargetSnapshot, "requestId">;

interface UploadSessionProjection {
  sessionId: string;
  artifactVersionId: string;
  revision: number;
  state: UploadState;
  sourceKind: string;
  originalFilename: string;
  mimeType: string;
  byteSize: number;
  contentSha256: string;
  pageCount: number | null;
  expiresAt: string;
  recognitionEligible: boolean;
  failureCode: string | null;
  uploadMode: UploadMode;
  partSize: number | null;
  partCount: number;
  uploadedPartCount: number;
}

interface UploadPartAuthorizationProjection {
  objectRef: string;
  partNumber: number;
  byteSize: number;
  contentSha256: string;
  url: string;
  requiredHeaders: Record<string, string>;
  expiresAt: string;
}

interface UploadAuthorizationProjection {
  objectRef: string;
  mode: UploadMode;
  expiresAt: string;
  parts: UploadPartAuthorizationProjection[];
  providerUploadId: string | null;
  partSize: number | null;
}

interface StartUploadProjection {
  session: UploadSessionProjection;
  authorization: UploadAuthorizationProjection;
  replayed: boolean;
}

interface CompleteUploadProjection {
  session: UploadSessionProjection;
  artifact: SourceArtifactVersion | null;
  replayed: boolean;
}

interface PacketAccessProjection {
  packetVersionId: string;
  downloadUrl: string;
  fileType: "pdf" | "note_app_file";
  expiresAt: string;
}

interface OmrMarkProjection {
  itemNo: number;
  selectedOptions: string[];
  confidence: number;
  ambiguous?: boolean;
}

interface UploadedArtifact {
  sessionId: string;
  artifact: SourceArtifactVersion;
}

const uploadTimeoutMs = 120_000;
const downloadTimeoutMs = 120_000;
const multipartPartBytes = 8 * 1024 * 1024;

type RecognitionRevisionProjection = RecognitionRevision;

export class ExternalLearningApi implements ExternalLearningGateway {
  constructor(private readonly client: ApiClient) {}

  async beginImport(
    command: BeginImportCommand,
    idempotencyKey: string,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<ImportWorkspace> {
    const uploaded = await this.uploadArtifact(command.file, idempotencyKey, onProgress);
    const payload: ApiProcessImportCommand = { learningTrack: command.learningTrack };
    const response = await this.client.post<ImportWorkspaceData>(
      `/api/v2/external-learning/imports/${encodeURIComponent(uploaded.sessionId)}/process`,
      payload,
      { idempotencyKey: derivedCommandKey(idempotencyKey, "process"), timeoutMs: 60_000 },
    );
    return importWorkspace(response);
  }

  async getImport(importId: string): Promise<ImportWorkspace> {
    const response = await this.client.get<ImportWorkspaceData>(
      `/api/v2/external-learning/imports/${encodeURIComponent(requiredId(importId, "importId"))}`,
      { timeoutMs: 30_000 },
    );
    return importWorkspace(response);
  }

  async reviseRecognition(
    command: RecognitionCorrectionCommand,
    idempotencyKey: string,
  ): Promise<ImportWorkspace> {
    const payload: ApiCorrectImportRecognitionCommand = {
      expectedRevisionNo: command.expectedRevisionNo,
      segments: [...command.segments],
    };
    const response = await this.client.put<ImportWorkspaceData>(
      `${importPath(command.importId)}/recognitions/${encodeURIComponent(requiredId(command.recognitionRevisionId, "recognitionRevisionId"))}`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return importWorkspace(response);
  }

  async confirmRecognition(
    command: RecognitionConfirmationCommand,
    idempotencyKey: string,
  ): Promise<ImportWorkspace> {
    const payload: ApiConfirmImportRecognitionCommand = {
      expectedRevisionNo: command.expectedRevisionNo,
      learningTrack: command.learningTrack,
    };
    const response = await this.client.post<ImportWorkspaceData>(
      `${importPath(command.importId)}/recognitions/${encodeURIComponent(requiredId(command.recognitionRevisionId, "recognitionRevisionId"))}/confirm`,
      payload,
      { idempotencyKey, timeoutMs: 60_000 },
    );
    return importWorkspace(response);
  }

  async confirmMatch(
    command: MatchConfirmationCommand,
    idempotencyKey: string,
  ): Promise<ImportWorkspace> {
    const payload: ApiConfirmImportMatchCommand = {
      expectedVersionNo: command.expectedVersionNo,
      confirmedContentVersionId: command.confirmedContentVersionId,
    };
    const response = await this.client.post<ImportWorkspaceData>(
      `${importPath(command.importId)}/matches/${encodeURIComponent(command.contentMatchVersionId)}/confirm`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return importWorkspace(response);
  }

  async listPackets(): Promise<PacketListSnapshot> {
    const response = await this.client.get<PacketListData>("/api/v2/external-learning/packets");
    return {
      ...response.data,
      requestId: response.requestId,
    };
  }

  async createPacket(
    command: CreatePacketCommand,
    idempotencyKey: string,
  ): Promise<OfflinePacketVersion> {
    const response = await this.client.post<OfflinePacketVersion>(
      "/api/v2/external-learning/packets",
      command,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return response.data;
  }

  async retryPacket(
    command: RetryPacketCommand,
    idempotencyKey: string,
  ): Promise<OfflinePacketVersion> {
    const payload: ApiRetryPacketCommand = {
      failedPacketVersionId: command.failedPacketVersionId,
      expectedVersionNo: command.expectedVersionNo,
    };
    const response = await this.client.post<OfflinePacketVersion>(
      `/api/v2/external-learning/packets/${encodeURIComponent(requiredId(command.packetId, "packetId"))}/retry`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return response.data;
  }

  async renewPacket(
    command: RenewPacketCommand,
    idempotencyKey: string,
  ): Promise<OfflinePacketVersion> {
    const payload: ApiRenewPacketCommand = {
      expiredPacketVersionId: command.expiredPacketVersionId,
      expectedVersionNo: command.expectedVersionNo,
    };
    const response = await this.client.post<OfflinePacketVersion>(
      `/api/v2/external-learning/packets/${encodeURIComponent(requiredId(command.packetId, "packetId"))}/renew`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return response.data;
  }

  async getPacketAccess(packetVersionId: string): Promise<PacketAccessGrant> {
    const response = await this.client.post<PacketAccessProjection>(
      `/api/v2/external-learning/packets/${encodeURIComponent(requiredId(packetVersionId, "packetVersionId"))}/access`,
      {},
      { timeoutMs: 30_000 },
    );
    validateAccess(response.data);
    const temporaryPath = await downloadPrivateFile(response.data);
    const localFilePath = await persistDownloadedFile(temporaryPath);
    return {
      packetVersionId: response.data.packetVersionId,
      localFilePath,
      fileType: response.data.fileType,
      expiresAt: response.data.expiresAt,
    };
  }

  async listAnswerTargets(): Promise<AnswerTargetSnapshot> {
    const response = await this.client.get<AnswerTargetData>(
      "/api/v2/external-learning/answer-ingestions/targets",
    );
    return {
      ...response.data,
      requestId: response.requestId,
    };
  }

  async getAnswerIngestion(ingestionId: string): Promise<AnswerIngestionWorkspace> {
    const response = await this.client.get<AnswerWorkspaceData>(answerIngestionPath(ingestionId));
    return answerWorkspace(response);
  }

  async createAnswerString(
    command: CreateAnswerStringCommand,
    idempotencyKey: string,
  ): Promise<AnswerIngestionWorkspace> {
    const payload: ApiCreateAnswerCandidateCommand = {
      packetVersionId: command.packetVersionId,
      track: command.track,
      inputMode: "answer_string",
      answerString: command.answerString,
    };
    const response = await this.client.post<AnswerWorkspaceData>(
      "/api/v2/external-learning/answer-ingestions",
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return answerWorkspace(response);
  }

  async createAnswerImage(
    command: CreateAnswerImageCommand,
    idempotencyKey: string,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<AnswerIngestionWorkspace> {
    if (command.track !== "xingce") {
      throw new ApiClientError(
        "答题卡图片识别仅支持行测客观题。",
        422,
        "answer-omr-track-unsupported",
      );
    }
    const uploaded = await this.uploadArtifact(
      command.file,
      derivedCommandKey(idempotencyKey, "upload"),
      onProgress,
    );
    const recognitionPayload: ApiStartRecognitionCommand = {
      artifactVersionId: uploaded.artifact.artifactVersionId,
      recognitionKind: "answer_sheet_omr",
    };
    const recognized = await this.client.post<RecognitionRevisionProjection>(
      "/api/v2/external-learning/recognitions",
      recognitionPayload,
      { idempotencyKey: derivedCommandKey(idempotencyKey, "recognition"), timeoutMs: 60_000 },
    );
    const packet = await this.client.get<OfflinePacketVersion>(
      `/api/v2/external-learning/packets/${encodeURIComponent(requiredId(command.packetVersionId, "packetVersionId"))}`,
      { timeoutMs: 30_000 },
    );
    const payload: ApiCreateAnswerCandidateCommand = {
      packetVersionId: command.packetVersionId,
      track: command.track,
      inputMode: "answer_sheet_omr",
      sourceArtifactVersionIds: [uploaded.artifact.artifactVersionId],
      recognitionRevisionIds: [recognized.data.recognitionRevisionId],
      entries: answerEntriesFromOmr(recognized.data, packet.data),
    };
    const response = await this.client.post<AnswerWorkspaceData>(
      "/api/v2/external-learning/answer-ingestions",
      payload,
      { idempotencyKey: derivedCommandKey(idempotencyKey, "candidate"), timeoutMs: 60_000 },
    );
    return answerWorkspace(response);
  }

  async reviseAnswer(
    command: ReviseAnswerCommand,
    idempotencyKey: string,
  ): Promise<AnswerIngestionWorkspace> {
    const payload: ApiCorrectAnswerCandidateCommand = {
      expectedVersionNo: command.expectedVersionNo,
      entries: command.answers.map((item) => ({
        bindingCode: item.answerBindingCode,
        answer: item.answer,
      })),
    };
    const response = await this.client.put<AnswerWorkspaceData>(
      `${answerIngestionPath(command.ingestionId)}/candidate`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return answerWorkspace(response);
  }

  async confirmAnswer(
    command: ConfirmAnswerCommand,
    idempotencyKey: string,
  ): Promise<AnswerIngestionWorkspace> {
    const payload: ApiConfirmAnswerCandidateCommand = {
      expectedVersionNo: command.expectedVersionNo,
      confirmation: "user_verified",
    };
    const response = await this.client.post<AnswerWorkspaceData>(
      `${answerIngestionPath(command.ingestionId)}/confirm`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    return answerWorkspace(response);
  }

  private async uploadArtifact(
    file: LocalImportFile | LocalAnswerImageFile,
    idempotencyKey: string,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<UploadedArtifact> {
    onProgress(progress(file.byteSize, 0));
    const fileInfo = await getFileInfo(file.localPath);
    if (fileInfo.size !== file.byteSize) {
      throw new ApiClientError(
        "本地文件在上传前已经发生变化，请重新选择。",
        409,
        "local-file-size-changed",
      );
    }
    const partSha256s = await uploadPartDigests(file, onProgress);
    const startPayload: ApiStartUploadCommand = {
      sourceKind: file.sourceKind,
      originalFilename: safeFileName(file.displayName),
      mimeType: file.mimeType,
      byteSize: file.byteSize,
      contentSha256: fileInfo.digest,
      pageCount: file.sourceKind === "pdf_upload" ? file.pageCount : null,
      ...(partSha256s.length ? { partSha256s } : {}),
    };
    const started = await this.client.post<StartUploadProjection>(
      "/api/v2/external-learning/uploads",
      startPayload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
    assertUploadReplay(started.data.session, file, fileInfo.digest, partSha256s.length);
    if (started.data.session.state === "awaiting_upload") {
      await uploadAuthorizedFile(
        file,
        started.data.authorization,
        partSha256s.length ? partSha256s : [fileInfo.digest],
        onProgress,
      );
    }
    const completed = await this.client.post<CompleteUploadProjection>(
      `/api/v2/external-learning/uploads/${encodeURIComponent(started.data.session.sessionId)}/complete`,
      {},
      { timeoutMs: 60_000 },
    );
    onProgress(progress(file.byteSize, file.byteSize));
    if (!completed.data.artifact || !completed.data.session.recognitionEligible) {
      throw uploadTerminalError(completed.data.session);
    }
    return {
      sessionId: completed.data.session.sessionId,
      artifact: completed.data.artifact,
    };
  }
}

function importWorkspace(envelope: ApiEnvelope<ImportWorkspaceData>): ImportWorkspace {
  return { ...envelope.data, requestId: envelope.requestId };
}

function answerWorkspace(envelope: ApiEnvelope<AnswerWorkspaceData>): AnswerIngestionWorkspace {
  return { ...envelope.data, requestId: envelope.requestId };
}

function importPath(importId: string): string {
  return `/api/v2/external-learning/imports/${encodeURIComponent(requiredId(importId, "importId"))}`;
}

function answerIngestionPath(ingestionId: string): string {
  return `/api/v2/external-learning/answer-ingestions/${encodeURIComponent(requiredId(ingestionId, "ingestionId"))}`;
}

function requiredId(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} is required`);
  return normalized;
}

function derivedCommandKey(base: string, suffix: string): string {
  const combined = `${base}:${suffix}`;
  return combined.length <= 160 ? combined : base;
}

function progress(totalBytes: number, sentBytes: number): UploadProgress {
  const safeSent = Math.min(totalBytes, Math.max(0, sentBytes));
  return {
    sentBytes: safeSent,
    totalBytes,
    percent: totalBytes > 0 ? Math.round((safeSent / totalBytes) * 100) : 0,
  };
}

function getFileInfo(filePath: string): Promise<{ size: number; digest: string }> {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().getFileInfo({
      filePath,
      digestAlgorithm: "sha256",
      success: (result) => {
        resolve({ size: result.size, digest: result.digest.toLowerCase() });
      },
      fail: (error) => {
        reject(new Error(error.errMsg || "无法读取本地文件摘要。"));
      },
    });
  });
}

async function uploadPartDigests(
  file: LocalImportFile | LocalAnswerImageFile,
  onProgress: (progress: UploadProgress) => void,
): Promise<string[]> {
  if (!requiresMultipart(file)) {
    onProgress(progress(file.byteSize, 0));
    return [];
  }
  const partCount = Math.ceil(file.byteSize / multipartPartBytes);
  const digests: string[] = [];
  for (let index = 0; index < partCount; index += 1) {
    const position = index * multipartPartBytes;
    const length = Math.min(multipartPartBytes, file.byteSize - position);
    const bytes = await readFile(file.localPath, position, length);
    if (bytes.byteLength !== length) {
      throw new ApiClientError("无法完整读取本地文件分片。", 409, "local-file-part-incomplete");
    }
    digests.push(sha256Hex(bytes));
  }
  onProgress(progress(file.byteSize, 0));
  return digests;
}

function requiresMultipart(file: LocalImportFile | LocalAnswerImageFile): boolean {
  return file.mimeType === "application/pdf" || file.byteSize > multipartPartBytes;
}

async function uploadAuthorizedFile(
  file: LocalImportFile | LocalAnswerImageFile,
  authorization: UploadAuthorizationProjection,
  expectedDigests: readonly string[],
  onProgress: (progress: UploadProgress) => void,
): Promise<void> {
  const parts = validateUploadAuthorization(file, authorization, expectedDigests);
  let sentBytes = 0;
  for (const part of parts) {
    const position = (part.partNumber - 1) * (authorization.partSize ?? file.byteSize);
    const bytes = await readFile(file.localPath, position, part.byteSize);
    if (bytes.byteLength !== part.byteSize || sha256Hex(bytes) !== part.contentSha256) {
      throw new ApiClientError(
        "本地文件在获得上传授权后发生了变化，请重新选择。",
        409,
        "local-file-part-changed",
      );
    }
    await putAuthorizedPart(part, bytes);
    sentBytes += part.byteSize;
    onProgress(progress(file.byteSize, sentBytes));
  }
}

function putAuthorizedPart(
  part: UploadPartAuthorizationProjection,
  bytes: ArrayBuffer,
): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.request<ArrayBuffer>({
      url: part.url,
      method: "PUT",
      data: bytes,
      timeout: uploadTimeoutMs,
      header: part.requiredHeaders,
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) resolve();
        else reject(providerError("文件分片上传未被对象存储接受。", result.statusCode));
      },
      fail: (error) => {
        reject(new ApiClientError(error.errMsg, 0, "object-upload-network"));
      },
    });
  });
}

function readFile(filePath: string, position: number, length: number): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().readFile({
      filePath,
      position,
      length,
      success: (result) => {
        if (result.data instanceof ArrayBuffer) resolve(result.data);
        else reject(new Error("本地文件读取结果不是二进制数据。"));
      },
      fail: (error) => {
        reject(new Error(error.errMsg || "无法读取本地文件。"));
      },
    });
  });
}

function validateUploadAuthorization(
  file: LocalImportFile | LocalAnswerImageFile,
  authorization: UploadAuthorizationProjection,
  expectedDigests: readonly string[],
): UploadPartAuthorizationProjection[] {
  if (Date.parse(authorization.expiresAt) <= Date.now()) {
    throw new ApiClientError("文件上传授权已经过期。", 409, "upload-authorization-expired");
  }
  const parts = [...authorization.parts].sort((left, right) => left.partNumber - right.partNumber);
  const expectedMode = requiresMultipart(file) ? "multipart" : "single_put";
  if (
    authorization.mode !== expectedMode ||
    parts.length !== expectedDigests.length ||
    (expectedMode === "multipart" &&
      (authorization.partSize !== multipartPartBytes || !authorization.providerUploadId)) ||
    (expectedMode === "single_put" &&
      (authorization.partSize !== null || authorization.providerUploadId !== null))
  ) {
    throw new ApiClientError(
      "对象存储上传授权与本地文件分片计划不一致。",
      503,
      "upload-plan-mismatch",
    );
  }
  let totalBytes = 0;
  for (const [index, part] of parts.entries()) {
    const expectedSize = Math.min(
      authorization.partSize ?? file.byteSize,
      file.byteSize - index * (authorization.partSize ?? file.byteSize),
    );
    if (
      part.partNumber !== index + 1 ||
      part.objectRef !== authorization.objectRef ||
      part.byteSize !== expectedSize ||
      part.contentSha256 !== expectedDigests[index] ||
      !part.url.startsWith("https://") ||
      Date.parse(part.expiresAt) <= Date.now() ||
      !Object.keys(part.requiredHeaders).length
    ) {
      throw new ApiClientError("对象存储返回了不可验证的分片授权。", 503, "upload-part-invalid");
    }
    totalBytes += part.byteSize;
  }
  if (totalBytes !== file.byteSize) {
    throw new ApiClientError(
      "对象存储分片字节数与本地文件不一致。",
      503,
      "upload-byte-count-mismatch",
    );
  }
  return parts;
}

function assertUploadReplay(
  session: UploadSessionProjection,
  file: LocalImportFile | LocalAnswerImageFile,
  digest: string,
  expectedPartCount: number,
): void {
  const expectedMode = requiresMultipart(file) ? "multipart" : "single_put";
  if (
    session.sourceKind !== file.sourceKind ||
    session.originalFilename !== safeFileName(file.displayName) ||
    session.mimeType !== file.mimeType ||
    session.byteSize !== file.byteSize ||
    session.contentSha256 !== digest ||
    session.uploadMode !== expectedMode ||
    session.partCount !== (expectedPartCount || 1) ||
    session.partSize !== (expectedMode === "multipart" ? multipartPartBytes : null)
  ) {
    throw new ApiClientError(
      "原上传命令与当前文件不一致，已拒绝复用幂等结果。",
      409,
      "upload-replay-mismatch",
    );
  }
}

function safeFileName(value: string): string {
  const normalized = value.trim();
  if (
    !normalized ||
    normalized.length > 255 ||
    normalized.includes("/") ||
    normalized.includes("\\")
  ) {
    throw new ApiClientError("文件名不符合上传安全规则。", 422, "unsafe-file-name");
  }
  return normalized;
}

function uploadTerminalError(session: UploadSessionProjection): ApiClientError {
  const stateMessage: Partial<Record<UploadState, string>> = {
    quarantined: "文件未通过安全扫描，已被隔离。",
    expired: "文件上传会话已经过期。",
    failed: "文件安全处理失败。",
    scan_failed: "文件安全扫描未完成，不能进入识别。",
  };
  return new ApiClientError(
    stateMessage[session.state] ?? "文件尚未达到可识别状态。",
    409,
    session.failureCode ?? `upload-${session.state}`,
  );
}

function providerError(message: string, statusCode: number): ApiClientError {
  return new ApiClientError(message, statusCode, `object-provider-${String(statusCode)}`);
}

function answerEntriesFromOmr(
  recognition: RecognitionRevisionProjection,
  packet: OfflinePacketVersion,
): NonNullable<ApiCreateAnswerCandidateCommand["entries"]> {
  if (
    recognition.recognitionKind !== "answer_sheet_omr" ||
    recognition.state !== "candidate" ||
    packet.state !== "ready" ||
    packet.items.length === 0 ||
    packet.items.some((item) => item.track !== "xingce")
  ) {
    throw new ApiClientError(
      "答题卡识别版本或学习包版本不能用于候选答案。",
      409,
      "answer-omr-lineage-invalid",
    );
  }
  const marks = omrMarks(recognition.candidatePayload);
  const marksByItemNo = new Map<number, OmrMarkProjection>();
  for (const mark of marks) {
    if (marksByItemNo.has(mark.itemNo)) {
      throw new ApiClientError("答题卡识别结果含有重复题号。", 409, "answer-omr-duplicate-item");
    }
    marksByItemNo.set(mark.itemNo, mark);
  }
  const packetOrderNumbers = new Set(packet.items.map((item) => item.orderNo));
  if (
    marksByItemNo.size !== packet.items.length ||
    [...marksByItemNo.keys()].some((itemNo) => !packetOrderNumbers.has(itemNo))
  ) {
    throw new ApiClientError(
      "答题卡题号与学习包不一致，已拒绝创建错位答案。",
      409,
      "answer-omr-item-mismatch",
    );
  }
  return [...packet.items]
    .sort((left, right) => left.orderNo - right.orderNo)
    .map((item) => {
      const mark = marksByItemNo.get(item.orderNo);
      if (!mark) {
        throw new ApiClientError("答题卡识别结果缺少题目。", 409, "answer-omr-item-missing");
      }
      return {
        bindingCode: item.answerBindingCode,
        answer: mark.selectedOptions.length ? mark.selectedOptions.join("") : "[未识别]",
        confidence: mark.confidence,
        requiresCorrection: Boolean(mark.ambiguous) || mark.selectedOptions.length !== 1,
      };
    });
}

function omrMarks(payload: Record<string, unknown>): OmrMarkProjection[] {
  const rawMarks = payload.omrMarks;
  if (!Array.isArray(rawMarks) || rawMarks.length === 0) {
    throw new ApiClientError("答题卡识别结果没有 OMR 标记。", 409, "answer-omr-marks-missing");
  }
  return rawMarks.map((raw) => {
    if (!raw || typeof raw !== "object") {
      throw new ApiClientError("答题卡 OMR 标记格式无效。", 409, "answer-omr-mark-invalid");
    }
    const mark = raw as Partial<OmrMarkProjection>;
    if (
      !Number.isInteger(mark.itemNo) ||
      Number(mark.itemNo) < 1 ||
      typeof mark.confidence !== "number" ||
      mark.confidence < 0 ||
      mark.confidence > 1 ||
      !Array.isArray(mark.selectedOptions) ||
      mark.selectedOptions.some((option) => !/^[A-H]$/.test(option)) ||
      new Set(mark.selectedOptions).size !== mark.selectedOptions.length ||
      (mark.ambiguous !== undefined && typeof mark.ambiguous !== "boolean")
    ) {
      throw new ApiClientError("答题卡 OMR 标记格式无效。", 409, "answer-omr-mark-invalid");
    }
    return {
      itemNo: Number(mark.itemNo),
      selectedOptions: mark.selectedOptions,
      confidence: mark.confidence,
      ...(mark.ambiguous === undefined ? {} : { ambiguous: mark.ambiguous }),
    };
  });
}

function validateAccess(access: PacketAccessProjection): void {
  if (!access.downloadUrl.startsWith("https://")) {
    throw new ApiClientError("学习包下载地址不是 HTTPS。", 503, "unsafe-download-url");
  }
  if (Date.parse(access.expiresAt) <= Date.now()) {
    throw new ApiClientError("学习包下载授权已经过期。", 409, "download-access-expired");
  }
}

function downloadPrivateFile(access: PacketAccessProjection): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: access.downloadUrl,
      timeout: downloadTimeoutMs,
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) {
          const path = result.tempFilePath || result.filePath;
          if (path) resolve(path);
          else reject(new Error("学习包下载完成但没有本地文件。"));
        } else {
          reject(providerError("学习包下载未被对象存储接受。", result.statusCode));
        }
      },
      fail: (error) => {
        reject(new ApiClientError(error.errMsg, 0, "packet-download-network"));
      },
    });
  });
}

function persistDownloadedFile(tempFilePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().saveFile({
      tempFilePath,
      success: (result) => {
        resolve(result.savedFilePath);
      },
      fail: (error) => {
        reject(new Error(error.errMsg || "学习包无法保存到本地。"));
      },
    });
  });
}
