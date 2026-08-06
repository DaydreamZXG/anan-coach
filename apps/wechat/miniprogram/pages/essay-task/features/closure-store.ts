import type { EssayRewriteDraft, EssayTransferDraft } from "./closure";
import type { EssayRewritePlanTaskCompletionCommandV2 } from "../../../api/essay-intelligence";
import type {
  EssayRewriteWriterContext,
  EssayTransferWriterContext,
} from "../../../features/essay-task/capabilities";

const schemaVersion = 2 as const;
const storageRoot = `gongkao.essay.closure.v${String(schemaVersion)}`;
const ttlMilliseconds = 7 * 24 * 60 * 60 * 1_000;

export type EssayClosureDraft =
  | {
      kind: "rewrite";
      taskVersionId: string;
      planTaskId: string;
      writerContext: EssayRewriteWriterContext;
      draft: EssayRewriteDraft;
      idempotencyKey?: string;
      atomicCommand?: EssayRewritePlanTaskCompletionCommandV2;
      terminalConflict?: true;
    }
  | {
      kind: "transfer";
      taskVersionId: string;
      planTaskId: string;
      writerContext: EssayTransferWriterContext;
      draft: EssayTransferDraft;
    };

interface StoredClosureDraft {
  schemaVersion: typeof schemaVersion;
  ownerUserId: string;
  expiresAtMs: number;
  payload: EssayClosureDraft;
}

export function storeEssayClosureDraft(ownerUserId: string, value: EssayClosureDraft): void {
  const owner = required(ownerUserId, "userId");
  assertDraft(value);
  const stored: StoredClosureDraft = {
    schemaVersion,
    ownerUserId: owner,
    expiresAtMs: Date.now() + ttlMilliseconds,
    payload: JSON.parse(JSON.stringify(value)) as EssayClosureDraft,
  };
  wx.setStorageSync(key(owner, value.planTaskId), stored);
}

export function loadEssayClosureDraft(
  ownerUserId: string,
  context:
    | {
        kind: "rewrite";
        taskVersionId: string;
        planTaskId: string;
        writerContext: EssayRewriteWriterContext;
      }
    | {
        kind: "transfer";
        taskVersionId: string;
        planTaskId: string;
        writerContext: EssayTransferWriterContext;
      },
): EssayClosureDraft | null {
  const owner = required(ownerUserId, "userId");
  const storageKey = key(owner, context.planTaskId);
  const value: unknown = wx.getStorageSync(storageKey);
  if (!isRecord(value) || !isStored(value, owner, context)) {
    if (value !== "" && value !== undefined && value !== null) wx.removeStorageSync(storageKey);
    return null;
  }
  try {
    assertDraft(value.payload);
    return JSON.parse(JSON.stringify(value.payload)) as EssayClosureDraft;
  } catch {
    wx.removeStorageSync(storageKey);
    return null;
  }
}

export function clearEssayClosureDraft(ownerUserId: string, planTaskId: string): void {
  wx.removeStorageSync(key(ownerUserId, planTaskId));
}

function isStored(
  value: Record<string, unknown>,
  ownerUserId: string,
  context:
    | {
        kind: "rewrite";
        taskVersionId: string;
        planTaskId: string;
        writerContext: EssayRewriteWriterContext;
      }
    | {
        kind: "transfer";
        taskVersionId: string;
        planTaskId: string;
        writerContext: EssayTransferWriterContext;
      },
): value is Record<string, unknown> & StoredClosureDraft {
  if (
    value.schemaVersion !== schemaVersion ||
    value.ownerUserId !== ownerUserId ||
    !Number.isSafeInteger(value.expiresAtMs) ||
    Number(value.expiresAtMs) <= Date.now() ||
    !isRecord(value.payload)
  ) {
    return false;
  }
  return (
    value.payload.kind === context.kind &&
    value.payload.taskVersionId === context.taskVersionId &&
    value.payload.planTaskId === context.planTaskId &&
    sameWriterContext(value.payload.writerContext, context.writerContext)
  );
}

function assertDraft(value: EssayClosureDraft): void {
  required(value.taskVersionId, "taskVersionId");
  required(value.planTaskId, "planTaskId");
  if (value.kind === "rewrite") {
    if (!Array.isArray(value.draft.resolutions)) throw new Error("Invalid essay rewrite draft");
    assertRewriteWriterContext(value.writerContext);
    const hasAtomicCommand = value.atomicCommand !== undefined;
    const hasIdempotencyKey = value.idempotencyKey !== undefined;
    if (hasAtomicCommand !== hasIdempotencyKey) {
      throw new Error("Essay rewrite recovery requires one complete atomic command pair");
    }
    if (value.terminalConflict && !value.atomicCommand) {
      throw new Error("Essay rewrite terminal conflict requires its frozen atomic command");
    }
    if (value.atomicCommand) {
      const command = value.atomicCommand;
      if (
        !value.idempotencyKey ||
        value.idempotencyKey.trim().length < 8 ||
        command.planTaskId !== value.planTaskId ||
        !validElapsedMinutes(command.elapsedMinutes) ||
        command.rewrite.attemptId !== value.writerContext.attemptId ||
        command.rewrite.baseReviewVersionId !== value.writerContext.baseReviewVersionId ||
        command.rewrite.baseAnswerVersionId !== value.writerContext.baseAnswerVersionId ||
        command.rewrite.findingDecisionArtifactVersionId !==
          value.writerContext.findingDecisionArtifactVersionId ||
        command.rewrite.basedOnAnswerVersionId !== value.writerContext.basedOnAnswerVersionId ||
        command.rewrite.basedOnRewriteVersionId !== value.writerContext.basedOnRewriteVersionId
      ) {
        throw new Error("Essay rewrite recovery command does not match its frozen context");
      }
    }
    return;
  }
  assertTransferWriterContext(value.writerContext);
  if (!Array.isArray(value.draft.decisions) || !Array.isArray(value.draft.selectedCriterionCodes)) {
    throw new Error("Invalid essay transfer draft");
  }
  if (
    ![
      "drafting",
      "attempt_saved",
      "answer_saved",
      "review_queued",
      "review_ready",
      "decision_saved",
      "context_invalid",
      "completed",
    ].includes(value.draft.phase)
  ) {
    throw new Error("Invalid essay transfer recovery phase");
  }
  const phase = value.draft.phase;
  if (
    (
      ["attempt_saved", "answer_saved", "review_queued", "review_ready", "decision_saved"] as const
    ).includes(phase as "attempt_saved") &&
    !value.draft.attemptId.trim()
  ) {
    throw new Error("Essay transfer recovery phase requires attemptId");
  }
  if (
    (["answer_saved", "review_queued", "review_ready", "decision_saved"] as const).includes(
      phase as "answer_saved",
    ) &&
    !value.draft.answerVersionId.trim()
  ) {
    throw new Error("Essay transfer recovery phase requires answerVersionId");
  }
  if (phase === "review_queued" && !value.draft.reviewJobId.trim()) {
    throw new Error("Essay transfer review_queued recovery requires reviewJobId");
  }
  if (
    (phase === "review_ready" || phase === "decision_saved") &&
    !value.draft.reviewVersionId.trim()
  ) {
    throw new Error("Essay transfer recovery phase requires reviewVersionId");
  }
  if (phase === "decision_saved" && !value.draft.findingDecisionArtifactVersionId.trim()) {
    throw new Error("Essay transfer decision_saved recovery requires its decision artifact");
  }
  const command = value.draft.atomicCommand;
  const idempotencyKey = value.draft.atomicIdempotencyKey;
  if ((command !== undefined) !== Boolean(idempotencyKey)) {
    throw new Error("Essay transfer recovery requires one complete atomic command pair");
  }
  if (
    command &&
    (idempotencyKey.trim().length < 8 ||
      command.planTaskId !== value.planTaskId ||
      !validElapsedMinutes(command.elapsedMinutes) ||
      command.transfer.sourceRewriteVersionId !== value.writerContext.sourceRewriteVersionId ||
      command.transfer.sourceFindingDecisionArtifactVersionId !==
        value.writerContext.sourceFindingDecisionArtifactVersionId ||
      command.transfer.transferAttemptId !== value.draft.attemptId ||
      command.transfer.transferAnswerVersionId !== value.draft.answerVersionId ||
      command.transfer.transferReviewVersionId !== value.draft.reviewVersionId ||
      command.transfer.transferFindingDecisionArtifactVersionId !==
        value.draft.findingDecisionArtifactVersionId ||
      command.transfer.basedOnTransferVerificationVersionId !==
        value.writerContext.basedOnTransferVerificationVersionId)
  ) {
    throw new Error("Essay transfer recovery command does not match its frozen context");
  }
}

function assertRewriteWriterContext(value: EssayRewriteWriterContext): void {
  required(value.attemptId, "attemptId");
  required(value.baseReviewVersionId, "baseReviewVersionId");
  required(value.baseAnswerVersionId, "baseAnswerVersionId");
  required(value.findingDecisionArtifactVersionId, "findingDecisionArtifactVersionId");
  required(value.basedOnAnswerVersionId, "basedOnAnswerVersionId");
  if (value.basedOnRewriteVersionId !== undefined) {
    required(value.basedOnRewriteVersionId, "basedOnRewriteVersionId");
  }
}

function assertTransferWriterContext(value: EssayTransferWriterContext): void {
  required(value.targetTaskVersionId, "targetTaskVersionId");
  required(value.targetMaterialBundleVersionId, "targetMaterialBundleVersionId");
  required(value.sourceRewriteVersionId, "sourceRewriteVersionId");
  required(value.sourceFindingDecisionArtifactVersionId, "sourceFindingDecisionArtifactVersionId");
  if (value.basedOnTransferVerificationVersionId !== undefined) {
    required(value.basedOnTransferVerificationVersionId, "basedOnTransferVerificationVersionId");
  }
}

function sameWriterContext(
  stored: unknown,
  expected: EssayRewriteWriterContext | EssayTransferWriterContext,
): boolean {
  if (!isRecord(stored) || stored.kind !== expected.kind) return false;
  return (
    Object.entries(expected).every(([key, value]) => stored[key] === value) &&
    Object.entries(stored).every(([key, value]) => expected[key as keyof typeof expected] === value)
  );
}

function validElapsedMinutes(value: number): boolean {
  return Number.isInteger(value) && value > 0 && value <= 1_440;
}

function key(ownerUserId: string, planTaskId: string): string {
  return `${storageRoot}.${encodeURIComponent(required(ownerUserId, "userId"))}.${encodeURIComponent(required(planTaskId, "planTaskId"))}`;
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Essay closure draft requires ${label}`);
  return normalized;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
