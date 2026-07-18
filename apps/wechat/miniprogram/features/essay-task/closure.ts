import type {
  EssayAnswerCreateCommand,
  EssayAttemptCreateCommand,
  EssayFindingDecisionArtifactCommand,
  EssayFindingDecisionV1,
  EssayMasteryStateVersion,
  EssayRewriteCommandV2,
  EssayRewritePlanTaskCompletionCommandV2,
  EssayReviewResponse,
  EssayTaskWorkspace,
  EssayTransferCommandV2,
  EssayTransferPlanTaskCompletionCommandV2,
} from "../../api/essay-intelligence";
import type { EssayRewriteWriterContext, EssayTransferWriterContext } from "./capabilities";

export const closureSourceTypeValues = ["typed", "ocr_confirmed"] as const;
export const closureSourceTypeLabels = ["键盘录入", "OCR 文本已人工核对"];
export const findingResolutionValues = ["", "resolved", "unresolved"] as const;
export const findingResolutionLabels = ["请选择", "已修正", "仍未解决"];
export const findingDecisionValues = ["", "accepted", "rejected", "uncertain"] as const;
export const findingDecisionLabels = ["请选择", "认可", "不认可", "存疑"];

export interface RewriteResolutionDraft {
  findingId: string;
  diagnosis: string;
  action: string;
  status: "" | "resolved" | "unresolved";
  statusIndex: number;
  learnerEvidence: string;
}

export interface EssayRewriteDraft {
  answerText: string;
  sourceType: (typeof closureSourceTypeValues)[number];
  sourceTypeIndex: number;
  sourceArtifactVersionId: string;
  recognitionRevisionId: string;
  userThought: string;
  pointFindingText: string;
  outlineText: string;
  resolutions: RewriteResolutionDraft[];
}

export interface TransferFindingDecisionDraft {
  findingId: string;
  diagnosis: string;
  action: string;
  decision: "" | EssayFindingDecisionV1["decision"];
  decisionIndex: number;
  learnerReason: string;
  followUpQuestion: string;
}

export type EssayTransferPhase =
  | "drafting"
  | "attempt_saved"
  | "answer_saved"
  | "review_queued"
  | "review_ready"
  | "decision_saved"
  | "context_invalid"
  | "completed";

export interface EssayTransferDraft {
  phase: EssayTransferPhase;
  answerText: string;
  sourceType: (typeof closureSourceTypeValues)[number];
  sourceTypeIndex: number;
  sourceArtifactVersionId: string;
  recognitionRevisionId: string;
  userThought: string;
  pointFindingText: string;
  outlineText: string;
  elapsedMinutesText: string;
  attemptId: string;
  answerVersionId: string;
  reviewJobId: string;
  reviewVersionId: string;
  findingDecisionArtifactVersionId: string;
  learnerUnderstanding: string;
  decisions: TransferFindingDecisionDraft[];
  selectedCriterionCodes: string[];
  attemptIdempotencyKey: string;
  answerIdempotencyKey: string;
  reviewIdempotencyKey: string;
  decisionIdempotencyKey: string;
  transferIdempotencyKey: string;
  atomicIdempotencyKey: string;
  atomicCommand?: EssayTransferPlanTaskCompletionCommandV2;
}

export interface CriterionOption {
  criterionCode: string;
  label: string;
  checked: boolean;
}

export function buildRewritePlanTaskCompletionCommand(
  rewrite: EssayRewriteCommandV2,
  planTaskId: string,
  elapsedMinutes: number,
  basedOnExecutionVersionId?: string,
): EssayRewritePlanTaskCompletionCommandV2 {
  validateCompletionContext(planTaskId, elapsedMinutes);
  return {
    rewrite,
    planTaskId,
    elapsedMinutes,
    ...(basedOnExecutionVersionId ? { basedOnExecutionVersionId } : {}),
  };
}

export function buildTransferPlanTaskCompletionCommand(
  transfer: EssayTransferCommandV2,
  planTaskId: string,
  elapsedMinutes: number,
  basedOnExecutionVersionId?: string,
): EssayTransferPlanTaskCompletionCommandV2 {
  validateCompletionContext(planTaskId, elapsedMinutes);
  return {
    transfer,
    planTaskId,
    elapsedMinutes,
    ...(basedOnExecutionVersionId ? { basedOnExecutionVersionId } : {}),
  };
}

function validateCompletionContext(planTaskId: string, elapsedMinutes: number): void {
  if (!planTaskId.trim()) throw new EssayClosureError("闭环命令缺少规划任务版本。");
  if (!Number.isInteger(elapsedMinutes) || elapsedMinutes <= 0 || elapsedMinutes > 1_440) {
    throw new EssayClosureError("闭环任务结算时长必须是 1–1440 分钟。");
  }
}

export class EssayClosureError extends Error {}

export function initialRewriteDraft(
  review: EssayReviewResponse,
  acceptedFindingIds: readonly string[],
): EssayRewriteDraft {
  const accepted = new Set(acceptedFindingIds);
  return {
    answerText: "",
    sourceType: "typed",
    sourceTypeIndex: 0,
    sourceArtifactVersionId: "",
    recognitionRevisionId: "",
    userThought: "",
    pointFindingText: "",
    outlineText: "",
    resolutions: review.findings
      .filter((finding) => accepted.has(finding.findingId))
      .map((finding) => ({
        findingId: finding.findingId,
        diagnosis: finding.diagnosis,
        action: finding.action,
        status: "",
        statusIndex: 0,
        learnerEvidence: "",
      })),
  };
}

export function initialTransferDraft(): EssayTransferDraft {
  return {
    phase: "drafting",
    answerText: "",
    sourceType: "typed",
    sourceTypeIndex: 0,
    sourceArtifactVersionId: "",
    recognitionRevisionId: "",
    userThought: "",
    pointFindingText: "",
    outlineText: "",
    elapsedMinutesText: "",
    attemptId: "",
    answerVersionId: "",
    reviewJobId: "",
    reviewVersionId: "",
    findingDecisionArtifactVersionId: "",
    learnerUnderstanding: "",
    decisions: [],
    selectedCriterionCodes: [],
    attemptIdempotencyKey: "",
    answerIdempotencyKey: "",
    reviewIdempotencyKey: "",
    decisionIdempotencyKey: "",
    transferIdempotencyKey: "",
    atomicIdempotencyKey: "",
  };
}

export function decisionsForReview(review: EssayReviewResponse): TransferFindingDecisionDraft[] {
  return review.findings.map((finding) => ({
    findingId: finding.findingId,
    diagnosis: finding.diagnosis,
    action: finding.action,
    decision: "",
    decisionIndex: 0,
    learnerReason: "",
    followUpQuestion: "",
  }));
}

export function acceptedFindingIdsFromWorkspace(
  workspace: EssayTaskWorkspace,
  expectedArtifactVersionId: string,
  expectedReviewVersionId: string,
): string[] {
  const artifact = workspace.currentArtifacts.review_acknowledgement;
  if (
    !artifact ||
    artifact.artifactVersionId !== expectedArtifactVersionId ||
    artifact.reviewVersionId !== expectedReviewVersionId
  ) {
    throw new EssayClosureError(
      "当前工作区没有规划所绑定的问题决策版本，不能推断本次应修正的问题。",
    );
  }
  const payload = artifact.payload as unknown;
  if (!isRecord(payload) || !Array.isArray(payload.decisions)) {
    throw new EssayClosureError("问题决策版本不是 essay-finding-decision@v1。");
  }
  const accepted = payload.decisions
    .filter(
      (item): item is { findingId: string; decision: "accepted" } =>
        isRecord(item) && item.decision === "accepted" && typeof item.findingId === "string",
    )
    .map((item) => item.findingId);
  if (!accepted.length || new Set(accepted).size !== accepted.length) {
    throw new EssayClosureError("问题决策版本没有唯一的已认可问题集。");
  }
  return accepted;
}

export function buildRewriteCommand(
  context: EssayRewriteWriterContext,
  draft: EssayRewriteDraft,
  acceptedFindingIds: readonly string[],
): EssayRewriteCommandV2 {
  const answerText = answerBody(draft.answerText, "请先完成改写答案。");
  const accepted = [...acceptedFindingIds];
  if (!accepted.length || new Set(accepted).size !== accepted.length) {
    throw new EssayClosureError("本次改写没有可验证的已认可问题。");
  }
  if (
    draft.resolutions.length !== accepted.length ||
    draft.resolutions.some((item) => !accepted.includes(item.findingId)) ||
    new Set(draft.resolutions.map((item) => item.findingId)).size !== accepted.length
  ) {
    throw new EssayClosureError("必须逐条回应本次已认可的所有问题。");
  }
  const resolutions = draft.resolutions.map((item) => {
    if (item.status !== "resolved" && item.status !== "unresolved") {
      throw new EssayClosureError("请逐条选择“已修正”或“仍未解决”。");
    }
    return {
      findingId: item.findingId,
      status: item.status,
      learnerEvidence: required(
        item.learnerEvidence,
        "每个问题都要写明新答案中的修正证据。",
        2_000,
      ),
    };
  });
  const thinking = thinkingSnapshot(draft);
  return {
    attemptId: context.attemptId,
    baseReviewVersionId: context.baseReviewVersionId,
    baseAnswerVersionId: context.baseAnswerVersionId,
    findingDecisionArtifactVersionId: context.findingDecisionArtifactVersionId,
    basedOnAnswerVersionId: context.basedOnAnswerVersionId,
    ...(context.basedOnRewriteVersionId
      ? { basedOnRewriteVersionId: context.basedOnRewriteVersionId }
      : {}),
    sourceType: draft.sourceType,
    ...(draft.sourceType === "ocr_confirmed"
      ? ocrBinding(draft.sourceArtifactVersionId, draft.recognitionRevisionId)
      : {}),
    answerText,
    ...(thinking ? { thinking } : {}),
    resolutions,
  };
}

export function buildTransferAttemptCommand(
  workspace: EssayTaskWorkspace,
  draft: EssayTransferDraft,
  pageElapsedSeconds: number,
): EssayAttemptCreateCommand {
  const rubricVersionId = workspace.rubricAvailability.rubric?.rubricVersionId;
  if (workspace.rubricAvailability.status !== "published" || !rubricVersionId) {
    throw new EssayClosureError("迁移作答必须绑定已发布的评分规程。");
  }
  const manualMinutes = draft.elapsedMinutesText.trim();
  const elapsedSeconds = manualMinutes
    ? parseMinutes(manualMinutes) * 60
    : Math.max(1, Math.floor(pageElapsedSeconds));
  return {
    taskVersionId: workspace.task.taskVersionId,
    materialBundleVersionId: workspace.material.materialBundleVersionId,
    rubricVersionId,
    sourceContext: "in_program",
    timedStatus: "timed",
    elapsedSeconds,
  };
}

export function buildTransferAnswerCommand(
  attemptId: string,
  draft: EssayTransferDraft,
): EssayAnswerCreateCommand {
  const thinking = thinkingSnapshot(draft);
  return {
    attemptId: required(attemptId, "迁移作答记录尚未建立。"),
    sourceType: draft.sourceType,
    ...(draft.sourceType === "ocr_confirmed"
      ? ocrBinding(draft.sourceArtifactVersionId, draft.recognitionRevisionId)
      : {}),
    answerText: answerBody(draft.answerText, "请先完成新题答案。"),
    ...(thinking ? { thinking } : {}),
  };
}

export function buildTransferDecisionArtifact(
  workspace: EssayTaskWorkspace,
  review: EssayReviewResponse,
  draft: EssayTransferDraft,
): EssayFindingDecisionArtifactCommand {
  if (
    review.taskVersionId !== workspace.task.taskVersionId ||
    review.materialBundleVersionId !== workspace.material.materialBundleVersionId ||
    review.answerVersionId !== draft.answerVersionId ||
    review.status !== "completed"
  ) {
    throw new EssayClosureError("新题评阅与当前作答版本不一致。");
  }
  if (
    draft.decisions.length !== review.findings.length ||
    new Set(draft.decisions.map((item) => item.findingId)).size !== review.findings.length ||
    draft.decisions.some(
      (item) => !review.findings.some((finding) => finding.findingId === item.findingId),
    )
  ) {
    throw new EssayClosureError("必须逐条决定新题评阅的所有问题。");
  }
  const decisions = draft.decisions.map((item): EssayFindingDecisionV1 => {
    if (!item.decision) throw new EssayClosureError("请逐条选择认可、不认可或存疑。");
    const learnerReason = item.learnerReason.trim();
    const followUpQuestion = item.followUpQuestion.trim();
    if (item.decision === "rejected" && !learnerReason) {
      throw new EssayClosureError("不认可某个问题时必须写明理由。");
    }
    if (item.decision === "uncertain" && (!learnerReason || !followUpQuestion)) {
      throw new EssayClosureError("存疑问题必须写明理由和待追问内容。");
    }
    return {
      findingId: item.findingId,
      decision: item.decision,
      ...(learnerReason ? { learnerReason } : {}),
      ...(followUpQuestion ? { followUpQuestion } : {}),
    };
  });
  return {
    artifactType: "review_acknowledgement",
    taskVersionId: workspace.task.taskVersionId,
    materialBundleVersionId: workspace.material.materialBundleVersionId,
    rubricVersionId: review.rubricVersionId,
    reviewVersionId: review.reviewVersionId,
    answerVersionId: null,
    basedOnArtifactVersionId: null,
    payload: {
      schemaType: "review_acknowledgement",
      contractVersion: "essay-finding-decision@v1",
      decisions,
      priorityFindingIds: decisions
        .filter((item) => item.decision === "accepted")
        .slice(0, 3)
        .map((item) => item.findingId),
      learnerUnderstanding: required(
        draft.learnerUnderstanding,
        "请用自己的话总结对新题评阅的理解。",
        3_000,
      ),
    },
  };
}

export function criterionOptions(
  sourceReview: EssayReviewResponse,
  transferReview: EssayReviewResponse,
  selected: readonly string[],
): CriterionOption[] {
  const sourceCodes = new Set(sourceReview.dimensions.map((item) => item.criterionCode));
  return transferReview.dimensions
    .filter((item) => sourceCodes.has(item.criterionCode))
    .map((item) => ({
      criterionCode: item.criterionCode,
      label: item.label,
      checked: selected.includes(item.criterionCode),
    }));
}

export function buildTransferCommand(
  context: EssayTransferWriterContext,
  draft: EssayTransferDraft,
  allowedCriterionCodes: readonly string[],
): EssayTransferCommandV2 {
  if (!draft.selectedCriterionCodes.length) {
    throw new EssayClosureError("请至少选择一个源评阅与新评阅共有的验证维度。");
  }
  if (
    new Set(draft.selectedCriterionCodes).size !== draft.selectedCriterionCodes.length ||
    draft.selectedCriterionCodes.some((code) => !allowedCriterionCodes.includes(code))
  ) {
    throw new EssayClosureError("迁移验证维度必须来自源评阅与新评阅的明确交集。");
  }
  return {
    sourceRewriteVersionId: context.sourceRewriteVersionId,
    sourceFindingDecisionArtifactVersionId: context.sourceFindingDecisionArtifactVersionId,
    transferAttemptId: required(draft.attemptId, "迁移作答记录缺失。"),
    transferAnswerVersionId: required(draft.answerVersionId, "迁移答案版本缺失。"),
    transferReviewVersionId: required(draft.reviewVersionId, "迁移评阅版本缺失。"),
    transferFindingDecisionArtifactVersionId: required(
      draft.findingDecisionArtifactVersionId,
      "迁移问题决策版本缺失。",
    ),
    ...(context.basedOnTransferVerificationVersionId
      ? { basedOnTransferVerificationVersionId: context.basedOnTransferVerificationVersionId }
      : {}),
    testedCriterionCodes: [...draft.selectedCriterionCodes],
  };
}

export function masterySummary(items: readonly EssayMasteryStateVersion[]): string {
  const counts = new Map<EssayMasteryStateVersion["state"], number>();
  for (const item of items) counts.set(item.state, (counts.get(item.state) ?? 0) + 1);
  const labels: Array<[EssayMasteryStateVersion["state"], string]> = [
    ["rewrite_due", "待改写"],
    ["transfer_due", "待迁移"],
    ["stable_mastered", "稳定掌握"],
    ["withdrawn", "已撤销"],
  ];
  return labels
    .filter(([state]) => counts.has(state))
    .map(([state, label]) => `${label} ${String(counts.get(state))}`)
    .join("·");
}

function thinkingSnapshot(draft: {
  userThought: string;
  pointFindingText: string;
  outlineText: string;
}) {
  const userThought = draft.userThought.trim();
  const pointFindingText = draft.pointFindingText.trim();
  const outlineText = draft.outlineText.trim();
  if (!userThought && !pointFindingText && !outlineText) return undefined;
  return {
    ...(userThought ? { userThought } : {}),
    ...(pointFindingText ? { pointFindingText } : {}),
    ...(outlineText ? { outlineText } : {}),
  };
}

function parseMinutes(value: string): number {
  const minutes = Number(value);
  if (!Number.isInteger(minutes) || minutes <= 0 || minutes > 1_440) {
    throw new EssayClosureError("作答时长必须是 1–1440 的整数分钟。");
  }
  return minutes;
}

function required(value: string, message: string, maximum?: number): string {
  const normalized = value.trim();
  if (!normalized) throw new EssayClosureError(message);
  if (maximum !== undefined && normalized.length > maximum) {
    throw new EssayClosureError(`${message}内容不能超过 ${String(maximum)} 字。`);
  }
  return normalized;
}

function answerBody(value: string, message: string): string {
  if (!value.trim()) throw new EssayClosureError(message);
  if (value.length > 10_000) {
    throw new EssayClosureError(`${message}内容不能超过 10000 字。`);
  }
  return value;
}

function ocrBinding(sourceArtifactVersionId: string, recognitionRevisionId: string) {
  if (!sourceArtifactVersionId.trim() || !recognitionRevisionId.trim()) {
    throw new EssayClosureError("OCR 答案必须先完成逐段核对并确认识别版本。");
  }
  return { sourceArtifactVersionId, recognitionRevisionId };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
