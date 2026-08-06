import type { RecognitionRevision } from "../../../../api/external-learning-contracts";
import type { TextSpan } from "../../../../api/generated/types.gen";

export type ExternalScorePhase =
  "idle" | "uploading" | "ocr_review" | "structured_form" | "final_review" | "confirmed" | "error";

export interface ExternalScoreSpanDraft extends TextSpan {
  sourceOrder: number;
  originalText: string;
  confidenceLabel: string;
  checked: boolean;
}

export interface ExternalScoreOcrState {
  phase: ExternalScorePhase;
  localImagePath: string;
  progressPercent: number;
  message: string;
  revision: RecognitionRevision | null;
  spans: ExternalScoreSpanDraft[];
}

export class ExternalScoreWorkflowError extends Error {}

export function emptyExternalScoreState(): ExternalScoreOcrState {
  return {
    phase: "idle",
    localImagePath: "",
    progressPercent: 0,
    message: "",
    revision: null,
    spans: [],
  };
}

export function reviewingExternalScoreState(
  revision: RecognitionRevision,
  localImagePath: string,
): ExternalScoreOcrState {
  if (revision.state !== "candidate" || revision.recognitionKind !== "printed_ocr") {
    throw new ExternalScoreWorkflowError("没有识别到可核对的成绩文字。");
  }
  const spans = recognitionSpans(revision).map((span, sourceOrder) => ({
    ...span,
    sourceOrder,
    originalText: span.text,
    confidenceLabel: `${String(Math.round(span.confidence * 100))}%`,
    checked: false,
  }));
  if (spans.length === 0) {
    throw new ExternalScoreWorkflowError("成绩截图没有识别出可核对文字块。");
  }
  return {
    phase: "ocr_review",
    localImagePath,
    progressPercent: 100,
    message: "请对照原图核对全部文字。识别结果不会直接影响能力判断。",
    revision,
    spans: spans.sort((left, right) => left.confidence - right.confidence),
  };
}

export function editExternalScoreSpan(
  state: ExternalScoreOcrState,
  spanId: string,
  text: string,
): ExternalScoreOcrState {
  return {
    ...state,
    spans: state.spans.map((span) =>
      span.spanId === spanId ? { ...span, text, checked: false } : span,
    ),
  };
}

export function toggleExternalScoreSpan(
  state: ExternalScoreOcrState,
  spanId: string,
): ExternalScoreOcrState {
  return {
    ...state,
    spans: state.spans.map((span) =>
      span.spanId === spanId ? { ...span, checked: !span.checked } : span,
    ),
  };
}

export function verifiedExternalScoreSpans(state: ExternalScoreOcrState): TextSpan[] {
  if (!state.revision || state.spans.length === 0) {
    throw new ExternalScoreWorkflowError("当前没有可确认的成绩识别结果。");
  }
  if (state.spans.some((span) => !span.checked)) {
    throw new ExternalScoreWorkflowError("请逐段核对并勾选全部识别文字。");
  }
  if (state.spans.some((span) => !span.text.trim())) {
    throw new ExternalScoreWorkflowError("文字块不能为空，请对照原图补齐。");
  }
  return [...state.spans]
    .sort((left, right) => left.sourceOrder - right.sourceOrder)
    .map(({ spanId, text, box, confidence }) => ({ spanId, text, box, confidence }));
}

export function externalScoreNeedsCorrection(state: ExternalScoreOcrState): boolean {
  return state.spans.some((span) => span.text !== span.originalText);
}

export function confirmedExternalScoreRevision(revision: RecognitionRevision): RecognitionRevision {
  if (revision.state !== "confirmed" || revision.recognitionKind !== "printed_ocr") {
    throw new ExternalScoreWorkflowError("成绩识别文字还没有完成核对。");
  }
  return revision;
}

function recognitionSpans(revision: RecognitionRevision): TextSpan[] {
  const value = revision.candidatePayload.textSpans;
  if (!Array.isArray(value)) {
    throw new ExternalScoreWorkflowError("成绩识别结果缺少可核对的文字位置。");
  }
  return value.map((item) => {
    if (!isTextSpan(item)) {
      throw new ExternalScoreWorkflowError("成绩识别文字的位置不完整，请重新上传。");
    }
    return item;
  });
}

function isTextSpan(value: unknown): value is TextSpan {
  if (!isRecord(value) || typeof value.spanId !== "string" || typeof value.text !== "string") {
    return false;
  }
  if (typeof value.confidence !== "number" || !isRecord(value.box)) return false;
  return (
    typeof value.box.pageNo === "number" &&
    typeof value.box.x === "number" &&
    typeof value.box.y === "number" &&
    typeof value.box.width === "number" &&
    typeof value.box.height === "number"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
