import type {
  XingceAttemptResult,
  XingceLearnerMaterial,
  XingceLearnerQuestion,
  XingceResultCode,
  XingceTaskSession,
} from "../../../api/xingce-learning";
import type { QuestionAttemptDraft, TaskRuntimeState } from "../../../task-runtime/contracts";
import type { RichContentBlock } from "../../../task-runtime/content-renderer/model";

export interface XingceOptionView {
  optionId: string;
  label: string;
  text: string;
  html: string | null;
  content: RichContentBlock[];
  selected: boolean;
  eliminated: boolean;
}

export interface XingceQuestionView {
  questionId: string;
  contentVersionId: string;
  moduleName: string;
  stem: string;
  stemHtml: string | null;
  stemContent: RichContentBlock[];
  positionLabel: string;
  options: XingceOptionView[];
  material: XingceLearnerMaterial | null;
  selectedOptionIds: string[];
  confidence: QuestionAttemptDraft["confidence"] | null;
  confidenceLabel: string;
  marked: boolean;
  skipped: boolean;
  elapsedLabel: string;
  draftText: string;
}

export interface XingceTaskView {
  title: string;
  modeLabel: string;
  current: number;
  total: number;
  elapsedLabel: string;
  syncLabel: string;
  question: XingceQuestionView;
  materialAvailable: boolean;
  sharedMaterial: boolean;
  answeredCount: number;
  skippedCount: number;
  unansweredCount: number;
}

export interface XingceResultItemView {
  questionId: string;
  resultCode: Exclude<XingceResultCode, "mixed">;
  resultLabel: string;
  resultTone: "success" | "warning" | "danger";
  selectedLabel: string;
  correctLabel: string;
  explanation: string;
  explanationSource: string;
}

export interface XingceResultView {
  headline: string;
  summary: string;
  correctCount: number;
  totalCount: number;
  items: XingceResultItemView[];
  reviewId: string;
}

const confidenceLabels: Record<NonNullable<QuestionAttemptDraft["confidence"]>, string> = {
  low: "不确定",
  medium: "比较确定",
  high: "非常确定",
};

const resultLabels: Record<Exclude<XingceResultCode, "mixed">, string> = {
  correct: "答对",
  incorrect: "答错",
  timeout: "超时",
  guessed_correct: "猜对",
  skipped: "跳过",
};

export function presentXingceTask(
  session: XingceTaskSession,
  runtime: TaskRuntimeState,
): XingceTaskView {
  const questionId = runtime.questionOrder[runtime.currentQuestionIndex];
  const question = session.bundle.questions.find((item) => item.questionId === questionId);
  const attempt = questionId ? runtime.attempts[questionId] : undefined;
  if (!question || !attempt) throw new Error("当前行测题面与作答断点不一致。");
  const answeredCount = runtime.questionOrder.filter(
    (id) => (runtime.attempts[id]?.selectedOptionIds.length ?? 0) > 0,
  ).length;
  const skippedCount = runtime.questionOrder.filter((id) => runtime.attempts[id]?.skipped).length;
  return {
    title: "今日行测真实训练",
    modeLabel: modeLabel(session.mode),
    current: runtime.currentQuestionIndex + 1,
    total: runtime.questionOrder.length,
    elapsedLabel: formatElapsed(runtime.totalElapsedSeconds),
    syncLabel: syncLabel(runtime),
    question: presentQuestion(
      question,
      attempt,
      session.bundle.materials,
      runtime.currentQuestionIndex,
      runtime.questionOrder.length,
    ),
    materialAvailable: question.materialId !== null,
    sharedMaterial: session.bundle.bundleType === "shared_material",
    answeredCount,
    skippedCount,
    unansweredCount: runtime.questionOrder.length - answeredCount - skippedCount,
  };
}

export function presentXingceResult(session: XingceTaskSession): XingceResultView | null {
  const result = session.result;
  const review = session.review;
  if (!result || !review) return null;
  return {
    headline:
      result.correctCount === result.totalCount
        ? "题目全部答对，仍需记录你的判断过程"
        : "结果已确认，复盘完成前不会冒充掌握",
    summary: `${String(result.correctCount)} / ${String(result.totalCount)} 题答对·结果来自固定题面版本`,
    correctCount: result.correctCount,
    totalCount: result.totalCount,
    items: result.items.map((item) => presentResultItem(result, item)),
    reviewId: review.reviewId,
  };
}

export function formatElapsed(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safeSeconds / 60);
  const seconds = safeSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function presentQuestion(
  question: XingceLearnerQuestion,
  attempt: QuestionAttemptDraft,
  materials: XingceLearnerMaterial[],
  index: number,
  total: number,
): XingceQuestionView {
  const selected = new Set(attempt.selectedOptionIds);
  const eliminated = new Set(attempt.eliminatedOptionIds);
  return {
    questionId: question.questionId,
    contentVersionId: question.contentVersionId,
    moduleName: question.moduleName,
    stem: question.stem,
    stemHtml: question.stemHtml,
    stemContent: question.stemContent,
    positionLabel: `第 ${String(index + 1)} / ${String(total)} 题`,
    options: question.options.map((option) => ({
      ...option,
      selected: selected.has(option.optionId),
      eliminated: eliminated.has(option.optionId),
    })),
    material:
      question.materialId === null
        ? null
        : (materials.find((material) => material.materialId === question.materialId) ?? null),
    selectedOptionIds: [...attempt.selectedOptionIds],
    confidence: attempt.confidence ?? null,
    confidenceLabel: attempt.confidence ? confidenceLabels[attempt.confidence] : "未标记",
    marked: attempt.marked,
    skipped: attempt.skipped,
    elapsedLabel: formatElapsed(attempt.elapsedSeconds),
    draftText: attempt.draftText,
  };
}

function presentResultItem(
  _result: XingceAttemptResult,
  item: XingceAttemptResult["items"][number],
): XingceResultItemView {
  return {
    questionId: item.questionId,
    resultCode: item.resultCode,
    resultLabel: resultLabels[item.resultCode],
    resultTone:
      item.resultCode === "correct"
        ? "success"
        : item.resultCode === "guessed_correct" || item.resultCode === "timeout"
          ? "warning"
          : "danger",
    selectedLabel: item.selectedOptionIds.length > 0 ? item.selectedOptionIds.join("、") : "未作答",
    correctLabel: item.correctOptionIds.join("、"),
    explanation: item.explanation ?? "服务端未提供可展示解析。",
    explanationSource: item.explanationSource ?? "解析来源未提供",
  };
}

function syncLabel(runtime: TaskRuntimeState): string {
  if (runtime.syncState === "saving") return "正在保存断点";
  if (runtime.syncState === "pending") return "有修改待保存";
  if (runtime.syncState === "conflict") return "服务端版本已更新";
  if (runtime.syncState === "error") return "保存未确认";
  return `已保存·v${String(runtime.revision)}`;
}

function modeLabel(mode: XingceTaskSession["mode"]): string {
  if (mode === "baseline") return "行测 Agent·受控基线";
  if (mode === "verification") return "行测 Agent·独立验证";
  return "行测 Agent·针对训练";
}
