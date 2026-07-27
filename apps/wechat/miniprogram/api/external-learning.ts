import type { OfflinePacketVersion, RecognitionRevision } from "./external-learning-contracts";
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
  RequestImportAiAnswerCommand as ApiRequestImportAIAnswerCommand,
  ConfirmImportAiAnswerCommand as ApiConfirmImportAIAnswerCommand,
  RenewPacketCommand as ApiRenewPacketCommand,
  RetryPacketCommand as ApiRetryPacketCommand,
  StartRecognitionCommand as ApiStartRecognitionCommand,
} from "./generated/types.gen";
import { SourceArtifactApi } from "./source-artifact";
import { persistUserDataFile } from "../platform/persistent-file";
import type { ApiEnvelope } from "../domain/contracts";
import type {
  AnswerIngestionWorkspace,
  AnswerTargetSnapshot,
  AIAnswerConfirmationCommand,
  AIAnswerRequestCommand,
  BeginImportCommand,
  ConfirmAnswerCommand,
  CreateAnswerImageCommand,
  CreateAnswerStringCommand,
  CreatePacketCommand,
  ExternalLearningGateway,
  ImportWorkspace,
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

const downloadTimeoutMs = 120_000;

type RecognitionRevisionProjection = RecognitionRevision;

export class ExternalLearningApi implements ExternalLearningGateway {
  private readonly artifacts: SourceArtifactApi;

  constructor(private readonly client: ApiClient) {
    this.artifacts = new SourceArtifactApi(client);
  }

  async beginImport(
    command: BeginImportCommand,
    idempotencyKey: string,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<ImportWorkspace> {
    const uploaded = await this.artifacts.upload(command.file, idempotencyKey, onProgress);
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

  async requestAIAnswer(command: AIAnswerRequestCommand, idempotencyKey: string): Promise<void> {
    const payload: ApiRequestImportAIAnswerCommand = {
      questionUnitId: requiredId(command.questionUnitId, "questionUnitId"),
      learningTrack: command.learningTrack,
    };
    await this.client.post(`${importPath(command.importId)}/ai-answers`, payload, {
      idempotencyKey,
      timeoutMs: 30_000,
    });
  }

  async confirmAIAnswer(
    command: AIAnswerConfirmationCommand,
    idempotencyKey: string,
  ): Promise<void> {
    const payload: ApiConfirmImportAIAnswerCommand = {
      expectedOutputFingerprint: command.expectedOutputFingerprint,
    };
    await this.client.post(
      `/api/v2/external-learning/ai-answers/${encodeURIComponent(requiredId(command.answerVersionId, "answerVersionId"))}/confirm`,
      payload,
      { idempotencyKey, timeoutMs: 30_000 },
    );
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
    const localFilePath = await persistDownloadedFile(temporaryPath, response.data.fileType);
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
    const uploaded = await this.artifacts.upload(
      command.file,
      derivedCommandKey(idempotencyKey, "upload"),
      onProgress,
    );
    const recognitionPayload: ApiStartRecognitionCommand = {
      artifactVersionId: uploaded.artifact.artifactVersionId,
      recognitionKind: "answer_sheet_omr",
      packetVersionId: command.packetVersionId,
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
        answer: mark.selectedOptions.join(""),
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

function persistDownloadedFile(
  tempFilePath: string,
  fileType: PacketAccessProjection["fileType"],
): Promise<string> {
  return persistUserDataFile({
    tempFilePath,
    filenamePrefix: "anan-learning-packet",
    extension: fileType === "note_app_file" ? "md" : "pdf",
    failureMessage: "学习包无法保存到本地。",
  });
}
