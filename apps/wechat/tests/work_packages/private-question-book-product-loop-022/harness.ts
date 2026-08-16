import type {
  PrivateQuestionBookDetail,
  PrivateQuestionBookPracticeLaunch,
  TodayDashboard,
} from "../../../miniprogram/api/generated/types.gen";
import type { ImportWorkspace } from "../../../miniprogram/features/external-learning/contracts";
import {
  contentMatch,
  importWorkspace,
  questionUnit,
  recognition,
} from "../../external-learning/fixtures";

export const planTaskId = "plan-task-question-book-022";
export const practiceStartUrl =
  "/xingce/xingce-task-intro/index?mode=targeted&planTaskId=plan-task-question-book-022";

export function bookDetail(
  overrides: Partial<PrivateQuestionBookDetail> = {},
): PrivateQuestionBookDetail {
  return {
    artifactVersionId: "artifact-version-1",
    bookId: "book-022",
    createdAt: "2026-08-14T08:00:00Z",
    importId: "import-1",
    nextUnresolvedQuestionUnitId: null,
    questions: [
      {
        candidateCount: 1,
        confirmedContentVersionId: "content-version-1",
        contentMatchVersionId: "match-version-1",
        ordinal: 1,
        questionUnitId: "question-unit-1",
        state: "matched",
      },
    ],
    recognitionRevisionId: "recognition-1",
    resolvedQuestionCount: 1,
    sourceVersionId: "source-version-1",
    state: "ready",
    title: "名师冲刺题本",
    totalQuestionCount: 1,
    unresolvedQuestionCount: 0,
    ...overrides,
  };
}

export function practiceLaunch(
  overrides: Partial<PrivateQuestionBookPracticeLaunch> = {},
): PrivateQuestionBookPracticeLaunch {
  return {
    bookId: "book-022",
    excludedCounts: {},
    excludedQuestionCount: 0,
    planTaskId,
    selectedQuestionCount: 1,
    sessionId: "session-022",
    sourceVersionId: "source-version-1",
    startUrl: practiceStartUrl,
    ...overrides,
  };
}

export function confirmedWorkspace(overrides: Partial<ImportWorkspace> = {}): ImportWorkspace {
  return importWorkspace({
    state: "ready",
    recognition: recognition({
      recognitionKind: "exam_segmentation",
      state: "confirmed",
      confirmedAt: "2026-08-14T08:00:00Z",
    }),
    recognitionSegments: [
      {
        segmentId: "segment-1",
        pageNo: 1,
        ordinal: 1,
        text: "完整题目",
        confidence: 0.96,
        state: "confirmed",
        requiresCorrection: false,
      },
    ],
    questionUnits: [
      questionUnit({
        questionUnitId: "question-unit-1",
        state: "ready",
        match: contentMatch({
          questionUnitId: "question-unit-1",
          state: "confirmed",
          confirmedContentVersionId: "content-version-1",
        }),
      }),
    ],
    diagnosticEligibility: "eligible_confirmed",
    ...overrides,
  });
}

export function todayDashboard(tasks: TodayDashboard["tasks"]): TodayDashboard {
  return { tasks } as TodayDashboard;
}

export function todayTask(
  overrides: Partial<TodayDashboard["tasks"][number]> = {},
): TodayDashboard["tasks"][number] {
  return {
    id: planTaskId,
    track: "xingce",
    stage: "objective_practice",
    status: "ready",
    startUrl: practiceStartUrl,
    ...overrides,
  } as TodayDashboard["tasks"][number];
}
