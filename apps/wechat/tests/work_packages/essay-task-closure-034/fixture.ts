import type { EssayTaskWorkspace } from "../../../miniprogram/pages/essay-task/shared/api/essay-intelligence";

export function essayWorkspace(savedReading = true): EssayTaskWorkspace {
  return {
    task: {
      taskVersionId: "task-version-1",
      taskKey: "guokao:2025:essay:1",
      prompt: "根据给定材料，概括基层治理的经验。",
      requirements: "全面、准确，不超过 300 字。",
      questionType: "summary",
      wordLimit: 300,
      maximumScore: 20,
      materialScope: { mode: "explicit", materialOrders: [5] },
    },
    material: {
      materialBundleVersionId: "material-version-1",
      sourceKind: "own_bank",
      segments: [
        {
          materialId: "material-5",
          segmentId: "segment-5",
          materialOrder: 5,
          segmentOrder: 5,
          text: "旧物承载个人记忆，也保留了传统手艺。",
        },
      ],
    },
    rubricAvailability: {
      status: "published",
      rubric: {
        rubricVersionId: "rubric-version-1",
        sourceKind: "system_compiled",
        verificationStatus: "unverified",
        totalMaximum: 20,
        criteria: [
          {
            criterionCode: "content",
            label: "内容",
            maximum: 20,
            scoringRule: "按材料覆盖度给分",
            materialEvidenceRequired: true,
          },
        ],
        points: [],
      },
    },
    currentArtifacts: savedReading
      ? {
          material_annotation: {
            artifactType: "material_annotation",
            artifactVersionId: "material-annotation-version-1",
            createdAt: "2026-08-16T10:00:00Z",
            inputFingerprint: "a".repeat(64),
            materialBundleVersionId: "material-version-1",
            payload: {
              schemaType: "material_annotation",
              overallTheme: "旧物的情感与文化价值",
              annotations: [
                {
                  segmentId: "segment-5",
                  paragraphSummary: "旧物承载记忆并保留传统手艺",
                  subjects: ["旧物", "群众"],
                },
              ],
            },
            scopeKey: "task-version-1:material_annotation",
            taskVersionId: "task-version-1",
            versionNo: 1,
          },
        }
      : {},
    progression: {
      attemptId: null,
      answerVersionId: null,
      reviewVersionId: null,
      reviewJobId: null,
      reviewStatus: null,
      rewriteVersionId: null,
      transferVerificationVersionId: null,
      transferStatus: null,
      currentReview: null,
    },
  };
}

export function currentPlan() {
  return {
    tasks: [
      { id: "plan-reading", status: "complete" as const, title: "梳理材料 5" },
      { id: "plan-points", status: "ready" as const, title: "从材料 5 提炼答案点" },
    ],
  };
}
