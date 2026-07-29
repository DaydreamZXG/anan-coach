import type { SourceArtifactVersion } from "./external-learning-contracts";
import { ApiClientError } from "./client";
import type { ApiClient } from "./client";
import type { StartUploadCommand, UploadMode, UploadState } from "./generated/types.gen";
import { sha256Hex } from "./sha256";
import type {
  LocalSourceArtifactFile,
  UploadProgress,
} from "../features/external-learning/contracts";

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

export interface UploadedArtifact {
  sessionId: string;
  artifact: SourceArtifactVersion;
}

const uploadTimeoutMs = 120_000;
const multipartPartBytes = 8 * 1024 * 1024;

/** Shared transport for immutable private source artifacts; it performs no OCR. */
export class SourceArtifactApi {
  constructor(private readonly client: ApiClient) {}

  async upload(
    file: LocalSourceArtifactFile,
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
    const startPayload: StartUploadCommand = {
      sourceKind: file.sourceKind,
      originalFilename: safeFileName(file.displayName),
      mimeType: file.mimeType,
      byteSize: file.byteSize,
      contentSha256: fileInfo.digest,
      pageCount: file.sourceKind === "pdf_upload" ? (file.pageCount ?? null) : null,
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
    if (
      !completed.data.artifact ||
      (file.mimeType !== "text/markdown" && !completed.data.session.recognitionEligible)
    ) {
      throw uploadTerminalError(completed.data.session);
    }
    return {
      sessionId: completed.data.session.sessionId,
      artifact: completed.data.artifact,
    };
  }
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
  file: LocalSourceArtifactFile,
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

function requiresMultipart(file: LocalSourceArtifactFile): boolean {
  return file.mimeType === "application/pdf" || file.byteSize > multipartPartBytes;
}

async function uploadAuthorizedFile(
  file: LocalSourceArtifactFile,
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
  file: LocalSourceArtifactFile,
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
  file: LocalSourceArtifactFile,
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
    session.partCount !== expectedPartCount ||
    session.partSize !== (expectedMode === "multipart" ? multipartPartBytes : null)
  ) {
    throw new ApiClientError(
      "服务器返回的上传会话与当前文件不一致，请重新选择。",
      409,
      "upload-session-contract-mismatch",
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
