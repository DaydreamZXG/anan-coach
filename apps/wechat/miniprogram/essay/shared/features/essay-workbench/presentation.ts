import type {
  EssayRewriteComparisonV1,
  EssayReviewResponse,
  EssayTaskWorkspace,
} from "../../api/essay-intelligence";
import { routes } from "../../../../app/navigation/routes";
import { essaySeverityLabel } from "../essay-task/presentation";
import { materialTextParagraphs } from "../essay-task/material-paragraphs";

export interface MaterialDirectoryItem {
  materialOrder: number;
  label: string;
  segmentCount: number;
  firstAnchorId: string;
}

export function materialDirectory(workspace: EssayTaskWorkspace): MaterialDirectoryItem[] {
  const orders = [...new Set(workspace.material.segments.map((item) => item.materialOrder))];
  return orders.map((materialOrder) => {
    const segments = workspace.material.segments.filter(
      (item) => item.materialOrder === materialOrder,
    );
    const paragraphCount = segments.reduce((total, item) => {
      const segmentParagraphCount = materialTextParagraphs(item.text, item.segmentId).length;
      return total + Math.max(1, segmentParagraphCount);
    }, 0);
    return {
      materialOrder,
      label: `材料 ${String(materialOrder)}`,
      segmentCount: paragraphCount,
      firstAnchorId: `material-${String(materialOrder)}`,
    };
  });
}

export function pointViews(workspace: EssayTaskWorkspace) {
  const artifact = workspace.currentArtifacts.point_extraction;
  if (!artifact || artifact.payload.schemaType !== "point_extraction") return [];
  return artifact.payload.points.map((point, index) => ({
    ...point,
    order: index + 1,
    sourceCount: point.sourceSegmentIds.length,
  }));
}

export function outlineViews(workspace: EssayTaskWorkspace) {
  const artifact = workspace.currentArtifacts.outline;
  if (!artifact || artifact.payload.schemaType !== "outline") return [];
  return artifact.payload.sections.map((section, index) => ({ ...section, order: index + 1 }));
}

export function answerSummary(workspace: EssayTaskWorkspace) {
  const answer = workspace.progression.currentAnswer;
  if (!answer) return null;
  return {
    answerVersionId: answer.answerVersionId,
    text: answer.answerText,
    wordCount: Array.from(answer.answerText).length,
    sourceLabel: answer.sourceType === "ocr_confirmed" ? "手写识别 · 已核对" : "键盘录入",
    hasOriginal: Boolean(answer.sourceArtifactVersionId && answer.recognitionRevisionId),
  };
}

export function reviewView(review: EssayReviewResponse) {
  const score = review.score;
  const findings = review.findings.map((item, index) => ({
    ...item,
    order: index + 1,
    severityLabel: essaySeverityLabel(item.severity),
    answerQuote: item.answerEvidenceRefs.map((ref) => ref.quote).join("；") || "未定位到答案原句",
    materialQuote:
      item.materialEvidenceRefs.map((ref) => ref.quote).join("；") || "未定位到材料原句",
  }));
  return {
    status: review.status,
    scoreAvailable: Boolean(score),
    scoreLabel: score
      ? `${score.scoreRange.lower.toFixed(1)}–${score.scoreRange.upper.toFixed(1)} / ${score.maximum.toFixed(0)}`
      : "当前评阅没有形成可信分数区间",
    recommendedScore: score?.scoreRange.recommended.toFixed(1) ?? "—",
    maximumScore: score?.maximum.toFixed(0) ?? "—",
    confidenceLabel: score
      ? {
          high: "较高可信",
          medium: "中等可信",
          low: "低可信",
        }[score.confidence]
      : "不可评分",
    confidenceReasons: score?.confidenceReasons ?? [],
    dimensions: review.dimensions.map((item) => ({
      ...item,
      rangeLabel: `${item.scoreRange.lower.toFixed(1)}–${item.scoreRange.upper.toFixed(1)} / ${item.maximum.toFixed(0)}`,
      percent: Math.round((item.scoreRange.recommended / item.maximum) * 100),
      answerQuote: item.answerEvidenceRefs.map((ref) => ref.quote).join("；"),
      materialQuote: item.materialEvidenceRefs.map((ref) => ref.quote).join("；"),
    })),
    findings,
    expressionFindings: findings.filter((item) => item.type === "expression"),
    coveredPoints: review.pointCoverage.filter((item) => item.state === "covered").length,
    totalPoints: review.pointCoverage.length,
  };
}

export function rewriteComparisonView(comparison: EssayRewriteComparisonV1) {
  return {
    statusLabel: comparison.rewrite.status === "passed" ? "本轮改写已通过" : "仍有问题待修正",
    originalWords: Array.from(comparison.originalAnswer.answerText).length,
    rewrittenWords: Array.from(comparison.rewrittenAnswer.answerText).length,
    segments: comparison.diffSegments.map((segment, index) => ({
      ...segment,
      key: `${String(index)}-${segment.operation}`,
      operationLabel: { equal: "保留", delete: "删除", insert: "新增" }[segment.operation],
    })),
  };
}

export function essayWriterUrl(
  stage: "essay_material_reading" | "essay_outlining" | "essay_drafting" | "essay_review",
  taskVersionId: string,
  planTaskId: string,
): string | null {
  if (!taskVersionId.trim() || !planTaskId.trim()) return null;
  return `${routes.essayTask}?stage=${stage}&taskVersionId=${encodeURIComponent(taskVersionId)}&planTaskId=${encodeURIComponent(planTaskId)}`;
}

export function requiredQuery(value: string | undefined, label: string): string {
  const normalized = value?.trim() ?? "";
  if (!normalized) throw new Error(`缺少${label}。`);
  return normalized;
}
