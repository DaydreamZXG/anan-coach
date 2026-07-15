import type {
  AnswerIngestionVersion,
  ContentMatchVersion,
  OfflinePacketVersion,
  RecognitionRevision,
  SourceArtifactVersion,
} from "../../miniprogram/api/external-learning-contracts";
import type {
  AnswerIngestionWorkspace,
  AnswerTargetSnapshot,
  ExternalLearningGateway,
  ImportWorkspace,
  PacketListSnapshot,
} from "../../miniprogram/features/external-learning/contracts";

export function sourceArtifact(
  overrides: Partial<SourceArtifactVersion> = {},
): SourceArtifactVersion {
  return {
    contractVersion: "source-artifact@v1",
    artifactVersionId: "artifact-version-1",
    artifactId: "artifact-1",
    ownerUserId: "user-1",
    versionNo: 1,
    sourceKind: "camera_image",
    state: "available",
    objectRef: "tos://private/artifact-version-1",
    mimeType: "image/jpeg",
    contentSha256: "a".repeat(64),
    byteSize: 1_024,
    pageCount: 1,
    supersedesArtifactVersionId: null,
    createdAt: "2026-07-15T01:00:00Z",
    ...overrides,
  };
}

export function recognition(overrides: Partial<RecognitionRevision> = {}): RecognitionRevision {
  return {
    contractVersion: "recognition-revision@v1",
    recognitionRevisionId: "recognition-1",
    artifactVersionId: "artifact-version-1",
    ownerUserId: "user-1",
    revisionNo: 1,
    recognitionKind: "printed_ocr",
    state: "candidate",
    processorPolicyVersion: "ocr-policy@v1",
    processorRoute: "approved-ocr-route",
    inputSha256: "b".repeat(64),
    outputSha256: "c".repeat(64),
    candidatePayload: {},
    confidence: 0.82,
    expiresAt: "2026-07-16T01:00:00Z",
    confirmedAt: null,
    createdAt: "2026-07-15T01:01:00Z",
    ...overrides,
  };
}

export function contentMatch(overrides: Partial<ContentMatchVersion> = {}): ContentMatchVersion {
  return {
    contractVersion: "content-match@v1",
    contentMatchVersionId: "match-version-1",
    contentMatchId: "match-1",
    artifactVersionId: "artifact-version-1",
    recognitionRevisionId: "recognition-1",
    ownerUserId: "user-1",
    versionNo: 1,
    state: "candidate",
    candidates: [
      {
        contentVersionId: "content-version-1",
        matchKind: "exact",
        score: 0.98,
        evidenceRefs: ["fingerprint:1"],
      },
    ],
    confirmedContentVersionId: null,
    matchingPolicyVersion: "matching-policy@v1",
    inputFingerprint: "visual-text-fingerprint-1",
    supersedesContentMatchVersionId: null,
    createdAt: "2026-07-15T01:02:00Z",
    ...overrides,
  };
}

export function importWorkspace(overrides: Partial<ImportWorkspace> = {}): ImportWorkspace {
  return {
    importId: "import-1",
    state: "review",
    artifact: sourceArtifact(),
    recognition: recognition(),
    recognitionSegments: [
      {
        segmentId: "segment-1",
        pageNo: 1,
        ordinal: 1,
        text: "题目文本",
        confidence: 0.82,
        state: "candidate",
        requiresCorrection: false,
      },
    ],
    match: null,
    partialReasons: [],
    failureReason: null,
    processingLabel: null,
    pollAfterMs: null,
    expiresAt: "2026-07-16T01:00:00Z",
    diagnosticEligibility: "blocked_unconfirmed",
    generatedAt: "2026-07-15T01:03:00Z",
    requestId: "request-import-1",
    ...overrides,
  };
}

export function packet(overrides: Partial<OfflinePacketVersion> = {}): OfflinePacketVersion {
  return {
    contractVersion: "offline-packet@v1",
    packetVersionId: "packet-version-1",
    packetId: "packet-1",
    ownerUserId: "user-1",
    versionNo: 1,
    state: "ready",
    renderFormat: "pdf",
    items: [
      {
        packetItemId: "packet-item-1",
        contentVersionId: "content-version-1",
        track: "xingce",
        orderNo: 1,
        answerBindingCode: "answer-binding-1",
      },
    ],
    manifestSha256: "d".repeat(64),
    licenseSnapshotRefs: ["license-snapshot-1"],
    artifactRef: "tos://private/packet-version-1.pdf",
    expiresAt: "2026-07-22T01:00:00Z",
    createdAt: "2026-07-15T01:04:00Z",
    ...overrides,
  };
}

export function packetList(overrides: Partial<PacketListSnapshot> = {}): PacketListSnapshot {
  return {
    packets: [packet()],
    eligibleContent: [
      {
        questionId: "question-1",
        contentVersionId: "content-version-1",
        contentLicenseId: "11111111-1111-4111-8111-111111111111",
        expectedAuthorizationEpoch: 1,
        components: ["question_prompt", "question_options"],
        title: "数量关系判断",
        track: "xingce",
        sourceLabel: "本人私有导入",
        versionLabel: "v1",
        selectedByDefault: true,
      },
    ],
    partialReasons: [],
    generatedAt: "2026-07-15T01:05:00Z",
    requestId: "request-packets-1",
    ...overrides,
  };
}

export function ingestion(overrides: Partial<AnswerIngestionVersion> = {}): AnswerIngestionVersion {
  return {
    contractVersion: "answer-ingestion@v1",
    ingestionVersionId: "ingestion-version-1",
    ingestionId: "ingestion-1",
    ownerUserId: "user-1",
    versionNo: 1,
    packetVersionId: "packet-version-1",
    sourceArtifactVersionIds: [],
    track: "xingce",
    inputMode: "answer_string",
    state: "candidate",
    candidatePayload: {},
    targetWriter: "xingce_learning",
    writerContractVersion: "xingce-learning-writer@v1",
    sourceLineageRefs: ["packet-version-1"],
    confirmedAt: null,
    committedFactRef: null,
    createdAt: "2026-07-15T01:06:00Z",
    ...overrides,
  };
}

export function answerWorkspace(
  overrides: Partial<AnswerIngestionWorkspace> = {},
): AnswerIngestionWorkspace {
  return {
    ingestion: ingestion(),
    items: [
      {
        itemId: "answer-item-1",
        orderNo: 1,
        answerBindingCode: "answer-binding-1",
        answer: "A",
        confidence: 0.74,
        requiresCorrection: true,
      },
    ],
    packetTitle: "行测练习包 1",
    packetItemCount: 1,
    partialReasons: [],
    expiresAt: "2026-07-16T01:00:00Z",
    diagnosticEligibility: "blocked_unconfirmed",
    generatedAt: "2026-07-15T01:07:00Z",
    requestId: "request-answer-1",
    ...overrides,
  };
}

export function answerTargets(overrides: Partial<AnswerTargetSnapshot> = {}): AnswerTargetSnapshot {
  return {
    packets: [
      {
        packetVersionId: "packet-version-1",
        packetTitle: "行测练习包 1",
        track: "xingce",
        itemCount: 1,
        versionLabel: "v1",
        expiresAt: "2026-07-22T01:00:00Z",
      },
    ],
    generatedAt: "2026-07-15T01:08:00Z",
    requestId: "request-targets-1",
    ...overrides,
  };
}

export function gateway(overrides: Partial<ExternalLearningGateway> = {}): ExternalLearningGateway {
  return {
    beginImport: () => Promise.resolve(importWorkspace()),
    getImport: () => Promise.resolve(importWorkspace()),
    reviseRecognition: () => Promise.resolve(importWorkspace()),
    confirmRecognition: () =>
      Promise.resolve(
        importWorkspace({
          recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T01:09:00Z" }),
          match: contentMatch(),
        }),
      ),
    confirmMatch: () =>
      Promise.resolve(
        importWorkspace({
          state: "ready",
          recognition: recognition({ state: "confirmed", confirmedAt: "2026-07-15T01:09:00Z" }),
          match: contentMatch({
            state: "confirmed",
            confirmedContentVersionId: "content-version-1",
          }),
          diagnosticEligibility: "eligible_confirmed",
        }),
      ),
    listPackets: () => Promise.resolve(packetList()),
    createPacket: () => Promise.resolve(packet({ state: "generating", artifactRef: null })),
    retryPacket: () =>
      Promise.resolve(packet({ versionNo: 2, state: "generating", artifactRef: null })),
    renewPacket: () =>
      Promise.resolve(packet({ versionNo: 2, state: "generating", artifactRef: null })),
    getPacketAccess: () =>
      Promise.resolve({
        packetVersionId: "packet-version-1",
        localFilePath: "/tmp/packet-version-1.pdf",
        fileType: "pdf",
        expiresAt: "2026-07-15T01:20:00Z",
      }),
    listAnswerTargets: () => Promise.resolve(answerTargets()),
    getAnswerIngestion: () => Promise.resolve(answerWorkspace()),
    createAnswerString: () => Promise.resolve(answerWorkspace()),
    createAnswerImage: () => Promise.resolve(answerWorkspace()),
    reviseAnswer: () =>
      Promise.resolve(answerWorkspace({ ingestion: ingestion({ versionNo: 2 }) })),
    confirmAnswer: () =>
      Promise.resolve(
        answerWorkspace({
          ingestion: ingestion({
            versionNo: 3,
            state: "committed",
            confirmedAt: "2026-07-15T01:10:00Z",
            committedFactRef: "xingce-fact-1",
          }),
          diagnosticEligibility: "eligible_committed",
        }),
      ),
    ...overrides,
  };
}
