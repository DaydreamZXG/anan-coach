import { afterEach, describe, expect, it, vi } from "vitest";
import { ExternalLearningApi } from "../../miniprogram/api/external-learning";
import type { ApiClient } from "../../miniprogram/api/client";
import type { ApiEnvelope } from "../../miniprogram/domain/contracts";
import type {
  LocalAnswerImageFile,
  LocalImportFile,
} from "../../miniprogram/features/external-learning/contracts";
import {
  answerWorkspace,
  contentMatch,
  importWorkspace,
  ingestion,
  packet,
  packetList,
  recognition,
  sourceArtifact,
} from "./fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("external learning API gateway", () => {
  it("hashes, authorizes, PUT uploads, completes, and starts the import workspace", async () => {
    const digest = zeroDigest;
    const file = image();
    const post = vi
      .fn()
      .mockResolvedValueOnce(envelope(startUpload(file, digest, "single_put")))
      .mockResolvedValueOnce(envelope(completeUpload(file, digest)))
      .mockResolvedValueOnce(
        envelope(
          withoutRequestId(
            importWorkspace({
              match: null,
              recognition: recognition(),
              diagnosticEligibility: "blocked_unconfirmed",
            }),
          ),
        ),
      );
    const rawRequest = vi.fn((options: WechatMiniprogram.RequestOption<ArrayBuffer>) => {
      expect(options).toMatchObject({
        url: "https://tos.example.test/private/object",
        method: "PUT",
        header: { "Content-Type": "image/jpeg", "x-tos-sha256": digest },
      });
      expect(options.header).not.toHaveProperty("Authorization");
      options.success?.(requestSuccess(new ArrayBuffer(0), 200));
      return {} as WechatMiniprogram.RequestTask;
    });
    stubFileRuntime(file, digest, rawRequest);
    const progress: number[] = [];
    const api = new ExternalLearningApi(client({ post }));

    const result = await api.beginImport(
      { file, learningTrack: "xingce" },
      "external-import-command",
      (value) => progress.push(value.percent),
    );

    expect(post).toHaveBeenNthCalledWith(
      1,
      "/api/v2/external-learning/uploads",
      expect.objectContaining({
        sourceKind: "camera_image",
        originalFilename: "question.jpg",
        contentSha256: digest,
      }),
      { idempotencyKey: "external-import-command", timeoutMs: 30_000 },
    );
    expect(post).toHaveBeenNthCalledWith(
      3,
      "/api/v2/external-learning/imports/upload-session-1/process",
      { learningTrack: "xingce" },
      { idempotencyKey: "external-import-command:process", timeoutMs: 60_000 },
    );
    expect(rawRequest).toHaveBeenCalledOnce();
    expect(progress.at(-1)).toBe(100);
    expect(result).toMatchObject({
      importId: "import-1",
      requestId: "request-test",
      diagnosticEligibility: "blocked_unconfirmed",
    });
  });

  it("hashes every multipart slice and PUT uploads the server-authorized parts", async () => {
    const digest = zeroDigest;
    const file = pdf();
    const post = vi
      .fn()
      .mockResolvedValueOnce(envelope(startUpload(file, digest, "multipart")))
      .mockResolvedValueOnce(envelope(completeUpload(file, digest)))
      .mockResolvedValueOnce(envelope(withoutRequestId(importWorkspace({ match: null }))));
    const rawRequest = vi.fn((options: WechatMiniprogram.RequestOption<ArrayBuffer>) => {
      expect(options).toMatchObject({
        url: "https://tos.example.test/private/object?partNumber=1",
        method: "PUT",
      });
      options.success?.(requestSuccess(new ArrayBuffer(0), 200));
      return {} as WechatMiniprogram.RequestTask;
    });
    const readFile = stubFileRuntime(file, digest, rawRequest);
    const observed: number[] = [];

    await new ExternalLearningApi(client({ post })).beginImport(
      { file, learningTrack: "xingce" },
      "external-import-multipart",
      (value) => observed.push(value.percent),
    );

    expect(post).toHaveBeenNthCalledWith(
      1,
      "/api/v2/external-learning/uploads",
      expect.objectContaining({
        sourceKind: "pdf_upload",
        pageCount: null,
        partSha256s: [digest],
      }),
      { idempotencyKey: "external-import-multipart", timeoutMs: 30_000 },
    );
    expect(readFile).toHaveBeenCalledTimes(2);
    expect(rawRequest).toHaveBeenCalledOnce();
    expect(observed.at(-1)).toBe(100);
  });

  it("keeps OCR confirmation and match confirmation on separate composite endpoints", async () => {
    const post = vi
      .fn()
      .mockResolvedValueOnce(
        envelope(
          withoutRequestId(
            importWorkspace({
              recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T01:00:00Z" }),
              match: contentMatch(),
            }),
          ),
        ),
      )
      .mockResolvedValueOnce(
        envelope(
          withoutRequestId(
            importWorkspace({
              state: "ready",
              recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T01:00:00Z" }),
              match: contentMatch({
                state: "confirmed",
                confirmedContentVersionId: "content-version-1",
              }),
              diagnosticEligibility: "eligible_confirmed",
            }),
          ),
        ),
      );
    const api = new ExternalLearningApi(client({ post }));

    const afterRecognition = await api.confirmRecognition(
      {
        importId: "import-1",
        recognitionRevisionId: "recognition-1",
        expectedRevisionNo: 1,
        learningTrack: "xingce",
      },
      "recognition-command",
    );
    expect(afterRecognition.match?.state).toBe("candidate");
    await api.confirmMatch(
      {
        importId: "import-1",
        contentMatchVersionId: "match-version-1",
        expectedVersionNo: 1,
        confirmedContentVersionId: "content-version-1",
      },
      "match-command",
    );

    expect(post).toHaveBeenNthCalledWith(
      1,
      "/api/v2/external-learning/imports/import-1/recognitions/recognition-1/confirm",
      { expectedRevisionNo: 1, learningTrack: "xingce" },
      { idempotencyKey: "recognition-command", timeoutMs: 60_000 },
    );
    expect(post).toHaveBeenNthCalledWith(
      2,
      "/api/v2/external-learning/imports/import-1/matches/match-version-1/confirm",
      { expectedVersionNo: 1, confirmedContentVersionId: "content-version-1" },
      { idempotencyKey: "match-command", timeoutMs: 30_000 },
    );
  });

  it("loads packet history and eligible content from one authoritative snapshot", async () => {
    const get = vi
      .fn()
      .mockResolvedValueOnce(
        envelope(
          withoutRequestId(packetList({ partialReasons: ["一个授权快照正在重新校验"] })),
          "request-packets",
        ),
      );
    const result = await new ExternalLearningApi(client({ get })).listPackets();

    expect(result).toMatchObject({
      packets: [{ packetVersionId: "packet-version-1" }],
      eligibleContent: [{ contentVersionId: "content-version-1" }],
      partialReasons: ["一个授权快照正在重新校验"],
      requestId: "request-packets",
    });
    expect(get).toHaveBeenCalledOnce();
    expect(get).toHaveBeenCalledWith("/api/v2/external-learning/packets");
  });

  it("downloads a short-lived private packet grant and persists the local file", async () => {
    const post = vi.fn(() =>
      Promise.resolve(
        envelope({
          packetVersionId: "packet-version-1",
          downloadUrl: "https://tos.example.test/private/packet.pdf",
          fileType: "pdf",
          expiresAt: "2099-07-15T01:00:00Z",
        }),
      ),
    );
    const downloadFile = vi.fn((options: WechatMiniprogram.DownloadFileOption) => {
      options.success?.({
        tempFilePath: "/tmp/downloaded.pdf",
        filePath: "",
        statusCode: 200,
        profile: {} as WechatMiniprogram.RequestProfile,
        errMsg: "downloadFile:ok",
      });
      return {} as WechatMiniprogram.DownloadTask;
    });
    const saveFile = vi.fn((options: WechatMiniprogram.SaveFileOption) => {
      options.success?.({ savedFilePath: "/saved/packet.pdf", errMsg: "saveFile:ok" });
    });
    vi.stubGlobal("wx", {
      downloadFile,
      getFileSystemManager: () => ({ saveFile }),
    });

    const grant = await new ExternalLearningApi(client({ post })).getPacketAccess(
      "packet-version-1",
    );

    expect(downloadFile).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "https://tos.example.test/private/packet.pdf",
      }),
    );
    expect(downloadFile.mock.calls[0]?.[0]).not.toHaveProperty("header");
    expect(saveFile).toHaveBeenCalledWith(
      expect.objectContaining({ tempFilePath: "/tmp/downloaded.pdf" }),
    );
    expect(grant.localFilePath).toBe("/saved/packet.pdf");
  });

  it("sends answer binding codes for corrections and atomically confirms the ingestion", async () => {
    const put = vi.fn(() =>
      Promise.resolve(
        envelope(withoutRequestId(answerWorkspace({ ingestion: ingestion({ versionNo: 2 }) }))),
      ),
    );
    const post = vi.fn(() =>
      Promise.resolve(
        envelope(
          withoutRequestId(
            answerWorkspace({
              ingestion: ingestion({
                versionNo: 3,
                state: "committed",
                committedFactRef: "fact-batch-1",
              }),
              diagnosticEligibility: "eligible_committed",
            }),
          ),
        ),
      ),
    );
    const api = new ExternalLearningApi(client({ post, put }));

    await api.reviseAnswer(
      {
        ingestionId: "ingestion-1",
        expectedVersionNo: 1,
        answers: [{ itemId: "item-1", answerBindingCode: "binding-1", answer: "B" }],
      },
      "answer-revision-command",
    );
    const committed = await api.confirmAnswer(
      { ingestionId: "ingestion-1", expectedVersionNo: 2 },
      "answer-confirm-command",
    );

    expect(put).toHaveBeenCalledWith(
      "/api/v2/external-learning/answer-ingestions/ingestion-1/candidate",
      { expectedVersionNo: 1, entries: [{ bindingCode: "binding-1", answer: "B" }] },
      { idempotencyKey: "answer-revision-command", timeoutMs: 30_000 },
    );
    expect(post).toHaveBeenCalledWith(
      "/api/v2/external-learning/answer-ingestions/ingestion-1/confirm",
      { expectedVersionNo: 2, confirmation: "user_verified" },
      { idempotencyKey: "answer-confirm-command", timeoutMs: 30_000 },
    );
    expect(committed.diagnosticEligibility).toBe("eligible_committed");
  });

  it("uploads an answer sheet, starts OMR, and maps marks onto packet binding codes", async () => {
    const digest = zeroDigest;
    const file = answerImage();
    const candidate = answerWorkspace({
      ingestion: ingestion({
        inputMode: "answer_sheet_omr",
        sourceArtifactVersionIds: ["artifact-version-1"],
      }),
    });
    const post = vi
      .fn()
      .mockResolvedValueOnce(envelope(startUpload(file, digest, "single_put")))
      .mockResolvedValueOnce(envelope(completeUpload(file, digest)))
      .mockResolvedValueOnce(
        envelope(
          recognition({
            recognitionKind: "answer_sheet_omr",
            candidatePayload: {
              omrMarks: [{ itemNo: 1, selectedOptions: ["B"], confidence: 0.93, ambiguous: false }],
            },
          }),
        ),
      )
      .mockResolvedValueOnce(envelope(withoutRequestId(candidate)));
    const get = vi.fn().mockResolvedValueOnce(envelope(packet()));
    const rawRequest = vi.fn((options: WechatMiniprogram.RequestOption<ArrayBuffer>) => {
      options.success?.(requestSuccess(new ArrayBuffer(0), 200));
      return {} as WechatMiniprogram.RequestTask;
    });
    stubFileRuntime(file, digest, rawRequest);

    const result = await new ExternalLearningApi(client({ get, post })).createAnswerImage(
      { packetVersionId: "packet-version-1", track: "xingce", file },
      "answer-image-command",
      vi.fn(),
    );

    expect(post).toHaveBeenNthCalledWith(
      3,
      "/api/v2/external-learning/recognitions",
      { artifactVersionId: "artifact-version-1", recognitionKind: "answer_sheet_omr" },
      { idempotencyKey: "answer-image-command:recognition", timeoutMs: 60_000 },
    );
    expect(post).toHaveBeenNthCalledWith(
      4,
      "/api/v2/external-learning/answer-ingestions",
      {
        packetVersionId: "packet-version-1",
        track: "xingce",
        inputMode: "answer_sheet_omr",
        sourceArtifactVersionIds: ["artifact-version-1"],
        recognitionRevisionIds: ["recognition-1"],
        entries: [
          {
            bindingCode: "answer-binding-1",
            answer: "B",
            confidence: 0.93,
            requiresCorrection: false,
          },
        ],
      },
      { idempotencyKey: "answer-image-command:candidate", timeoutMs: 60_000 },
    );
    expect(result.ingestion.inputMode).toBe("answer_sheet_omr");
  });
});

function client(overrides: Record<string, unknown>): ApiClient {
  return {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    ...overrides,
  } as unknown as ApiClient;
}

function envelope<T>(
  data: T,
  requestId = "request-test",
  generatedAt = "2026-07-15T01:00:00Z",
): ApiEnvelope<T> {
  return { data, requestId, generatedAt };
}

const zeroDigest = "5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef";

function withoutRequestId<T extends { requestId: string }>(value: T): Omit<T, "requestId"> {
  const { requestId, ...data } = value;
  if (!requestId) throw new Error("fixture request metadata missing");
  return data;
}

function image(): LocalImportFile {
  return {
    localPath: "/tmp/question.jpg",
    displayName: "question.jpg",
    sourceKind: "camera_image",
    mimeType: "image/jpeg",
    byteSize: 1_024,
    pageCount: null,
  };
}

function pdf(): LocalImportFile {
  return {
    localPath: "/tmp/questions.pdf",
    displayName: "questions.pdf",
    sourceKind: "pdf_upload",
    mimeType: "application/pdf",
    byteSize: 1_024,
    pageCount: null,
  };
}

function answerImage(): LocalAnswerImageFile {
  return {
    localPath: "/tmp/answers.jpg",
    displayName: "answers.jpg",
    sourceKind: "answer_sheet",
    mimeType: "image/jpeg",
    byteSize: 1_024,
  };
}

function startUpload(
  file: LocalImportFile | LocalAnswerImageFile,
  digest: string,
  mode: "single_put" | "multipart",
) {
  return {
    session: uploadSession(file, "awaiting_upload", digest, mode),
    authorization: {
      objectRef: "tos://private/object",
      mode,
      expiresAt: "2099-07-15T01:00:00Z",
      parts: [
        {
          objectRef: "tos://private/object",
          partNumber: 1,
          byteSize: file.byteSize,
          contentSha256: digest,
          url: `https://tos.example.test/private/object${mode === "multipart" ? "?partNumber=1" : ""}`,
          requiredHeaders: { "Content-Type": file.mimeType, "x-tos-sha256": digest },
          expiresAt: "2099-07-15T01:00:00Z",
        },
      ],
      providerUploadId: mode === "multipart" ? "provider-upload-1" : null,
      partSize: mode === "multipart" ? 8 * 1024 * 1024 : null,
    },
    replayed: false,
  };
}

function completeUpload(file: LocalImportFile | LocalAnswerImageFile, digest: string) {
  const mode = file.mimeType === "application/pdf" ? "multipart" : "single_put";
  return {
    session: {
      ...uploadSession(file, "available", digest, mode),
      recognitionEligible: true,
      uploadedPartCount: 1,
    },
    artifact: sourceArtifact({
      sourceKind: file.sourceKind,
      mimeType: file.mimeType,
      byteSize: file.byteSize,
      contentSha256: digest,
      pageCount: "pageCount" in file ? file.pageCount : null,
    }),
    replayed: false,
  };
}

function uploadSession(
  file: LocalImportFile | LocalAnswerImageFile,
  state: string,
  digest: string,
  mode: "single_put" | "multipart",
) {
  return {
    sessionId: "upload-session-1",
    artifactVersionId: "artifact-version-1",
    revision: 1,
    state,
    sourceKind: file.sourceKind,
    originalFilename: file.displayName,
    mimeType: file.mimeType,
    byteSize: file.byteSize,
    contentSha256: digest,
    pageCount: "pageCount" in file ? file.pageCount : null,
    expiresAt: "2099-07-15T01:00:00Z",
    recognitionEligible: false,
    failureCode: null,
    uploadMode: mode,
    partSize: mode === "multipart" ? 8 * 1024 * 1024 : null,
    partCount: 1,
    uploadedPartCount: 0,
  };
}

function stubFileRuntime(
  file: LocalImportFile | LocalAnswerImageFile,
  digest: string,
  request: ReturnType<typeof vi.fn>,
): ReturnType<typeof vi.fn> {
  const readFile = vi.fn((options: WechatMiniprogram.ReadFileOption) => {
    options.success?.({
      data: new ArrayBuffer(options.length ?? file.byteSize),
      errMsg: "readFile:ok",
    });
  });
  vi.stubGlobal("wx", {
    getFileSystemManager: () => ({
      getFileInfo: (options: WechatMiniprogram.GetFileInfoOption) =>
        options.success?.({ size: file.byteSize, digest, errMsg: "getFileInfo:ok" }),
      readFile,
    }),
    request,
  });
  return readFile;
}

function requestSuccess(
  data: ArrayBuffer,
  statusCode: number,
): WechatMiniprogram.RequestSuccessCallbackResult<ArrayBuffer> {
  return {
    data,
    statusCode,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}
