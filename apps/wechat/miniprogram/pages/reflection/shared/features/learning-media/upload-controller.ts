import {
  putPrivateLearningMedia,
  type LearningMediaApi,
  type LearningMediaUploadProjection,
} from "../../api/learning-media";
import { createIdempotencyKey } from "../../../../../platform/command-id";
import {
  assertVoiceProjection,
  availableArtifactVersionId,
  isTerminalLearningMediaState,
  maximumReflectionVoiceTotalBytes,
  validateVoiceCollection,
  type InspectedVoiceFile,
  type ReflectionVoiceSegment,
} from "./contracts";
import { inspectVoiceFile } from "./platform";

export interface LearningMediaUploadGateway {
  startUpload: LearningMediaApi["startUpload"];
  completeUpload: LearningMediaApi["completeUpload"];
  getUpload: LearningMediaApi["getUpload"];
}

export interface LearningMediaUploadDependencies {
  inspect(localPath: string): Promise<InspectedVoiceFile>;
  put: typeof putPrivateLearningMedia;
  createCommandId(prefix: string): Promise<string>;
  wait(milliseconds: number): Promise<void>;
  now(): number;
  pollingIntervalMilliseconds: number;
  maximumPollingAttempts: number;
}

export class LearningMediaOperationCancelled extends Error {
  constructor() {
    super("录音操作已失效。");
    this.name = "LearningMediaOperationCancelled";
  }
}

export class ReflectionVoiceUploadCoordinator {
  private readonly dependencies: LearningMediaUploadDependencies;

  constructor(
    private readonly gateway: LearningMediaUploadGateway,
    dependencies: Partial<LearningMediaUploadDependencies> = {},
  ) {
    this.dependencies = {
      inspect: dependencies.inspect ?? inspectVoiceFile,
      put: dependencies.put ?? putPrivateLearningMedia,
      createCommandId: dependencies.createCommandId ?? createIdempotencyKey,
      wait: dependencies.wait ?? wait,
      now: dependencies.now ?? Date.now,
      pollingIntervalMilliseconds: dependencies.pollingIntervalMilliseconds ?? 1_500,
      maximumPollingAttempts: dependencies.maximumPollingAttempts ?? 60,
    };
  }

  async uploadAll(
    source: readonly ReflectionVoiceSegment[],
    onChange: (segments: ReflectionVoiceSegment[]) => void,
    isCurrent: () => boolean,
  ): Promise<ReflectionVoiceSegment[]> {
    validateVoiceCollection(source);
    let segments = source.map((segment) => ({ ...segment }));
    const inspected = new Map<string, InspectedVoiceFile>();
    let totalBytes = 0;
    for (const segment of segments) {
      ensureCurrent(isCurrent);
      if (segment.state === "available") {
        totalBytes += segment.byteSize;
        continue;
      }
      const file = await this.dependencies.inspect(segment.localPath);
      ensureCurrent(isCurrent);
      inspected.set(segment.segmentId, file);
      totalBytes += file.byteSize;
      segments = replaceSegment(segments, segment.segmentId, {
        ...segment,
        byteSize: file.byteSize,
        contentSha256: file.sha256,
      });
    }
    if (totalBytes > maximumReflectionVoiceTotalBytes) {
      throw new Error("本次复盘的录音总大小不能超过 15MB。");
    }
    validateVoiceCollection(segments);
    emit(segments, onChange, isCurrent);

    for (const current of segments) {
      if (current.state === "available") continue;
      const file = inspected.get(current.segmentId);
      if (!file) throw new Error("录音完整性检查结果缺失。");
      try {
        const updated = await this.uploadOne(
          current,
          file,
          (next) => {
            segments = replaceSegment(segments, current.segmentId, next);
            emit(segments, onChange, isCurrent);
          },
          isCurrent,
        );
        segments = replaceSegment(segments, current.segmentId, updated);
        emit(segments, onChange, isCurrent);
      } catch (error) {
        if (error instanceof LearningMediaOperationCancelled) throw error;
        const failed: ReflectionVoiceSegment = {
          ...segmentById(segments, current.segmentId),
          state: "failed",
          failureMessage: error instanceof Error ? error.message : "录音上传未能完成。",
        };
        delete failed.artifactVersionId;
        segments = replaceSegment(segments, current.segmentId, failed);
        emit(segments, onChange, isCurrent);
      }
    }
    return segments;
  }

  private async uploadOne(
    source: ReflectionVoiceSegment,
    file: InspectedVoiceFile,
    update: (segment: ReflectionVoiceSegment) => void,
    isCurrent: () => boolean,
  ): Promise<ReflectionVoiceSegment> {
    let segment: ReflectionVoiceSegment = {
      ...source,
      byteSize: file.byteSize,
      contentSha256: file.sha256,
    };
    if (isRetryableTerminalState(segment.serverState)) {
      segment = resetUploadAttempt(segment);
      update(segment);
    }
    segment = await this.prepareAttempt(segment, update, isCurrent);
    const resumedExistingSession = Boolean(segment.uploadSessionId);
    let projection = await this.resolveUploadProjection(segment, file);
    ensureCurrent(isCurrent);
    assertVoiceProjection(projection, expected(segment, file));

    if (
      resumedExistingSession &&
      (isRetryableTerminalState(projection.session.state) ||
        (projection.session.state === "awaiting_upload" &&
          authorizationHasExpired(projection, this.dependencies.now())))
    ) {
      segment = resetUploadAttempt(segment);
      update(segment);
      segment = await this.prepareAttempt(segment, update, isCurrent);
      projection = await this.resolveUploadProjection(segment, file);
      ensureCurrent(isCurrent);
      assertVoiceProjection(projection, expected(segment, file));
    }

    segment.uploadSessionId = projection.session.uploadSessionId;
    segment.serverState = projection.session.state;
    update(segment);

    if (projection.session.state === "awaiting_upload") {
      const authorization = projection.authorization;
      if (!authorization) throw new Error("录音上传授权缺失。");
      if (authorizationHasExpired(projection, this.dependencies.now())) {
        throw new Error("录音上传授权已过期，本地草稿已保留，请重试。");
      }
      segment.state = "uploading";
      update(segment);
      await this.dependencies.put(authorization, file.bytes);
      ensureCurrent(isCurrent);
      if (!segment.completeIdempotencyKey) throw new Error("录音上传完成命令缺失。");
      projection = await this.gateway.completeUpload(
        projection.session.uploadSessionId,
        segment.completeIdempotencyKey,
      );
      ensureCurrent(isCurrent);
      assertVoiceProjection(projection, expected(segment, file));
      segment.serverState = projection.session.state;
      update(segment);
    }

    if (projection.session.state === "scanning") {
      segment.state = "scanning";
      update(segment);
      projection = await this.pollUntilTerminal(projection, segment, file, isCurrent);
      segment.serverState = projection.session.state;
    }
    return applyTerminalProjection(segment, projection);
  }

  private async prepareAttempt(
    source: ReflectionVoiceSegment,
    update: (segment: ReflectionVoiceSegment) => void,
    isCurrent: () => boolean,
  ): Promise<ReflectionVoiceSegment> {
    const segment = { ...source };
    if (!segment.startIdempotencyKey) {
      segment.startIdempotencyKey = await this.dependencies.createCommandId("learning-media-start");
      ensureCurrent(isCurrent);
    }
    if (!segment.completeIdempotencyKey) {
      segment.completeIdempotencyKey =
        await this.dependencies.createCommandId("learning-media-complete");
      ensureCurrent(isCurrent);
    }
    segment.state = "authorizing";
    delete segment.failureMessage;
    update(segment);
    return segment;
  }

  private resolveUploadProjection(
    segment: ReflectionVoiceSegment,
    file: InspectedVoiceFile,
  ): Promise<LearningMediaUploadProjection> {
    if (segment.uploadSessionId) return this.gateway.getUpload(segment.uploadSessionId);
    if (!segment.startIdempotencyKey) throw new Error("录音上传命令缺失。");
    return this.gateway.startUpload(
      {
        purpose: "reflection_voice",
        originalFilename: segment.originalFilename,
        mimeType: segment.mimeType,
        byteSize: file.byteSize,
        contentSha256: file.sha256,
        durationMilliseconds: segment.durationMilliseconds,
      },
      segment.startIdempotencyKey,
    );
  }

  private async pollUntilTerminal(
    initial: LearningMediaUploadProjection,
    segment: ReflectionVoiceSegment,
    file: InspectedVoiceFile,
    isCurrent: () => boolean,
  ): Promise<LearningMediaUploadProjection> {
    let projection = initial;
    for (let attempt = 0; attempt < this.dependencies.maximumPollingAttempts; attempt += 1) {
      if (isTerminalLearningMediaState(projection.session.state)) return projection;
      await this.dependencies.wait(this.dependencies.pollingIntervalMilliseconds);
      ensureCurrent(isCurrent);
      projection = await this.gateway.getUpload(projection.session.uploadSessionId);
      ensureCurrent(isCurrent);
      assertVoiceProjection(projection, expected(segment, file));
    }
    throw new Error("录音仍在安全检查，草稿已保留，可稍后继续。");
  }
}

function resetUploadAttempt(source: ReflectionVoiceSegment): ReflectionVoiceSegment {
  const segment = { ...source, state: "local" as const };
  delete segment.uploadSessionId;
  delete segment.serverState;
  delete segment.startIdempotencyKey;
  delete segment.completeIdempotencyKey;
  delete segment.artifactVersionId;
  delete segment.failureMessage;
  return segment;
}

function isRetryableTerminalState(state: ReflectionVoiceSegment["serverState"]): boolean {
  return state === "expired" || state === "quarantined" || state === "failed";
}

function authorizationHasExpired(projection: LearningMediaUploadProjection, now: number): boolean {
  const expiresAt = projection.authorization?.expiresAt;
  if (!expiresAt) return false;
  const expiresAtMilliseconds = Date.parse(expiresAt);
  if (!Number.isFinite(expiresAtMilliseconds)) {
    throw new Error("录音上传授权的到期时间无效。");
  }
  return expiresAtMilliseconds <= now;
}

function applyTerminalProjection(
  segment: ReflectionVoiceSegment,
  projection: LearningMediaUploadProjection,
): ReflectionVoiceSegment {
  const artifactVersionId = availableArtifactVersionId(projection);
  if (artifactVersionId) {
    const available: ReflectionVoiceSegment = {
      ...segment,
      state: "available",
      serverState: "available",
      artifactVersionId,
    };
    delete available.failureMessage;
    return available;
  }
  const failed: ReflectionVoiceSegment = {
    ...segment,
    state: "failed",
    serverState: projection.session.state,
    failureMessage: terminalFailureMessage(
      projection.session.state,
      projection.session.failureCode ?? null,
    ),
  };
  delete failed.artifactVersionId;
  return failed;
}

function terminalFailureMessage(state: string, failureCode: string | null): string {
  switch (state) {
    case "quarantined":
      return "这段录音未通过安全检查，请删除后重新录制。";
    case "expired":
      return "这次上传授权已过期，本地草稿仍已保留。";
    case "failed":
      return failureCode
        ? `录音安全检查失败（${failureCode}），本地草稿仍已保留。`
        : "录音安全检查失败，本地草稿仍已保留。";
    default:
      return "录音尚未成为可用素材，本地草稿仍已保留。";
  }
}

function expected(segment: ReflectionVoiceSegment, file: InspectedVoiceFile) {
  return {
    originalFilename: segment.originalFilename,
    mimeType: segment.mimeType,
    byteSize: file.byteSize,
    durationMilliseconds: segment.durationMilliseconds,
    contentSha256: file.sha256,
  };
}

function replaceSegment(
  segments: readonly ReflectionVoiceSegment[],
  segmentId: string,
  replacement: ReflectionVoiceSegment,
): ReflectionVoiceSegment[] {
  return segments.map((segment) =>
    segment.segmentId === segmentId ? { ...replacement } : segment,
  );
}

function segmentById(
  segments: readonly ReflectionVoiceSegment[],
  segmentId: string,
): ReflectionVoiceSegment {
  const segment = segments.find((candidate) => candidate.segmentId === segmentId);
  if (!segment) throw new Error("录音草稿在上传过程中已变更。");
  return segment;
}

function emit(
  segments: readonly ReflectionVoiceSegment[],
  onChange: (segments: ReflectionVoiceSegment[]) => void,
  isCurrent: () => boolean,
): void {
  ensureCurrent(isCurrent);
  onChange(segments.map((segment) => ({ ...segment })));
}

function ensureCurrent(isCurrent: () => boolean): void {
  if (!isCurrent()) throw new LearningMediaOperationCancelled();
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
