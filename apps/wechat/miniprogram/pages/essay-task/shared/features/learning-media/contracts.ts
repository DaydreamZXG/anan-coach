import {
  learningMediaContractVersion,
  type LearningMediaArtifact,
  type LearningMediaMimeType,
  type LearningMediaUploadProjection,
  type LearningMediaUploadState,
} from "../../api/learning-media";

export const maximumReflectionVoiceSegments = 3;
export const maximumReflectionVoiceDurationMilliseconds = 60_000;
export const maximumReflectionVoiceTotalBytes = 15 * 1024 * 1024;

export type VoiceSegmentState =
  "local" | "authorizing" | "uploading" | "scanning" | "available" | "failed";

export interface ReflectionVoiceSegment {
  segmentId: string;
  localPath: string;
  originalFilename: string;
  mimeType: LearningMediaMimeType;
  byteSize: number;
  durationMilliseconds: number;
  state: VoiceSegmentState;
  contentSha256?: string;
  startIdempotencyKey?: string;
  completeIdempotencyKey?: string;
  uploadSessionId?: string;
  serverState?: LearningMediaUploadState;
  artifactVersionId?: string;
  failureMessage?: string;
}

export interface ReflectionLearningMediaDraft {
  rawText: string;
  strategyNote: string;
  doubtNote: string;
  tags: string[];
  methodCorrectCalculationErrorQuestionIds: string[];
  voiceSegments: ReflectionVoiceSegment[];
}

export interface InspectedVoiceFile {
  byteSize: number;
  sha256: string;
  bytes: ArrayBuffer;
}

export function validateVoiceCollection(segments: readonly ReflectionVoiceSegment[]): void {
  if (segments.length > maximumReflectionVoiceSegments) {
    throw new Error(`每次复盘最多保留 ${String(maximumReflectionVoiceSegments)} 段录音。`);
  }
  const totalBytes = segments.reduce((sum, segment) => sum + segment.byteSize, 0);
  if (totalBytes > maximumReflectionVoiceTotalBytes) {
    throw new Error("本次复盘的录音总大小不能超过 15MB。");
  }
  for (const segment of segments) validateVoiceSegment(segment);
}

export function validateVoiceSegment(segment: ReflectionVoiceSegment): void {
  if (!segment.segmentId.trim() || !segment.localPath.trim()) {
    throw new Error("录音本地草稿不完整。");
  }
  if (!Number.isInteger(segment.byteSize) || segment.byteSize <= 0) {
    throw new Error("录音文件大小无效。");
  }
  if (
    !Number.isInteger(segment.durationMilliseconds) ||
    segment.durationMilliseconds <= 0 ||
    segment.durationMilliseconds > maximumReflectionVoiceDurationMilliseconds
  ) {
    throw new Error("单段录音时长必须在 60 秒以内。");
  }
  assertMimeAndFilename(segment.mimeType, segment.originalFilename);
  if (segment.state === "available") {
    if (segment.serverState !== "available" || !segment.artifactVersionId?.trim()) {
      throw new Error("录音尚未通过安全检查。");
    }
  } else if (segment.artifactVersionId) {
    throw new Error("这段录音尚未上传完成。");
  }
}

export function assertVoiceProjection(
  projection: LearningMediaUploadProjection,
  expected: Pick<
    ReflectionVoiceSegment,
    "originalFilename" | "mimeType" | "byteSize" | "durationMilliseconds"
  > & { contentSha256: string },
): void {
  const session = projection.session;
  const receivedContractVersion = projection.contractVersion;
  const receivedPurpose: string = session.purpose;
  if (receivedContractVersion !== learningMediaContractVersion) {
    throw new Error("当前小程序需要更新后才能继续上传录音。");
  }
  if (
    receivedPurpose !== "reflection_voice" ||
    session.originalFilename !== expected.originalFilename ||
    session.mimeType !== expected.mimeType ||
    session.byteSize !== expected.byteSize ||
    session.durationMilliseconds !== expected.durationMilliseconds ||
    session.contentSha256 !== expected.contentSha256
  ) {
    throw new Error("录音会话与本地文件不匹配。");
  }
  if (session.state === "awaiting_upload" && !projection.authorization) {
    throw new Error("录音上传授权缺失。");
  }
  if (session.state !== "awaiting_upload" && projection.authorization) {
    throw new Error("非等待上传状态不应返回上传授权。");
  }
  if (session.state === "available") assertAvailableArtifact(projection, expected);
  else if (projection.artifact) {
    throw new Error("未通过安全检查的录音不应返回可用素材。");
  }
}

export function availableArtifactVersionId(
  projection: LearningMediaUploadProjection,
): string | null {
  if (projection.session.state !== "available" || projection.artifact?.state !== "available") {
    return null;
  }
  return projection.artifact.artifactVersionId;
}

export function isTerminalLearningMediaState(state: LearningMediaUploadState): boolean {
  return ["available", "quarantined", "failed", "expired"].includes(state);
}

function assertAvailableArtifact(
  projection: LearningMediaUploadProjection,
  expected: Pick<ReflectionVoiceSegment, "mimeType" | "byteSize" | "durationMilliseconds"> & {
    contentSha256: string;
  },
): asserts projection is LearningMediaUploadProjection & { artifact: LearningMediaArtifact } {
  const artifact = projection.artifact;
  const session = projection.session;
  if (
    !artifact ||
    artifact.state !== "available" ||
    artifact.artifactId !== session.artifactId ||
    artifact.artifactVersionId !== session.artifactVersionId ||
    artifact.mimeType !== expected.mimeType ||
    artifact.byteSize !== expected.byteSize ||
    artifact.durationMilliseconds !== expected.durationMilliseconds ||
    artifact.contentSha256 !== expected.contentSha256
  ) {
    throw new Error("可用录音素材与上传会话不匹配。");
  }
}

function assertMimeAndFilename(mimeType: LearningMediaMimeType, filename: string): void {
  const normalized = filename.trim().toLowerCase();
  const valid =
    (mimeType === "audio/mpeg" && normalized.endsWith(".mp3")) ||
    (mimeType === "audio/aac" && normalized.endsWith(".aac")) ||
    (mimeType === "audio/mp4" && (normalized.endsWith(".m4a") || normalized.endsWith(".mp4")));
  if (!valid || normalized.includes("/") || normalized.includes("\\")) {
    throw new Error("录音文件名与音频格式不匹配。");
  }
}
