import { describe, expect, it } from "vitest";
import type { RecognitionRevision } from "../miniprogram/api/external-learning-contracts";
import type {
  EssayRewriteComparisonV1,
  EssayTaskWorkspace,
} from "../miniprogram/api/essay-intelligence";
import {
  canonicalHandwritingText,
  confirmedHandwritingBinding,
  confirmHandwritingSpan,
  correctionSpans,
  editHandwritingSpan,
  needsRecognitionCorrection,
  reviewingHandwritingState,
} from "../miniprogram/features/essay-handwriting/workflow";
import {
  answerSummary,
  essayWriterUrl,
  materialDirectory,
  outlineViews,
  pointViews,
  rewriteComparisonView,
} from "../miniprogram/features/essay-workbench/presentation";
import { withEssayTaskContext } from "../miniprogram/features/essay-task/capabilities";

describe("stage 5 essay workbench", () => {
  it("forces every handwriting span through explicit user confirmation", () => {
    let state = reviewingHandwritingState(candidateRevision(), "/tmp/answer.jpg");
    expect(state.spans.map((item) => item.spanId)).toEqual(["lower", "higher"]);
    state = editHandwritingSpan(state, "lower", "修订后的第二段");
    expect(needsRecognitionCorrection(state)).toBe(true);
    expect(() => correctionSpans(state)).toThrow("逐段核对");
    state = confirmHandwritingSpan(state, "lower");
    state = confirmHandwritingSpan(state, "higher");
    expect(correctionSpans(state).map((item) => item.spanId)).toEqual(["higher", "lower"]);
    expect(canonicalHandwritingText(state)).toBe("第一段\n修订后的第二段");
  });

  it("only exposes an immutable OCR binding after server confirmation", () => {
    expect(() => confirmedHandwritingBinding(candidateRevision())).toThrow("尚未由用户确认");
    expect(
      confirmedHandwritingBinding({
        ...candidateRevision(),
        state: "confirmed",
        confirmedAt: "2026-07-16T08:00:00+08:00",
      }),
    ).toEqual({
      sourceArtifactVersionId: "artifact-1",
      recognitionRevisionId: "recognition-1",
    });
  });

  it("projects material, point, outline and current answer without a second writer", () => {
    const workspace = workspaceFixture();
    expect(materialDirectory(workspace)).toEqual([
      { materialOrder: 1, label: "材料 1", segmentCount: 2, firstAnchorId: "material-1" },
    ]);
    expect(pointViews(workspace)[0]?.sourceCount).toBe(1);
    expect(outlineViews(workspace)[0]?.plannedWords).toBe(180);
    expect(answerSummary(workspace)).toMatchObject({
      wordCount: 4,
      sourceLabel: "手写 OCR · 已确认",
      hasOriginal: true,
    });
    expect(essayWriterUrl("essay_drafting", "task-1", "plan-1")).toContain(
      "/pages/essay-task/index",
    );
  });

  it("does not crash the today dashboard on a malformed server task URL", () => {
    const malformed = "/pages/essay-task/index?source=%E0%A4%A";
    expect(withEssayTaskContext(malformed, "essay_drafting", "plan-1")).toBe(malformed);
  });

  it("renders backend-owned original/rewrite diff segments", () => {
    const view = rewriteComparisonView({
      rewrite: { status: "passed" },
      originalAnswer: { answerText: "旧答案" },
      rewrittenAnswer: { answerText: "新答案内容" },
      diffSegments: [
        { operation: "delete", text: "旧" },
        { operation: "insert", text: "新" },
        { operation: "equal", text: "答案" },
      ],
    } as unknown as EssayRewriteComparisonV1);
    expect(view.statusLabel).toBe("本轮改写已通过");
    expect(view.segments.map((item) => item.operationLabel)).toEqual(["删除", "新增", "保留"]);
  });
});

function candidateRevision(): RecognitionRevision {
  return {
    contractVersion: "recognition-revision@v1",
    recognitionRevisionId: "recognition-1",
    artifactVersionId: "artifact-1",
    ownerUserId: "user-1",
    revisionNo: 1,
    recognitionKind: "handwriting_ocr",
    state: "candidate",
    processorPolicyVersion: "policy-1",
    processorRoute: "route-1",
    inputSha256: "1".repeat(64),
    outputSha256: "2".repeat(64),
    candidatePayload: {
      kind: "handwriting_ocr",
      textSpans: [
        {
          spanId: "higher",
          text: "第一段",
          confidence: 0.95,
          box: { pageNo: 1, x: 0, y: 0, width: 0.8, height: 0.2 },
        },
        {
          spanId: "lower",
          text: "第二段",
          confidence: 0.61,
          box: { pageNo: 1, x: 0, y: 0.3, width: 0.8, height: 0.2 },
        },
      ],
    },
    confidence: 0.78,
    expiresAt: "2026-07-17T08:00:00+08:00",
    confirmedAt: null,
    createdAt: "2026-07-16T08:00:00+08:00",
  };
}

function workspaceFixture(): EssayTaskWorkspace {
  return {
    task: { taskVersionId: "task-1", wordLimit: 400 },
    material: {
      segments: [
        { materialOrder: 1, segmentOrder: 1 },
        { materialOrder: 1, segmentOrder: 2 },
      ],
    },
    currentArtifacts: {
      point_extraction: {
        payload: {
          schemaType: "point_extraction",
          points: [
            {
              pointId: "point-1",
              category: "problem",
              statement: "责任边界不清",
              sourceSegmentIds: ["segment-1"],
            },
          ],
        },
      },
      outline: {
        payload: {
          schemaType: "outline",
          sections: [
            {
              sectionId: "section-1",
              heading: "问题",
              argument: "先归纳责任问题",
              sourcePointIds: ["point-1"],
              plannedWords: 180,
            },
          ],
        },
      },
    },
    progression: {
      currentAnswer: {
        answerVersionId: "answer-1",
        answerText: "治理有效",
        sourceType: "ocr_confirmed",
        sourceArtifactVersionId: "artifact-1",
        recognitionRevisionId: "recognition-1",
      },
    },
  } as unknown as EssayTaskWorkspace;
}
