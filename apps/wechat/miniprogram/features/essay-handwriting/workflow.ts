import type { RecognitionRevision } from "../../api/external-learning-contracts";
import type { TextSpan } from "../../api/generated/types.gen";

export type HandwritingPhase =
  "idle" | "uploading" | "review" | "confirming" | "confirmed" | "error";

export interface HandwritingSpanDraft extends TextSpan {
  sourceOrder: number;
  originalText: string;
  confidenceLabel: string;
  checked: boolean;
}

export interface EssayHandwritingState {
  phase: HandwritingPhase;
  localImagePath: string;
  progressPercent: number;
  message: string;
  revision: RecognitionRevision | null;
  spans: HandwritingSpanDraft[];
}

export interface EssayOcrBinding {
  sourceArtifactVersionId: string;
  recognitionRevisionId: string;
}

export class EssayHandwritingError extends Error {}

export function emptyHandwritingState(): EssayHandwritingState {
  return {
    phase: "idle",
    localImagePath: "",
    progressPercent: 0,
    message: "",
    revision: null,
    spans: [],
  };
}

export function reviewingHandwritingState(
  revision: RecognitionRevision,
  localImagePath: string,
): EssayHandwritingState {
  if (revision.state !== "candidate" || revision.recognitionKind !== "handwriting_ocr") {
    throw new EssayHandwritingError("没有识别到可逐段核对的手写文字。");
  }
  const spans = recognitionSpans(revision).map((span, sourceOrder) => ({
    ...span,
    sourceOrder,
    originalText: span.text,
    confidenceLabel: `${String(Math.round(span.confidence * 100))}%`,
    checked: false,
  }));
  return {
    phase: "review",
    localImagePath,
    progressPercent: 100,
    message: "识别完成。全部文字块都必须逐段核对；列表已按识别置信度从低到高排列。",
    revision,
    spans: spans.sort((left, right) => left.confidence - right.confidence),
  };
}

export function editHandwritingSpan(
  state: EssayHandwritingState,
  spanId: string,
  text: string,
): EssayHandwritingState {
  return {
    ...state,
    phase: "review",
    spans: state.spans.map((span) =>
      span.spanId === spanId ? { ...span, text, checked: false } : span,
    ),
  };
}

export function confirmHandwritingSpan(
  state: EssayHandwritingState,
  spanId: string,
): EssayHandwritingState {
  return {
    ...state,
    spans: state.spans.map((span) =>
      span.spanId === spanId ? { ...span, checked: !span.checked } : span,
    ),
  };
}

export function correctionSpans(state: EssayHandwritingState): TextSpan[] {
  if (!state.revision || state.spans.length === 0) {
    throw new EssayHandwritingError("当前没有可确认的手写识别结果。");
  }
  if (state.spans.some((span) => !span.checked)) {
    throw new EssayHandwritingError("请逐段核对并勾选全部识别文字。");
  }
  if (state.spans.some((span) => !span.text.trim())) {
    throw new EssayHandwritingError("识别文字不能为空；请按原图补齐后再确认。");
  }
  return [...state.spans]
    .sort((left, right) => left.sourceOrder - right.sourceOrder)
    .map(({ spanId, text, box, confidence }) => ({ spanId, text, box, confidence }));
}

export function needsRecognitionCorrection(state: EssayHandwritingState): boolean {
  return state.spans.some((span) => span.text !== span.originalText);
}

export function canonicalHandwritingText(state: EssayHandwritingState): string {
  correctionSpans(state);
  return [...state.spans]
    .sort((left, right) => left.sourceOrder - right.sourceOrder)
    .map((span) => span.text)
    .join("\n");
}

export function confirmedHandwritingBinding(revision: RecognitionRevision): EssayOcrBinding {
  if (revision.state !== "confirmed" || revision.recognitionKind !== "handwriting_ocr") {
    throw new EssayHandwritingError("手写识别文字还没有完成核对。");
  }
  return {
    sourceArtifactVersionId: revision.artifactVersionId,
    recognitionRevisionId: revision.recognitionRevisionId,
  };
}

function recognitionSpans(revision: RecognitionRevision): TextSpan[] {
  const value = revision.candidatePayload.textSpans;
  if (!Array.isArray(value)) {
    throw new EssayHandwritingError("手写识别结果缺少逐段坐标。");
  }
  return value.map((item) => {
    if (!isTextSpan(item)) {
      throw new EssayHandwritingError("手写识别结果缺少文字位置，请重新识别这一页。");
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
