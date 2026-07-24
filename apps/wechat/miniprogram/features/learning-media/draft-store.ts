import {
  validateVoiceCollection,
  type ReflectionLearningMediaDraft,
  type ReflectionVoiceSegment,
  type VoiceSegmentState,
} from "./contracts";

const schemaVersion = 1 as const;
const storageRoot = `gongkao.learning-media.reflection-draft.v${String(schemaVersion)}`;

interface StoredReflectionDraft {
  schemaVersion: typeof schemaVersion;
  ownerUserId: string;
  reviewId: string;
  updatedAt: string;
  draft: ReflectionLearningMediaDraft;
}

export function emptyReflectionLearningMediaDraft(): ReflectionLearningMediaDraft {
  return {
    rawText: "",
    strategyNote: "",
    doubtNote: "",
    tags: [],
    methodCorrectCalculationErrorQuestionIds: [],
    voiceSegments: [],
  };
}

export function loadReflectionLearningMediaDraft(
  ownerUserId: string,
  reviewId: string,
): ReflectionLearningMediaDraft {
  const storageKey = key(ownerUserId, reviewId);
  const stored: unknown = wx.getStorageSync(storageKey);
  if (stored === "" || stored === undefined || stored === null) {
    return emptyReflectionLearningMediaDraft();
  }
  if (!isStoredDraft(stored, ownerUserId.trim(), reviewId.trim())) {
    wx.removeStorageSync(storageKey);
    return emptyReflectionLearningMediaDraft();
  }
  return copyDraft(stored.draft);
}

export function storeReflectionLearningMediaDraft(
  ownerUserId: string,
  reviewId: string,
  draft: ReflectionLearningMediaDraft,
): void {
  const owner = required(ownerUserId, "userId");
  const target = required(reviewId, "reviewId");
  validateVoiceCollection(draft.voiceSegments);
  const stored: StoredReflectionDraft = {
    schemaVersion,
    ownerUserId: owner,
    reviewId: target,
    updatedAt: new Date().toISOString(),
    draft: copyDraft(draft),
  };
  wx.setStorageSync(key(owner, target), stored);
}

export function clearReflectionLearningMediaDraft(ownerUserId: string, reviewId: string): void {
  wx.removeStorageSync(key(ownerUserId, reviewId));
}

function isStoredDraft(
  value: unknown,
  ownerUserId: string,
  reviewId: string,
): value is StoredReflectionDraft {
  if (
    !isRecord(value) ||
    value.schemaVersion !== schemaVersion ||
    value.ownerUserId !== ownerUserId ||
    value.reviewId !== reviewId ||
    typeof value.updatedAt !== "string" ||
    Number.isNaN(Date.parse(value.updatedAt)) ||
    !isReflectionDraft(value.draft)
  ) {
    return false;
  }
  try {
    validateVoiceCollection(value.draft.voiceSegments);
    return true;
  } catch {
    return false;
  }
}

function isReflectionDraft(value: unknown): value is ReflectionLearningMediaDraft {
  return (
    isRecord(value) &&
    typeof value.rawText === "string" &&
    typeof value.strategyNote === "string" &&
    typeof value.doubtNote === "string" &&
    Array.isArray(value.tags) &&
    value.tags.every((tag) => typeof tag === "string") &&
    new Set(value.tags).size === value.tags.length &&
    (value.methodCorrectCalculationErrorQuestionIds === undefined ||
      (Array.isArray(value.methodCorrectCalculationErrorQuestionIds) &&
        value.methodCorrectCalculationErrorQuestionIds.length <= 3 &&
        value.methodCorrectCalculationErrorQuestionIds.every(
          (questionId) => typeof questionId === "string" && questionId.trim().length > 0,
        ) &&
        new Set(value.methodCorrectCalculationErrorQuestionIds).size ===
          value.methodCorrectCalculationErrorQuestionIds.length)) &&
    Array.isArray(value.voiceSegments) &&
    value.voiceSegments.every(isVoiceSegment)
  );
}

function isVoiceSegment(value: unknown): value is ReflectionVoiceSegment {
  if (!isRecord(value)) return false;
  const states: VoiceSegmentState[] = [
    "local",
    "authorizing",
    "uploading",
    "scanning",
    "available",
    "failed",
  ];
  return (
    typeof value.segmentId === "string" &&
    typeof value.localPath === "string" &&
    typeof value.originalFilename === "string" &&
    ["audio/mpeg", "audio/mp4", "audio/aac"].includes(String(value.mimeType)) &&
    typeof value.byteSize === "number" &&
    typeof value.durationMilliseconds === "number" &&
    states.includes(value.state as VoiceSegmentState) &&
    optionalString(value.contentSha256) &&
    optionalString(value.startIdempotencyKey) &&
    optionalString(value.completeIdempotencyKey) &&
    optionalString(value.uploadSessionId) &&
    optionalString(value.serverState) &&
    optionalString(value.artifactVersionId) &&
    optionalString(value.failureMessage)
  );
}

function copyDraft(draft: ReflectionLearningMediaDraft): ReflectionLearningMediaDraft {
  const legacyDraft = draft as unknown as {
    methodCorrectCalculationErrorQuestionIds?: string[];
  };
  return {
    rawText: draft.rawText,
    strategyNote: draft.strategyNote,
    doubtNote: draft.doubtNote,
    tags: [...draft.tags],
    // Drafts written before the Stage 3 item-interpretation field existed can still be
    // present on a shared device. They remain account/review scoped and migrate in memory.
    methodCorrectCalculationErrorQuestionIds: [
      ...(legacyDraft.methodCorrectCalculationErrorQuestionIds ?? []),
    ],
    voiceSegments: draft.voiceSegments.map((segment) => ({ ...segment })),
  };
}

function key(ownerUserId: string, reviewId: string): string {
  return `${storageRoot}.${encodeURIComponent(required(ownerUserId, "userId"))}.${encodeURIComponent(required(reviewId, "reviewId"))}`;
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Reflection learning-media draft requires ${label}`);
  return normalized;
}

function optionalString(value: unknown): boolean {
  return value === undefined || typeof value === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
