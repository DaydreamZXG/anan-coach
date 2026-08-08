import { afterEach, describe, expect, it, vi } from "vitest";

import {
  putPrivateLearningMedia,
  type LearningMediaUploadProjection,
} from "../miniprogram/pages/reflection/shared/api/learning-media";
import { sha256Hex } from "../miniprogram/api/sha256";
import {
  loadReflectionLearningMediaDraft,
  storeReflectionLearningMediaDraft,
} from "../miniprogram/pages/reflection/shared/features/learning-media/draft-store";
import {
  maximumReflectionVoiceTotalBytes,
  type ReflectionVoiceSegment,
} from "../miniprogram/pages/reflection/shared/features/learning-media/contracts";
import {
  ensureRecordPermission,
  persistRecordedVoice,
} from "../miniprogram/pages/reflection/shared/features/learning-media/platform";
import {
  LearningMediaOperationCancelled,
  ReflectionVoiceUploadCoordinator,
  type LearningMediaUploadGateway,
} from "../miniprogram/pages/reflection/shared/features/learning-media/upload-controller";
import {
  advanceTrajectoryReplay,
  createTrajectoryReplayState,
  ReplayClock,
  seekTrajectoryReplay,
  selectTrajectoryReplaySurface,
  type ReplaySource,
} from "../miniprogram/pages/reflection/features/replay-runtime";
import { strokesVisibleAtReplay } from "../miniprogram/pages/reflection/shared/task-runtime/annotation-layer/strokes";
import {
  activeXingceSession,
  apiSuccess,
  reflectedXingceSession,
  submittedXingceSession,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

const content = new Uint8Array([0x61, 0x62, 0x63]).buffer;
const digest = sha256Hex(content);

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("reflection trajectory replay runtime", () => {
  const source: ReplaySource = {
    elapsedSeconds: 2,
    materialAvailable: true,
    annotations: [
      {
        strokeId: "question-stroke-1",
        tool: "pen",
        width: 0.006,
        points: [{ x: 0.1, y: 0.2, t: 0 }],
        atMs: 500,
        erasedAtMs: 4_500,
      },
      {
        strokeId: "material-stroke-legacy",
        tool: "highlighter",
        width: 0.03,
        points: [{ x: 0.3, y: 0.4, t: 0 }],
        atMs: 1_000,
      },
    ],
    actionEvents: [
      { type: "option_changed", atMs: 3_000 },
      { type: "annotation_erased", atMs: 4_500 },
    ],
  };

  it("builds one frozen replay timeline including legacy material strokes and erase time", () => {
    const initial = createTrajectoryReplayState(source);
    expect(initial).toMatchObject({
      durationMs: 4_500,
      replayMs: 0,
      playing: false,
      surface: "question",
      summaryLabel: "1 次擦除 · 1 次回改",
    });
    expect(initial.surfaceOptions.map((entry) => entry.value)).toEqual(["question", "material"]);
    expect(initial.annotations.map((stroke) => stroke.strokeId)).toEqual(["question-stroke-1"]);
    expect(
      selectTrajectoryReplaySurface(initial, source, "material").annotations.map(
        (stroke) => stroke.strokeId,
      ),
    ).toEqual(["material-stroke-legacy"]);
  });

  it("pauses on seek, clamps the slider, and stops exactly at the frozen duration", () => {
    const initial = { ...createTrajectoryReplayState(source), playing: true };
    expect(seekTrajectoryReplay(initial, 9_999)).toMatchObject({
      replayMs: 4_500,
      playing: false,
    });
    expect(advanceTrajectoryReplay({ ...initial, replayMs: 4_200 }, 480)).toMatchObject({
      replayMs: 4_500,
      playing: false,
    });
  });

  it("owns one timer per page and releases it on stop/unload", () => {
    vi.useFakeTimers();
    const owner = {};
    const clock = new ReplayClock();
    const tick = vi.fn(() => true);
    clock.start(owner, tick, 120);
    vi.advanceTimersByTime(240);
    expect(tick).toHaveBeenCalledTimes(2);
    expect(clock.active(owner)).toBe(true);
    clock.stop(owner);
    vi.advanceTimersByTime(240);
    expect(tick).toHaveBeenCalledTimes(2);
    expect(clock.active(owner)).toBe(false);
  });
});

describe("reflection learning-media upload chain", () => {
  it("computes SHA-256, uses the private required headers, completes and polls to available", async () => {
    const put = vi.fn(() => Promise.resolve());
    const gateway: LearningMediaUploadGateway = {
      startUpload: vi.fn(() => Promise.resolve(projection("awaiting_upload"))),
      completeUpload: vi.fn(() => Promise.resolve(projection("scanning"))),
      getUpload: vi.fn(() => Promise.resolve(projection("available"))),
    };
    const observed: ReflectionVoiceSegment[][] = [];
    const coordinator = new ReflectionVoiceUploadCoordinator(gateway, {
      inspect: () =>
        Promise.resolve({ byteSize: content.byteLength, sha256: digest, bytes: content }),
      put,
      createCommandId: (prefix) => Promise.resolve(`${prefix}-stable-command`),
      wait: () => Promise.resolve(),
      pollingIntervalMilliseconds: 0,
      maximumPollingAttempts: 2,
    });

    const result = await coordinator.uploadAll(
      [localSegment()],
      (segments) => observed.push(segments),
      () => true,
    );

    expect(gateway.startUpload).toHaveBeenCalledWith(
      {
        purpose: "reflection_voice",
        originalFilename: "reflection-1.mp3",
        mimeType: "audio/mpeg",
        byteSize: 3,
        contentSha256: digest,
        durationMilliseconds: 12_000,
      },
      "learning-media-start-stable-command",
    );
    expect(put).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "https://objects.example.test/private/voice",
        requiredHeaders: {
          "Content-Type": "audio/mpeg",
          "x-content-sha256": digest,
        },
      }),
      content,
    );
    expect(gateway.completeUpload).toHaveBeenCalledWith(
      "upload-session-1",
      "learning-media-complete-stable-command",
    );
    expect(result[0]).toMatchObject({
      state: "available",
      serverState: "available",
      artifactVersionId: "artifact-version-1",
      contentSha256: digest,
    });
    expect(observed.at(-1)?.[0]?.state).toBe("available");
  });

  it("never converts quarantined media into an artifact and preserves the local draft", async () => {
    const gateway: LearningMediaUploadGateway = {
      startUpload: vi.fn(() => Promise.resolve(projection("awaiting_upload"))),
      completeUpload: vi.fn(() => Promise.resolve(projection("scanning"))),
      getUpload: vi.fn(() => Promise.resolve(projection("quarantined", "malware_detected"))),
    };
    const coordinator = coordinatorWith(gateway);

    const result = await coordinator.uploadAll(
      [localSegment()],
      () => undefined,
      () => true,
    );

    expect(result[0]).toMatchObject({
      localPath: "/saved/reflection-1.mp3",
      state: "failed",
      serverState: "quarantined",
    });
    expect(result[0]).not.toHaveProperty("artifactVersionId");
    expect(result[0]?.failureMessage).toContain("未通过安全检查");
  });

  it.each(["expired", "quarantined", "failed"] as const)(
    "starts a new upload attempt when the user retries a persisted %s terminal session",
    async (terminalState) => {
      const gateway: LearningMediaUploadGateway = {
        startUpload: vi.fn(() => Promise.resolve(projection("awaiting_upload"))),
        completeUpload: vi.fn(() => Promise.resolve(projection("scanning"))),
        getUpload: vi.fn(() => Promise.resolve(projection("available"))),
      };
      const coordinator = coordinatorWith(gateway);
      const source = localSegment({
        state: "failed",
        serverState: terminalState,
        uploadSessionId: "upload-session-terminal",
        startIdempotencyKey: "old-start-command",
        completeIdempotencyKey: "old-complete-command",
        contentSha256: digest,
        failureMessage: "上次上传已失败。",
      });

      const result = await coordinator.uploadAll(
        [source],
        () => undefined,
        () => true,
      );

      expect(gateway.getUpload).toHaveBeenCalledTimes(1);
      expect(gateway.getUpload).toHaveBeenCalledWith("upload-session-1");
      expect(gateway.startUpload).toHaveBeenCalledWith(
        expect.objectContaining({ contentSha256: digest, durationMilliseconds: 12_000 }),
        "learning-media-start-stable-command",
      );
      expect(gateway.completeUpload).toHaveBeenCalledWith(
        "upload-session-1",
        "learning-media-complete-stable-command",
      );
      expect(result[0]).toMatchObject({
        localPath: "/saved/reflection-1.mp3",
        contentSha256: digest,
        state: "available",
      });
    },
  );

  it("restarts an awaiting-upload session whose persisted authorization has expired", async () => {
    const expiredAuthorization = projection("awaiting_upload");
    if (!expiredAuthorization.authorization) throw new Error("authorization fixture is required");
    expiredAuthorization.session.uploadSessionId = "upload-session-expired-authorization";
    expiredAuthorization.authorization.expiresAt = "2026-07-16T00:10:00Z";
    const gateway: LearningMediaUploadGateway = {
      startUpload: vi.fn(() => Promise.resolve(projection("awaiting_upload"))),
      completeUpload: vi.fn(() => Promise.resolve(projection("scanning"))),
      getUpload: vi
        .fn()
        .mockResolvedValueOnce(expiredAuthorization)
        .mockResolvedValueOnce(projection("available")),
    };
    const coordinator = new ReflectionVoiceUploadCoordinator(gateway, {
      inspect: () =>
        Promise.resolve({ byteSize: content.byteLength, sha256: digest, bytes: content }),
      put: () => Promise.resolve(),
      createCommandId: (prefix) => Promise.resolve(`${prefix}-fresh-command`),
      wait: () => Promise.resolve(),
      now: () => Date.parse("2026-07-16T00:20:00Z"),
      pollingIntervalMilliseconds: 0,
      maximumPollingAttempts: 2,
    });

    const result = await coordinator.uploadAll(
      [
        localSegment({
          state: "failed",
          serverState: "awaiting_upload",
          uploadSessionId: "upload-session-expired-authorization",
          startIdempotencyKey: "old-start-command",
          completeIdempotencyKey: "old-complete-command",
          contentSha256: digest,
        }),
      ],
      () => undefined,
      () => true,
    );

    expect(gateway.getUpload).toHaveBeenNthCalledWith(1, "upload-session-expired-authorization");
    expect(gateway.startUpload).toHaveBeenCalledWith(
      expect.objectContaining({ contentSha256: digest }),
      "learning-media-start-fresh-command",
    );
    expect(gateway.completeUpload).toHaveBeenCalledWith(
      "upload-session-1",
      "learning-media-complete-fresh-command",
    );
    expect(result[0]).toMatchObject({ state: "available", localPath: "/saved/reflection-1.mp3" });
  });

  it("continues polling a scanning session after a prior timeout without re-uploading bytes", async () => {
    const put = vi.fn(() => Promise.resolve());
    const gateway: LearningMediaUploadGateway = {
      startUpload: vi.fn(),
      completeUpload: vi.fn(),
      getUpload: vi
        .fn()
        .mockResolvedValueOnce(projection("scanning"))
        .mockResolvedValueOnce(projection("available")),
    };
    const coordinator = new ReflectionVoiceUploadCoordinator(gateway, {
      inspect: () =>
        Promise.resolve({ byteSize: content.byteLength, sha256: digest, bytes: content }),
      put,
      wait: () => Promise.resolve(),
      pollingIntervalMilliseconds: 0,
      maximumPollingAttempts: 2,
    });

    const result = await coordinator.uploadAll(
      [
        localSegment({
          state: "failed",
          serverState: "scanning",
          uploadSessionId: "upload-session-1",
          startIdempotencyKey: "persisted-start-command",
          completeIdempotencyKey: "persisted-complete-command",
          contentSha256: digest,
          failureMessage: "仍在安全检查。",
        }),
      ],
      () => undefined,
      () => true,
    );

    expect(gateway.startUpload).not.toHaveBeenCalled();
    expect(gateway.completeUpload).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
    expect(gateway.getUpload).toHaveBeenCalledTimes(2);
    expect(result[0]).toMatchObject({ state: "available", uploadSessionId: "upload-session-1" });
  });

  it("replays the same start command after a crash before the upload session was persisted", async () => {
    const createCommandId = vi.fn(() => Promise.reject(new Error("must reuse persisted command")));
    const gateway: LearningMediaUploadGateway = {
      startUpload: vi.fn(() => Promise.resolve(projection("awaiting_upload"))),
      completeUpload: vi.fn(() => Promise.resolve(projection("scanning"))),
      getUpload: vi.fn(() => Promise.resolve(projection("available"))),
    };
    const coordinator = new ReflectionVoiceUploadCoordinator(gateway, {
      inspect: () =>
        Promise.resolve({ byteSize: content.byteLength, sha256: digest, bytes: content }),
      put: () => Promise.resolve(),
      createCommandId,
      wait: () => Promise.resolve(),
      pollingIntervalMilliseconds: 0,
      maximumPollingAttempts: 2,
    });

    const result = await coordinator.uploadAll(
      [
        localSegment({
          state: "authorizing",
          startIdempotencyKey: "persisted-start-command",
          completeIdempotencyKey: "persisted-complete-command",
          contentSha256: digest,
        }),
      ],
      () => undefined,
      () => true,
    );

    expect(createCommandId).not.toHaveBeenCalled();
    expect(gateway.startUpload).toHaveBeenCalledWith(
      expect.objectContaining({ contentSha256: digest }),
      "persisted-start-command",
    );
    expect(gateway.completeUpload).toHaveBeenCalledWith(
      "upload-session-1",
      "persisted-complete-command",
    );
    expect(result[0]).toMatchObject({
      state: "available",
      artifactVersionId: "artifact-version-1",
    });
  });

  it("checks the real combined file size before starting any server upload", async () => {
    const gateway: LearningMediaUploadGateway = {
      startUpload: vi.fn(),
      completeUpload: vi.fn(),
      getUpload: vi.fn(),
    };
    const oversized = Math.floor(maximumReflectionVoiceTotalBytes / 2) + 1;
    const coordinator = new ReflectionVoiceUploadCoordinator(gateway, {
      inspect: () =>
        Promise.resolve({ byteSize: oversized, sha256: digest, bytes: new ArrayBuffer(0) }),
    });

    await expect(
      coordinator.uploadAll(
        [localSegment({ segmentId: "voice-1" }), localSegment({ segmentId: "voice-2" })],
        () => undefined,
        () => true,
      ),
    ).rejects.toThrow("15MB");
    expect(gateway.startUpload).not.toHaveBeenCalled();
  });

  it("drops a late authorization response after its page epoch is invalidated", async () => {
    let release: ((value: LearningMediaUploadProjection) => void) | undefined;
    const gateway: LearningMediaUploadGateway = {
      startUpload: vi.fn(
        () =>
          new Promise<LearningMediaUploadProjection>((resolve) => {
            release = resolve;
          }),
      ),
      completeUpload: vi.fn(),
      getUpload: vi.fn(),
    };
    let current = true;
    const changes: ReflectionVoiceSegment[][] = [];
    const pending = coordinatorWith(gateway).uploadAll(
      [localSegment()],
      (segments) => changes.push(segments),
      () => current,
    );
    await vi.waitFor(() => {
      expect(release).toBeTypeOf("function");
    });
    current = false;
    release?.(projection("awaiting_upload"));

    await expect(pending).rejects.toBeInstanceOf(LearningMediaOperationCancelled);
    expect(gateway.completeUpload).not.toHaveBeenCalled();
    expect(changes.at(-1)?.[0]?.state).toBe("authorizing");
  });
});

describe("reflection learning-media local boundary", () => {
  it("isolates the full draft by account and review", () => {
    const storage = memoryStorage();
    vi.stubGlobal("wx", storage.wx);
    storeReflectionLearningMediaDraft("user-a", "review-a", {
      rawText: "我当时看反了题干。",
      strategyNote: "先圈方向。",
      doubtNote: "",
      tags: ["misread"],
      methodCorrectCalculationErrorQuestionIds: [],
      voiceSegments: [localSegment()],
    });

    expect(loadReflectionLearningMediaDraft("user-a", "review-a")).toMatchObject({
      rawText: "我当时看反了题干。",
      voiceSegments: [{ segmentId: "voice-1" }],
    });
    expect(loadReflectionLearningMediaDraft("user-b", "review-a").voiceSegments).toEqual([]);
    expect(loadReflectionLearningMediaDraft("user-a", "review-b").rawText).toBe("");
  });

  it("migrates an account-scoped draft written before item interpretations were introduced", () => {
    const storage = memoryStorage();
    vi.stubGlobal("wx", storage.wx);
    storage.values.set("gongkao.learning-media.reflection-draft.v1.user-a.review-a", {
      schemaVersion: 1,
      ownerUserId: "user-a",
      reviewId: "review-a",
      updatedAt: "2026-07-16T00:00:00Z",
      draft: {
        rawText: "旧草稿仍应恢复。",
        strategyNote: "",
        doubtNote: "",
        tags: ["misread"],
        voiceSegments: [],
      },
    });

    expect(loadReflectionLearningMediaDraft("user-a", "review-a")).toMatchObject({
      rawText: "旧草稿仍应恢复。",
      methodCorrectCalculationErrorQuestionIds: [],
    });
  });

  it("renders an explicit denied permission state instead of starting a fake recording", async () => {
    const authorize = vi.fn();
    vi.stubGlobal("wx", {
      getSetting: () => Promise.resolve({ authSetting: { "scope.record": false } }),
      authorize,
    });

    await expect(ensureRecordPermission()).resolves.toBe("denied");
    expect(authorize).not.toHaveBeenCalled();
  });

  it("persists recorded voice drafts with FileSystemManager.copyFile", async () => {
    const copyFile = vi.fn((options: WechatMiniprogram.CopyFileOption) => {
      options.success?.({ errCode: 0, errMsg: "copyFile:ok" });
    });
    vi.stubGlobal("wx", {
      env: { USER_DATA_PATH: "/user-data" },
      getFileSystemManager: () => ({ copyFile }),
    });

    const localPath = await persistRecordedVoice("/tmp/reflection.mp3");

    expect(copyFile).toHaveBeenCalledOnce();
    const copyOptions = copyFile.mock.calls[0]?.[0];
    expect(copyOptions?.srcPath).toBe("/tmp/reflection.mp3");
    expect(copyOptions?.destPath).toMatch(
      /^\/user-data\/anan-reflection-voice-\d+-[a-z0-9]+\.mp3$/u,
    );
    expect(localPath).toBe(copyOptions?.destPath);
  });

  it("PUTs exact bytes with only the server-required headers", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption<ArrayBuffer>) => {
      expect(options.header).toEqual({
        "Content-Type": "audio/mpeg",
        "x-content-sha256": digest,
      });
      expect(options.header).not.toHaveProperty("Authorization");
      expect(options.data).toBe(content);
      options.success?.(requestResult(200));
      return {} as WechatMiniprogram.RequestTask;
    });
    vi.stubGlobal("wx", { request });

    await putPrivateLearningMedia(
      {
        objectRef: "tos://bucket/private/voice",
        url: "https://objects.example.test/private/voice",
        requiredHeaders: {
          "Content-Type": "audio/mpeg",
          "x-content-sha256": digest,
        },
        expiresAt: "2099-07-16T00:00:00Z",
      },
      content,
    );

    expect(request).toHaveBeenCalledOnce();
  });
});

describe("reflection page voice binding", () => {
  it("submits only an available artifact version with the reflection command", async () => {
    const runtime = await createReflectionPage(availableSegment());

    await runtime.page.submitReflection.call(runtime.page);

    const reflectionRequest = runtime.requests.find((request) =>
      request.url.includes("/reflection"),
    );
    expect(reflectionRequest?.data).toMatchObject({
      rawText: "我当时忽略了否定词。",
      tags: ["misread"],
      voiceArtifactVersionIds: ["artifact-version-1"],
    });
  });

  it("blocks reflection submission while a local voice remains unready", async () => {
    const runtime = await createReflectionPage(localSegment());

    await runtime.page.submitReflection.call(runtime.page);

    expect(runtime.requests.some((request) => request.url.includes("/reflection"))).toBe(false);
    expect(runtime.showToast).toHaveBeenCalledWith({
      title: "有段录音还没准备好，先上传或删掉它",
      icon: "none",
    });
  });

  it("drives replay playback, seek, surfaces and unload through the real page lifecycle", async () => {
    const runtime = await createReflectionPage(availableSegment(), trajectorySession());
    vi.useFakeTimers();

    runtime.page.openReplay.call(runtime.page);
    expect(runtime.page.data).toMatchObject({
      replayOpen: true,
      replayMs: 0,
      replayDurationMs: 4_500,
      replaySurface: "question",
      replayContentHeight: 1_600,
    });
    expect(
      (runtime.page.data.replayAnnotations as Array<{ strokeId: string }>).map(
        (stroke) => stroke.strokeId,
      ),
    ).toEqual(["question-stroke-1"]);

    runtime.page.toggleReplayPlayback.call(runtime.page);
    await vi.advanceTimersByTimeAsync(240);
    expect(runtime.page.data.replayMs).toBe(960);
    expect(vi.getTimerCount()).toBe(1);
    runtime.page.toggleReplayPlayback.call(runtime.page);
    await vi.advanceTimersByTimeAsync(240);
    expect(runtime.page.data.replayMs).toBe(960);
    expect(vi.getTimerCount()).toBe(0);

    runtime.page.toggleReplayPlayback.call(runtime.page);
    runtime.page.seekReplay.call(runtime.page, {
      detail: { value: 3_000 },
    } as WechatMiniprogram.CustomEvent<{ value: number }>);
    expect(runtime.page.data).toMatchObject({ replayMs: 3_000, replayPlaying: false });
    expect(runtime.page.data.replayCurrentEvent).toContain("改答");
    expect(vi.getTimerCount()).toBe(0);
    const questionStrokes = runtime.page.data.replayAnnotations as Parameters<
      typeof strokesVisibleAtReplay
    >[0];
    expect(strokesVisibleAtReplay(questionStrokes, 4_499)).toHaveLength(1);
    expect(strokesVisibleAtReplay(questionStrokes, 4_500)).toHaveLength(0);

    runtime.page.selectReplaySurface.call(runtime.page, {
      currentTarget: { dataset: { surface: "material" } },
    } as unknown as WechatMiniprogram.BaseEvent);
    expect(runtime.page.data.replaySurface).toBe("material");
    expect(
      (runtime.page.data.replayAnnotations as Array<{ strokeId: string }>).map(
        (stroke) => stroke.strokeId,
      ),
    ).toEqual(["material-stroke-1"]);

    runtime.page.toggleReplayPlayback.call(runtime.page);
    expect(vi.getTimerCount()).toBe(1);
    runtime.page.onUnload.call(runtime.page);
    const writesAfterUnload = runtime.setData.mock.calls.length;
    const replayAfterUnload = runtime.page.data.replayMs;
    await vi.advanceTimersByTimeAsync(480);
    expect(runtime.setData).toHaveBeenCalledTimes(writesAfterUnload);
    expect(runtime.page.data.replayMs).toBe(replayAfterUnload);
    expect(vi.getTimerCount()).toBe(0);
  });
});

function coordinatorWith(gateway: LearningMediaUploadGateway): ReflectionVoiceUploadCoordinator {
  return new ReflectionVoiceUploadCoordinator(gateway, {
    inspect: () =>
      Promise.resolve({ byteSize: content.byteLength, sha256: digest, bytes: content }),
    put: () => Promise.resolve(),
    createCommandId: (prefix) => Promise.resolve(`${prefix}-stable-command`),
    wait: () => Promise.resolve(),
    pollingIntervalMilliseconds: 0,
    maximumPollingAttempts: 2,
  });
}

function localSegment(overrides: Partial<ReflectionVoiceSegment> = {}): ReflectionVoiceSegment {
  return {
    segmentId: "voice-1",
    localPath: "/saved/reflection-1.mp3",
    originalFilename: "reflection-1.mp3",
    mimeType: "audio/mpeg",
    byteSize: 3,
    durationMilliseconds: 12_000,
    state: "local",
    ...overrides,
  };
}

function availableSegment(): ReflectionVoiceSegment {
  return localSegment({
    state: "available",
    serverState: "available",
    contentSha256: digest,
    uploadSessionId: "upload-session-1",
    artifactVersionId: "artifact-version-1",
  });
}

function projection(
  state: LearningMediaUploadProjection["session"]["state"],
  failureCode: string | null = null,
): LearningMediaUploadProjection {
  const available = state === "available";
  return {
    contractVersion: "learning-media-upload@v1",
    session: {
      uploadSessionId: "upload-session-1",
      artifactId: "artifact-1",
      artifactVersionId: "artifact-version-1",
      state,
      revision: state === "awaiting_upload" ? 1 : 2,
      purpose: "reflection_voice",
      originalFilename: "reflection-1.mp3",
      mimeType: "audio/mpeg",
      byteSize: 3,
      contentSha256: digest,
      durationMilliseconds: 12_000,
      failureCode,
      expiresAt: "2099-07-16T00:00:00Z",
      updatedAt: "2026-07-16T00:00:00Z",
    },
    authorization:
      state === "awaiting_upload"
        ? {
            objectRef: "tos://bucket/private/voice",
            url: "https://objects.example.test/private/voice",
            requiredHeaders: {
              "Content-Type": "audio/mpeg",
              "x-content-sha256": digest,
            },
            expiresAt: "2099-07-16T00:00:00Z",
          }
        : null,
    artifact: available
      ? {
          artifactId: "artifact-1",
          artifactVersionId: "artifact-version-1",
          versionNo: 1,
          purpose: "reflection_voice",
          state: "available",
          mimeType: "audio/mpeg",
          byteSize: 3,
          contentSha256: digest,
          durationMilliseconds: 12_000,
          retentionPolicyVersion: "learning-media-retention@v1",
          createdAt: "2026-07-16T00:00:00Z",
        }
      : null,
    replayed: false,
  };
}

interface ReflectionPageDefinition {
  data: Record<string, unknown>;
  onLoad(this: ReflectionTestPage, options: Record<string, string>): void;
  onUnload(this: ReflectionTestPage): void;
  submitReflection(this: ReflectionTestPage): Promise<void>;
  openReplay(this: ReflectionTestPage): void;
  closeReplay(this: ReflectionTestPage): void;
  toggleReplayPlayback(this: ReflectionTestPage): void;
  seekReplay(
    this: ReflectionTestPage,
    event: WechatMiniprogram.CustomEvent<{ value: number }>,
  ): void;
  selectReplaySurface(this: ReflectionTestPage, event: WechatMiniprogram.BaseEvent): void;
}

interface ReflectionTestPage extends ReflectionPageDefinition {
  setData(patch: Record<string, unknown>): void;
  createSelectorQuery(): {
    select(selector: string): unknown;
    fields(options: Record<string, boolean>): unknown;
    exec(callback: (results: Array<{ height: number; scrollHeight: number }>) => void): void;
  };
}

async function createReflectionPage(
  segment: ReflectionVoiceSegment,
  session = submittedXingceSession(),
) {
  let captured: ReflectionPageDefinition | undefined;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const storage = memoryStorage();
  const globalData = { accessToken: "token-a", userId: "user-a" };
  const showToast = vi.fn(() => Promise.resolve());
  vi.stubGlobal("Page", (definition: ReflectionPageDefinition) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("wx", {
    ...storage.wx,
    showToast,
    request: (request: WechatMiniprogram.RequestOption) => {
      requests.push(request);
      if (request.method === "GET") {
        request.success?.(apiSuccess(session));
        return;
      }
      if (request.url.includes("/reflection")) {
        request.success?.(apiSuccess(reflectedXingceSession()));
      }
    },
    getRandomValues: () => Promise.resolve({ randomValues: new Uint8Array(16).buffer }),
    getFileSystemManager: () => ({
      unlink: (options: WechatMiniprogram.UnlinkOption) => {
        options.success?.({ errMsg: "unlink:ok", errCode: 0 });
      },
    }),
    redirectTo: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
    navigateBack: vi.fn(() => Promise.resolve()),
    nextTick: (callback: () => void) => {
      callback();
    },
  });
  storeReflectionLearningMediaDraft("user-a", xingceIds.review, {
    rawText: "我当时忽略了否定词。",
    strategyNote: "",
    doubtNote: "",
    tags: ["misread"],
    methodCorrectCalculationErrorQuestionIds: [],
    voiceSegments: [segment],
  });
  await import("../miniprogram/pages/reflection/index");
  if (!captured) throw new Error("reflection page was not registered");
  const setData = vi.fn(function (this: ReflectionTestPage, patch: Record<string, unknown>) {
    Object.assign(this.data, patch);
  });
  const query = {
    select: vi.fn(() => query),
    fields: vi.fn(() => query),
    exec: vi.fn((callback: (results: Array<{ height: number; scrollHeight: number }>) => void) => {
      callback([{ height: 640, scrollHeight: 1_600 }]);
    }),
  };
  const page: ReflectionTestPage = {
    ...captured,
    data: JSON.parse(JSON.stringify(captured.data)) as Record<string, unknown>,
    setData,
    createSelectorQuery: () => query,
  };
  page.onLoad.call(page, { sessionId: xingceIds.session, reviewId: xingceIds.review });
  await vi.waitFor(() => {
    expect(page.data.status).toBe("ready");
  });
  return { page, requests, showToast, setData, query };
}

function trajectorySession() {
  const session = submittedXingceSession();
  session.bundle = activeXingceSession({ sharedMaterial: true }).bundle;
  const firstAnswer = session.answers[0];
  if (!firstAnswer) throw new Error("trajectory answer fixture is required");
  const firstResult = session.result?.items[0];
  if (!firstResult) throw new Error("trajectory result fixture is required");
  const timing = firstResult.timingEvidence;
  if (!timing) throw new Error("trajectory timing fixture is required");
  firstResult.timingEvidence = {
    status: timing.status,
    elapsedSeconds: 2,
    advisorySeconds: timing.advisorySeconds,
  };
  session.answers[0] = {
    ...firstAnswer,
    elapsedSeconds: 2,
    annotations: [
      {
        strokeId: "question-stroke-1",
        tool: "pen",
        width: 0.006,
        points: [{ x: 0.1, y: 0.2, t: 0 }],
        atMs: 500,
        erasedAtMs: 4_500,
      },
      {
        strokeId: "material-stroke-1",
        surface: "material",
        tool: "highlighter",
        width: 0.03,
        points: [{ x: 0.3, y: 0.4, t: 0 }],
        atMs: 1_000,
      },
    ],
    actionEvents: [
      { type: "option_changed", optionId: "C", atMs: 3_000 },
      { type: "annotation_erased", atMs: 4_500 },
    ],
  };
  return session;
}

function memoryStorage() {
  const values = new Map<string, unknown>();
  return {
    values,
    wx: {
      getStorageSync: (key: string) => values.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => {
        values.set(key, value);
      },
      removeStorageSync: (key: string) => {
        values.delete(key);
      },
      getStorageInfoSync: () => ({
        keys: [...values.keys()],
        currentSize: 0,
        limitSize: 10_240,
      }),
    },
  };
}

function requestResult(
  statusCode: number,
): WechatMiniprogram.RequestSuccessCallbackResult<ArrayBuffer> {
  return {
    data: new ArrayBuffer(0),
    statusCode,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}
