import type { TodayDashboard } from "../../../miniprogram/api/generated/types.gen";
import type {
  XingceAnswerCheckpoint,
  XingceResultCode,
  XingceTaskMode,
  XingceTaskSession,
} from "../../../miniprogram/api/xingce-learning";

export const xingceIds = {
  planTask: "10000000-0000-4000-8000-000000000001",
  session: "20000000-0000-4000-8000-000000000001",
  attempt: "30000000-0000-4000-8000-000000000001",
  review: "40000000-0000-4000-8000-000000000001",
  reflection: "50000000-0000-4000-8000-000000000001",
  candidate: "55000000-0000-4000-8000-000000000001",
  decision: "60000000-0000-4000-8000-000000000001",
  receipt: "70000000-0000-4000-8000-000000000001",
} as const;

export function todayWithXingceTask(
  mode: XingceTaskMode = "targeted",
  status: "ready" | "in_progress" = "ready",
): TodayDashboard {
  return {
    planVersionId: "80000000-0000-4000-8000-000000000001",
    tasks: [
      {
        id: xingceIds.planTask,
        track: "xingce",
        stage:
          mode === "baseline"
            ? "objective_baseline"
            : mode === "verification"
              ? "objective_verification"
              : "objective_practice",
        status,
        startUrl: `/pages/practice/index?mode=${mode}`,
      },
    ],
  } as unknown as TodayDashboard;
}

export function activeXingceSession(
  options: {
    mode?: XingceTaskMode;
    sharedMaterial?: boolean;
    answers?: XingceAnswerCheckpoint[];
    revision?: number;
  } = {},
): XingceTaskSession {
  const shared = options.sharedMaterial ?? false;
  const materialId = shared ? "material-1" : null;
  return {
    sessionId: xingceIds.session,
    planTaskId: xingceIds.planTask,
    revision: options.revision ?? 1,
    state: "answering",
    mode: options.mode ?? "targeted",
    bundle: {
      bundleVersionId: "a".repeat(64),
      bundleType: shared ? "shared_material" : "single",
      materials: shared
        ? [
            {
              materialId: "material-1",
              title: "2025 年公共服务材料",
              text: "甲地与乙地公共服务数据。",
              html: null,
              assets: [
                {
                  assetId: "asset-1",
                  url: "https://assets.example.test/table.png",
                  altText: "两地公共服务数据表",
                  originalOcrUrl: "https://assets.example.test/table.png",
                  contentKind: "graph",
                },
              ],
              content: [
                {
                  type: "paragraph",
                  blockId: "material-block-1",
                  text: "甲地与乙地公共服务数据。",
                  html: null,
                },
              ],
              anchors: [
                {
                  anchorId: "material-anchor-1",
                  blockId: "material-block-1",
                  label: "材料 1",
                },
              ],
            },
          ]
        : [],
      questions: [
        learnerQuestion("q-1", materialId, "以下说法正确的是？"),
        learnerQuestion("q-2", materialId, "根据材料可以推出的是？"),
        learnerQuestion("q-3", materialId, "下列判断符合题意的是？"),
      ],
    },
    currentQuestionId: "q-1",
    answers: options.answers ?? [],
    totalElapsedSeconds: 12,
    result: null,
    verificationOutcome: null,
    review: null,
    completion: null,
  };
}

export function submittedXingceSession(
  resultCode: Exclude<XingceResultCode, "mixed"> = "incorrect",
): XingceTaskSession {
  const skipped = resultCode === "skipped";
  const active = activeXingceSession({
    answers: ["q-1", "q-2", "q-3"].map((questionId) =>
      skipped ? answer(questionId, [], true) : answer(questionId, ["B"]),
    ),
    revision: 3,
  });
  const correct = resultCode === "correct" || resultCode === "guessed_correct";
  const confidence = resultCode === "guessed_correct" ? "low" : "high";
  const objectiveVerdict = correct ? "correct" : skipped ? "unanswered" : "incorrect";
  const groupOutcome =
    resultCode === "correct" ? "attained" : skipped ? "interrupted" : "not_attained";
  return {
    ...active,
    state: "reflection_required",
    revision: 4,
    result: {
      contractVersion: "xingce-attempt-result@v2",
      attemptId: xingceIds.attempt,
      resultCode,
      correctCount: correct ? 3 : 0,
      totalCount: 3,
      items: active.bundle.questions.map((question) => ({
        questionId: question.questionId,
        resultCode,
        selectedOptionIds: skipped ? [] : ["B"],
        correctOptionIds: ["A"],
        factContractVersion: "xingce-result-facts@v1",
        objectiveVerdict,
        disposition: skipped ? "active_abandon" : "answered",
        confidenceEvidence: skipped ? "unreported" : confidence,
        timingEvidence: {
          status: resultCode === "timeout" ? "advisory_exceeded" : "within_advisory",
          elapsedSeconds: resultCode === "timeout" ? 80 : 30,
          advisorySeconds: 60,
        },
        explanation: "解析只在服务端交卷后返回。",
        explanationSource: "国考题库固定版本",
      })),
      groupSummary: {
        outcome: groupOutcome,
        genuineCorrect: resultCode === "correct" ? 3 : 0,
        guessedCorrect: resultCode === "guessed_correct" ? 3 : 0,
        methodCorrectCalculationError: 0,
        advisoryExceeded: resultCode === "timeout" ? 3 : 0,
        activeAbandon: skipped ? 3 : 0,
        pendingReflection: true,
        pendingCause: false,
        immediateVerification: false,
        scheduled: false,
      },
    },
    verificationOutcome: null,
    review: {
      reviewId: xingceIds.review,
      revision: 1,
      state: "reflection_required",
      aiCandidate: {
        state: "unavailable",
        candidateVersionId: null,
        reason: null,
        causeCode: null,
        summary: null,
        improvement: null,
        question: null,
        rationale: null,
        explanation: null,
        missingEvidence: [],
        evidenceRefs: [],
        limitations: [],
        quotaState: "reserved",
        entitlementPolicyVersion: "metered-model-round@v1",
      },
      reflection: null,
      causeDecision: null,
    },
  };
}

export function reflectedXingceSession(
  resultCode: Exclude<XingceResultCode, "mixed"> = "incorrect",
): XingceTaskSession {
  const submitted = submittedXingceSession(resultCode);
  if (!submitted.review) throw new Error("fixture review is required");
  return {
    ...submitted,
    state: "cause_decision_required",
    revision: 5,
    review: {
      ...submitted.review,
      revision: 2,
      state: "cause_decision_required",
      aiCandidate: {
        state: "candidate_ready",
        candidateVersionId: xingceIds.candidate,
        reason: null,
        causeCode: "question_direction_misread",
        summary: "题干方向识别错误",
        improvement: "先圈出题目问法再看选项。",
        question: null,
        rationale: "用户原话明确提到把不符合看成符合。",
        explanation: null,
        missingEvidence: [],
        evidenceRefs: [xingceIds.reflection],
        limitations: ["只能说明本组作答。"],
        quotaState: "consumed",
        entitlementPolicyVersion: "metered-model-round@v1",
      },
      reflection: {
        reflectionVersionId: xingceIds.reflection,
        rawText: "我把题干中的不符合看成了符合。",
        tags: ["misread"],
        strategyNote: "先圈问题方向。",
        doubtNote: null,
        voiceArtifactVersionIds: [],
        itemInterpretations: [],
        versionNo: 1,
      },
    },
    result: submitted.result
      ? {
          ...submitted.result,
          groupSummary: submitted.result.groupSummary
            ? {
                ...submitted.result.groupSummary,
                pendingReflection: false,
                pendingCause: true,
              }
            : null,
        }
      : null,
  };
}

export function completedXingceSession(
  decision: "replace" | "reject" = "replace",
): XingceTaskSession {
  const reflected = reflectedXingceSession();
  if (!reflected.review) throw new Error("fixture review is required");
  return {
    ...reflected,
    state: "completed",
    revision: 6,
    review: {
      ...reflected.review,
      revision: 3,
      state: "immediate_verification_due",
      causeDecision: {
        decisionVersionId: xingceIds.decision,
        candidateVersionId: xingceIds.candidate,
        confirmedCauseVersionId: decision === "replace" ? xingceIds.decision : null,
        decision,
        replacementCause: decision === "replace" ? "题干方向识别错误" : null,
        versionNo: 1,
      },
    },
    completion: {
      receiptId: xingceIds.receipt,
      settlementStatus: "completed",
      plan: todayWithXingceTask("targeted", "in_progress"),
    },
    result: reflected.result
      ? {
          ...reflected.result,
          groupSummary: reflected.result.groupSummary
            ? {
                ...reflected.result.groupSummary,
                pendingCause: false,
                immediateVerification: true,
              }
            : null,
        }
      : null,
  };
}

export function answer(
  questionId: string,
  selectedOptionIds: string[],
  skipped = false,
): XingceAnswerCheckpoint {
  return {
    questionId,
    selectedOptionIds,
    eliminatedOptionIds: [],
    confidence: selectedOptionIds.length > 0 ? "medium" : null,
    marked: false,
    skipped,
    elapsedSeconds: 8,
    draftText: "",
  };
}

export function apiSuccess(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "xingce-test-request",
      generatedAt: "2026-07-15T05:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}

export function createMemoryWechatStorage(): {
  wxStorage: Pick<
    WechatMiniprogram.Wx,
    "getStorageSync" | "setStorageSync" | "removeStorageSync" | "getStorageInfoSync"
  >;
  values: Map<string, unknown>;
} {
  const values = new Map<string, unknown>();
  const getStoredValue = ((key: string): unknown =>
    values.has(key) ? values.get(key) : "") as WechatMiniprogram.Wx["getStorageSync"];
  return {
    values,
    wxStorage: {
      getStorageSync: getStoredValue,
      setStorageSync: (key: string, value: unknown): void => {
        values.set(key, value);
      },
      removeStorageSync: (key: string): void => {
        values.delete(key);
      },
      getStorageInfoSync: () => ({
        keys: [...values.keys()],
        currentSize: 0,
        limitSize: 10_240,
      }),
    },
  };
}

function learnerQuestion(questionId: string, materialId: string | null, stem: string) {
  return {
    questionId,
    contentVersionId: (questionId === "q-1" ? "b" : "c").repeat(64),
    materialId,
    moduleName: "资料分析",
    stem,
    stemHtml: null,
    stemContent: [
      {
        type: "paragraph" as const,
        blockId: `stem-${questionId}`,
        text: stem,
        html: null,
      },
    ],
    options: [
      learnerOption(questionId, "A"),
      learnerOption(questionId, "B"),
      learnerOption(questionId, "C"),
      learnerOption(questionId, "D"),
    ],
  };
}

function learnerOption(questionId: string, label: string) {
  const text = `选项 ${label}`;
  return {
    optionId: label,
    label,
    text,
    html: null,
    content: [
      {
        type: "paragraph" as const,
        blockId: `option-${questionId}-${label}`,
        text,
        html: null,
      },
    ],
  };
}
