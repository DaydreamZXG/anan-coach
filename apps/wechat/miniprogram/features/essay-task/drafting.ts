import type {
  EssayDraftingCompletionCommandV2,
  EssayReviewIntentView,
  EssayReviewQueueCommand,
  EssayTaskWorkspace,
  ReflectionPayload,
} from "../../api/essay-intelligence";

export const sourceContextValues = ["in_program", "paper", "external"] as const;
export const sourceContextLabels = ["小程序内作答", "纸质作答", "其他平台作答"];
export const timedStatusValues = ["timed", "untimed", "unknown"] as const;
export const timedStatusLabels = ["严格计时", "不限时练习", "用时不确定"];
export const answerSourceTypeValues = ["typed", "ocr_confirmed"] as const;
export const answerSourceTypeLabels = ["键盘录入", "OCR 文本已人工核对"];
export const reflectionCauseValues = [
  "missed_material",
  "wrong_abstraction",
  "weak_structure",
  "unsupported_claim",
  "expression",
  "time_management",
  "format",
] as const satisfies ReflectionPayload["causeCodes"];
export const reflectionCauseLabels: Record<ReflectionCauseCode, string> = {
  missed_material: "漏看材料",
  wrong_abstraction: "概括层级不准",
  weak_structure: "结构组织薄弱",
  unsupported_claim: "观点缺少材料支撑",
  expression: "表达不够规范",
  time_management: "时间分配失衡",
  format: "格式或题型要求",
};

export type ReflectionCauseCode = ReflectionPayload["causeCodes"][number];

export interface EssayDraftingDraft {
  answerText: string;
  userThought: string;
  pointFindingText: string;
  outlineText: string;
  sourceContext: (typeof sourceContextValues)[number];
  sourceContextIndex: number;
  timedStatus: (typeof timedStatusValues)[number];
  timedStatusIndex: number;
  elapsedMinutesText: string;
  sourceType: (typeof answerSourceTypeValues)[number];
  sourceTypeIndex: number;
  sourceArtifactVersionId: string;
  recognitionRevisionId: string;
  reflectionCauseCodes: ReflectionCauseCode[];
  whatIWillChange: string;
  verificationPlan: string;
}

export interface DraftingCompletionReceipt {
  attemptId: string;
  answerVersionId: string;
  reflectionArtifactVersionId: string;
}

export interface PendingEssayDraftingCompletion {
  phase: "completion_pending";
  taskVersionId: string;
  planTaskId: string;
  draft: EssayDraftingDraft;
  completion: {
    idempotencyKey: string;
    command: EssayDraftingCompletionCommandV2;
  };
}

export interface PendingEssayReviewRecovery {
  phase: "review_pending";
  taskVersionId: string;
  planTaskId: string;
  receipt: DraftingCompletionReceipt;
  intent: Pick<EssayReviewIntentView, "intentId" | "intentVersionId">;
  ensure?: {
    idempotencyKey: string;
    jobId?: string;
    reviewVersionId?: string;
  };
}

export type PendingEssayDraftingSubmission =
  PendingEssayDraftingCompletion | PendingEssayReviewRecovery;

export class EssayDraftingError extends Error {}

export function initialEssayDraftingDraft(workspace?: EssayTaskWorkspace): EssayDraftingDraft {
  const currentReflection = workspace?.currentArtifacts.reflection;
  const reflection =
    currentReflection?.payload.schemaType === "reflection" ? currentReflection.payload : undefined;
  return {
    answerText: "",
    userThought: reflection?.whatIThought ?? "",
    pointFindingText: "",
    outlineText: "",
    sourceContext: "in_program",
    sourceContextIndex: 0,
    timedStatus: "timed",
    timedStatusIndex: 0,
    elapsedMinutesText: "",
    sourceType: "typed",
    sourceTypeIndex: 0,
    sourceArtifactVersionId: "",
    recognitionRevisionId: "",
    reflectionCauseCodes: reflection ? [...reflection.causeCodes] : [],
    whatIWillChange: reflection?.whatIWillChange ?? "",
    verificationPlan: reflection?.verificationPlan ?? "",
  };
}

export function buildDraftingCompletionCommand(
  workspace: EssayTaskWorkspace,
  draft: EssayDraftingDraft,
  pageElapsedSeconds: number,
  context: {
    planTaskId: string;
    basedOnExecutionVersionId?: string;
    voiceArtifactVersionIds?: string[];
  },
): EssayDraftingCompletionCommandV2 {
  const rubricVersionId = workspace.rubricAvailability.rubric?.rubricVersionId;
  if (!rubricVersionId || workspace.rubricAvailability.status !== "published") {
    throw new EssayDraftingError("提交答案需要绑定已发布的评分规程版本。");
  }
  const planTaskId = required(context.planTaskId, "本次作答缺少今日计划任务版本。");
  const existingAttemptId = workspace.progression.attemptId ?? undefined;
  const existingAnswerVersionId = workspace.progression.answerVersionId ?? undefined;
  if (existingAnswerVersionId && !existingAttemptId) {
    throw new EssayDraftingError("工作区答案缺少所属作答记录，不能确认该版本。");
  }

  const elapsedSeconds = resolveElapsedSeconds(draft, pageElapsedSeconds);
  const elapsedMinutes =
    elapsedSeconds === undefined
      ? Math.max(1, Math.ceil(Math.max(1, pageElapsedSeconds) / 60))
      : Math.ceil(elapsedSeconds / 60);
  const userThought = required(draft.userThought, "请记录你作答时的真实思路。");
  const whatIWillChange = required(draft.whatIWillChange, "请写下下一次作答要具体改变什么。");
  const verificationPlan = required(draft.verificationPlan, "请写下你会怎样验证这次改法是否有效。");
  if (draft.reflectionCauseCodes.length === 0) {
    throw new EssayDraftingError("请至少选择一个本次作答的主要问题来源。");
  }
  if (new Set(draft.reflectionCauseCodes).size !== draft.reflectionCauseCodes.length) {
    throw new EssayDraftingError("复盘问题来源不能重复。");
  }

  const voiceArtifactVersionIds = context.voiceArtifactVersionIds ?? [];
  if (
    voiceArtifactVersionIds.length > 3 ||
    new Set(voiceArtifactVersionIds).size !== voiceArtifactVersionIds.length ||
    voiceArtifactVersionIds.some((value) => !value.trim())
  ) {
    throw new EssayDraftingError("语音复盘必须选择最多 3 个不重复的已就绪录音。");
  }

  const attempt: EssayDraftingCompletionCommandV2["attempt"] = existingAttemptId
    ? { mode: "existing", attemptId: existingAttemptId }
    : {
        mode: "create",
        sourceContext: draft.sourceContext,
        timedStatus: draft.timedStatus,
        ...(elapsedSeconds === undefined ? {} : { elapsedSeconds }),
      };
  const pointFindingText = optional(draft.pointFindingText);
  const outlineText = optional(draft.outlineText);
  const answer: EssayDraftingCompletionCommandV2["answer"] = existingAnswerVersionId
    ? { mode: "existing", answerVersionId: existingAnswerVersionId }
    : {
        mode: "create",
        sourceType: draft.sourceType,
        answerText: answerText(draft.answerText),
        ...(draft.sourceType === "ocr_confirmed"
          ? ocrBinding(draft.sourceArtifactVersionId, draft.recognitionRevisionId)
          : {}),
        thinking: {
          userThought,
          ...(pointFindingText ? { pointFindingText } : {}),
          ...(outlineText ? { outlineText } : {}),
        },
      };
  const currentReflection = workspace.currentArtifacts.reflection;
  const basedOnReflectionArtifactVersionId =
    currentReflection?.artifactType === "reflection" &&
    currentReflection.answerVersionId === existingAnswerVersionId
      ? currentReflection.artifactVersionId
      : undefined;

  return {
    planTaskId,
    taskVersionId: workspace.task.taskVersionId,
    materialBundleVersionId: workspace.material.materialBundleVersionId,
    rubricVersionId,
    elapsedMinutes,
    attempt,
    answer,
    reflection: {
      schemaType: "reflection",
      causeCodes: [...draft.reflectionCauseCodes],
      whatIThought: userThought,
      whatIWillChange,
      verificationPlan,
      voiceArtifactVersionIds: [...voiceArtifactVersionIds],
    },
    ...(context.basedOnExecutionVersionId
      ? { basedOnExecutionVersionId: context.basedOnExecutionVersionId }
      : {}),
    ...(basedOnReflectionArtifactVersionId ? { basedOnReflectionArtifactVersionId } : {}),
  };
}

export function buildReviewQueueCommand(
  attemptId: string,
  answerVersionId: string,
): EssayReviewQueueCommand {
  return {
    attemptId,
    answerVersionId,
    reviewScope: "grounded_full",
  };
}

/** Drops answer and reflection bodies as soon as the server confirms the atomic completion. */
export function promoteToReviewRecovery(
  pending: PendingEssayDraftingCompletion,
  receipt: DraftingCompletionReceipt,
  intent: Pick<EssayReviewIntentView, "intentId" | "intentVersionId">,
): PendingEssayReviewRecovery {
  return {
    phase: "review_pending",
    taskVersionId: pending.taskVersionId,
    planTaskId: pending.planTaskId,
    receipt,
    intent: {
      intentId: intent.intentId,
      intentVersionId: intent.intentVersionId,
    },
  };
}

export function reflectionCauseOptions(codes: ReflectionCauseCode[]) {
  return reflectionCauseValues.map((code) => ({
    code,
    label: reflectionCauseLabels[code],
    checked: codes.includes(code),
  }));
}

export function isPendingEssayDraftingSubmission(
  value: unknown,
  context: { taskVersionId: string; planTaskId: string },
): value is PendingEssayDraftingSubmission {
  if (!isRecord(value)) return false;
  if (value.taskVersionId !== context.taskVersionId || value.planTaskId !== context.planTaskId) {
    return false;
  }
  if (value.phase === "completion_pending") {
    if (!validDraft(value.draft)) return false;
    const completion = value.completion;
    return (
      isRecord(completion) &&
      typeof completion.idempotencyKey === "string" &&
      completion.idempotencyKey.length >= 8 &&
      validCompletionCommand(completion.command, context)
    );
  }
  if (
    value.phase !== "review_pending" ||
    !validReceipt(value.receipt) ||
    !isRecord(value.intent) ||
    typeof value.intent.intentId !== "string" ||
    value.intent.intentId.length === 0 ||
    typeof value.intent.intentVersionId !== "string" ||
    value.intent.intentVersionId.length === 0
  ) {
    return false;
  }
  if (value.ensure !== undefined) {
    const ensure = value.ensure;
    if (
      !isRecord(ensure) ||
      typeof ensure.idempotencyKey !== "string" ||
      ensure.idempotencyKey.length < 8 ||
      !optionalString(ensure.jobId) ||
      !optionalString(ensure.reviewVersionId)
    ) {
      return false;
    }
  }
  return true;
}

function resolveElapsedSeconds(
  draft: EssayDraftingDraft,
  pageElapsedSeconds: number,
): number | undefined {
  if (draft.timedStatus !== "timed") return undefined;
  const manualMinutes = draft.elapsedMinutesText.trim();
  if (!manualMinutes) return Math.max(1, Math.floor(pageElapsedSeconds));
  const minutes = Number(manualMinutes);
  if (!Number.isInteger(minutes) || minutes <= 0 || minutes > 1_440) {
    throw new EssayDraftingError("严格计时需填写 1–1440 的整数分钟，或留空使用本页计时。");
  }
  return minutes * 60;
}

function answerText(value: string): string {
  if (!value.trim()) throw new EssayDraftingError("请先完成申论答案再提交。");
  if (value.length > 10_000) {
    throw new EssayDraftingError("答案不能超过 10000 字。");
  }
  return value;
}

function ocrBinding(sourceArtifactVersionId: string, recognitionRevisionId: string) {
  if (!sourceArtifactVersionId.trim() || !recognitionRevisionId.trim()) {
    throw new EssayDraftingError("OCR 答案必须先完成逐段核对并确认识别版本。");
  }
  return { sourceArtifactVersionId, recognitionRevisionId };
}

function required(value: string, message: string): string {
  const normalized = value.trim();
  if (!normalized) throw new EssayDraftingError(message);
  return normalized;
}

function optional(value: string): string | undefined {
  const normalized = value.trim();
  return normalized || undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function optionalString(value: unknown): boolean {
  return value === undefined || typeof value === "string";
}

function optionalNullableString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === "string";
}

function validReceipt(value: unknown): value is DraftingCompletionReceipt {
  return (
    isRecord(value) &&
    typeof value.attemptId === "string" &&
    value.attemptId.length > 0 &&
    typeof value.answerVersionId === "string" &&
    value.answerVersionId.length > 0 &&
    typeof value.reflectionArtifactVersionId === "string" &&
    value.reflectionArtifactVersionId.length > 0
  );
}

function validCompletionCommand(
  value: unknown,
  context: { taskVersionId: string; planTaskId: string },
): value is EssayDraftingCompletionCommandV2 {
  if (
    !isRecord(value) ||
    value.taskVersionId !== context.taskVersionId ||
    value.planTaskId !== context.planTaskId ||
    typeof value.materialBundleVersionId !== "string" ||
    typeof value.rubricVersionId !== "string" ||
    !Number.isInteger(value.elapsedMinutes) ||
    !optionalNullableString(value.basedOnExecutionVersionId) ||
    !optionalNullableString(value.basedOnReflectionArtifactVersionId) ||
    !isRecord(value.attempt) ||
    !isRecord(value.answer) ||
    !isRecord(value.reflection)
  ) {
    return false;
  }
  const attempt = value.attempt;
  const answer = value.answer;
  const reflection = value.reflection;
  const attemptValid =
    (attempt.mode === "existing" && typeof attempt.attemptId === "string") ||
    (attempt.mode === "create" &&
      typeof attempt.sourceContext === "string" &&
      sourceContextValues.includes(attempt.sourceContext as (typeof sourceContextValues)[number]) &&
      typeof attempt.timedStatus === "string" &&
      timedStatusValues.includes(attempt.timedStatus as (typeof timedStatusValues)[number]) &&
      (attempt.timedStatus === "timed"
        ? Number.isInteger(attempt.elapsedSeconds) && Number(attempt.elapsedSeconds) > 0
        : attempt.elapsedSeconds === undefined || attempt.elapsedSeconds === null));
  const answerValid =
    (answer.mode === "existing" && typeof answer.answerVersionId === "string") ||
    (answer.mode === "create" &&
      typeof answer.sourceType === "string" &&
      answerSourceTypeValues.includes(
        answer.sourceType as (typeof answerSourceTypeValues)[number],
      ) &&
      typeof answer.answerText === "string" &&
      answer.answerText.trim().length > 0 &&
      (answer.sourceType === "ocr_confirmed"
        ? typeof answer.sourceArtifactVersionId === "string" &&
          answer.sourceArtifactVersionId.length > 0 &&
          typeof answer.recognitionRevisionId === "string" &&
          answer.recognitionRevisionId.length > 0
        : answer.sourceArtifactVersionId === undefined &&
          answer.recognitionRevisionId === undefined) &&
      isRecord(answer.thinking) &&
      typeof answer.thinking.userThought === "string" &&
      answer.thinking.userThought.trim().length > 0 &&
      optionalNullableString(answer.basedOnAnswerVersionId));
  const causeCodes = reflection.causeCodes;
  const voiceArtifactVersionIds = reflection.voiceArtifactVersionIds;
  const reflectionValid =
    reflection.schemaType === "reflection" &&
    Array.isArray(causeCodes) &&
    causeCodes.length > 0 &&
    causeCodes.every((item) => reflectionCauseValues.includes(item as ReflectionCauseCode)) &&
    new Set(causeCodes).size === causeCodes.length &&
    typeof reflection.whatIThought === "string" &&
    reflection.whatIThought.trim().length > 0 &&
    typeof reflection.whatIWillChange === "string" &&
    reflection.whatIWillChange.trim().length > 0 &&
    typeof reflection.verificationPlan === "string" &&
    reflection.verificationPlan.trim().length > 0 &&
    Array.isArray(voiceArtifactVersionIds) &&
    voiceArtifactVersionIds.length <= 3 &&
    voiceArtifactVersionIds.every((item) => typeof item === "string" && item.trim().length > 0) &&
    new Set(voiceArtifactVersionIds).size === voiceArtifactVersionIds.length;
  return (
    attemptValid &&
    answerValid &&
    reflectionValid &&
    !(attempt.mode === "create" && answer.mode === "existing")
  );
}

function validDraft(value: unknown): value is EssayDraftingDraft {
  if (!isRecord(value)) return false;
  return (
    typeof value.answerText === "string" &&
    typeof value.userThought === "string" &&
    typeof value.pointFindingText === "string" &&
    typeof value.outlineText === "string" &&
    typeof value.elapsedMinutesText === "string" &&
    typeof value.whatIWillChange === "string" &&
    typeof value.verificationPlan === "string" &&
    Number.isInteger(value.sourceContextIndex) &&
    Number.isInteger(value.timedStatusIndex) &&
    Number.isInteger(value.sourceTypeIndex) &&
    typeof value.sourceArtifactVersionId === "string" &&
    typeof value.recognitionRevisionId === "string" &&
    typeof value.sourceContext === "string" &&
    sourceContextValues.includes(value.sourceContext as (typeof sourceContextValues)[number]) &&
    typeof value.timedStatus === "string" &&
    timedStatusValues.includes(value.timedStatus as (typeof timedStatusValues)[number]) &&
    typeof value.sourceType === "string" &&
    answerSourceTypeValues.includes(value.sourceType as (typeof answerSourceTypeValues)[number]) &&
    (value.sourceType === "typed" ||
      (value.sourceArtifactVersionId.length > 0 && value.recognitionRevisionId.length > 0)) &&
    Array.isArray(value.reflectionCauseCodes) &&
    value.reflectionCauseCodes.every((item: unknown) =>
      reflectionCauseValues.includes(item as ReflectionCauseCode),
    )
  );
}
