import { authedV2 } from "../../api/endpoints";
import type {
  CompleteUploadProjection,
  SourceArtifactVersion,
  StartUploadCommand,
  StartUploadProjection,
  UploadAuthorizationProjection,
  UploadPartAuthorizationProjection,
} from "../../api/generated/types.gen";
import type { LocalSourceImageFile } from "./source-image";

/** 上传进度回调：percent 0-100 */
export interface UploadProgress {
  sentBytes: number;
  totalBytes: number;
  percent: number;
}

const UPLOAD_TIMEOUT_MS = 120_000;
const MULTIPART_PART_BYTES = 8 * 1024 * 1024;

/**
 * 成绩截图上传：start 拿对象存储授权 → 逐分片 PUT → complete 产出 artifact。
 * 与旧版同一契约：v2 envelope + 幂等键，图片单分片，超 8MB 走 multipart。
 */
export async function uploadSourceImage(
  file: LocalSourceImageFile,
  idempotencyKey: string,
  onProgress: (progress: UploadProgress) => void,
): Promise<SourceArtifactVersion> {
  onProgress(progress(file.byteSize, 0));
  const fileInfo = await getFileInfo(file.localPath);
  if (fileInfo.size !== file.byteSize) {
    throw new Error("本地文件在上传前已经发生变化，请重新选择。");
  }
  const partSha256s = await uploadPartDigests(file);
  const command: StartUploadCommand = {
    sourceKind: file.sourceKind,
    originalFilename: safeFileName(file.displayName),
    mimeType: file.mimeType,
    byteSize: file.byteSize,
    contentSha256: fileInfo.digest,
    pageCount: null,
    ...(partSha256s.length ? { partSha256s } : {}),
  };
  const started = await authedV2<StartUploadProjection>({
    method: "POST",
    path: "/api/v2/external-learning/uploads",
    body: command,
    idempotencyKey,
  });
  assertUploadReplay(started, file, fileInfo.digest, partSha256s.length);
  if (started.session.state === "awaiting_upload") {
    await uploadAuthorizedFile(
      file,
      started.authorization,
      partSha256s.length ? partSha256s : [fileInfo.digest],
      onProgress,
    );
  }
  const completed = await authedV2<CompleteUploadProjection>({
    method: "POST",
    path: `/api/v2/external-learning/uploads/${encodeURIComponent(started.session.sessionId)}/complete`,
    body: {},
    idempotencyKey: `${idempotencyKey}:complete`,
  });
  onProgress(progress(file.byteSize, file.byteSize));
  if (!completed.artifact || !completed.session.recognitionEligible) {
    throw new Error(uploadTerminalMessage(completed.session.state, completed.session.failureCode));
  }
  return completed.artifact;
}

// ═══════ 内部实现 ═══════

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

function requiresMultipart(file: LocalSourceImageFile): boolean {
  return file.byteSize > MULTIPART_PART_BYTES;
}

async function uploadPartDigests(file: LocalSourceImageFile): Promise<string[]> {
  if (!requiresMultipart(file)) return [];
  const partCount = Math.ceil(file.byteSize / MULTIPART_PART_BYTES);
  const digests: string[] = [];
  for (let index = 0; index < partCount; index += 1) {
    const position = index * MULTIPART_PART_BYTES;
    const length = Math.min(MULTIPART_PART_BYTES, file.byteSize - position);
    const bytes = await readFile(file.localPath, position, length);
    if (bytes.byteLength !== length) throw new Error("无法完整读取本地文件分片。");
    digests.push(sha256Hex(bytes));
  }
  return digests;
}

async function uploadAuthorizedFile(
  file: LocalSourceImageFile,
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
      throw new Error("本地文件在获得上传授权后发生了变化，请重新选择。");
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
      timeout: UPLOAD_TIMEOUT_MS,
      header: part.requiredHeaders,
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) resolve();
        else reject(new Error(`文件分片上传未被对象存储接受（${result.statusCode}）。`));
      },
      fail: (error) => {
        reject(new Error(error.errMsg || "文件分片上传网络失败。"));
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
  file: LocalSourceImageFile,
  authorization: UploadAuthorizationProjection,
  expectedDigests: readonly string[],
): UploadPartAuthorizationProjection[] {
  if (Date.parse(authorization.expiresAt) <= Date.now()) {
    throw new Error("文件上传授权已经过期。");
  }
  const parts = [...authorization.parts].sort((left, right) => left.partNumber - right.partNumber);
  const expectedMode = requiresMultipart(file) ? "multipart" : "single_put";
  if (authorization.mode !== expectedMode || parts.length !== expectedDigests.length) {
    throw new Error("对象存储上传授权与本地文件分片计划不一致。");
  }
  let totalBytes = 0;
  for (const [index, part] of parts.entries()) {
    if (
      part.partNumber !== index + 1 ||
      part.contentSha256 !== expectedDigests[index] ||
      !part.url.startsWith("https://") ||
      Date.parse(part.expiresAt) <= Date.now() ||
      !Object.keys(part.requiredHeaders).length
    ) {
      throw new Error("对象存储返回了不可验证的分片授权。");
    }
    totalBytes += part.byteSize;
  }
  if (totalBytes !== file.byteSize) {
    throw new Error("对象存储分片字节数与本地文件不一致。");
  }
  return parts;
}

function assertUploadReplay(
  started: StartUploadProjection,
  file: LocalSourceImageFile,
  digest: string,
  expectedPartCount: number,
): void {
  const session = started.session;
  if (
    session.sourceKind !== file.sourceKind ||
    session.mimeType !== file.mimeType ||
    session.byteSize !== file.byteSize ||
    session.contentSha256 !== digest ||
    session.partCount !== (expectedPartCount || 1)
  ) {
    throw new Error("原上传命令与当前文件不一致，已拒绝复用幂等结果。");
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
    throw new Error("文件名不符合上传安全规则。");
  }
  return normalized;
}

function uploadTerminalMessage(state: string, failureCode: string | null): string {
  const stateMessage: Record<string, string> = {
    quarantined: "文件未通过安全扫描，已被隔离。",
    expired: "文件上传会话已经过期。",
    failed: "文件安全处理失败。",
    scan_failed: "文件安全扫描未完成，不能进入识别。",
  };
  return stateMessage[state] ?? `文件尚未达到可识别状态（${failureCode ?? state}）。`;
}

// ═══════ sha256（小程序无 crypto.subtle，纯 TS 实现） ═══════

const roundConstants: readonly number[] = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
] as const;

const initialHash: readonly number[] = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
] as const;

function sha256Hex(input: ArrayBuffer): string {
  const source = new Uint8Array(input);
  const paddedLength = Math.ceil((source.length + 9) / 64) * 64;
  const padded = new Uint8Array(paddedLength);
  padded.set(source);
  padded[source.length] = 0x80;
  const bitLength = source.length * 8;
  const dataView = new DataView(padded.buffer);
  dataView.setUint32(paddedLength - 8, Math.floor(bitLength / 0x1_0000_0000), false);
  dataView.setUint32(paddedLength - 4, bitLength >>> 0, false);

  const hash = [...initialHash];
  const words = new Uint32Array(64);
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      words[index] = dataView.getUint32(offset + index * 4, false);
    }
    for (let index = 16; index < 64; index += 1) {
      const left = words[index - 15] ?? 0;
      const right = words[index - 2] ?? 0;
      const sigma0 = rotateRight(left, 7) ^ rotateRight(left, 18) ^ (left >>> 3);
      const sigma1 = rotateRight(right, 17) ^ rotateRight(right, 19) ^ (right >>> 10);
      words[index] = ((words[index - 16] ?? 0) + sigma0 + (words[index - 7] ?? 0) + sigma1) >>> 0;
    }

    let a = hash[0] ?? 0;
    let b = hash[1] ?? 0;
    let c = hash[2] ?? 0;
    let d = hash[3] ?? 0;
    let e = hash[4] ?? 0;
    let f = hash[5] ?? 0;
    let g = hash[6] ?? 0;
    let h = hash[7] ?? 0;
    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choose = (e & f) ^ (~e & g);
      const temporary1 =
        (h + sum1 + choose + (roundConstants[index] ?? 0) + (words[index] ?? 0)) >>> 0;
      const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temporary2 = (sum0 + majority) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temporary1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temporary1 + temporary2) >>> 0;
    }
    hash[0] = ((hash[0] ?? 0) + a) >>> 0;
    hash[1] = ((hash[1] ?? 0) + b) >>> 0;
    hash[2] = ((hash[2] ?? 0) + c) >>> 0;
    hash[3] = ((hash[3] ?? 0) + d) >>> 0;
    hash[4] = ((hash[4] ?? 0) + e) >>> 0;
    hash[5] = ((hash[5] ?? 0) + f) >>> 0;
    hash[6] = ((hash[6] ?? 0) + g) >>> 0;
    hash[7] = ((hash[7] ?? 0) + h) >>> 0;
  }

  return hash.map((value) => value.toString(16).padStart(8, "0")).join("");
}

function rotateRight(value: number, count: number): number {
  return (value >>> count) | (value << (32 - count));
}
